"""Readers for Facade's animation formats. See docs/ASSETS.md for the layouts.

Everything here reads from the player's own install; nothing it returns may be committed.
"""
import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

DEFAULT_FACADE = Path(r"C:\Program Files (x86)\Facade")
CHARACTERS = ("grace", "trip")

# Floats per animation frame. Each frame is a run of vec3s: 0-1 unused, 2 root offset,
# 3-4 angles in degrees, 5.. body-space joint positions.
FRAME_FLOATS = {"grace": 243, "trip": 234}
FIRST_JOINT = 5

AINDEX_RECORD = 259
AINDEX_NAME_LEN = 255

# .sbinary tokens
REC_START = 199998
EOF_ID = 199999         # REC_START, EOF_ID ends the file
BODY_START = 200000
OP_PLAY = 200001        # a b: play global frames a..b
OP_HOLD = 200004        # a: hold global frame a
OP_SPEECH = 200005      # voiced-line block (base frame, mouth codes, cues, subtitle)
BODY_END = 200007
END = -1
# Opcode meanings so far are inferred from usage; see docs/ASSETS.md.
OP_NAMES = {"frames": "frames", OP_PLAY: "play", 200002: "op2", 200003: "op3", OP_HOLD: "hold", 200006: "segment"}


def anim_dir(facade, char):
    return Path(facade) / "util" / "sources" / "facade" / "animation" / char


@dataclass
class Clip:
    index: int
    name: str
    start: int  # first global frame
    end: int    # one past the last global frame


def read_aindex(facade, char):
    data = (anim_dir(facade, char) / f"{char}.aindex").read_bytes()
    clips, prev = [], 0
    for i in range(len(data) // AINDEX_RECORD):
        rec = data[i * AINDEX_RECORD:(i + 1) * AINDEX_RECORD]
        name = rec[:AINDEX_NAME_LEN].split(b"\0", 1)[0].decode("latin-1")
        (end,) = struct.unpack_from("<i", rec, AINDEX_NAME_LEN)
        clips.append(Clip(i, name, prev, end))
        prev = end
    return clips


def read_frames(facade, char, clips=None):
    """All clips concatenated in index order: array (frames, channels, 3). Global frame numbers index this."""
    clips = clips or read_aindex(facade, char)
    n = FRAME_FLOATS[char]
    parts = []
    for c in clips:
        f = np.fromfile(anim_dir(facade, char) / f"{char}{c.index:03}.abinary", "<f4").reshape(-1, n)
        if len(f) != c.end - c.start:
            raise ValueError(f"{char} clip {c.index} {c.name}: {len(f)} frames, index says {c.end - c.start}")
        parts.append(f)
    return np.concatenate(parts).reshape(-1, n // 3, 3)


@dataclass
class Performance:
    script_index: int
    header: list
    ops: list = field(default_factory=list)  # (name, args): ("play", [a, b]), ("hold", [a, ...]), ("frames", [...])
    base_frame: int | None = None
    mouth: list = field(default_factory=list)  # phrases: (start_ms, [phoneme codes])
    cues: list = field(default_factory=list)   # (time_ms, cue id); the last is (duration_ms, 0)
    text: str | None = None


PHONEMES = range(31, 71)  # phoneme codes; cue ids are below 31
PHONEME_START = 70        # opens a phrase


def _parse_speech(args, perf):
    """OP_SPEECH args:
    base frame, 0, flag,
    [phoneme codes..., 0]                      first phrase, starting at 0 ms (if any)
    then entries, each a time in ms followed by either
        phoneme codes..., 0                    a phrase starting at that time
        a cue id                               a timed cue
    and finally (duration_ms, 0),
    END, script index, subtitle chars..., END
    """
    perf.base_frame = args[0]
    i = 3

    def phrase(start, i):
        codes = []
        while args[i] != 0:
            codes.append(args[i]); i += 1
        perf.mouth.append((start, codes))
        return i + 1

    if args[i] in PHONEMES:
        i = phrase(0, i)
    while True:
        t, x = args[i], args[i + 1]
        if x in PHONEMES:
            i = phrase(t, i + 1)
        else:
            perf.cues.append((t, x)); i += 2
            if x == 0:
                break
    i += 2  # END, script index
    perf.text = "".join(chr(c) for c in args[i:args.index(END, i)])


def read_records_raw(facade, char):
    """{script index: (record order, [tokens from REC_START up to and including BODY_END])}.
    Record order is the position in the file, which is how the engine's in-memory table is indexed."""
    toks = np.fromfile(anim_dir(facade, char) / f"{char}Script.sbinary", "<i4").tolist()
    starts = [k for k, t in enumerate(toks) if t == REC_START]
    out = {}
    for order, (s, e) in enumerate(zip(starts, starts[1:] + [len(toks)])):
        rec = toks[s:e]
        if rec[1] == EOF_ID:
            break
        out[rec[1]] = (order, rec[:rec.index(BODY_END) + 1])
    return out


def read_sbinary(facade, char):
    toks = np.fromfile(anim_dir(facade, char) / f"{char}Script.sbinary", "<i4").tolist()
    starts = [k for k, t in enumerate(toks) if t == REC_START]
    perfs = {}
    for s, e in zip(starts, starts[1:] + [len(toks)]):
        rec = toks[s:e]
        if rec[1] == EOF_ID:
            break
        b = rec.index(BODY_START)
        perf = Performance(rec[1], rec[2:b])
        body = rec[b + 1:rec.index(BODY_END, b)]
        # Split into (opcode, args) segments; args run until the next opcode.
        # Leading bare numbers are an explicit frame list.
        op, args = "frames", []
        for t in body + [BODY_END]:
            if t < BODY_START:
                args.append(t)
                continue
            if op == OP_SPEECH:
                _parse_speech(args, perf)
            elif op != "frames" or args:
                perf.ops.append((OP_NAMES.get(op, op), args))
            op, args = t, []
        perfs[perf.script_index] = perf
    return perfs
