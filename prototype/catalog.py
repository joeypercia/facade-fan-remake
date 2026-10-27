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

    def reference_clips(self, character, seconds=12.0):
        """Paths to a few longer, calm recordings for voice conditioning."""
        import soundfile as sf
        calm = [l for l in self.lines if l["character"] == character and l["audio"]
                and not set(l["direction"]) & {"SCREAM", "YELL", "ANGRY", "MUTED", "OFFSCREEN", "CRY", "SOB"}
                and len(l["text"]) > 60]
        random.Random(7).shuffle(calm)
        picked, total = [], 0.0
        for l in calm:
            path = FACADE / l["audio"]
            info = sf.info(path)
            if 2.5 < info.duration < 7:
                picked.append(path)
                total += info.duration
            if total >= seconds:
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
