"""Objective checks for the voice path (nothing is played or saved).

1. Streaming: time to the first chunk, and whether chunk seams jump more than the audio around them.
2. Expressiveness: the same sentence under each emotion's conditioning, with loudness and pitch
   statistics, to confirm the emotions actually sound different.
"""
import statistics
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from catalog import Catalog  # noqa: E402
from streaming import stream  # noqa: E402
from voice import EMOTIONS, SR, Voices  # noqa: E402


def seam_report(chunks):
    """Sample-to-sample jump at each seam, relative to the 99th percentile jump inside the audio."""
    x = np.concatenate(chunks)
    jumps = np.abs(np.diff(x))
    typical = np.percentile(jumps, 99)
    seams = np.cumsum([len(c) for c in chunks[:-1]])
    worst = max((jumps[s - 1] for s in seams), default=0.0)
    return worst / typical if typical else 0.0


def pitch_stats(x):
    import librosa
    f0, voiced, _ = librosa.pyin(x, fmin=70, fmax=400, sr=SR, frame_length=2048)
    f0 = f0[voiced]
    rms = float(np.sqrt(np.mean(x ** 2)))
    if len(f0) < 5:
        return rms, float("nan"), float("nan")
    return rms, float(np.median(f0)), float(np.std(f0))


def main():
    v = Voices(Catalog(), mute=True)
    v.load()
    m = v.model
    print("conditionings:", sorted(f"{c}:{e}" for c, e in v.conds))

    print("\n1. streaming")
    firsts, ratios = [], []
    for text in ["Well, if you ask me, the best drink in the house is mine.",
                 "Trip, nobody wants one of your martinis. Just pour the wine.",
                 "I was just trying to be hospitable, that's all."] * 2:
        m.conds = v.conds[("trip", "neutral")]
        t0 = time.perf_counter()
        chunks, first = [], None
        for c in stream(m, text):
            first = first or time.perf_counter() - t0
            chunks.append(c)
        total = time.perf_counter() - t0
        r = seam_report(chunks)
        firsts.append(first)
        ratios.append(r)
        print(f"  first chunk {first:.2f}s | whole {total:.2f}s for {sum(map(len, chunks)) / SR:.2f}s audio"
              f" | {len(chunks)} chunks | worst seam jump {r:.2f}x typical")
    print(f"  median first chunk {statistics.median(firsts):.2f}s; worst seam {max(ratios):.2f}x typical")

    print("\n2. expressiveness (same sentence, 3 renders each; rms loudness, median pitch, pitch spread)")
    text = "I can't believe you would say that to me, tonight of all nights."
    for ch in ("grace", "trip"):
        for emotion in EMOTIONS:
            if (ch, emotion) not in v.conds:
                continue
            m.conds = v.conds[(ch, emotion)]
            stats = [pitch_stats(np.concatenate(list(stream(m, text)))) for _ in range(3)]
            rms, f0, spread = (statistics.mean(s[i] for s in stats) for i in range(3))
            print(f"  {ch:5} {emotion:8} rms {rms:.3f} | pitch {f0:5.0f} Hz | spread {spread:5.1f} Hz")


if __name__ == "__main__":
    main()
