"""Build a local line catalog from the player's own Facade install.

The original game encodes each voiced line as a `static final int` constant in
facade/util/GraceScript.class and TripScript.class, named

    e<char>Script_dialog_<BEAT>_<DIRECTION...>_<spoken_words>

This script reads those constants and pairs each one with its recording under
Sounds/<char>/. The output is written to the user's local data folder and must
never be committed or redistributed: it is derived from copyrighted material.

Usage: python tools/extract_catalog.py [--facade "C:/Program Files (x86)/Facade"] [--out data/catalog.json]
"""
import argparse
import json
import re
import struct
import sys
from pathlib import Path

from facade_formats import read_sbinary

DEFAULT_FACADE = Path(r"C:\Program Files (x86)\Facade")


def read_class_constants(path):
    """Return {field_name: int_value} for every static final int field in a .class file."""
    data = Path(path).read_bytes()
    pos = 8
    (count,) = struct.unpack_from(">H", data, pos)
    pos += 2
    pool = [None] * count
    i = 1
    while i < count:
        tag = data[pos]
        pos += 1
        if tag == 1:  # Utf8
            (n,) = struct.unpack_from(">H", data, pos)
            pool[i] = data[pos + 2:pos + 2 + n].decode("utf-8", "replace")
            pos += 2 + n
        elif tag == 3:  # Integer
            pool[i] = struct.unpack_from(">i", data, pos)[0]
            pos += 4
        elif tag == 4:  # Float
            pos += 4
        elif tag in (5, 6):  # Long, Double take two slots
            pos += 8
            i += 1
        elif tag in (7, 8, 16):  # Class, String, MethodType
            pos += 2
        elif tag in (9, 10, 11, 12, 18):  # refs, NameAndType, InvokeDynamic
            pos += 4
        elif tag == 15:  # MethodHandle
            pos += 3
        else:
            raise ValueError(f"{path}: unknown constant pool tag {tag} at {pos - 1}")
        i += 1

    pos += 6  # access_flags, this_class, super_class
    (n_ifaces,) = struct.unpack_from(">H", data, pos)
    pos += 2 + 2 * n_ifaces
    (n_fields,) = struct.unpack_from(">H", data, pos)
    pos += 2
    out = {}
    for _ in range(n_fields):
        _, name_idx, _, n_attrs = struct.unpack_from(">HHHH", data, pos)
        pos += 8
        for _ in range(n_attrs):
            attr_name_idx, attr_len = struct.unpack_from(">HI", data, pos)
            if pool[attr_name_idx] == "ConstantValue":
                (cv,) = struct.unpack_from(">H", data, pos + 6)
                if isinstance(pool[cv], int):
                    out[pool[name_idx]] = pool[cv]
            pos += 6 + attr_len
    return out


def index_recordings(sounds_dir):
    """Map recording number -> path relative to the Facade install, e.g. 9 -> Sounds/grace/01/00009.wav."""
    found = {}
    for wav in sounds_dir.rglob("*.wav"):
        if wav.stem.isdigit():
            found[int(wav.stem)] = wav
    return found


DIALOG = re.compile(r"^e(grace|trip)Script_dialog_(?P<beat>[A-Za-z0-9]+)_(?P<rest>.+)$")


def split_direction(rest):
    """Leading ALL-CAPS tokens are performance directions; the lowercase remainder is the line."""
    # Some names glue a direction onto the first word ("FRUSTRATIONit"); split those apart.
    tokens = []
    for t in rest.split("_"):
        m = re.match(r"^([A-Z]{2,})([a-z].*)$", t)
        tokens.extend(m.groups() if m else [t])
    direction = []
    while tokens and tokens[0].isupper():
        direction.append(tokens.pop(0))
    # Trailing caps are directions too ("...hang up here FRUSTRATED SIGH").
    trailing = []
    while tokens and tokens[-1].isupper():
        trailing.insert(0, tokens.pop())
    return direction + trailing, tokens


def words_to_text(tokens):
    # Digits glued to words disambiguate homographs in the original naming (e.g. "simply1"); drop them.
    words = [re.sub(r"(?<=[a-z])\d+$", "", t) for t in tokens if t]
    return " ".join(words)


def build(facade):
    util = facade / "util"
    lines = []
    for char in ("grace", "trip"):
        cls = util / "classes" / "facade" / "util" / f"{char.capitalize()}Script.class"
        recordings = index_recordings(util / "sources" / "facade" / "Sounds" / char)
        perfs = read_sbinary(facade, char)
        for name, value in sorted(read_class_constants(cls).items(), key=lambda kv: kv[1]):
            m = DIALOG.match(name)
            if not m:
                continue
            direction, tokens = split_direction(m["rest"])
            wav = recordings.get(value)
            perf = perfs.get(value)
            lines.append({
                "id": f"{char}:{value}",
                "character": char,
                "script_index": value,
                "beat": m["beat"],
                "direction": direction,
                # The performance script carries the real subtitle; the constant name is a fallback.
                "text": (perf.text.strip() if perf and perf.text else words_to_text(tokens)),
                "audio": str(wav.relative_to(facade)).replace("\\", "/") if wav else None,
                "phrases": perf.mouth if perf else [],
                "cues": perf.cues if perf else [],
            })
    return lines


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--facade", type=Path, default=DEFAULT_FACADE)
    ap.add_argument("--out", type=Path, default=Path("data/catalog.json"))
    args = ap.parse_args()
    if not (args.facade / "util" / "classes").is_dir():
        sys.exit(f"No Facade install found at {args.facade}. Install the original game first.")
    lines = build(args.facade)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(lines, indent=1), encoding="utf-8")
    with_audio = sum(1 for l in lines if l["audio"])
    print(f"{len(lines)} lines ({with_audio} with recordings) -> {args.out}")


if __name__ == "__main__":
    main()
