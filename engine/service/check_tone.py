"""Does match_take move a generated line's sound toward the original take? Compares brightness
(spectral centroid) and loudness: original take vs. raw synthesis vs. matched synthesis."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import line_service as ls  # noqa: E402


def stats(x):
    import librosa
    c = librosa.feature.spectral_centroid(y=x, sr=ls.OUT_SR)[0]
    rms = librosa.feature.rms(y=x)[0]
    voiced = rms > rms.max() * 0.1
    return float(np.median(c[voiced])), float(np.sqrt(np.mean(rms[voiced] ** 2)))


def main():
    import soxr
    import librosa
    lines = ls.Lines.__new__(ls.Lines)
    from catalog import Catalog
    from voice import Voices
    cat = Catalog()
    by_id = {(l["character"], l["script_index"]): l for l in cat.lines}
    v = Voices(cat, mute=True)
    v.load()
    for key, text in [(("trip", 1055), "Where's the wine? I brought new glasses."),        # behind the door
                      (("trip", 1286), "You need to help me break in these new cocktail tools.")]:  # in the room
        line = by_id[key]
        v.model.conds = v.conds[(key[0], "neutral")]
        raw = soxr.resample(v.model.generate(text).squeeze(0).cpu().numpy(), v.model.sr, ls.OUT_SR)
        off = {"OFFSCREEN", "MUTED"} & set(line["direction"]) or line["beat"].startswith("PBEHINDDOOR")
        matched = ls.match_take(raw, ls.FACADE / line["audio"], bool(off))
        orig, _ = librosa.load(str(ls.FACADE / line["audio"]), sr=ls.OUT_SR)
        print(f"{key} offscreen={bool(off)}")
        for name, x in (("original take", orig), ("raw synthesis", raw), ("matched", matched)):
            c, r = stats(x)
            print(f"   {name:14} brightness {c:6.0f} Hz   loudness {r:.3f}")


if __name__ == "__main__":
    main()
