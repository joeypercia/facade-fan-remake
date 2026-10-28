"""Off-script scenes, and where everyone is.

The drama manager has stock reactions for a guest who goes off script: deflections when it didn't
follow what they said, "uncooperative" lines when they go quiet or act strangely, and warnings when
they head for the door or the kitchen. Any of those (or the guest walking out into the hallway)
starts a scene: the LLM plays the new situation for real instead of the stock line.

While a scene is on, the story lines the drama manager wants are held back (remembered) and the
scene continues. A new scene always gets room (MIN_SKIPPED lines or MIN_S seconds); after that each
story line is put to the LLM: is the scene still going anywhere? Coming back, the story line is
spoken, bridged from the scene, with what was held back worked in. The story comes back when the
guest's own words turn back to the evening, after MAX_SKIPPED held-back lines, or when the guest
has stopped feeding the scene for QUIET_S seconds.

Positions come from the bridge with each line request (world.cpp): floor x, z in the engine's units.
"""
import math
import time

import requests

from config import LLM_URL

MIN_SKIPPED = 2   # a new scene always gets this many held-back story lines...
MIN_S = 30        # ...or this long, before the LLM is asked whether it's run its course
MAX_SKIPPED = 6
QUIET_S = 50

# beat-name ending -> what is happening (the beat names are the catalog's, e.g. T1L2LEAVEAPARTMENT)
MOMENTS = [
    ("DEFLECT", "the guest said something the hosts don't have a ready answer for"),
    ("UNCOOP", "the guest is being odd: quiet, not moving, or doing strange things"),
    ("LEAVEAPARTMENT", "the guest is heading out the front door"),
    ("LEAVEFORKITCHEN", "the guest is wandering off toward the kitchen"),
]

NEAR = 70        # within this of a landmark: "by the bar"
CLOSE = 80       # guest within this of the speaker: "right next to you"
FAR = 220        # beyond this: "across the room"

PLACES = {"bar": "the bar", "couch": "the couch", "window": "the big window", "worktable": "Grace's work table",
          "wedding_picture": "the wedding picture", "italy_picture": "the photo from Italy",
          "painting": "the big abstract painting", "phone": "the phone", "door": "the front door"}


class Scene:
    def __init__(self, what):
        self.what = what
        self.started = self.last_guest = time.time()
        self.skipped = []         # "TRIP: <story line>" held back while the scene went on
        self.guest_back = False   # the guest's own words turned back to the evening

    def must_end(self):
        return (self.guest_back or len(self.skipped) >= MAX_SKIPPED or
                time.time() - self.last_guest > QUIET_S)

    def still_alive(self, char, story_line, recent):
        """Stay in the scene for this story line? A new scene always gets room to develop (MIN_SKIPPED
        held-back lines or MIN_S seconds); after that the LLM judges, leaning toward staying."""
        if len(self.skipped) < MIN_SKIPPED and time.time() - self.started < MIN_S:
            return True
        try:
            r = requests.post(LLM_URL, timeout=1.5, json={
                "messages": [
                    {"role": "system", "content":
                        "You judge the flow of an improvised evening. The guest took the conversation "
                        "somewhere new, and the hosts are going along with it. Let it develop: answer STAY "
                        "while it is still going anywhere (the guest is into it, it's mid-exchange, a joke "
                        "or argument is building). Answer RETURN only if it has clearly run dry, so the "
                        "hosts' planned next line would come naturally now. Answer with one word: STAY or "
                        "RETURN."},
                    {"role": "user", "content":
                        f"Conversation:\n{recent}\n\nThe new situation: {self.what}\n"
                        f"Planned next line, {char.upper()}: \"{story_line}\"\nSTAY or RETURN?"},
                ],
                "temperature": 0.0, "max_tokens": 4, "chat_template_kwargs": {"enable_thinking": False},
            })
            r.raise_for_status()
            return "RETURN" not in r.json()["choices"][0]["message"]["content"].upper()
        except (requests.RequestException, ValueError, KeyError, IndexError):
            return False


def takes_it_somewhere_new(text, recent, scene=None):
    """Is the guest taking the evening somewhere new (or carrying on with the scene in progress)?"""
    ask = (f"The conversation has gone somewhere new: {scene.what}\n"
           "Is the guest still on that (NEW), or back on the evening's usual business (USUAL)?") if scene else (
        "Is the guest taking the evening somewhere new (NEW), or is this ordinary visit business (USUAL)?")
    try:
        r = requests.post(LLM_URL, timeout=1.0, json={
            "messages": [
                {"role": "system", "content":
                    "You judge what a guest visiting old friends Grace and Trip is doing. Words in *asterisks* "
                    "or (parentheses) are actions. Answer with one word, NEW or USUAL.\n"
                    "USUAL: greetings, thanks, small talk, drinks, compliments or complaints about the "
                    "apartment and its things, questions about Grace and Trip, their marriage or their jobs, "
                    "agreeing, disagreeing, taking sides, hugging, saying goodbye.\n"
                    "NEW: a topic, game, story or activity of the guest's own that has nothing to do with the "
                    "visit (ghosts, juggling, a seance, a dance-off), odd physical actions, inviting them "
                    "somewhere else.\n"
                    "Examples: \"hi guys!\" USUAL. \"your place looks great\" USUAL. \"are you two okay?\" "
                    "USUAL. \"I'll have a beer\" USUAL. \"*juggles oranges*\" NEW. \"let's play hide and "
                    "seek\" NEW. \"I think this building is haunted\" NEW. When unsure: USUAL."},
                {"role": "user", "content": f"Conversation:\n{recent}\n\nThe guest now: \"{text}\"\n{ask}"},
            ],
            "temperature": 0.0, "max_tokens": 4, "chat_template_kwargs": {"enable_thinking": False},
        })
        r.raise_for_status()
        return "NEW" in r.json()["choices"][0]["message"]["content"].upper()
    except (requests.RequestException, ValueError, KeyError, IndexError):
        return False


def moment(beat):
    """What off-script moment a reaction line is for, or None for an ordinary line."""
    for end, what in MOMENTS:
        if beat.endswith(end) or beat.startswith(end):
            return what
    return None


def _d(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _past_door(where):
    """How far the guest is past the front door toward the living room (negative: out in the hallway)."""
    p, door, couch = where.get("player"), where.get("door"), where.get("couch")
    if not (p and door and couch):
        return None
    inward = (couch[0] - door[0], couch[1] - door[1])
    return ((p[0] - door[0]) * inward[0] + (p[1] - door[1]) * inward[1]) / (math.hypot(*inward) or 1)


def outside(where):
    """The guest is out in the hallway (more than 20 units past the door)."""
    d = _past_door(where)
    return d is not None and d < -20


def inside(where):
    """The guest is well into the apartment (same test as the bridge's following)."""
    d = _past_door(where)
    return d is not None and d > 100


def describe(where, char):
    """One sentence on where the guest is, relative to the room and to the speaker."""
    p, me = where.get("player"), where.get(char)
    if not p:
        return ""
    if outside(where):
        place = "out in the hallway, outside the apartment"
    else:
        near = min(((_d(p, where[k]), k) for k in PLACES if k in where), default=(1e9, None))
        place = f"by {PLACES[near[1]]}" if near[0] < NEAR else "in the living room"
    rel = ""
    if me:
        d = _d(p, me)
        rel = (f", right next to {char.capitalize()}" if d < CLOSE else
               f", far from {char.capitalize()}" if d > FAR else "")
    return f"The guest is {place}{rel}."
