"""How a generated line is performed: facial expression, a gesture landing on one word, where to look.

The LLM picks from menus of the engine's own animations (the IDs are Façade's: GraceScript /
TripScript gesture scripts, FullExpressions, SpriteID). The bridge applies the choice on top of the
drama manager's behaviours (engine/bridge/perform.cpp).

The answer is one short line, "face | gesture | word | look", checked against the menus: a JSON
schema made the model spend ~40 tokens on braces and indentation (0.7-0.9 s); this takes ~0.2 s.
"""
import re

import requests

from config import LLM_URL

ARM_L, ARM_R, ARMS_BOTH, BODY = 0, 1, 2, 3   # gesture body part; BODY = a one-shot on the body layer

# name -> (body part, gesture script, when to use it). Arm gestures are the scripts' "hold" phase:
# the engine plays the way in and holds; the bridge returns the arm to rest when the line ends.
GESTURES = {
    "grace": {
        "hand on hip": (ARM_R, 89, "impatient, challenging"),
        "arms crossed": (ARMS_BOTH, 80, "closed off, defensive, cold"),
        "welcoming hand": (ARM_L, 77, "inviting, offering"),
        "explaining hand": (ARM_R, 92, "explaining, reasoning"),
        "small emphasis": (ARM_R, 134, "light stress on a word"),
        "strong emphasis": (ARM_R, 124, "insisting, firm"),
        "left emphasis": (ARM_L, 129, "light stress, other hand"),
        "exasperated": (ARMS_BOTH, 158, "both arms up, fed up"),
        "pleading": (ARMS_BOTH, 153, "both arms out, begging to be understood"),
        "nod": (BODY, 51, "agreeing"),
        "slow nod": (BODY, 21, "thoughtful agreement"),
        "sigh": (BODY, 13, "weary, disappointed"),
        "sigh laugh": (BODY, 12, "rueful, sarcastic"),
    },
    "trip": {
        "hand on hip": (ARM_R, 103, "impatient, challenging"),
        "arms crossed": (ARMS_BOTH, 81, "closed off, defensive"),
        "welcoming arm": (ARM_L, 48, "inviting, offering"),
        "come-in wave": (ARM_R, 51, "beckoning someone over or in"),
        "questioning hand": (ARM_R, 59, "asking"),
        "proposing hand": (ARM_L, 69, "suggesting an idea, selling it"),
        "suggesting hand": (ARM_L, 254, "a casual suggestion"),
        "point": (ARM_L, 260, "pointing at something in the room the line mentions"),
        "point at picture": (ARM_L, 176, "only when the line is about a picture or photo"),
        "small emphasis": (ARM_R, 216, "light stress on a word"),
        "strong emphasis": (ARM_R, 206, "insisting, firm"),
        "exasperated": (ARMS_BOTH, 243, "both arms up, fed up"),
        "arms spread": (ARMS_BOTH, 230, "big, open, enthusiastic"),
        "big nod": (BODY, 53, "eager agreement"),
        "nod": (BODY, 54, "agreeing"),
    },
}

# FullExpressions eFEBase_*
FACES = {"pleasant": 0, "smile": 1, "impatient": 2, "hopeful": 3, "skeptical": 4, "serious": 5,
         "frown": 6, "surprised": 7, "shocked": 8}

# SpriteID; "partner" is the other character
LOOKS = {"guest": 2, "partner": None, "bar": 7, "window": 46, "wedding picture": 38,
         "Italy photo": 39, "painting": 34, "couch": 3}

KEEP, NONE = "same", "none"

PROMPT = """You direct the acting in a stage play. For {Name}'s line, answer with ONE line:
face | gesture | word | look

face: {faces}, or same (keep the current expression).
gesture: none, or one of:
{gestures}
  People gesture on about half their lines; never pick one that contradicts the words.
word: the single most stressed word of the line, where the gesture lands (copied from the line).
look: guest (when talking to the guest), partner ({Partner}, when talking to them), or an object only
  if the line is about it: {objects}; or same.

Example: skeptical | sigh laugh | production | partner"""


def prompt(char):
    partner = "trip" if char == "grace" else "grace"
    return PROMPT.format(
        Name=char.capitalize(), Partner=partner.capitalize(), faces=", ".join(FACES),
        gestures="\n".join(f"  {g} ({hint})" for g, (_, _, hint) in GESTURES[char].items()),
        objects=", ".join(o for o in LOOKS if o not in ("guest", "partner")))


def choose(char, text, direction, recent, timeout=2.5):
    """The LLM's pick for this line, as {"face", "gesture", "word", "look"} (only valid entries);
    {} if it fails."""
    try:
        r = requests.post(LLM_URL, timeout=timeout, json={
            "messages": [
                {"role": "system", "content": prompt(char)},
                {"role": "user", "content":
                    (f"Just before:\n{recent}\n\n" if recent else "") +
                    f"{char.upper()} ({direction}): {text}"},
            ],
            "temperature": 0.6, "max_tokens": 30, "chat_template_kwargs": {"enable_thinking": False},
        })
        r.raise_for_status()
        reply = r.json()["choices"][0]["message"]["content"]
    except (requests.RequestException, ValueError, KeyError, IndexError):
        return {}
    return parse(char, reply)


# Faces the model reaches for that aren't on the menu (it often echoes the line's direction).
FACE_WORDS = {"happy": "smile", "amused": "smile", "warm": "smile", "friendly": "pleasant", "calm": "pleasant",
              "annoyed": "impatient", "irritated": "impatient", "frustrated": "impatient", "angry": "frown",
              "sad": "frown", "upset": "frown", "hurt": "frown", "worried": "serious", "anxious": "serious",
              "nervous": "serious", "doubtful": "skeptical", "sarcastic": "skeptical", "excited": "surprised",
              "neutral": KEEP}


def parse(char, reply):
    """Read "face | gesture | word | look", or the same as "face: ..." lines."""
    labeled = dict(re.findall(r"(?im)^\W*(face|gesture|word|look)\W*:\s*(.+?)\s*$", reply))
    if len(labeled) == 4:
        fields = [labeled[k] for k in ("face", "gesture", "word", "look")]
    else:
        fields = reply.strip().splitlines()[0].split("|") if reply.strip() else []
    fields = [re.sub(r"^\W*(face|gesture|word|look)\W*:\s*", "", f.strip(), flags=re.I) for f in fields]
    fields = [f.strip().strip("*`'\".").lower() for f in fields]
    if len(fields) != 4:
        return {}
    face, gesture, word, look = fields
    lookup = lambda name, menu: next((k for k in menu if k.lower() == name), None)
    return {"face": lookup(FACE_WORDS.get(face, face), FACES), "gesture": lookup(gesture, GESTURES[char]),
            "word": word, "look": lookup(look, LOOKS)}


def spec(char, pick, text, audio_s):
    """The bridge's form: "face=<expr> look=<sprite> gesture=<part>,<script> at=<ms> end=<ms>"."""
    face = FACES.get(pick.get("face"), -1)
    look = LOOKS.get(pick.get("look"), -1)
    if pick.get("look") == "partner":
        look = 1 if char == "grace" else 0
    part, script, _ = GESTURES[char].get(pick.get("gesture"), (-1, -1, ""))
    return (f"face={face} look={look} gesture={part},{script} "
            f"at={word_time(text, pick.get('word', ''), audio_s)} end={int(audio_s * 1000)}")


def word_time(text, word, audio_s):
    """When `word` is spoken, in ms, assuming even pacing over the line (a little before, so the
    gesture's way in lands on the word). The first word if it isn't found."""
    words = re.findall(r"[A-Za-z']+", text)
    w = re.sub(r"[^a-z']", "", (word or "").lower())
    i = next((k for k, x in enumerate(words) if x.lower() == w), 0)
    before = sum(len(x) + 1 for x in words[:i])
    total = sum(len(x) + 1 for x in words) or 1
    return max(0, int(audio_s * 1000 * before / total) - 250)
