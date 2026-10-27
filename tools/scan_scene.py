"""Recover the apartment layout from the engine DLL in the player's install.

The engine builds the scene in code: each wall, prop and character is created by one call to a
"sprite" constructor, with its type id, name, flags and sizes pushed as arguments, followed by
position/scale vectors built with a Vec3 constructor. This scans every call to the constructor and
collects those pushed values. Output goes to data/ (local only).

Usage: python tools/scan_scene.py [--facade ...]
"""
import argparse
import json
import struct
from pathlib import Path

import capstone
import pefile

import facade_formats as ff

# Addresses in animEngineDLL.dll v1.1 (image base 0x10000000).
SPRITE_CTOR = 0x10002310
VEC3_CTOR = 0x10001014


def as_float(v):
    return struct.unpack("<f", struct.pack("<I", v & 0xFFFFFFFF))[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--facade", type=Path, default=ff.DEFAULT_FACADE)
    ap.add_argument("--out", type=Path, default=Path("data/scene.json"))
    args = ap.parse_args()

    pe = pefile.PE(str(args.facade / "util" / "sources" / "facade" / "animEngineDLL.dll"))
    base = pe.OPTIONAL_HEADER.ImageBase
    img = pe.get_memory_mapped_image()
    text = next(s for s in pe.sections if s.Name.startswith(b".text"))
    code = img[text.VirtualAddress:text.VirtualAddress + text.Misc_VirtualSize]
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.skipdata = True  # keep sweeping past bytes that aren't instructions

    def c_string(va):
        off = va - base
        if not 0 <= off < len(img):
            return None
        raw = img[off:off + 80].split(b"\0", 1)[0]
        return raw.decode("latin-1") if raw and all(32 <= b < 127 for b in raw) else None

    insns = list(md.disasm(code, base + text.VirtualAddress))
    sprites = []
    for k, ins in enumerate(insns):
        if ins.mnemonic != "call" or ins.op_str != hex(SPRITE_CTOR):
            continue
        # Pushes since the previous call are this call's arguments (cdecl, pushed right to left).
        pushes, vecs, j = [], [], k - 1
        while j >= 0 and insns[j].mnemonic != "call" or (j >= 0 and insns[j].op_str == hex(VEC3_CTOR)):
            i = insns[j]
            if i.mnemonic == "push" and i.op_str.startswith(("0x", "-")) or (i.mnemonic == "push" and i.op_str.isdigit()):
                pushes.append(int(i.op_str, 0))
            j -= 1
            if k - j > 60:
                break
        pushes.reverse()  # now in argument order: arg0 first
        # A Vec3 built just before the call is the position: three float pushes then VEC3_CTOR.
        name = next((c_string(p) for p in pushes if c_string(p)), None)
        type_id = pushes[0] if pushes else None
        floats = [round(as_float(p), 2) for p in pushes if abs(p) > 0xFFFF and c_string(p) is None]
        sprites.append({"call": hex(ins.address), "type": type_id, "name": name,
                        "args": pushes, "floats": floats})

    # Positions: the Vec3 the engine builds right after creating each sprite, before the next one.
    calls = {int(s["call"], 16): s for s in sprites}
    order = sorted(calls)
    for n, addr in enumerate(order):
        nxt = order[n + 1] if n + 1 < len(order) else addr + 0x400
        floats = []
        for ins in (i for i in insns if addr < i.address < nxt):
            if ins.mnemonic == "push" and ins.op_str.startswith("0x") and int(ins.op_str, 16) > 0xFFFF:
                floats.append(round(as_float(int(ins.op_str, 16)), 2))
            if ins.mnemonic == "call" and ins.op_str == hex(VEC3_CTOR) and len(floats) >= 3:
                calls[addr].setdefault("vecs", []).append(floats[-3:][::-1])  # pushed z, y, x
        if n > 2000:
            break
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(sprites, indent=1), encoding="utf-8")
    named = sum(1 for s in sprites if s["name"])
    print(f"{len(sprites)} sprite constructions ({named} named) -> {args.out}")


if __name__ == "__main__":
    main()
