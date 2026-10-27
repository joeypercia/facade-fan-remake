"""Render one of the original clips as a stick figure, to check the animation decoding by eye.

Bones are inferred, not read from the game: joint pairs whose distance stays constant across
every frame are joined (a minimum spanning tree over distance variance). Output goes to data/,
which is local-only.

Usage: python tools/preview_skeleton.py grace gr_armL_sip
"""
import argparse
import heapq
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

import facade_formats as ff


def infer_bones(joints):
    """joints: (frames, J, 3). Returns [(a, b)] edges of the most rigid spanning tree."""
    d = np.linalg.norm(joints[:, :, None] - joints[:, None], axis=-1)
    mean = d.mean(0)
    cost = d.std(0) / (mean + 1e-6) + 0.002 * mean
    np.fill_diagonal(cost, np.inf)
    n = cost.shape[0]
    seen, edges = {0}, []
    heap = [(cost[0, j], 0, j) for j in range(1, n)]
    heapq.heapify(heap)
    while heap and len(seen) < n:
        _, a, b = heapq.heappop(heap)
        if b in seen:
            continue
        seen.add(b)
        edges.append((a, b))
        for k in range(n):
            if k not in seen:
                heapq.heappush(heap, (cost[b, k], b, k))
    return edges


def render(joints, edges, size=360, views=((0, 1), (2, 1))):
    """Front (x, y) and side (z, y) views side by side for each frame."""
    lo, hi = joints.reshape(-1, 3).min(0), joints.reshape(-1, 3).max(0)
    scale = 0.9 * size / (hi - lo).max()
    frames = []
    for pose in joints:
        img = Image.new("RGB", (size * len(views), size), (24, 22, 28))
        draw = ImageDraw.Draw(img)
        for v, (h, vert) in enumerate(views):
            def px(p):
                cx = v * size + size / 2 + (p[h] - (lo[h] + hi[h]) / 2) * scale
                cy = size / 2 - (p[vert] - (lo[vert] + hi[vert]) / 2) * scale
                return cx, cy
            for a, b in edges:
                draw.line([px(pose[a]), px(pose[b])], fill=(235, 200, 150), width=2)
            for p in pose:
                x, y = px(p)
                draw.ellipse([x - 2, y - 2, x + 2, y + 2], fill=(255, 120, 90))
        frames.append(img)
    return frames


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("character", choices=ff.CHARACTERS)
    ap.add_argument("clip", help="clip name from the .aindex, e.g. gr_armL_sip")
    ap.add_argument("--facade", type=Path, default=ff.DEFAULT_FACADE)
    ap.add_argument("--out", type=Path, default=Path("data/previews"))
    args = ap.parse_args()

    clips = ff.read_aindex(args.facade, args.character)
    frames = ff.read_frames(args.facade, args.character, clips)
    joints = frames[:, ff.FIRST_JOINT:]
    edges = infer_bones(joints)
    clip = next((c for c in clips if c.name == args.clip), None)
    if clip is None:
        raise SystemExit(f"no clip {args.clip}; choose from: {', '.join(c.name for c in clips)}")
    imgs = render(joints[clip.start:clip.end], edges)
    args.out.mkdir(parents=True, exist_ok=True)
    out = args.out / f"{clip.name}.gif"
    imgs[0].save(out, save_all=True, append_images=imgs[1:], duration=1000 // 30, loop=0)
    print(f"{len(imgs)} frames -> {out}")


if __name__ == "__main__":
    main()
