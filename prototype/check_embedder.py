"""Does loading the line-matching embedder slow down speech generation?"""
import sys
import time
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).parent))
from catalog import Catalog  # noqa: E402
from streaming import stream  # noqa: E402
from voice import Voices  # noqa: E402

TEXT = "Just water, thank you."


def timed(v, label):
    t0 = time.perf_counter()
    first, n = None, 0
    for chunk in stream(v.model, TEXT):
        first = first or time.perf_counter() - t0
        n += len(chunk)
    print(f"{label:28} first {first:.2f}s total {time.perf_counter() - t0:.2f}s audio {n / 24000:.2f}s "
          f"| threads {torch.get_num_threads()} | grad {torch.is_grad_enabled()}", flush=True)


def main():
    c = Catalog()
    v = Voices(c, mute=True)
    v.load()
    v.model.conds = v.conds[("grace", "warm")]
    timed(v, "before embedder")
    timed(v, "before embedder (again)")
    c.nearest("grace", "warm up")
    timed(v, "after embedder loaded")
    timed(v, "after embedder (again)")


if __name__ == "__main__":
    main()
