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
        # Warm the LLM with a real rewrite (same system prompt, so it's cached): the first line of
        # the evening otherwise pays several seconds of setup mid-scene.
        self.write_words("trip", self.by_id[("trip", 1055)], self.by_id[("trip", 1055)]["text"])
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

    def guest_since(self, char, line):
        """What the guest has said since this character last spoke (their line should answer it),
        unless they can't hear the guest (the argument behind the door, other offscreen lines)."""
        if {"OFFSCREEN", "MUTED"} & set(line["direction"]) or line["beat"].startswith("PBEHINDDOOR"):
            return []
        since = []
        for who, t in reversed(self.memory):
            if who == char:
                break
            if who == "guest":
                since.insert(0, t)
        return since

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
        since = self.guest_since(char, line)
        answer = (f"The guest just said: \"{' / '.join(since[-3:])}\". {char.capitalize()} hears it: respond to "
                  f"it naturally (acknowledge it, answer if it's a question) while still doing what the "
                  f"director's line does.\n") if since else ""
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
                f"{char.upper()} speaks next ({direction}). The director's line: \"{original}\"\n" + answer +
                (f"It must still mention: {', '.join(keys)}.\n" if (keys := key_words(original)) else "") +
                f"At most {max_words(original)} words. Write {char.capitalize()}'s line in your own words, "
                f"not the director's."},
        ]
        limit = max(max_words(original) + 8, 18) if since else max_words(original)  # room to answer
        plain = re.sub(r"\s*\{name\}\s*,?", " ", original)
        for attempt in range(3):
            r = requests.post(LLM_URL, timeout=20, json={
                "messages": prompt, "temperature": 0.7, "top_p": 0.9, "max_tokens": 90,
                "chat_template_kwargs": {"enable_thinking": False},
            })
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"].strip()
            text = re.sub(r'^\s*(grace|trip)\s*[:|]\s*', "", text, flags=re.I).strip().strip('"').strip()
            text = plain_ascii(text.splitlines()[0].strip()) if text else ""
            kept = sum(k.lower() in text.lower() for k in keys) if text else 0
            fits = text and len(text.split()) <= limit and kept * 2 >= len(keys)  # at least half the details
            copied = text and same_words(text, plain, threshold=0.85)
            if fits and not copied:
                return text
            prompt.append({"role": "assistant", "content": text})
            prompt.append({"role": "user", "content":
                           "That's the director's line almost word for word. Say it differently, in "
                           f"{char.capitalize()}'s own words" + (" and respond to what the guest said" if since else "") +
                           "." if copied else
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
        audio = soxr.resample(wav, self.voices.model.sr, OUT_SR)
        # Sound like the take it replaces: through the door, down the hall, in the room.
        orig = line.get("audio")
        offscreen = bool({"OFFSCREEN", "MUTED"} & directions) or line["beat"].startswith("PBEHINDDOOR")
        if orig:
            audio = match_take(audio, FACADE / orig, offscreen)
        return audio, emotion

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
        # The script ends at its last cue time; end just after our audio so no words get clipped.
        speech += [int(audio_s * 1000) + 150, 0, -1, script]
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
        short = len(re.findall(r"[A-Za-z']+", original.replace("{name}", ""))) < MIN_WORDS_TO_REWRITE
        if short and not self.guest_since(char, line):
            # A short reaction with nothing to answer: the actor's own "uh..." is better.
            self.memory.append((char, original.replace("{name}", self.player_name or "")))
            return {"replace": False, "why": "short reaction"}
        with self.lock:   # one line at a time: the voice model isn't re-entrant
            text = self.write_words(char, line, original)
            if not text:
                return {"replace": False, "why": "no words"}
            plain = re.sub(r"\s*\{name\}\s*,?", " ", original)
            if same_words(text, plain, threshold=0.85):  # (only if every retry still copied it)
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

    def understand(self, text):
        """Restate what the player typed in the plain phrasing Façade's 2005 parser recognizes, so the
        original drama manager picks the right reaction. The real words go to tonight's memory."""
        text = text.strip()
        if not text or text in (" ", "warm up nlu"):
            return text
        self.heard(text)
        recent = "\n".join(f"{who.upper()}: {t}" for who, t in self.memory[-6:-1])
        r = requests.post(LLM_URL, timeout=10, json={
            "messages": [
                {"role": "system", "content": UNDERSTAND_PROMPT},
                {"role": "user", "content": (f"Just before:\n{recent}\n\n" if recent else "") +
                                            f"The guest typed: {text}"},
            ],
            "temperature": 0.2, "max_tokens": 40, "chat_template_kwargs": {"enable_thinking": False},
        })
        r.raise_for_status()
        out = plain_ascii(r.json()["choices"][0]["message"]["content"].strip().splitlines()[0]).strip('"')
        out = re.sub(r"^(the guest (typed|said|says)|guest|restated)\s*:\s*", "", out, flags=re.I).strip().strip('"')
        if not out or len(out) > 160:
            return text
        log(f"    parser hears: {out}")
        return out


UNDERSTAND_PROMPT = """You help a 2005 keyword-based text parser understand a player in the interactive drama Façade.
The player is a guest at Grace and Trip's apartment. Restate what the guest typed as ONE short, plain, complete
sentence (or two) that keeps the same meaning and makes their intent obvious, using everyday words:
- greetings: "Hi!", "Hello Grace.", "How are you?"
- agreeing / disagreeing: "Yes.", "I agree.", "No.", "I disagree."
- drinks: name the drink plainly: "I'd like a glass of merlot.", "I'll have a beer.", "Just water, please."
- praise / criticism of things or people: "Your apartment is beautiful.", "That painting is ugly."
- feelings and support: "I'm sorry.", "Are you okay?", "I'm on your side, Grace."
- topics: marriage, divorce, sex, therapy, affairs, parents, the Italy trip, the wedding picture, the art.
- flirting, hugging, kissing, leaving: say it plainly ("I want to hug you.", "I should go.").
Fix spelling and slang ("hows it goin" -> "Hi! How are you?"). Keep names (Grace, Trip). If it's addressed to
someone, keep who. Never add things the guest didn't mean. Output only the restated sentence."""

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


def match_take(audio, orig_path, offscreen):
    """Make a generated line sit where the original take sat: match its average tone (a smoothed EQ
    curve from the two spectra, so a muffled through-the-door take muffles ours) and its loudness.
    Offscreen lines also get a short room reverb."""
    import librosa
    try:
        ref, sr = librosa.load(str(orig_path), sr=OUT_SR, mono=True)
    except Exception:
        return audio
    if len(ref) < OUT_SR // 4 or len(audio) < OUT_SR // 4:
        return audio
    n_fft, hop = 1024, 256

    def active_spectrum(x):
        S = np.abs(librosa.stft(x, n_fft=n_fft, hop_length=hop))
        e = S.sum(axis=0)
        keep = e > e.max() * 0.05  # skip silences
        return S[:, keep].mean(axis=1) + 1e-6

    gain_db = 20 * np.log10(active_spectrum(ref) / active_spectrum(audio))
    # Smooth across frequency (~1/3 octave) and limit, so we copy the tone, not the words.
    k = np.maximum(1, (np.arange(len(gain_db)) * 0.12).astype(int))
    smooth = np.array([gain_db[max(0, i - k[i]):i + k[i] + 1].mean() for i in range(len(gain_db))])
    smooth = np.clip(smooth - np.median(smooth), -30, 12)
    S = librosa.stft(audio, n_fft=n_fft, hop_length=hop)
    out = librosa.istft(S * (10 ** (smooth / 20))[:, None], hop_length=hop, length=len(audio))
    if offscreen:  # a little room: exponentially decaying noise burst, ~0.35 s
        n = int(0.35 * OUT_SR)
        ir = np.random.default_rng(0).standard_normal(n) * np.exp(-np.arange(n) / (0.08 * OUT_SR))
        wet = np.convolve(out, ir / np.abs(ir).sum())[: len(out)]
        out = 0.75 * out + 0.6 * wet
    def loud(x):  # RMS of the voiced parts
        frames = librosa.feature.rms(y=x, frame_length=1024, hop_length=256)[0]
        return np.sqrt(np.mean(frames[frames > frames.max() * 0.1] ** 2)) + 1e-9
    out *= loud(ref) / loud(out)
    peak = np.abs(out).max()
    return out * (0.98 / peak) if peak > 0.98 else out


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
JOBS = None  # generation runs on one long-lived thread: PyTorch sets up CUDA per thread (~2 s each)


def worker():
    import queue as _q
    LINES.voices.warm_thread()
    while True:
        args, reply = JOBS.get()
        try:
            reply.put(LINES.line(*args))
        except Exception as e:
            log(f"error: {e!r}")
            reply.put({"replace": False, "why": repr(e)})


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        import queue as _q
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        try:
            if self.path == "/line":
                reply = _q.Queue()
                JOBS.put(((int(body.get("char", -1)), [int(s) for s in body.get("scripts", [])]), reply))
                out = reply.get()
            elif self.path == "/heard":
                LINES.heard(body.get("text", ""))
                out = {"ok": True}
            elif self.path == "/understand":
                out = {"text": LINES.understand(body.get("text", ""))}
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
    global LINES, JOBS
    import queue
    log("loading voices and lines...")
    LINES = Lines()
    JOBS = queue.Queue()
    threading.Thread(target=worker, daemon=True).start()
    log(f"ready on 127.0.0.1:{PORT}. Start Façade now.")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
