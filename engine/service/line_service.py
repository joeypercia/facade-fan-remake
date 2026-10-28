"""Line service: new words, voice and lip sync for each line the original drama manager chooses.

The bridge (in the game's Java process) calls this for every line before it plays:

    POST /line  {"char": 0|1, "scripts": [s1, s2, s3, s4]}
        -> {"replace": true, "script": s, "file": n, "text": "..."} or {"replace": false}
    POST /heard {"text": "..."}   what the player typed (tonight's memory)

For a replacement it writes, into %LOCALAPPDATA%\\FacadeFanRemake\\inject:
    <char>_<script>.tok   the line's script record (int32 tokens, the engine's own format) with a new
                          speech block: generated phonemes, cues retimed to the new audio, new subtitle
    <char>_<script>.job   "file=<n>" and "wav=<path>": which voice file the engine will open, and ours
    <char>_<n>_<id>.wav   the generated voice, 22.05 kHz mono 16-bit like the originals
The engine side of the bridge patches its script table from the .tok and serves the .wav when
the engine opens file <n>, then answers with <char>_<script>.ack.

Run: python engine/service/line_service.py   (needs llama-server: prototype/start_llm.py)
"""
import json
import os
import re
import sys
import threading
import time
import uuid
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import requests
import soxr

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "prototype"))
sys.path.insert(0, str(ROOT / "game"))

import facade_formats as ff  # noqa: E402
from catalog import Catalog  # noqa: E402
from config import LLM_URL  # noqa: E402
from phonemes import text_to_codes  # noqa: E402
from story_arc import CHARACTERS, SETTING  # noqa: E402
from voice import same_words  # noqa: E402

FACADE = Path(os.environ.get("FACADE_DIR", r"C:\Program Files (x86)\Facade"))
INJECT = Path(os.environ["LOCALAPPDATA"]) / "FacadeFanRemake" / "inject"
PORT = int(os.environ.get("FFR_SERVICE_PORT", "8095"))
CHARS = {0: "grace", 1: "trip"}
OUT_SR = 22050
# Beats whose "lines" are really the player's name or gender; a generated line just says the name.
NAME_BEATS = {"player", "MALE", "FEMALE"}
MIN_WORDS_TO_REWRITE = 3    # short reactions ("uh...", "ha ha!") keep their original performance


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


class Lines:
    def __init__(self):
        self.catalog = Catalog()
        self.by_id = {(l["character"], l["script_index"]): l for l in self.catalog.lines}
        self.raw = {c: ff.read_records_raw(FACADE, c) for c in CHARS.values()}
        self.perf = {c: ff.read_sbinary(FACADE, c) for c in CHARS.values()}
        self.memory = []      # tonight: ("guest"|"grace"|"trip", text)
        self.player_name = None
        self.lock = threading.Lock()
        from voice import Voices
        self.voices = Voices(self.catalog, mute=True)
        self.voices.load()
        self.voices.warm_thread()  # this thread; request threads warm themselves on first use
        # Warm the LLM too: the first request otherwise pays ~15 s of setup mid-scene.
        requests.post(LLM_URL, timeout=120, json={"messages": [{"role": "user", "content": "Say hi."}],
                                                   "max_tokens": 4, "chat_template_kwargs": {"enable_thinking": False}})
        INJECT.mkdir(parents=True, exist_ok=True)
        for f in INJECT.iterdir():
            f.unlink(missing_ok=True)

    # ---- the brief: what the drama manager wants said ----
    def brief(self, char, scripts):
        parts, primary = [], None
        for s in scripts:
            if s <= 0:
                continue
            l = self.by_id.get((char, s))
            if l is None:
                continue
            if l["beat"] in NAME_BEATS or l["beat"].islower():   # a name fragment
                if l["beat"].islower() and l["beat"] not in NAME_BEATS:
                    self.player_name = l["beat"].capitalize()
                parts.append("{name}")
                continue
            parts.append(l["text"])
            if primary is None and self.perf[char].get(s) and self.perf[char][s].text:
                primary = (s, l)
        return primary, " ".join(parts).replace(" ,", ",")

    def write_words(self, char, line, original):
        name = self.player_name
        if name:
            original = original.replace("{name}", name)
            name_rule = f"The guest is called {name}."
        else:  # the generic "Player" fragment: the original doesn't know the name either
            original = re.sub(r"\s*\{name\}\s*,?", " ", original).strip()
            name_rule = "You don't know the guest's name: never use a name for them."
        direction = " ".join(w.lower() for w in line["direction"]) or "natural"
        recent = "\n".join(f"{who.upper()}: {t}" for who, t in self.memory[-14:])
        prompt = [
            {"role": "system", "content": "\n\n".join([
                SETTING, CHARACTERS["grace"], CHARACTERS["trip"],
                name_rule,
                "You write one line of dialogue at a time for a stage play. The play's director has "
                "already decided who speaks next and what the line must do; you choose the exact words "
                "so they fit what has actually been said tonight.",
                "Rules: keep the same intent, emotion and length (within about a third) as the director's "
                "line. Keep every concrete detail it mentions (objects, names, places, facts, questions): "
                "the next lines of the play follow from them. Change the wording so it reacts to what the "
                "guest actually said and to what was said before; don't just repeat the director's line. "
                "Natural spoken English; no stage directions, no quotation marks, no speaker name. "
                "Output only the line.",
            ])},
            {"role": "user", "content":
                (f"Tonight so far:\n{recent}\n\n" if recent else "") +
                f"{char.upper()} speaks next ({direction}). The director's line: \"{original}\"\n" +
                (f"It must still mention: {', '.join(keys)}.\n" if (keys := key_words(original)) else "") +
                f"At most {max_words(original)} words. Write {char.capitalize()}'s line."},
        ]
        limit = max_words(original)
        for attempt in range(2):
            r = requests.post(LLM_URL, timeout=20, json={
                "messages": prompt, "temperature": 0.7, "top_p": 0.9, "max_tokens": 90,
                "chat_template_kwargs": {"enable_thinking": False},
            })
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"].strip()
            text = re.sub(r'^\s*(grace|trip)\s*[:|]\s*', "", text, flags=re.I).strip().strip('"').strip()
            text = plain_ascii(text.splitlines()[0].strip()) if text else ""
            if text and len(text.split()) <= limit and all(k.lower() in text.lower() for k in keys):
                return text
            prompt.append({"role": "assistant", "content": text})
            prompt.append({"role": "user", "content":
                           f"Too long or missing details. At most {limit} words, and mention: "
                           f"{', '.join(keys) or 'the same things'}. Only the line."})
        return ""  # couldn't get a faithful line: the original plays

    def voice(self, char, line, text):
        from voice import EMOTIONS, emotion_for
        directions = set(line["direction"])
        emotion = next((e for e, (dirs, _) in EMOTIONS.items() if directions & dirs),
                       emotion_for(" ".join(line["direction"])))
        cond = self.voices.conds.get((char, emotion)) or self.voices.conds.get((char, "neutral"))
        if cond is None:
            return None
        self.voices.model.conds = cond
        wav = self.voices.model.generate(text).squeeze(0).cpu().numpy()
        return soxr.resample(wav, self.voices.model.sr, OUT_SR), emotion

    def record(self, char, script, text, audio_s):
        """The original record with its speech block replaced: new phonemes, cues retimed, new text."""
        order, rec = self.raw[char][script]
        perf = self.perf[char][script]
        b = rec.index(ff.OP_SPEECH)
        e = next(i for i in range(b + 1, len(rec)) if rec[i] >= ff.BODY_START)  # next opcode
        orig_end = perf.cues[-1][0] if perf.cues else 1000
        orig_wav = self.by_id[(char, script)].get("audio")
        orig_s = _wav_seconds(FACADE / orig_wav) if orig_wav else orig_end / 1000 + 0.45
        ratio = audio_s / max(orig_s, 0.2)
        speech = [perf.base_frame, 0, 1, ff.PHONEME_START] + text_to_codes(text) + [0]
        for t, cue in perf.cues[:-1]:
            speech += [max(1, int(t * ratio)), cue]
        speech += [max(200, int(orig_end * ratio)), 0, -1, script]
        speech += [ord(c) for c in text.encode("latin-1", "replace").decode("latin-1")] + [-1]
        return rec[:b + 1] + speech + rec[e:]

    def line(self, char_id, scripts):
        t0 = time.perf_counter()
        char = CHARS.get(char_id)
        if not char:
            return {"replace": False}
        primary, original = self.brief(char, scripts)
        if not primary:
            return {"replace": False}
        script, line = primary
        if len(re.findall(r"[A-Za-z']+", original.replace("{name}", ""))) < MIN_WORDS_TO_REWRITE:
            self.memory.append((char, original.replace("{name}", self.player_name or "")))
            return {"replace": False, "why": "short reaction"}
        with self.lock:   # one line at a time: the voice model isn't re-entrant
            text = self.write_words(char, line, original)
            if not text:
                return {"replace": False, "why": "no words"}
            plain = re.sub(r"\s*\{name\}\s*,?", " ", original)
            if same_words(text, plain, threshold=0.85):
                # Essentially the original line: the actor's real performance beats a synthetic copy.
                self.memory.append((char, plain.strip()))
                return {"replace": False, "why": "same as original"}
            t1 = time.perf_counter()
            voiced = self.voice(char, line, text)
            if voiced is None:
                return {"replace": False, "why": "voice off for this character"}
            audio, emotion = voiced
        file_no = self.perf[char][script].base_frame + 1
        wav_path = INJECT / f"{char}_{file_no}_{uuid.uuid4().hex[:8]}.wav"
        _write_wav(wav_path, audio)
        rec = self.record(char, script, text, len(audio) / OUT_SR)
        stem = INJECT / f"{char}_{script}"
        np.array(rec, "<i4").tofile(stem.with_suffix(".tok"))
        stem.with_suffix(".job.tmp").write_text(f"file={file_no}\nwav={wav_path}\n", encoding="utf-8")
        os.replace(stem.with_suffix(".job.tmp"), stem.with_suffix(".job"))  # the job file appears last
        self.memory.append((char, text))
        log(f"{char} #{script} [{emotion}] {time.perf_counter() - t0:.2f}s (words {t1 - t0:.2f}s)\n"
            f"    was: {original}\n    now: {text}")
        return {"replace": True, "script": script, "file": file_no, "text": text}

    def heard(self, text):
        text = text.strip()
        if text and text != "warm up nlu":
            self.memory.append(("guest", text))
            log(f"guest: {text}")


STOPWORDS = set("""a an the and or but if so to of in on at for with from by about as into than then that this
these those there here what when where which who whom why how is are was were be been being am do does did done
have has had having i you he she it we they me him her us them my your his its our their mine yours just really
very oh uh um well yeah yes no not don't can't won't i'm you're it's that's there's let's come go get got make
made know think thought want like look see say said tell one some any all more much little lot way thing things
tonight now again too also only even still back up out over right okay ok please god jesus trip grace""".split())


def key_words(line):
    """Concrete content words of the director's line that the rewrite must keep (at most three)."""
    words = re.findall(r"[A-Za-z']+", re.sub(r"\{name\}", "", line))
    keys = [w for w in words if len(w) >= 4 and w.lower() not in STOPWORDS]
    # Prefer nouns-ish: keep order, drop duplicates, at most three.
    seen, out = set(), []
    for w in keys:
        if w.lower() not in seen:
            seen.add(w.lower())
            out.append(w.lower())
    return out[:3]


def max_words(line):
    n = len(re.findall(r"[A-Za-z']+", line))
    return max(6, int(n * 1.35) + 2)


def plain_ascii(text):
    """The engine's subtitles are 8-bit: fold typographic punctuation to plain ASCII."""
    for a, b in (("—", " - "), ("–", "-"), ("’", "'"), ("‘", "'"), ("“", '"'),
                 ("”", '"'), ("…", "...")):
        text = text.replace(a, b)
    return re.sub(r"\s+", " ", text.encode("ascii", "ignore").decode()).strip()


def _wav_seconds(path):
    try:
        with wave.open(str(path)) as w:
            return w.getnframes() / w.getframerate()
    except (OSError, wave.Error):
        return 1.0


def _write_wav(path, audio):
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(OUT_SR)
        w.writeframes(pcm.tobytes())


LINES = None
_warm = threading.local()


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        try:
            if self.path == "/line":
                if not getattr(_warm, "done", False):  # PyTorch sets up CUDA per thread
                    LINES.voices.warm_thread()
                    _warm.done = True
                out = LINES.line(int(body.get("char", -1)), [int(s) for s in body.get("scripts", [])])
            elif self.path == "/heard":
                LINES.heard(body.get("text", ""))
                out = {"ok": True}
            else:
                self.send_error(404)
                return
        except Exception as e:  # never break the game: the bridge plays the original line instead
            log(f"error: {e!r}")
            out = {"replace": False, "why": repr(e)}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def main():
    global LINES
    log("loading voices and lines...")
    LINES = Lines()
    log(f"ready on 127.0.0.1:{PORT}. Start Façade now.")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
