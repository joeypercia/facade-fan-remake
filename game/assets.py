"""Read the apartment, textures and animation from the player's own Facade install, for the client.

Nothing here is written to disk: geometry is decoded from the engine at startup, textures are
converted to PNG per request, and animation is packed into memory.
"""
import io
import json
import re
import struct
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import facade_formats as ff  # noqa: E402
from preview_skeleton import infer_bones  # noqa: E402

TEXTURE_DIR = ("util", "sources", "facade", "textures")

# Prop name -> original texture file (without .bmp). Unlisted props are skipped.
PROP_TEXTURES = {
    "plant": "plant", "ashtray": "ashtray", "phone": "phoneontable", "answering machine": "answeringmachine",
    "red wine bottle": "redwinebottle", "white wine bottle": "whitewinebottle",
    "martini glass 1": "martini_empty_hold", "martini glass 2": "martini_empty_hold",
    "martini glass 3": "martini_empty_hold", "martini glass 4": "martini_empty_hold",
    "big abstract painting": "painting1", "little painting": "littlepainting", "rug": "rug",
    "wedding photo": "weddingpic", "italy picture": "tuscany", "wedding gown sketch": "gownsketch1",
    "wedding gown post its": "post_its", "wedding gown sketch 3": "gownsketch3",
    "wedding gown sketch 4": "gownsketch4", "advice ball": "eightball", "brass bull": "brassbull",
    "front door": "frontdoor", "bedroom door": "bedroomdoor", "elevator button": "elevatorButton",
    **{f"trinket {i}": f"trinket{i}" for i in range(1, 9)},
}
WALL_TEXTURES = {"LR_backwindow": "nyc"}

# Furniture the original draws outside the sprite system: simple stand-ins placed to match the
# room, as (name, x, y, z, width, height, depth, colour). Replace with real models later.
FURNITURE = [
    ("couch", -10, 20, -300, 150, 40, 55, "#7a6a58"),
    ("couch back", -10, 45, -325, 150, 30, 10, "#6e5f4f"),
    ("coffee table", -10, 14, -250, 80, 28, 40, "#3b2f2a"),
    ("bar", -183, 20, -290, 30, 40, 90, "#2e2420"),
    ("bar top", -183, 41, -290, 34, 3, 94, "#c9b79c"),
    ("armoire", 175, 55, -300, 40, 110, 70, "#ede6da"),
    ("work table", -175, 22, -440, 40, 44, 70, "#8a7a64"),
]


def _f(v):
    return struct.unpack("<f", struct.pack("<I", v & 0xFFFFFFFF))[0] if abs(v) > 0xFFFF else float(v)


@lru_cache(maxsize=1)
def scene(facade=ff.DEFAULT_FACADE):
    """Walls, floors and props with positions, rotations, sizes and texture names."""
    data = ROOT / "data" / "scene.json"
    if not data.exists():
        subprocess.run([sys.executable, str(ROOT / "tools" / "scan_scene.py"), "--facade", str(facade),
                        "--out", str(data)], check=True)
    out = {"walls": [], "planes": [], "props": [], "furniture": []}
    for s in json.loads(data.read_text(encoding="utf-8")):
        a, name = s["args"], s["name"]
        if len(a) < 15:
            continue
        z, y, x = _f(a[2]), _f(a[3]), _f(a[4])
        tex_id, kind = a[6], a[7]
        if a[0] == 1:  # horizontal panel: floor or ceiling
            out["planes"].append({"name": name, "x": x, "y": y, "z": z, "w": _f(a[12]), "d": _f(a[13]),
                                  "ceiling": name.startswith("ceiling")})
        elif tex_id == -1:  # plain wall panel
            tex = next((t for p, t in WALL_TEXTURES.items() if name.startswith(p)), None)
            out["walls"].append({"name": name, "x": x, "y": y, "z": z, "ry": _f(a[10]), "h": _f(a[12]),
                                 "w": _f(a[13]), "tex": tex, "solid": a[1] == 1})
        else:  # textured object: flat (kind 7) or billboard sprite (kind -2)
            tex = PROP_TEXTURES.get(name)
            if tex is None or y < 0:  # unknown, or parked off-stage (held drinks)
                continue
            out["props"].append({"name": name, "x": x, "y": y, "z": z, "ry": _f(a[11]),
                                 "flat": _f(a[12]) == 90.0, "h": _f(a[13]), "w": _f(a[14]),
                                 "billboard": kind == -2, "tex": tex})
    out["furniture"] = [dict(zip(("name", "x", "y", "z", "w", "h", "d", "color"), f)) for f in FURNITURE]
    return out


def texture_png(name, facade=ff.DEFAULT_FACADE):
    """The named original texture as PNG bytes, with its colour key (pure magenta) made transparent."""
    from PIL import Image
    if not re.fullmatch(r"[A-Za-z0-9_]+", name):
        raise FileNotFoundError(name)
    path = Path(facade).joinpath(*TEXTURE_DIR, f"{name}.bmp")
    img = Image.open(path).convert("RGBA")
    px = np.array(img)
    key = (px[..., 0] > 240) & (px[..., 1] < 16) & (px[..., 2] > 240)
    px[key, 3] = 0
    buf = io.BytesIO()
    Image.fromarray(px).save(buf, "PNG")
    return buf.getvalue()


def sound_effect(name, facade=ff.DEFAULT_FACADE):
    """Path to one of the original global sound effects, or None."""
    if not re.fullmatch(r"[A-Za-z0-9_]+", name):
        return None
    path = Path(facade) / "util" / "sources" / "facade" / "Sounds" / "global" / f"{name}.wav"
    return path if path.is_file() else None


@lru_cache(maxsize=2)
def animation(char, facade=ff.DEFAULT_FACADE):
    """(info dict, float32 bytes) for one character: every frame's joint positions, plus clip table
    and inferred bones. Frames are (frames, joints, 3) in the original body-space units."""
    clips = ff.read_aindex(facade, char)
    frames = ff.read_frames(facade, char, clips)
    joints = frames[:, ff.FIRST_JOINT:].astype(np.float32)
    edges = infer_bones(joints)
    rest = joints[0]
    info = {
        "frames": int(joints.shape[0]),
        "joints": int(joints.shape[1]),
        "clips": [{"name": c.name, "start": c.start, "end": c.end} for c in clips],
        "bones": [[int(a), int(b)] for a, b in edges],
        "head": int(np.argmax(rest[:, 1])),
        "min_y": float(joints[..., 1].min()),
    }
    return info, joints.tobytes()
