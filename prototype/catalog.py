"""The player's local line catalog: style examples per beat, and nearest-recording lookup."""
import json
import random

import numpy as np

from config import CATALOG, FACADE


class Catalog:
    def __init__(self):
        if not CATALOG.exists():
            raise SystemExit("data/catalog.json missing: run tools/extract_catalog.py first")
        self.lines = [l for l in json.loads(CATALOG.read_text(encoding="utf-8")) if l["text"]]
        self._index = None

    def examples(self, beats, character, n=6, seed=None):
        pool = [l for l in self.lines if l["beat"] in beats and l["character"] == character and l["audio"]]
        random.Random(seed).shuffle(pool)
        return [l["text"] for l in pool[:n]]

    def reference_clips(self, character, directions, avoid=(), seconds=12.0, min_len=2.5, max_len=7.0,
                        count=None):
        """Paths to recordings marked with any of `directions` (NEUTRAL also matches takes with no
        direction at all), skipping any marked with `avoid`, between min_len and max_len seconds
        long, until `seconds` of audio (or `count` clips) are collected."""
        import soundfile as sf

        def fits(l):
            d = set(l["direction"])
            if d & set(avoid):
                return False
            return bool(d & set(directions)) or ("NEUTRAL" in directions and not d)

        pool = [l for l in self.lines if l["character"] == character and l["audio"]
                and len(l["text"]) > 40 and fits(l)]
        random.Random(7).shuffle(pool)
        picked, total = [], 0.0
        for l in pool:
            path = FACADE / l["audio"]
            info = sf.info(path)
            if min_len < info.duration < max_len:
                picked.append(path)
                total += info.duration
            if total >= seconds or (count and len(picked) >= count):
                break
        return picked

    def nearest(self, character, text):
        """(similarity, line) of the closest recorded line for this character."""
        if self._index is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer("BAAI/bge-small-en-v1.5")
            self._index = {}
            for ch in ("grace", "trip"):
                rec = [l for l in self.lines if l["character"] == ch and l["audio"]]
                emb = self._model.encode([l["text"] for l in rec], normalize_embeddings=True, batch_size=256)
                self._index[ch] = (rec, emb)
        rec, emb = self._index[character]
        q = self._model.encode([text], normalize_embeddings=True)[0]
        sims = emb @ q
        i = int(np.argmax(sims))
        return float(sims[i]), rec[i]
