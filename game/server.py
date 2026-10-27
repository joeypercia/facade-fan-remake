"""Local game server: serves the client, the player's own assets, and runs the evening.

    python game/server.py            then open http://127.0.0.1:8090
    FACADE_VOICE=0 python game/server.py   subtitles only (no voice model; useful when the GPU is busy)

Listens on 127.0.0.1 only. Needs llama-server running (prototype/start_llm.py).

Events go to the browser over Server-Sent Events (/api/events); the browser sends what the guest
types with POST /api/say. One evening at a time.
"""
import asyncio
import base64
import json
import os
import queue
import sys
import threading
import time
from pathlib import Path

import numpy as np
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "prototype"))

import assets  # noqa: E402
from catalog import Catalog  # noqa: E402
from director import Director  # noqa: E402
from story_arc import ARC, SPOTS  # noqa: E402

VOICE = os.environ.get("FACADE_VOICE", "1") != "0"

app = FastAPI()
app.mount("/client", StaticFiles(directory=HERE / "client"), name="client")


class Game:
    """One evening: the director, the voices, and the event stream to the browser."""

    def __init__(self):
        self.catalog = Catalog()
        self.voices = None
        if VOICE:
            from voice import Voices
            self.voices = Voices(self.catalog, mute=True)
            self.voices.load()
            self.voices.player = _Sink(self)
        self.director = None
        self.listeners = []
        self.jobs = queue.Queue()   # ("start", name, pronouns) or ("say", text)
        self.line_seq = 0
        # One long-lived worker runs the whole evening: PyTorch sets up CUDA per thread, so a
        # fresh thread per turn would add ~1 s before every reply.
        threading.Thread(target=self._worker, daemon=True).start()

    def emit(self, event):
        for q, loop in list(self.listeners):
            loop.call_soon_threadsafe(q.put_nowait, event)

    def start(self, name, pronouns):
        self.jobs.put(("start", name, pronouns))

    def say(self, text):
        self.jobs.put(("say", text))

    def _worker(self):
        if self.voices:
            self.voices.warm_thread()
        while True:
            job = self.jobs.get()
            try:
                if job[0] == "start":
                    self.director = Director(self.catalog, job[1], job[2])
                    self.director.prime()
                    self.emit({"type": "beat", "index": 0, "title": ARC[0]["title"], "staging": ARC[0]["staging"]})
                    self.emit({"type": "cue", "text": "", "sfx": ARC[0].get("sfx", [])})
                    self._turn(self.director.opening())
                elif self.director and not self.director.ended:
                    text = job[1]
                    # Several messages typed while they were talking count as one turn.
                    while not self.jobs.empty() and self.jobs.queue[0][0] == "say":
                        text += " " + self.jobs.get()[1]
                    self._turn(self.director.turn(text))
            except Exception as e:  # keep the evening alive; show the error in the console
                self.emit({"type": "error", "message": repr(e)})
                import traceback
                traceback.print_exc()

    def _turn(self, lines):
        self.emit({"type": "thinking"})
        for line in lines:
            self.line_seq += 1
            self.current = self.line_seq
            from voice import emotion_for
            self.emit({"type": "line", "id": self.line_seq, "speaker": line.speaker, "text": line.text,
                       "delivery": line.delivery, "emotion": emotion_for(line.delivery),
                       "continues": line.part_of_previous})
            info = self.voices.say(line) if self.voices else {"source": "subtitle"}
            self.emit({"type": "line_end", "id": self.line_seq, "source": info.get("source")})
        for ev in self.director.events:
            if ev["type"] == "beat":
                ev["staging"] = ARC[ev["index"]]["staging"]
            self.emit(ev)
        self.emit({"type": "turn_end"})
        if self.director.intro_pending and not self.director.ended:
            self._cue(self.director.beat)
            self._turn(self.director.beat_intro())  # the characters open the new beat themselves

    def _cue(self, beat):
        """Show the beat's opening stage direction and play its sound effects."""
        if beat.get("cue"):
            self.emit({"type": "cue", "text": beat["cue"], "sfx": beat.get("sfx", [])})

    def audio(self, chunk):
        pcm = (np.clip(chunk, -1, 1) * 32767).astype("<i2").tobytes()
        self.emit({"type": "audio", "id": self.current, "pcm": base64.b64encode(pcm).decode()})


class _Sink:
    """Stands in for the speaker: voice chunks go to the browser instead."""

    def __init__(self, game):
        self.game = game

    def push(self, x):
        self.game.audio(np.asarray(x, dtype="float32"))

    def wait(self):
        pass


GAME = None


@app.on_event("startup")
def _startup():
    global GAME
    t = time.perf_counter()
    GAME = Game()
    print(f"game ready in {time.perf_counter() - t:.1f}s (voice {'on' if VOICE else 'off'})", flush=True)


@app.get("/")
def index():
    return FileResponse(HERE / "client" / "index.html")


@app.get("/api/scene")
def scene():
    return {**assets.scene(), "spots": SPOTS, "voice": VOICE}


@app.get("/api/tex/{name}.png")
def texture(name: str):
    try:
        return Response(assets.texture_png(name), media_type="image/png",
                        headers={"Cache-Control": "max-age=3600"})
    except (FileNotFoundError, OSError):
        raise HTTPException(404)


@app.get("/api/sfx/{name}.wav")
def sfx(name: str):
    path = assets.sound_effect(name)
    if path is None:
        raise HTTPException(404)
    return FileResponse(path, media_type="audio/wav", headers={"Cache-Control": "max-age=3600"})


@app.get("/api/anim/{char}.json")
def anim_info(char: str):
    if char not in ("grace", "trip"):
        raise HTTPException(404)
    return assets.animation(char)[0]


@app.get("/api/anim/{char}.bin")
def anim_frames(char: str):
    if char not in ("grace", "trip"):
        raise HTTPException(404)
    return Response(assets.animation(char)[1], media_type="application/octet-stream")


@app.post("/api/start")
async def start(req: Request):
    body = await req.json()
    name = (body.get("name") or "Joe").strip()[:30]
    pronouns = (body.get("pronouns") or "they/them").strip()[:20]
    GAME.start(name, pronouns)
    return {"ok": True}


@app.post("/api/say")
async def say(req: Request):
    text = ((await req.json()).get("text") or "").strip()[:400]
    if text:
        GAME.say(text)
    return {"ok": True}


@app.get("/api/events")
async def events(req: Request):
    q, loop = asyncio.Queue(), asyncio.get_running_loop()
    GAME.listeners.append((q, loop))

    async def stream():
        try:
            yield ": connected\n\n"
            while not await req.is_disconnected():
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(ev)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            GAME.listeners.remove((q, loop))

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("FACADE_PORT", "8090")), log_level="warning")
