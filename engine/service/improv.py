"""Off-script scenes, and where everyone is.

The drama manager has stock reactions for a guest who goes off script: deflections when it didn't
follow what they said, "uncooperative" lines when they go quiet or act strangely, and warnings when
they head for the door or the kitchen. Any of those, the guest taking the evening somewhere new
(a classifier on their words), or the guest walking out into the hallway starts a scene.

A scene pauses the story. The next story line the drama manager pushes is held: the bridge keeps
that character's thread waiting inside the push (and the other character's too, at their next
line), so the drama manager can't move on to the next beat while the scene plays. Meanwhile the
bridge asks the service for scene lines (/scene): Grace and Trip answer whatever the guest says or
does, and chime in on their own when the guest goes quiet. The scene ends when the guest's own
words turn back to the evening, when it fizzles out (nobody has added anything for QUIET_S), or
after MAX_S; then the held story line plays, bridged from the scene.

Positions come from the bridge with each request (world.cpp): floor x, z in the engine's units.
"""
import math
import re
import time

import requests

from config import LLM_URL

QUIET_S = 20      # nobody (guest or hosts) has added anything for this long: the scene is over
CHIME_S = 12      # the guest has been quiet this long: a host keeps the scene going...
MAX_CHIMES = 1    # ...at most this many times in a row
MAX_S = 240

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
    def __init__(self, what, why="words"):
        self.what = what
        self.why = why            # "words" (the guest's), "moment" (a stock reaction), "outside"
        self.started = self.last_guest = self.last_line = time.time()
        self.inbox = []           # the guest's words not answered yet
        self.chimes = 0           # host lines in a row with no guest input
        self.speakers = []        # who has spoken in the scene
        self.guest_back = False   # the guest's own words turned back to the evening

    def heard(self, text):
        self.inbox.append(text)
        self.last_guest = time.time()
        self.chimes = 0

    def spoke(self, char):
        self.speakers.append(char)
        self.last_line = time.time()


def takes_it_somewhere_new(text, recent, scene=None):
    """Is the guest taking the evening somewhere new (or carrying on with the scene in progress)?"""
    if scene:
        ask = (f"The conversation has gone somewhere new: {scene.what}\nIs the guest still in that (NEW), or "
               "changing the subject back to the visit itself (USUAL)? Replies, answers, laughter, jokes, "
               "and questions about what was just said count as NEW. Moving on to drinks, praising or asking "
               "about the apartment or its things, going back inside, or \"anyway...\" counts as USUAL.\n"
               "Examples: \"haha yeah\" NEW. \"wait, really?\" NEW. \"anyway, I'd love a glass of wine\" USUAL. "
               "\"your apartment looks great\" USUAL. \"ok, let's go back inside\" USUAL. \"ok, enough of "
               "that\" USUAL.")
    else:
        ask = "Is the guest taking the evening somewhere new (NEW), or is this ordinary visit business (USUAL)?"
    try:
        r = requests.post(LLM_URL, timeout=2.0, json={
            "messages": [
                {"role": "system", "content":
                    "You judge what a guest visiting old friends Grace and Trip is doing. Words in *asterisks* "
                    "or (parentheses) are actions. Answer with one word, NEW or USUAL.\n"
                    "USUAL: greetings, thanks, small talk, drinks, compliments or complaints about the "
                    "apartment and its things, questions about Grace and Trip, their marriage or their jobs, "
                    "agreeing, disagreeing, taking sides, hugging, saying goodbye.\n"
                    "NEW: a topic, game, story or activity of the guest's own that has nothing to do with the "
                    "visit (ghosts, juggling, a seance, a dance-off), odd physical actions, asking one of them to "
                    "step out or talk somewhere else, inviting them somewhere else.\n"
                    "Examples: \"hi guys!\" USUAL. \"your place looks great\" USUAL. \"are you two okay?\" "
                    "USUAL. \"I'll have a beer\" USUAL. \"*juggles oranges*\" NEW. \"let's play hide and "
                    "seek\" NEW. \"I think this building is haunted\" NEW. \"trip, come out to the hallway "
                    "for a sec\" NEW. When unsure: " + ("NEW." if scene else "USUAL.")},
                {"role": "user", "content": f"Conversation:\n{recent}\n\nThe guest now: \"{text}\"\n{ask}"},
            ],
            "temperature": 0.0, "max_tokens": 4, "chat_template_kwargs": {"enable_thinking": False},
        })
        r.raise_for_status()
        out = r.json()["choices"][0]["message"]["content"].upper()
        return "NEW" in out if not scene else "USUAL" not in out
    except (requests.RequestException, ValueError, KeyError, IndexError):
        return bool(scene)


def moment(beat):
    """What off-script moment a reaction line is for, or None for an ordinary line."""
    for end, what in MOMENTS:
        if beat.endswith(end) or beat.startswith(end):
            return what
    return None


def addressed(text):
    """The host the guest's words are aimed at, if they name one."""
    t = text.lower()
    g, tr = re.search(r"\bgrace\b", t), re.search(r"\btrip\b", t)
    if g and not tr:
        return "grace"
    if tr and not g:
        return "trip"
    return None


def _d(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def past_door(where, who="player"):
    """How far someone is past the front door toward the living room (negative: out in the hallway)."""
    p, door, couch = where.get(who), where.get("door"), where.get("couch")
    if not (p and door and couch):
        return None
    inward = (couch[0] - door[0], couch[1] - door[1])
    return ((p[0] - door[0]) * inward[0] + (p[1] - door[1]) * inward[1]) / (math.hypot(*inward) or 1)


def outside(where, who="player"):
    """Out in the hallway (more than 20 units past the door)."""
    d = past_door(where, who)
    return d is not None and d < -20


def inside(where, who="player"):
    """Well into the apartment (same test as the bridge's following)."""
    d = past_door(where, who)
    return d is not None and d > 100


def _place(where, who):
    if outside(where, who):
        return "out in the hallway, outside the apartment"
    d = past_door(where, who)
    if d is not None and d < 60:
        return "at the front door"
    p = where.get(who)
    near = min(((_d(p, where[k]), k) for k in PLACES if k in where), default=(1e9, None)) if p else (1e9, None)
    return f"by {PLACES[near[1]]}" if near[0] < NEAR else "in the living room"


def describe(where, char):
    """Where the guest and the speaker are, in plain words."""
    if not where.get("player"):
        return ""
    out = f"The guest is {_place(where, 'player')}"
    me = where.get(char)
    if me:
        d = _d(where["player"], me)
        out += (f", right next to {char.capitalize()}" if d < CLOSE else
                f", far from {char.capitalize()}" if d > FAR else "")
        out += f". {char.capitalize()} is {_place(where, char)}"
    return out + "."
