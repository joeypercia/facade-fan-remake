"""Line service: answers, voice and lip sync for the lines the original drama manager chooses.

A story line plays as written (the actor's own take) unless the guest has said something it should
answer: then the character says a few generated words first, and the actor's take follows them.

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
DEADLINE_S = 5.5            # the bridge waits 6.5 s for a line; later than this we don't deliver
UNANSWERED_S = 25           # the guest's words still want an answer for this long
GAP_S = 0.12                # between a lead-in and the actor's take
# Sampling for generated lines. (llama.cpp's DRY penalty against repeated runs of words garbled the
# text in testing: "a lot_of good stories", "butI", a stray "</think>". Repeats are caught by
# `repeats` instead.)
SAMPLING = {"top_p": 0.9, "chat_template_kwargs": {"enable_thinking": False}}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


class Lines:
    def __init__(self):
        self.catalog = Catalog()
        self.by_id = {(l["character"], l["script_index"]): l for l in self.catalog.lines}
        self.raw = {c: ff.read_records_raw(FACADE, c) for c in CHARS.values()}
        self.perf = {c: ff.read_sbinary(FACADE, c) for c in CHARS.values()}
        self.memory = []      # tonight: ("guest"|"grace"|"trip", text)
        self.unanswered = []  # (time, words) the guest said that no line has answered yet
        self.generated = []   # tonight's generated words (lead-ins, scene lines), so none comes back
        self.player_name = None
        self.scene = None     # an off-script scene in progress (improv.Scene): the story is paused
        self.ending = None    # the scene that just ended: the next story line bridges from it
        self.came_in = False  # the guest has been inside the apartment
        self.where = {}       # everyone's position, as of the last request
        self.ended_at = 0.0   # when the last scene ended
        self.hall_done = False  # this trip out to the hallway has had its scene
        self.lines_kind = "answer"
        # Scene lines need a record to ride on: an ordinary onscreen line per character (its slot is
        # only borrowed while the story is paused, so the drama manager can't want it meanwhile).
        self.template = {c: self.pick_template(c) for c in CHARS.values()}
        self.lock = threading.Lock()
        from voice import Voices
        self.voices = Voices(self.catalog, mute=True)
        self.voices.load()
        self.voices.warm_thread()  # this thread; request threads warm themselves on first use
        # Warm the LLM with a real lead-in (same system prompt, so it's cached): the first answer of
        # the evening otherwise pays several seconds of setup mid-scene.
        warm = self.by_id[("trip", 1074)]
        self.lead_in("trip", warm, warm["text"], ["hi!"], None)
        self.restate("hi!", "")   # (the first typed line of the evening timed out without this)
        improv.takes_it_somewhere_new("hi!", "")
        self.generated = []
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
        """What the guest has said that no line has answered yet (the next line should), unless this
        character can't hear the guest (the argument behind the door, other offscreen lines). Words
        older than UNANSWERED_S have been overtaken by the conversation and are dropped."""
        if {"OFFSCREEN", "MUTED"} & set(line["direction"]) or line["beat"].startswith("PBEHINDDOOR"):
            return []
        self.unanswered = [(t, w) for t, w in self.unanswered if time.time() - t < UNANSWERED_S]
        return [w for _, w in self.unanswered]

    def lead_in(self, char, line, original, since, bridge):
        """A few words for the character to say before the drama manager's own line, so it fits what
        was just said: an answer to the guest's words, or a way back from a scene. "" when the line
        already does that (or nothing fits): then the line plays as written."""
        place = improv.describe(self.where, char)
        name_rule = (f"The guest is called {self.player_name}." if self.player_name else
                     "You don't know the guest's name: never use a name for them.")
        recent = "\n".join(f"{who.upper()}: {t}" for who, t in self.memory[-12:])
        Char = char.capitalize()
        task = f"In a moment {Char} will say this line from the play: \"{original}\"\nBut first, "
        if since:
            task += f"{Char} replies to what the guest just said: \"{' / '.join(since[-3:])}\"."
        if bridge:
            task += (f"{' And' if since else ''} {Char} closes off what the conversation had wandered into "
                     f"({bridge.what}), coming back to the evening.")
        task += f"\nWrite only that reply (not the play's line): {Char}'s words, at most 12."
        prompt = [
            {"role": "system", "content": "\n\n".join([
                SETTING, CHARACTERS["grace"], CHARACTERS["trip"], name_rule,
                "You write a character's quick reply to what the guest just said. Right after it, the character "
                "goes on with a line of the play (given to you), so the reply must lead naturally into that line "
                "without contradicting it, repeating it or saying what it is about to say.",
                "Rules: one short sentence, at most 12 words. Reply to the guest's actual words, as the "
                "character would: answer a question (or dodge it, in character: Grace and Trip hide their "
                "troubles from guests), return a greeting, acknowledge a remark. Take the guest's words at face "
                "value: greetings and ordinary questions get ordinary, friendly replies, never shock or offence. "
                "Stay consistent with the conversation and with where everyone is; never invent facts about "
                "the guest, and no pets, visitors or events that aren't in the play. Natural spoken English; no "
                "stage directions, no quotation marks, no speaker name. Don't reuse words from the play's line "
                "or from earlier lines. Output only the reply.\n"
                "Examples of the kind of reply (write your own, for this moment):\n"
                "guest \"you guys ok?\", line \"So, can I get you a drink?\" -> \"Oh, we're great, totally fine.\"\n"
                "guest \"what was that noise?\", line \"So, what do you think of the painting?\" -> \"Oh, that? "
                "Nothing, just us being silly.\"",
            ])},
            {"role": "user", "content":
                (f"Tonight so far:\n{recent}\n\n" if recent else "") + (place + "\n" if place else "") + task},
        ]
        earlier = self.generated[-30:] + [t for who, t in self.memory[-20:] if who == char]
        for _ in range(2):
            r = requests.post(LLM_URL, timeout=3, json={
                "messages": prompt, "temperature": 0.5, "max_tokens": 40, **SAMPLING,
            })
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"].strip()
            text = re.sub(r'^\s*(grace|trip)\s*[:|]\s*', "", text, flags=re.I).strip().strip('"').strip()
            text = plain_ascii(re.sub(r"\*[^*]*\*|\([^)]*\)", " ", text.splitlines()[0])) if text else ""
            if not text or re.fullmatch(r"\W*none\W*", text, re.I):
                return ""
            text = text[0].upper() + text[1:]
            meta = bool(re.search(r"\b(here's|here is) (\w+'s )?(line|response)|\b(scene|script|director)\b",
                                  text, re.I)) or text.lower().startswith(("sure,", "sure!", "certainly"))
            echo = same_words(text, original, threshold=0.6)
            repeat = repeats(text, earlier)
            # "Hey, everything's fine. Hey! God it's been so long...": the same word twice in a row
            same_start = first_word(text) == first_word(original)
            if len(text.split()) <= 18 and not (meta or echo or repeat or same_start):
                return text
            prompt += [{"role": "assistant", "content": text},
                       {"role": "user", "content":
                           "That repeats something already said tonight; reply with something new." if repeat else
                           f"The line starts with \"{first_word(original)}\" too; start the reply differently."
                           if same_start else "Only the short reply (at most 12 words), not the line itself."}]
        return ""

    def improvise(self, char, scene, guest_words):
        """A line inside an off-script scene (improv.Scene): answer the guest, keep it going."""
        name_rule = (f"The guest is called {self.player_name}." if self.player_name else
                     "You don't know the guest's name: never use a name for them.")
        recent = "\n".join(f"{who.upper()}: {t}" for who, t in self.memory[-14:])
        other = "trip" if char == "grace" else "grace"
        place = improv.describe(self.where, char)
        last = next(((who, t) for who, t in reversed(self.memory) if who in ("grace", "trip")), None)
        said_last = ""
        if last and last[0] != char:   # "I didn't say anything about the ceiling" (Grace had)
            said_last = (f" The last host line was {last[0].capitalize()}'s, not {char.capitalize()}'s: "
                         f"\"{last[1]}\".")
        if guest_words:
            task = (f"The guest just said or did: \"{' / '.join(guest_words[-3:])}\".{said_last} {char.capitalize()} "
                    f"answers the guest directly: if it's a question or a request to {char.capitalize()}, answer it "
                    f"or do it (or refuse, in character).")
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
                "now; no cryptic metaphors, and don't keep coming back to the same joke. Take the guest's "
                "words at face value: an ordinary question gets a straight answer, never shock or offence.",
                "Stay consistent with the conversation and with where everyone physically is: never say you "
                "are somewhere you aren't, never invent facts about the guest, and don't speak for "
                f"{other.capitalize()} or the guest. Words in "
                "*asterisks* or (parentheses) are things the guest physically does. Never mention a script, a "
                "play or a director.",
                "Natural spoken English; no stage directions, no quotation marks, no speaker name. One or "
                "two short sentences, at most 16 words. Never reuse a phrase or sentence pattern from earlier "
                "lines (no \"You're not the first...\" twice), and don't tack an offer or question on the end out "
                "of habit (\"Want a drink?\"). Output only the line.",
            ])},
            {"role": "user", "content":
                (f"Tonight so far:\n{recent}\n\n" if recent else "") +
                f"What's going on: {scene.what}.\n" + (f"{place}\n" if place else "") + task +
                f"\nWrite {char.upper()}'s line."},
        ]
        earlier = self.generated[-30:] + [t for _, t in self.memory[-20:]]
        for attempt in range(3):
            r = requests.post(LLM_URL, timeout=4, json={
                "messages": prompt, "temperature": 0.75 + 0.1 * attempt, "max_tokens": 60, **SAMPLING,
            })
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"].strip()
            text = re.sub(r'^\s*(grace|trip)\s*[:|]\s*', "", text, flags=re.I).strip().strip('"').strip()
            text = plain_ascii(re.sub(r"\*[^*]*\*|\([^)]*\)", " ", text.splitlines()[0])) if text else ""
            text = re.sub(r"\s*-\s*([.,!?])", r"\1", re.sub(r"\s+([.,!?])", r"\1", text))  # left by a removed *action*
            # too long: keep the whole sentences that fit (a long first sentence only if it's not very long)
            text = first_sentences(text, 18) or (text if len(text.split()) <= 24 else "")
            repeat = repeats(text, earlier) if text else False
            narration = bool(re.match(r"^(\w+ )?(clears|sighs|laughs|looks|nods|smiles|shrugs|pauses|turns)\b",
                                      text, re.I))
            # the model talking about the task ("Sure, here's Trip's line...") instead of doing it
            meta = bool(re.search(r"\b(here's|here is) (\w+'s )?(line|response)|\bin character\b|\b(scene|script)\b",
                                  text, re.I)) or text.lower().startswith(("sure,", "sure!", "certainly"))
            if text and not repeat and not narration and not meta:
                return text
            log(f"    (scene line rejected: {'repeat' if repeat else 'meta' if meta else 'other'}: {text})")
            if meta or repeat or not text:
                # Start over rather than argue with it: shown its own attempt, it rewrote the same line
                # ("I heard that. It sounded like a knock..." three times). Empty: it wrote only an *action*.
                if repeat:
                    prompt[1] = {"role": "user", "content": prompt[1]["content"] +
                                 f"\n(Already said tonight, so not this: \"{text}\")"}
                prompt = prompt[:2] + ([] if text else [
                    {"role": "assistant", "content": r.json()["choices"][0]["message"]["content"].strip()},
                    {"role": "user", "content": f"Only the words {char.capitalize()} says out loud; no actions. "
                                                f"If {char.capitalize()} does something, say it in the words."}])
                continue
            prompt += [{"role": "assistant", "content": text},
                       {"role": "user", "content": "Only the words, nothing else."}]
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

    def record(self, char, script, text, audio_s, audio=None):
        """The original record with its speech block replaced: new phonemes, cues retimed, new text.
        With `audio`, the phonemes go in one phrase per stretch of the text (split at punctuation),
        each starting when it's spoken, as in the actor's records; otherwise all in one phrase."""
        order, rec = self.raw[char][script]
        perf = self.perf[char][script]
        b = rec.index(ff.OP_SPEECH)
        e = next(i for i in range(b + 1, len(rec)) if rec[i] >= ff.BODY_START)  # next opcode
        orig_end = perf.cues[-1][0] if perf.cues else 1000
        orig_wav = self.by_id[(char, script)].get("audio")
        orig_s = _wav_seconds(FACADE / orig_wav) if orig_wav else orig_end / 1000 + 0.45
        ratio = audio_s / max(orig_s, 0.2)
        phrases = phrase_times(text, audio) if audio is not None else [(0, text)]
        events = [(t, 0, [ff.PHONEME_START] + text_to_codes(chunk) + [0]) for t, chunk in phrases[1:]]
        events += [(max(1, int(t * ratio)), 1, [cue]) for t, cue in perf.cues[:-1]]
        speech = [perf.base_frame, 0, 1, ff.PHONEME_START] + text_to_codes(phrases[0][1]) + [0]
        for t, _, rest in sorted(events, key=lambda ev: (ev[0], ev[1])):
            speech += [t] + rest
        # The script ends at its last cue time; end just after our audio so no words get clipped.
        speech += [int(audio_s * 1000) + 150, 0, -1, script]
        speech += [ord(c) for c in text.encode("latin-1", "replace").decode("latin-1")] + [-1]
        return rec[:b + 1] + speech + rec[e:]

    def takes(self, char, scripts):
        """The actor's recordings of a line, in order (name fragments included), as [(script, audio)];
        None if one is missing (then the line can only be voiced whole)."""
        out = []
        for s in scripts:
            if s <= 0:
                continue
            l, p = self.by_id.get((char, s)), self.perf[char].get(s)
            if not (l and l.get("audio") and p and p.cues):
                return None
            out.append((s, _read_wav(FACADE / l["audio"])))
        return out or None

    def joined_record(self, char, script, lead, lead_s, takes, text, total_s):
        """`script`'s record with a speech block for lead-in + takes: the lead-in's phonemes from 0,
        then each take's own phrases and cues, moved to where that take starts in the joined audio
        (not stretched: the drama manager's gestures stay on the actor's words)."""
        order, rec = self.raw[char][script]
        b = rec.index(ff.OP_SPEECH)
        e = next(i for i in range(b + 1, len(rec)) if rec[i] >= ff.BODY_START)
        speech = [self.perf[char][script].base_frame, 0, 1, ff.PHONEME_START] + text_to_codes(lead) + [0]
        events, at = [], lead_s + GAP_S
        for s, audio in takes:
            p, off = self.perf[char][s], int(at * 1000)
            events += [(t + off, 0, list(codes) + [0]) for t, codes in p.mouth]
            events += [(t + off, 1, [cue]) for t, cue in p.cues[:-1]]
            at += len(audio) / OUT_SR
        for t, _, rest in sorted(events, key=lambda ev: (ev[0], ev[1])):
            speech += [max(1, t)] + rest
        speech += [int(total_s * 1000) + 150, 0, -1, script]
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
        # The drama manager's lines are written for its beats; reworded, they drifted into nonsense
        # ("Well come on in, you're freezing"). So a line plays as written, in the actor's own voice,
        # unless there's something to answer first: then a few lead-in words go before it (lead_in)
        # and the whole line is voiced. Fragments of a longer sentence ("You think we should...",
        # "get divorced...") always play as written; the guest's words wait for the next whole line.
        written = re.sub(r"\s+", " ", original.replace("{name}", self.player_name) if self.player_name else
                         re.sub(r"\s*\{name\}\s*,?", " ", original)).strip()
        since, bridge = self.guest_since(char, line), self.ending
        if since and improv.addressed(since[-1]) not in (None, char):
            since = []   # said to the other host: it waits for their line
        if fragment(original) or not (since or bridge):
            self.memory.append((char, written))
            log(f"{char} #{script} {line['beat']} as written: {written}")
            return {"replace": False, "why": "fragment" if fragment(original) else "nothing to answer"}
        with self.lock:   # one line at a time: the voice model isn't re-entrant
            first = self.lead_in(char, line, written, since, bridge)
            self.ending = None
            self.lines_kind = "return" if bridge else "answer"
            text = f"{first} {written}" if first else ""
            if first:
                self.generated.append(first)
            if not first or len(text) <= MAX_SUBTITLE:
                self.unanswered = []   # answered now: by the lead-in, or by the line itself (NONE)
            if not text or len(text) > MAX_SUBTITLE:   # (too long: the next line answers instead)
                self.memory.append((char, written))
                log(f"{char} #{script} {line['beat']} as written ({'it answers' if not first else 'no room'}): {written}")
                return {"replace": False, "why": "line answers it" if not first else "too long with a lead-in"}
            t1 = time.perf_counter()
            # Only the lead-in is generated speech; the actor's take(s) follow it, so the line keeps
            # its performance, the drama manager's own gestures stay on its words, and the voice model
            # renders a few words instead of the whole line (1-3 s less). No acting call either: the
            # drama manager performs its line as usual.
            takes = self.takes(char, scripts)
            voiced = self.voice(char, line, first if takes else text)
            if voiced is None:
                return {"replace": False, "why": "voice off for this character"}
            audio, emotion = voiced
            rec = None
            if takes:
                lead_s = len(audio) / OUT_SR
                audio = np.concatenate([audio, np.zeros(int(GAP_S * OUT_SR), audio.dtype)] +
                                       [a.astype(audio.dtype) for _, a in takes])
                rec = self.joined_record(char, script, first, lead_s, takes, text, len(audio) / OUT_SR)
        if time.perf_counter() - t0 > DEADLINE_S:
            # The bridge has (almost) stopped waiting and played the original; writing files now
            # would leave a stale line queued for the next time this script comes up.
            log(f"{char} #{script} too slow ({time.perf_counter() - t0:.1f}s): dropped")
            return {"replace": False, "why": "too slow"}
        out = self.emit(char, script, text, audio, None, rec)
        log(f"{char} #{script} {line['beat']} {self.lines_kind.upper()} [{emotion}] "
            f"{time.perf_counter() - t0:.2f}s (words {t1 - t0:.2f}s, "
            f"{'lead-in + actor' if takes else 'whole line voiced'})\n"
            f"    answering: {' / '.join(since) or '-'}\n    now: {text}")
        return out

    def emit(self, char, script, text, audio, pick, rec=None):
        """Queue a generated line for the engine (see the module docstring) and remember it.
        `pick`: the acting (performance.choose), or None to leave it to the drama manager.
        `rec`: the record to play (default: `script`'s, retimed to the audio)."""
        file_no = self.perf[char][script].base_frame + 1
        wav_path = INJECT / f"{char}_{file_no}_{uuid.uuid4().hex[:8]}.wav"
        _write_wav(wav_path, audio)
        rec = rec or self.record(char, script, text, len(audio) / OUT_SR, audio)
        stem = INJECT / f"{char}_{script}"
        np.array(rec, "<i4").tofile(stem.with_suffix(".tok"))
        stem.with_suffix(".job.tmp").write_text(f"file={file_no}\nwav={wav_path}\n", encoding="utf-8")
        os.replace(stem.with_suffix(".job.tmp"), stem.with_suffix(".job"))  # the job file appears last
        self.memory.append((char, text))
        out = {"replace": True, "script": script, "file": file_no, "text": text,
               "ms": int(len(audio) / OUT_SR * 1000)}
        if pick is not None:
            out["perform"] = performance.spec(char, dict(pick), text, len(audio) / OUT_SR)
        return out

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
            # A named host answers; a follow-up ("what do you mean") goes to whoever just spoke;
            # otherwise they take turns (Grace answered all four of the ghost scene's lines in
            # testing while Trip stood by).
            last = s.speakers[-1] if s.speakers else None
            follow_up = last and re.match(r"\W*(what do you mean|what\?|huh|really|why|how so|wait|like what)\b",
                                          words[-1], re.I)
            char = (improv.addressed(words[-1]) or (last if follow_up else None) or
                    {"grace": "trip", "trip": "grace"}.get(last) or self.nearer_host())
            if char not in free:
                char = next(iter(free))
            s.inbox.clear()
            self.unanswered = []   # the scene answers them, not the story's next line
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
            self.generated.append(text)
        s.spoke(char)
        out.update(say=True, char=next(k for k, v in CHARS.items() if v == char))
        log(f"{char} SCENE [{emotion}] {time.perf_counter() - t0:.2f}s\n    now: {text}\n    acting: {pick or 'none chosen'}")
        return out

    def heard(self, text):
        text = text.strip()
        if text and text != "warm up nlu":
            self.memory.append(("guest", text))
            self.unanswered.append((time.time(), text))
            log(f"guest: {text}")

    def understand(self, text):
        """What the player typed: (text for the drama manager, hide, follow).
        Also asks, alongside, whether the guest wants someone to come along (follow: improv.follow_request)."""
        text = text.strip()
        if not text or text in (" ", "warm up nlu"):
            return text, False, None
        t0 = time.time()
        self.heard(text)
        recent = "\n".join(f"{who.upper()}: {t}" for who, t in self.memory[-6:-1])
        follow = {}
        asking = threading.Thread(target=lambda: follow.update(
            who=improv.follow_request(text, recent, self.nearer_host())), daemon=True)
        asking.start()
        out, hide = self.restate(text, recent)
        asking.join(timeout=max(0.0, 1.3 - (time.time() - t0)))   # the bridge waits 1.5 s in all
        who = follow.get("who")
        if who is not None:
            log(f"    follow: {['stay', 'grace', 'trip', 'whoever is nearer'][who + 1]}")
        return out, hide, who

    def restate(self, text, recent):
        """Restate what the player typed in the plain phrasing Façade's 2005 parser recognizes, so the
        original drama manager picks the right reaction. The real words go to tonight's memory.
        Returns (text for the drama manager, hide): during a scene the scene answers instead, and the
        drama manager (paused) hears nothing."""
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
        r = requests.post(LLM_URL, timeout=1.3, json={   # (the bridge waits 1.5 s, then sends the raw words)
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
        out = _HEARD.sub(lambda m: f"{m.group(1)}{m.group(2) or ''} notice{'d' if m.group(4).lower() == 'heard' else ''}", out)
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
someone, keep who; never add a name the guest didn't use. Never add things the guest didn't mean: confusion
stays confusion ("umm what" -> "What?"; "huh?" -> "What?"), and a question stays the same question
("what happened" -> "What happened?").
The parser takes some words as the guest's verdict on Grace or Trip, and they answer "You think I'm...":
hear, heard, listen, talk (as "you're communicative"), fight, argue (angry), scared, afraid, hide, embarrassed
(afraid), lie, pretend, fake (lying), blame, fault, problem (to blame), control, force, stop (controlling),
alone, lonely (unloved). Use those words only when the guest really tells Grace or Trip what they are or should
do ("you two should talk more", "you're lying", "stop fighting"). Otherwise say it without them:
"i heard you guys arguing" -> "Is everything okay with you two?"; "that sounded scary" -> "Are you two okay?";
"thought i heard a commotion" -> "Is everything okay?"; "can we talk about the painting" -> "Tell me about the
painting."; "did you hear that?" -> "Did you notice that?". Output only the restated sentence."""

# Hearing words in a restatement that isn't advice: the parser would read "you're communicative".
_HEARD = re.compile(r"\b(I|we|you)( just| thought I| thought we)? (over)?(heard|hear)\b", re.I)

def fragment(line):
    """A piece of a sentence the drama manager plays across several lines ("You think we should....",
    "get divorced...") or a bare reaction ("Uh...", "Ha ha!"): it can't take a lead-in."""
    text = re.sub(r"\{name\}", "", line).strip()
    words = re.findall(r"[A-Za-z']+", text)
    if len(words) < 3 or text[:1].islower():
        return True
    return len(words) < 6 and bool(re.search(r"(\.\.\.|--|-)\s*$", text))


def first_sentences(text, max_words):
    """The whole sentences from the start that fit in max_words ("" if the first doesn't)."""
    out = ""
    for s in re.findall(r"[^.?!]+[.?!]*\s*", text):
        if len((out + s).split()) > max_words:
            break
        out += s
    return out.strip()


def first_word(text):
    m = re.search(r"[A-Za-z']+", text)
    return m.group().lower() if m else ""


def repeats(text, earlier):
    """Says again what an earlier line said: much the same words, the same opening four words
    ("You're not the first to think we're fighting" / "You're not the first to offer..."), or any
    run of five words ("...we have very strong opinions about things" / "...about a lot of things"),
    or a whole sentence again ("Want a drink?" at the end of three lines in a row)."""
    def words(t):
        return re.findall(r"[a-z']+", t.lower())

    def runs(w):
        return {tuple(w[i:i + 5]) for i in range(len(w) - 4)}

    def sentences(t):
        return {tuple(s) for s in map(words, re.split(r"[.?!]+", t)) if len(s) >= 2}
    w = words(text)
    o, r, s = (w[:4] if len(w) >= 5 else None), runs(w), sentences(text)
    for t in earlier:
        wt = words(t)
        if same_words(text, t, threshold=0.6) or (o and wt[:4] == o) or r & runs(wt) or s & sentences(t):
            return True
    return False


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


def phrase_times(text, audio):
    """[(start ms, words)]: the text split at punctuation, each stretch starting where it falls in
    the voiced part of the audio (by its share of the letters). The first starts at 0."""
    chunks = [c.strip() for c in re.findall(r"[^,.;:?!-]+[,.;:?!-]*", text) if re.search(r"[A-Za-z]", c)]
    if len(chunks) < 2:
        return [(0, text)]
    hop = OUT_SR // 100   # 10 ms
    rms = np.sqrt(np.convolve(audio.astype(np.float64) ** 2, np.ones(hop) / hop, "same")[::hop])
    voiced = np.nonzero(rms > rms.max() * 0.1)[0]
    on, off = (voiced[0], voiced[-1]) if len(voiced) else (0, len(rms))
    sizes = [len(re.sub(r"[^A-Za-z]", "", c)) for c in chunks]
    out, done = [], 0
    for c, n in zip(chunks, sizes):
        out.append((int((on + (off - on) * done / sum(sizes)) * 10), c))
        done += n
    out[0] = (0, out[0][1])
    return out


def _wav_seconds(path):
    try:
        with wave.open(str(path)) as w:
            return w.getnframes() / w.getframerate()
    except (OSError, wave.Error):
        return 1.0


def _read_wav(path):
    """An original take (22.05 kHz mono 16-bit) as float32."""
    with wave.open(str(path)) as w:
        pcm = np.frombuffer(w.readframes(w.getnframes()), "<i2")
        sr = w.getframerate()
    audio = pcm.astype(np.float32) / 32768
    return audio if sr == OUT_SR else soxr.resample(audio, sr, OUT_SR)


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
                text, hide, follow = LINES.understand(body.get("text", ""))
                out = {"text": text, "hide": hide}
                if follow is not None:
                    out["follow"] = follow
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
