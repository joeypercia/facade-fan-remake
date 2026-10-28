"""Offline check of the line service on line choices from real sessions (no game needed).

Verifies each generated record round-trips through the same parser that decoded the originals,
and that the voice file and job file are written as the bridge expects.
"""
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import line_service as ls  # noqa: E402
import facade_formats as ff  # noqa: E402

# (char id, scripts) as logged from pushMultipleAnimationScripts, in session order.
PUSHES = [(1, [1055, -1, -1, -1]), (0, [950, -1, -1, -1]), (0, [951, -1, -1, -1]),
          (1, [304, -1, -1, -1]), (1, [1083, 1082, -1, -1]), (1, [1473, 306, 1474, -1])]
HEARD = {3: "hi guys!", 4: "good, it's great to see you"}


def parse_record(tokens, char, script):
    """Run a single record through the same reader used for the original files."""
    body = [0, 2, 2, 0] + tokens + [ff.REC_START, ff.EOF_ID]
    path = Path(ls.INJECT) / "_check.sbinary"
    np.array(body, "<i4").tofile(path)
    import facade_formats
    toks = np.fromfile(path, "<i4").tolist()
    path.unlink()
    # reuse read_sbinary's logic on an in-memory stream
    perfs = {}
    starts = [k for k, t in enumerate(toks) if t == ff.REC_START]
    for s, e in zip(starts, starts[1:]):
        rec = toks[s:e]
        perf = ff.Performance(rec[1], [])
        b = rec.index(ff.BODY_START)
        seg = rec[b + 1:rec.index(ff.BODY_END, b)]
        sp = seg.index(ff.OP_SPEECH)
        nxt = next((i for i in range(sp + 1, len(seg)) if seg[i] >= ff.BODY_START), len(seg))
        facade_formats._parse_speech(seg[sp + 1:nxt], perf)
        perfs[rec[1]] = perf
    return perfs[script]


def main():
    lines = ls.Lines()
    for i, (cid, scripts) in enumerate(PUSHES):
        if i in HEARD:
            lines.heard(HEARD[i])
        t = time.perf_counter()
        out = lines.line(cid, scripts)
        dt = time.perf_counter() - t
        if not out.get("replace"):
            print(f"{scripts}: kept original ({out.get('why', 'not dialogue')}) [{dt:.2f}s]")
            continue
        char = ls.CHARS[cid]
        stem = ls.INJECT / f"{char}_{out['script']}"
        tok = np.fromfile(stem.with_suffix(".tok"), "<i4").tolist()
        job = dict(l.split("=", 1) for l in stem.with_suffix(".job").read_text().splitlines())
        perf = parse_record(tok, char, out["script"])
        wav = Path(job["wav"])
        print(f"{scripts}: {dt:.2f}s  file {job['file']}  wav {wav.stat().st_size // 1024} KB  "
              f"record ok: text={perf.text!r}, {sum(len(c) for _, c in perf.mouth)} phonemes, cues {perf.cues}")
        assert perf.text == out["text"].encode("latin-1", "replace").decode("latin-1")
        assert int(job["file"]) == out["file"] and wav.exists()


if __name__ == "__main__":
    main()
