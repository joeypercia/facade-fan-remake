"""Text -> Façade phoneme codes, learned from the original lines (no pronunciation dictionary).

Each voiced line in the player's install pairs its subtitle with the phoneme codes the engine uses
for lip sync. This learns, by hard EM over monotonic alignments, how chunks of 1-3 letters map to
one code or to nothing (silent letters), then decodes new text with the same model.

    python tools/phonemes.py train        # writes data/phoneme_model.json (local only)
    python tools/phonemes.py say "Where are the new wine glasses?"
"""
import json
import math
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from numba import njit

ROOT = Path(__file__).resolve().parent.parent
MODEL = ROOT / "data" / "phoneme_model.json"
SILENCE = 70        # marks the start of a phrase; not a phoneme
MAXCHUNK = 3
EPS = 0             # "emits nothing" (codes are 31..69)


def letters(text):
    """Lowercase letters with '_' between words (word edges matter: 'th' vs 't h')."""
    words = re.findall(r"[a-z]+", text.lower().replace("'", ""))
    return "_" + "_".join(words) + "_" if words else ""


def codes_of(phrases):
    return [c for _, cs in phrases for c in cs if c != SILENCE]


@njit(cache=True)
def _align(chunk_ids, n, codes, logp, n_codes):
    """Viterbi over (letter position, code position). chunk_ids[i, k] is the id of the chunk of
    length k+1 starting at letter i (-1 if past the end). logp[chunk, code] with code 0 = nothing.
    Returns the alignment as (start, length, code) triples in an array."""
    m = len(codes)
    NEG = -1e18
    best = np.full((n + 1, m + 1), NEG)
    back = np.zeros((n + 1, m + 1, 2), np.int64)  # (chunk length, emitted code or 0)
    best[0, 0] = 0.0
    for i in range(n):
        for j in range(m + 1):
            s = best[i, j]
            if s <= NEG:
                continue
            for k in range(MAXCHUNK):
                cid = chunk_ids[i, k]
                if cid < 0:
                    break
                ni = i + k + 1
                # emit nothing (only single letters may be silent)
                if k == 0:
                    v = s + logp[cid, 0]
                    if v > best[ni, j]:
                        best[ni, j] = v
                        back[ni, j, 0] = 1
                        back[ni, j, 1] = 0
                if j < m:
                    v = s + logp[cid, codes[j] - 30]
                    if v > best[ni, j + 1]:
                        best[ni, j + 1] = v
                        back[ni, j + 1, 0] = k + 1
                        back[ni, j + 1, 1] = codes[j]
    if best[n, m] <= NEG:
        return np.zeros((0, 3), np.int64)
    out = []
    i, j = n, m
    while i > 0:
        k, c = back[i, j, 0], back[i, j, 1]
        out.append((i - k, k, c))
        i -= k
        if c != 0:
            j -= 1
    res = np.zeros((len(out), 3), np.int64)
    for t in range(len(out)):
        res[t, 0], res[t, 1], res[t, 2] = out[len(out) - 1 - t]
    return res


def _chunk_table(s, vocab, grow):
    n = len(s)
    t = np.full((n, MAXCHUNK), -1, np.int64)
    for i in range(n):
        for k in range(MAXCHUNK):
            if i + k + 1 > n:
                break
            ch = s[i:i + k + 1]
            if ch not in vocab:
                if not grow:
                    continue
                vocab[ch] = len(vocab)
            t[i, k] = vocab[ch]
    return t


def load_pairs():
    lines = json.loads((ROOT / "data" / "catalog.json").read_text(encoding="utf-8"))
    pairs = []
    for l in lines:
        cs = codes_of(l.get("phrases") or [])
        s = letters(l["text"])
        if cs and s and len(s) < 400 and all(31 <= c <= 69 for c in cs):
            pairs.append((s, cs))
    return pairs


def train(iters=8):
    pairs = load_pairs()
    random.Random(1).shuffle(pairs)
    test, train_set = pairs[: len(pairs) // 10], pairs[len(pairs) // 10:]
    vocab = {}
    tables = [(_chunk_table(s, vocab, True), len(s), np.array(cs, np.int64)) for s, cs in train_set]
    n_codes = 40  # index 0 = nothing, 1..39 = codes 31..69
    # Start uniform with a mild preference for single letters emitting a code.
    logp = np.full((len(vocab), n_codes), math.log(1 / n_codes))
    for it in range(iters):
        counts = np.full((len(vocab), n_codes), 0.1)
        aligned = 0
        for t, n, cs in tables:
            path = _align(t, n, cs, logp, n_codes)
            if len(path) == 0:
                continue
            aligned += 1
            for start, k, c in path:
                counts[t[start, k - 1], 0 if c == 0 else c - 30] += 1
        logp = np.log(counts / counts.sum(axis=1, keepdims=True))
        # A chunk that can't explain itself should cost something: prefer common chunks.
        print(f"iter {it + 1}: aligned {aligned}/{len(tables)} lines")
    inv = {v: k for k, v in vocab.items()}
    model = {"chunks": {inv[i]: [round(float(x), 4) for x in logp[i]] for i in range(len(vocab))}}
    MODEL.parent.mkdir(parents=True, exist_ok=True)
    MODEL.write_text(json.dumps(model), encoding="utf-8")
    acc = evaluate(model, test)
    print(f"held-out: {acc:.1%} of codes right (edit-distance accuracy) on {len(test)} lines -> {MODEL}")


def _decode(s, model):
    """Best segmentation of the letters into chunks, each emitting its most likely code or nothing."""
    ch = model["chunks"]
    n = len(s)
    best = [(-1e18, None)] * (n + 1)
    best[0] = (0.0, None)
    for i in range(n):
        if best[i][0] <= -1e17:
            continue
        for k in range(1, MAXCHUNK + 1):
            if i + k > n or s[i:i + k] not in ch:
                continue
            lp = ch[s[i:i + k]]
            opts = [(lp[0], 0)] if k == 1 else []
            c = int(np.argmax(lp[1:])) + 1
            opts.append((lp[c], c + 30))
            for score, code in opts:
                v = best[i][0] + score - 0.3 * (k - 1)
                if v > best[i + k][0]:
                    best[i + k] = (v, (i, code))
    out, i = [], n
    while i > 0 and best[i][1]:
        prev, code = best[i][1]
        if code:
            out.append(code)
        i = prev
    return out[::-1]


def text_to_codes(text, model=None):
    model = model or json.loads(MODEL.read_text(encoding="utf-8"))
    return _decode(letters(text), model)


def evaluate(model, pairs):
    right = total = 0
    for s, cs in pairs:
        pred = _decode(s, model)
        d = _edit(pred, cs)
        right += max(0, len(cs) - d)
        total += len(cs)
    return right / total


def _edit(a, b):
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


if __name__ == "__main__":
    if sys.argv[1:2] == ["train"]:
        train()
    elif sys.argv[1:2] == ["say"]:
        print(text_to_codes(" ".join(sys.argv[2:])))
