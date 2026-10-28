"""The drama brain: prompts the local LLM for one exchange in the current beat and streams lines out.

The model answers in a line format that can be parsed while it streams:
    TRIP|cheerful|Line of dialogue.
    GRACE|dry|Line of dialogue.
    BEAT|done
"""
import json
import random
import re
import time
from dataclasses import dataclass

import requests

from config import LLM_URL
from story import BEATS, CHARACTERS, SETTING

MIN_TURNS_PER_BEAT = 4

FORMAT = """Write what Grace and Trip say and do next, as a short stretch of a stage play.
Output ONLY lines in this exact format, one per line, nothing else:
SPEAKER|delivery|text
- SPEAKER is TRIP or GRACE.
- delivery is one or two words of acting direction (warm, dry, forced cheer, pointed, hurt...).
- text is what they say aloud. Stage business goes in *asterisks* inside the text, e.g. *gestures at the bar*.
- Audible sounds may go inline where they happen, sparingly: [sigh] [laugh] [chuckle] [gasp] [groan] [sniff] [clear throat].
  Example: GRACE|weary|[sigh] Trip, please. Not tonight.
Keep each line short and natural, like real speech: usually one or two sentences.
Write 2 to 4 lines, then stop. If the beat's goal has been reached, add a final line: BEAT|done"""


@dataclass
class Line:
    speaker: str
    delivery: str
    text: str
    t_ready: float  # seconds since the request started
    part_of_previous: bool = False  # a later sentence of the same line


class Brain:
    def __init__(self, catalog, beat_id, player_name="the guest", pronouns="they/them"):
        self.catalog = catalog
        self.beat = BEATS[beat_id]
        self.player = player_name
        self.pronouns = pronouns
        self.history = []  # tonight only: never saved
        self.seed = random.randrange(1 << 30)  # a different set of example lines each evening

    def system_prompt(self):
        b = self.beat
        examples = []
        for ch in ("trip", "grace"):
            # Same examples all evening: an unchanged prompt prefix lets the server reuse its
            # cache, so each turn only processes the new exchange (~0.02 s vs ~0.8 s to first token).
            ex = self.catalog.examples(b["examples_from"], ch, n=5, seed=self.seed)
            examples.append(f"{ch.upper()} sounds like:\n" + "\n".join(f"  - {e}" for e in ex))
        return "\n\n".join([
            SETTING,
            f"GRACE: {CHARACTERS['grace']}",
            f"TRIP: {CHARACTERS['trip']}",
            f"The guest is called {self.player} and uses {self.pronouns} pronouns. "
            f"When Grace or Trip refer to {self.player}, always use {self.pronouns}.",
            f"CURRENT BEAT: {b['title']}. {b['situation']}\n"
            f"Trip wants: {b['goals']['trip']}\nGrace wants: {b['goals']['grace']}\n"
            f"The beat ends when: {b['ends_when']}",
            "Original lines from this moment, for voice and tone only (do not repeat them verbatim):\n"
            + "\n\n".join(examples),
            "React specifically to what the guest just said or did. Stay in this beat. "
            "Every line must be new: never reuse a line or phrase either of them already said tonight. "
            "Keep track of what each person has already ordered or refused.",
            FORMAT,
        ])

    def prime(self):
        """Process the evening's system prompt once up front, so the first turn hits the cache."""
        requests.post(LLM_URL, timeout=120, json={
            "messages": [{"role": "system", "content": self.system_prompt()}, {"role": "user", "content": "."}],
            "max_tokens": 1, "chat_template_kwargs": {"enable_thinking": False},
        }).raise_for_status()

    def turn(self, player_input, note=None, stage=False):
        """Yield Line objects as soon as each one finishes streaming.
        `note` is extra context for this request only; it isn't kept in the history.
        With stage=True the input is a stage direction, not something the guest said or did."""
        content = f"(Stage direction: {player_input})" if stage else f"{self.player.upper()}: {player_input}"
        self.history.append({"role": "user", "content": content})
        messages = [{"role": "system", "content": self.system_prompt()}] + self._window()
        if note:
            messages[-1] = {"role": "user", "content": f"{messages[-1]['content']}\n\n[{note}]"}
        t0 = time.perf_counter()
        resp = requests.post(LLM_URL, stream=True, timeout=120, json={
            "messages": messages,
            "stream": True,
            "temperature": 0.7,
            "top_p": 0.9,
            "presence_penalty": 0.4,
            "frequency_penalty": 0.3,
            "max_tokens": 300,
            "chat_template_kwargs": {"enable_thinking": False},
        })
        resp.raise_for_status()
        buf, said = "", []
        self._spoke_this_turn = False
        self.t_first_token = None
        for raw in resp.iter_lines():
            if not raw.startswith(b"data: ") or raw == b"data: [DONE]":
                continue
            delta = json.loads(raw[6:])["choices"][0]["delta"].get("content") or ""
            if delta and self.t_first_token is None:
                self.t_first_token = time.perf_counter() - t0
            buf += delta
            while "\n" in buf:
                row, buf = buf.split("\n", 1)
                lines, _ = self._emit(row, t0, final=True)
                said += lines
                yield from lines
            # Hand over finished sentences of the line still streaming, so the voice can start early.
            lines, buf = self._emit(buf, t0, final=False)
            said += lines
            yield from lines
        lines, _ = self._emit(buf, t0, final=True)
        said += lines
        yield from lines
        # History keeps whole lines: rejoin the sentence fragments of each line.
        merged = []
        for l in said:
            if merged and l.part_of_previous:
                merged[-1] = (merged[-1][0], merged[-1][1], merged[-1][2] + " " + l.text)
            else:
                merged.append((l.speaker, l.delivery, l.text))
        self.history.append({"role": "assistant", "content": "\n".join("|".join(m) for m in merged)})

    HISTORY_KEEP = 48   # messages (~24 exchanges) the model sees
    HISTORY_DROP = 16   # when over, drop this many oldest at once, so the cached prefix rarely moves

    def _window(self):
        start = getattr(self, "_window_start", 0)
        if len(self.history) - start > self.HISTORY_KEEP:
            start += self.HISTORY_DROP
            if self.history[start]["role"] == "assistant":  # begin on the guest's turn
                start += 1
            self._window_start = start
        return self.history[start:]

    def _emit(self, row, t0, final):
        """Split `row` (one output line, possibly still streaming) into sentence Lines.

        Returns (lines, remainder). When not final, the last, unfinished sentence comes back as
        the remainder for the caller to keep buffering. Once part of a line has been emitted,
        its remainder no longer carries the SPEAKER|delivery| header; self._cont remembers it.
        """
        parts = row.split("|", 2)
        has_header = len(parts) == 3 and parts[0].strip().upper() in ("TRIP", "GRACE")
        cont = getattr(self, "_cont", None)
        if not has_header and cont is None:
            if final:
                self._parse(row, t0)  # control lines such as BEAT|done
                return [], ""
            return [], row
        if has_header:
            speaker, delivery, text = parts[0].strip().lower(), parts[1].strip(), parts[2]
            prefix, continued = f"{parts[0]}|{parts[1]}|", False
        else:
            (speaker, delivery), text, prefix, continued = cont, row, "", True
        # An ellipsis is a trailing-off pause inside a line, not a sentence end.
        sentences = re.split(r"(?<=[.!?])(?<!\.\.\.)\s+", text)
        done, rest = (sentences, "") if final else (sentences[:-1], sentences[-1])
        if not final and not done and not self._spoke_this_turn:
            # Nothing voiced yet this turn: a first clause of 4+ words is worth sending early.
            for m in re.finditer(r"\S[,;:—]\s+(?=\S)", rest):
                head = rest[:m.start() + 2]
                if len(head.split()) >= 4:
                    done, rest = [head], rest[m.end():]
                    break
        lines = []
        for s in done:
            if s.strip():
                lines.append(Line(speaker, delivery, s.strip(), time.perf_counter() - t0, continued))
                continued = True
                self._spoke_this_turn = True
        if final:
            self._cont = None
            return lines, ""
        if lines:
            self._cont = (speaker, delivery)
            prefix = ""
        return lines, prefix + rest

    def _parse(self, row, t0):
        parts = row.strip().split("|", 2)
        if len(parts) == 2 and parts[0].upper() == "BEAT":
            # The model tends to call the beat over too soon. Until there's a real beat
            # sequencer, only accept it after a few exchanges.
            guest_turns = sum(1 for m in self.history if m["role"] == "user")
            self.beat_done = guest_turns >= MIN_TURNS_PER_BEAT
            return None
        if len(parts) != 3 or parts[0].upper() not in ("TRIP", "GRACE"):
            return None
        return Line(parts[0].lower(), parts[1].strip(), parts[2].strip(), time.perf_counter() - t0)
