"""The director: walks the evening's arc beat by beat and asks the local model for each exchange.

Builds on the prototype's Brain (streaming, sentence splitting). Adds:
- the beat sequencer: advance when the model signals the beat's goal is met (after the beat's
  minimum number of guest turns) or when its maximum is reached;
- control lines the model can emit alongside dialogue:
      BEAT|done            the current beat's goal has been reached
      SIDE|grace / SIDE|trip   the guest just took that character's side
      EXIT|grace / EXIT|trip   that character leaves the room (one-on-one beat)
      ENTER|grace / ENTER|trip that character comes back
      KICKOUT              the guest has behaved so badly that they are asked to leave
- tonight's memory: everything the guest said and did, so it can come back later in the
  evening. Nothing is kept between evenings.
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "prototype"))
from brain import Brain  # noqa: E402

from story_arc import ARC, CHARACTERS, SETTING  # noqa: E402

FORMAT = """Write what Grace and Trip say and do next, as a short stretch of a stage play.
Output ONLY lines in these formats, one per line, nothing else:
SPEAKER|delivery|text
- SPEAKER is TRIP or GRACE. Only characters who are in the room can speak.
- delivery is one or two words of acting direction (warm, dry, forced cheer, pointed, hurt, furious...).
- text is what they say aloud. Stage business goes in *asterisks*, e.g. *gestures at the bar*.
- Audible sounds may go inline, sparingly: [sigh] [laugh] [chuckle] [gasp] [groan] [sniff] [clear throat].
Control lines, only when they apply:
BEAT|done        the current beat's goal has been reached
SIDE|grace       the guest just clearly took Grace's side (or SIDE|trip)
EXIT|trip        Trip leaves the room for now (or EXIT|grace)
ENTER|trip       Trip comes back into the room (or ENTER|grace)
KICKOUT          the guest has been so rude or inappropriate that they are thrown out
Keep each line short and natural, like real speech: usually one or two sentences.
Write 2 to 4 dialogue lines, then stop. React specifically to what the guest just said or did.
Never reuse a line or phrase either of them already said tonight."""


MAX_RETRIES = 2


def _norm(text):
    """A line's spoken words only, lowercased, for spotting repeats."""
    import re
    text = re.sub(r"\*[^*]*\*|\[[^\]]*\]", " ", text)
    return " ".join(re.findall(r"[a-z0-9']+", text.lower()))


class Director(Brain):
    def __init__(self, catalog, player_name="the guest", pronouns="they/them"):
        self.catalog = catalog
        self.player = player_name
        self.pronouns = pronouns
        self.history = []        # tonight only
        self.memory = []         # what the guest said and did tonight
        self.seed = random.randrange(1 << 30)
        self.beat_index = 0
        self.turns_in_beat = 0
        self.affinity = {"grace": 0, "trip": 0}
        self.present = {"grace": True, "trip": True}
        self.events = []         # control events from the last turn, for the server
        self.said = set()        # normalized lines already spoken tonight
        self.intro_pending = False
        self._sided = False
        self.ended = False
        self.kicked_out = False

    @property
    def beat(self):
        return ARC[self.beat_index]

    def system_prompt(self):
        b = self.beat
        examples = []
        for ch in ("trip", "grace"):
            ex = self.catalog.examples(b["examples_from"], ch, n=5, seed=self.seed + self.beat_index)
            if ex:
                examples.append(f"{ch.upper()} sounds like:\n" + "\n".join(f"  - {e}" for e in ex))
        # Stable parts first so the server's prompt cache survives from turn to turn.
        parts = [
            SETTING,
            CHARACTERS["grace"],
            CHARACTERS["trip"],
            f"The guest is called {self.player} and uses {self.pronouns} pronouns. "
            f"When Grace or Trip refer to {self.player}, always use {self.pronouns}.",
            FORMAT,
            f"CURRENT BEAT ({self.beat_index + 1} of {len(ARC)}): {b['title']}. {b['situation']}\n"
            f"Trip wants: {b['goals']['trip']}\nGrace wants: {b['goals']['grace']}\n"
            f"The beat's goal is reached when: {b['ends_when']}",
        ]
        if examples:
            parts.append("Original lines from this part of the evening, for voice and tone only "
                         "(do not repeat them verbatim):\n" + "\n\n".join(examples))
        return "\n\n".join(parts)

    def _situation_note(self):
        """Per-turn facts, sent with the guest's input rather than in the cached system prompt."""
        notes = []
        away = [c for c, here in self.present.items() if not here]
        if away:
            notes.append(f"{' and '.join(a.capitalize() for a in away)} is out of the room.")
        if self.beat.get("one_on_one") and all(self.present.values()) and self.turns_in_beat == 0:
            leaver = "trip" if self.affinity["grace"] >= self.affinity["trip"] else "grace"
            notes.append(f"Now {leaver.capitalize()} should find a reason to step out to the kitchen "
                         f"(emit EXIT|{leaver}).")
        side = max(self.affinity, key=self.affinity.get)
        if self.affinity[side] >= 2:
            notes.append(f"So far the guest has mostly sided with {side.capitalize()}.")
        if self.memory[:-1]:
            recent = "; ".join(self.memory[-8:-1])
            notes.append(f"Earlier tonight the guest said or did: {recent}")
        return " ".join(notes)

    def turn(self, player_input, stage=False, retry=0):
        """One exchange. With stage=True the input is a stage cue, not something the guest did:
        it isn't remembered and doesn't count as one of the beat's guest turns."""
        if not retry:
            self.events = []
            self._sided = False
            if not stage:
                self.memory.append(player_input)
        note = self._situation_note()
        if stage:
            note += (" A new part of the evening begins now. Make this beat's situation happen right away, "
                     "without waiting for the guest.")
        if retry:
            note += " Write new spoken dialogue now: at least two lines, unlike anything said tonight."
        spoke = False  # at least one new line of real dialogue (3+ words)
        for line in super().turn(player_input, note=note):
            key = _norm(line.text)
            if key in self.said:
                continue  # the model repeated itself: skip the line
            if len(key.split()) >= 3:
                self.said.add(key)
                spoke = True
            yield line
        if not spoke and retry < MAX_RETRIES and not self.ended:
            del self.history[-2:]  # forget the hollow exchange and ask again
            yield from self.turn(player_input, stage=stage, retry=retry + 1)
            return
        if not stage:
            self.turns_in_beat += 1
            self._maybe_advance()

    def opening(self):
        """The evening starts with the guest knocking."""
        yield from self.turn("*knocks on the apartment door*", stage=True)

    def beat_intro(self):
        """The characters start a new beat on their own, as soon as it begins, from its cue."""
        self.intro_pending = False
        yield from self.turn(self.beat.get("cue", "*a moment passes*"), stage=True)

    def _parse(self, row, t0):
        parts = [p.strip() for p in row.strip().split("|")]
        tag = parts[0].upper()
        if tag == "BEAT" and len(parts) == 2:
            self._beat_done = True
            return None
        if tag == "SIDE" and len(parts) == 2 and parts[1].lower() in self.affinity:
            if not self._sided:  # at most one per turn
                self._sided = True
                self.affinity[parts[1].lower()] += 1
                self.events.append({"type": "side", "who": parts[1].lower()})
            return None
        if tag in ("EXIT", "ENTER") and len(parts) == 2 and parts[1].lower() in self.present:
            who = parts[1].lower()
            self.present[who] = tag == "ENTER"
            self.events.append({"type": tag.lower(), "who": who})
            return None
        if tag == "KICKOUT":
            self.kicked_out = True
            self.events.append({"type": "kickout"})
            return None
        line = super()._parse(row, t0)
        if line and not self.present.get(line.speaker, True):
            # The model let an absent character talk: treat it as them coming back.
            self.present[line.speaker] = True
            self.events.append({"type": "enter", "who": line.speaker})
        return line

    def _maybe_advance(self):
        lo, hi = self.beat["turns"]
        done = getattr(self, "_beat_done", False)
        self._beat_done = False
        if self.kicked_out:
            self.ended = True
            self.events.append({"type": "end", "how": "kicked out"})
            return
        if (done and self.turns_in_beat >= lo) or self.turns_in_beat >= hi:
            if self.beat.get("final"):
                self.ended = True
                self.events.append({"type": "end", "how": "evening over"})
                return
            # Whoever stepped out is back for the next beat.
            for who, here in self.present.items():
                if not here:
                    self.present[who] = True
                    self.events.append({"type": "enter", "who": who})
            self.beat_index += 1
            self.turns_in_beat = 0
            self.intro_pending = True
            self.events.append({"type": "beat", "index": self.beat_index, "title": self.beat["title"],
                                "staging": self.beat["staging"]})
