"""Line service: new words, voice and lip sync for each line the original drama manager chooses.

The bridge (in the game's Java process) calls this for every line before it plays:

    POST /line  {"char": 0|1, "scripts": [s1, s2, s3, s4]}
        -> {"replace": true, "script": s, "file": n, "text": "...", "perform": "..."} or {"replace": false}
           ("perform": the line's expression, gesture and gaze for the bridge; see performance.py)
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
import improv  # noqa: E402
import performance  # noqa: E402
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
DEADLINE_S = 5.5            # the bridge waits 6.5 s for a line; later than this we don't deliver


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
        self.scene = None     # an off-script scene in progress (improv.Scene): the story is paused
        self.ending = None    # the scene that just ended: the next story line bridges from it
        self.came_in = False  # the guest has been inside the apartment
        self.where = {}       # everyone's position, as of the last request
        self.ended_at = 0.0   # when the last scene ended
        self.hall_done = False  # this trip out to the hallway has had its scene
        self.lines_kind = "story"
        # Scene lines need a record to ride on: an ordinary onscreen line per character (its slot is
        # only borrowed while the story is paused, so the drama manager can't want it meanwhile).
        self.template = {c: self.pick_template(c) for c in CHARS.values()}
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

    def pick_template(self, char):
        fallback = None
        for l in self.catalog.lines:
            s = l["script_index"]
            if (l["character"] != char or not l.get("audio") or {"OFFSCREEN", "MUTED"} & set(l["direction"]) or
                    l["beat"].startswith("PBEHINDDOOR") or l["beat"] in NAME_BEATS or l["beat"].islower() or
                    not (self.perf[char].get(s) and self.perf[char][s].text) or len(l["text"].split()) < 8):
                continue
            if "DEFLECT" in l["beat"]:
                return l
            fallback = fallback or l
        return fallback

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

    def write_words(self, char, line, original, where=None):
        where = where or {}
        place = improv.describe(where, char)
        bridge, self.ending = self.ending, None
        self.lines_kind = "return" if bridge else "story"
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
        if bridge:
            answer += (f"The conversation had wandered off to something else ({bridge.what}) and is now coming "
                       f"back to the evening: open with a few words that close that off naturally, then say "
                       f"the director's line.\n")
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
                (f"Tonight so far:\n{recent}\n\n" if recent else "") + (place + "\n" if place else "") +
                f"{char.upper()} speaks next ({direction}). The director's line: \"{original}\"\n" + answer +
                (f"It must still mention: {', '.join(keys)}.\n" if (keys := key_words(original)) else "") +
                f"At most {max_words(original) + (12 if bridge else 0)} words. Write {char.capitalize()}'s line in your own words, "
                f"not the director's."},
        ]
        limit = max(max_words(original) + 8, 18) if since else max_words(original)  # room to answer
        if bridge:
            limit += 12
        plain = re.sub(r"\s*\{name\}\s*,?", " ", original)
        t0 = time.perf_counter()
        for attempt in range(3):
            if attempt and time.perf_counter() - t0 > 3.5:
                break  # out of time: the original line plays
            r = requests.post(LLM_URL, timeout=4, json={
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

    def improvise(self, char, scene, guest_words):
        """A line inside an off-script scene (improv.Scene): answer the guest, keep it going."""
        name_rule = (f"The guest is called {self.player_name}." if self.player_name else
                     "You don't know the guest's name: never use a name for them.")
        recent = "\n".join(f"{who.upper()}: {t}" for who, t in self.memory[-14:])
        other = "trip" if char == "grace" else "grace"
        place = improv.describe(self.where, char)
        if guest_words:
            task = (f"The guest just said or did: \"{' / '.join(guest_words[-3:])}\". {char.capitalize()} answers "
                    f"that directly: if it's a question or a request to {char.capitalize()}, answer it or do it "
                    f"(or refuse, in character).")
        else:
            task = (f"The guest has gone quiet. {char.capitalize()} keeps the moment going: a question to the "
                    f"guest, a reaction to what was just said, or a remark to {other.capitalize()}.")
        prompt = [
            {"role": "system", "content": "\n\n".join([
                SETTING, CHARACTERS["grace"], CHARACTERS["trip"], name_rule,
                f"You write {char.capitalize()}'s next line in an improvised scene. The guest has taken the "
                "evening somewhere new and the hosts go along with it for real, the way these two people "
                "would: answer, react, joke, play along, build on what was said or done. They are glad to "
                "see their old friend and want the evening to go well, so they're warm with the guest, even "
                "teasing; any sharpness goes at each other, and Grace and Trip's own tensions can still show. "
                "Don't steer back to earlier topics. Say plain, concrete things about what's happening right "
                "now; no cryptic metaphors, and don't keep coming back to the same joke.",
                "Stay consistent with the conversation and with where everyone physically is: never say you "
                "are somewhere you aren't, never invent facts about the guest, and don't speak for "
                f"{other.capitalize()} or the guest. Words in "
                "*asterisks* or (parentheses) are things the guest physically does. Never mention a script, a "
                "play or a director.",
                "Natural spoken English; no stage directions, no quotation marks, no speaker name. At most "
                "25 words. Output only the line.",
            ])},
            {"role": "user", "content":
                (f"Tonight so far:\n{recent}\n\n" if recent else "") +
                f"What's going on: {scene.what}.\n" + (f"{place}\n" if place else "") + task +
                f"\nWrite {char.upper()}'s line."},
        ]
        for _ in range(3):
            r = requests.post(LLM_URL, timeout=4, json={
                "messages": prompt, "temperature": 0.8, "top_p": 0.9, "max_tokens": 70,
                "chat_template_kwargs": {"enable_thinking": False},
            })
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"].strip()
            text = re.sub(r'^\s*(grace|trip)\s*[:|]\s*', "", text, flags=re.I).strip().strip('"').strip()
            text = plain_ascii(re.sub(r"\*[^*]*\*|\([^)]*\)", " ", text.splitlines()[0])) if text else ""
            text = re.sub(r"\s*-\s*([.,!?])", r"\1", re.sub(r"\s+([.,!?])", r"\1", text))  # left by a removed *action*
            repeat = any(same_words(text, t, threshold=0.8) for _, t in self.memory[-10:]) if text else False
            narration = bool(re.match(r"^(\w+ )?(clears|sighs|laughs|looks|nods|smiles|shrugs|pauses|turns)\b",
                                      text, re.I))
            # the model talking about the task ("Sure, here's Trip's line...") instead of doing it
            meta = bool(re.search(r"\b(here's|here is) (\w+'s )?(line|response)|\bin character\b|\b(scene|script)\b",
                                  text, re.I)) or text.lower().startswith(("sure,", "sure!", "certainly"))
            if text and len(text.split()) <= 32 and not repeat and not narration and not meta:
                return text
            if meta or not text:   # start over rather than argue with it (empty: it wrote only an *action*)
                prompt = prompt[:2] + ([] if meta else [
                    {"role": "assistant", "content": r.json()["choices"][0]["message"]["content"].strip()},
                    {"role": "user", "content": f"Only the words {char.capitalize()} says out loud; no actions. "
                                                f"If {char.capitalize()} does something, say it in the words."}])
                continue
            prompt += [{"role": "assistant", "content": text},
                       {"role": "user", "content": "That repeats something already said; say something new." if repeat
                        else "Shorter: at most 28 words. Only the line."}]
        return ""

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

    def line(self, char_id, scripts, where=None):
        t0 = time.perf_counter()
        char = CHARS.get(char_id)
        if not char:
            return {"replace": False}
        primary, original = self.brief(char, scripts)
        if not primary:
            return {"replace": False}
        script, line = primary
        if where:
            self.where = where
            if improv.inside(where):
                self.came_in = True
        if self.pause_story(char, line):
            return {"replace": False, "hold": True}
        short = len(re.findall(r"[A-Za-z']+", original.replace("{name}", ""))) < MIN_WORDS_TO_REWRITE
        if short and not self.guest_since(char, line) and not improv.moment(line["beat"]) and not self.scene:
            # A short reaction with nothing to answer: the actor's own "uh..." is better.
            self.memory.append((char, original.replace("{name}", self.player_name or "")))
            return {"replace": False, "why": "short reaction"}
        with self.lock:   # one line at a time: the voice model isn't re-entrant
            text = fit_subtitle(self.write_words(char, line, original, where))
            if not text:
                return {"replace": False, "why": "no words"}
            plain = re.sub(r"\s*\{name\}\s*,?", " ", original)
            if self.lines_kind == "story" and same_words(text, plain, threshold=0.85):  # (only if every retry still copied it)
                # Essentially the original line: the actor's real performance beats a synthetic copy.
                self.memory.append((char, plain.strip()))
                return {"replace": False, "why": "same as original"}
            t1 = time.perf_counter()
            # The acting (expression, gesture, gaze). Chosen before the voice renders, not alongside
            # it: sharing the GPU slowed both more than doing them one after the other.
            direction = " ".join(w.lower() for w in line["direction"]) or "natural"
            recent = "\n".join(f"{who.upper()}: {t}" for who, t in self.memory[-4:])
            pick = performance.choose(char, text, direction, recent, timeout=1.0)
            t2 = time.perf_counter()
            voiced = self.voice(char, line, text)
            if voiced is None:
                return {"replace": False, "why": "voice off for this character"}
            audio, emotion = voiced
        if time.perf_counter() - t0 > DEADLINE_S:
            # The bridge has (almost) stopped waiting and played the original; writing files now
            # would leave a stale line queued for the next time this script comes up.
            log(f"{char} #{script} too slow ({time.perf_counter() - t0:.1f}s): dropped")
            return {"replace": False, "why": "too slow"}
        out = self.emit(char, script, text, audio, pick)
        log(f"{char} #{script} {line['beat']} {self.lines_kind.upper()} [{emotion}] "
            f"{time.perf_counter() - t0:.2f}s (words {t1 - t0:.2f}s, "
            f"acting {t2 - t1:.2f}s)\n"
            f"    was: {original}\n    now: {text}\n    acting: {pick or 'none chosen'}")
        return out

    def emit(self, char, script, text, audio, pick):
        """Queue a generated line for the engine (see the module docstring) and remember it."""
        file_no = self.perf[char][script].base_frame + 1
        wav_path = INJECT / f"{char}_{file_no}_{uuid.uuid4().hex[:8]}.wav"
        _write_wav(wav_path, audio)
        rec = self.record(char, script, text, len(audio) / OUT_SR)
        stem = INJECT / f"{char}_{script}"
        np.array(rec, "<i4").tofile(stem.with_suffix(".tok"))
        stem.with_suffix(".job.tmp").write_text(f"file={file_no}\nwav={wav_path}\n", encoding="utf-8")
        os.replace(stem.with_suffix(".job.tmp"), stem.with_suffix(".job"))  # the job file appears last
        self.memory.append((char, text))
        perform = performance.spec(char, dict(pick), text, len(audio) / OUT_SR)
        return {"replace": True, "script": script, "file": file_no, "text": text, "perform": perform,
                "ms": int(len(audio) / OUT_SR * 1000)}

    # ---- scenes: the story paused while the guest takes the evening somewhere new ----
    def pause_story(self, char, line):
        """Should this story line wait (the bridge holds it) while a scene plays? Starts a scene when
        the drama manager reaches for a stock off-script reaction or the guest is out in the hallway."""
        if not self.came_in:   # the guest starts out in the hallway; the greeting is the drama manager's
            return False
        if not improv.outside(self.where):
            self.hall_done = False
        if self.scene is None:
            what = improv.moment(line["beat"])
            if what and time.time() - self.ended_at > 15:
                self.scene = improv.Scene(what, "moment")
            elif improv.outside(self.where) and not self.hall_done:
                self.hall_done = True   # one hallway scene per trip out there
                self.scene = improv.Scene("the guest has walked out of the apartment into the hallway", "outside")
            else:
                return False
            for t in self.guest_since(char, line)[-2:]:
                self.scene.heard(t)
            log(f"    scene starts: {self.scene.what}")
        log(f"    story paused at {char} #{line['script_index']} {line['beat']}")
        return True

    def nearer_host(self):
        p = self.where.get("player")
        if not p or not self.where.get("grace") or not self.where.get("trip"):
            return "trip"
        return min(("grace", "trip"), key=lambda c: improv._d(p, self.where[c]))

    def end_scene(self, why):
        s, self.scene = self.scene, None
        self.ending, self.ended_at = s, time.time()
        log(f"    scene ends ({why}) after {time.time() - s.started:.0f}s, {len(s.speakers)} line(s); story resumes")
        return {"end": True}

    def scene_step(self, where, free=(0, 1)):
        """The bridge holds the story and asks what happens next: a scene line, wait, or end.
        `free`: the characters not in the middle of a line (only they can be given one)."""
        s = self.scene
        if s is None:
            return {"end": True}
        if where:
            self.where = where
        free = {CHARS[c] for c in free if c in CHARS}
        if not free:
            return {"wait": True}
        now = time.time()
        if s.why == "outside" and improv.inside(self.where) and not s.inbox:
            return self.end_scene("the guest came back in")
        if s.guest_back and not s.inbox:
            return self.end_scene("the guest is back on the evening")
        if now - s.started > improv.MAX_S:
            return self.end_scene("time")
        words = list(s.inbox)
        if words:
            last = s.speakers[-1] if s.speakers else None
            char = improv.addressed(words[-1]) or last or self.nearer_host()
            if char not in free:
                char = next(iter(free))
            s.inbox.clear()
        elif now - max(s.last_guest, s.last_line) > improv.QUIET_S:
            return self.end_scene("it went quiet")
        elif now - s.last_line > improv.CHIME_S and now - s.last_guest > improv.CHIME_S and s.chimes < improv.MAX_CHIMES:
            last = s.speakers[-1] if s.speakers else "trip"
            char = "grace" if last == "trip" else "trip"
            if char not in free:
                char = next(iter(free))
            s.chimes += 1
        else:
            return {"wait": True}
        t0 = time.perf_counter()
        template = self.template[char]
        with self.lock:
            text = fit_subtitle(self.improvise(char, s, words))
            if not text:
                return {"wait": True}
            pick = performance.choose(char, text, "natural",
                                      "\n".join(f"{w.upper()}: {t}" for w, t in self.memory[-4:]), timeout=1.0)
            audio, emotion = self.voice(char, template, text)
            out = self.emit(char, template["script_index"], text, audio, pick)
        s.spoke(char)
        out.update(say=True, char=next(k for k, v in CHARS.items() if v == char))
        log(f"{char} SCENE [{emotion}] {time.perf_counter() - t0:.2f}s\n    now: {text}\n    acting: {pick or 'none chosen'}")
        return out

    def heard(self, text):
        text = text.strip()
        if text and text != "warm up nlu":
            self.memory.append(("guest", text))
            log(f"guest: {text}")

    def understand(self, text):
        """Restate what the player typed in the plain phrasing Façade's 2005 parser recognizes, so the
        original drama manager picks the right reaction. The real words go to tonight's memory.
        Returns (text for the drama manager, hide): during a scene the scene answers instead, and the
        drama manager (paused) hears nothing."""
        text = text.strip()
        if not text or text in (" ", "warm up nlu"):
            return text, False
        self.heard(text)
        recent = "\n".join(f"{who.upper()}: {t}" for who, t in self.memory[-6:-1])
        if self.came_in and improv.takes_it_somewhere_new(text, recent, self.scene):
            if self.scene:
                self.scene.heard(text)
                return "", True
            self.scene = improv.Scene(f"the guest's \"{text}\"")
            self.scene.heard(text)
            log(f"    scene starts: {self.scene.what}")
            # The story pauses at the drama manager's next line. Until then it hears the raw words, not
            # one of the parser's stock acts ("I should go."), which it would act on.
            plain = re.sub(r"[*()]", " ", text).strip()
            log(f"    parser hears (as typed): {plain}")
            return plain, False
        if self.scene:
            self.scene.guest_back = True   # the guest turned back to the evening: the story picks up
            log("    guest is back on the evening; the scene will close")
        r = requests.post(LLM_URL, timeout=2.5, json={
            "messages": [
                {"role": "system", "content": UNDERSTAND_PROMPT},
                {"role": "user", "content": (f"Just before:\n{recent}\n\n" if recent else "") +
                                            f"The guest typed: {text}"},
            ],
            "temperature": 0.2, "max_tokens": 40, "chat_template_kwargs": {"enable_thinking": False},
        })
        r.raise_for_status()
        out = plain_ascii(r.json()["choices"][0]["message"]["content"].strip().splitlines()[0]).strip('"')
        out = re.sub(r"^(the guest (typed|said|says)|guest|restated)\s*[:,]\s*", "", out, flags=re.I).strip().strip('"')
        if not out or len(out) > 160:
            return text, False
        log(f"    parser hears: {out}")
        return out, False


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
- words in *asterisks* or (parentheses) are actions: say what the guest does or means ("*hugs Grace*" ->
  "I want to hug you, Grace."; "*sits on the couch*" -> "I'll sit down.").
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


MAX_SUBTITLE = 140  # longer subtitles overran the engine's buffer (garbage after ~150 characters)


def fit_subtitle(text):
    """At most MAX_SUBTITLE characters: cut after the last whole sentence that fits, else at a word."""
    if len(text) <= MAX_SUBTITLE:
        return text
    head = text[:MAX_SUBTITLE]
    end = max(head.rfind(". "), head.rfind("? "), head.rfind("! "))
    return head[:end + 1] if end > 40 else head[:head.rfind(" ")].rstrip(",;:- ") + "..."


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
        fn, args, reply = JOBS.get()
        try:
            reply.put(fn(*args))
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
                JOBS.put((LINES.line, (int(body.get("char", -1)), [int(s) for s in body.get("scripts", [])],
                                       body.get("where") or {}), reply))
                out = reply.get()
            elif self.path == "/scene":
                reply = _q.Queue()
                JOBS.put((LINES.scene_step, (body.get("where") or {}, body.get("free", [0, 1])), reply))
                out = reply.get()
            elif self.path == "/heard":
                LINES.heard(body.get("text", ""))
                out = {"ok": True}
            elif self.path == "/understand":
                text, hide = LINES.understand(body.get("text", ""))
                out = {"text": text, "hide": hide}
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
