"""How much does LLM generation on the same GPU slow down the voice's first chunk?"""
import json
import statistics
import sys
import threading
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from catalog import Catalog  # noqa: E402
from config import LLM_URL  # noqa: E402
from streaming import stream  # noqa: E402
from voice import Voices  # noqa: E402

TEXT = "Well, if you ask me, the best drink in the house is mine."


def llm_busy(stop):
    while not stop.is_set():
        r = requests.post(LLM_URL, stream=True, json={
            "messages": [{"role": "user", "content": "Write a long paragraph about cocktails."}],
            "stream": True, "max_tokens": 400, "chat_template_kwargs": {"enable_thinking": False}})
        for raw in r.iter_lines():
            if stop.is_set():
                break


def first_chunks(v, n=5):
    out = []
    for _ in range(n):
        t0 = time.perf_counter()
        for _chunk in stream(v.model, TEXT):
            out.append(time.perf_counter() - t0)
            break
    return statistics.median(out)


def main():
    v = Voices(Catalog(), mute=True)
    v.load()
    v.model.conds = v.conds[("trip", "neutral")]
    print(f"first chunk, GPU otherwise idle:      {first_chunks(v):.2f}s")
    stop = threading.Event()
    t = threading.Thread(target=llm_busy, args=(stop,), daemon=True)
    t.start()
    time.sleep(1)
    print(f"first chunk, LLM generating alongside: {first_chunks(v):.2f}s")
    stop.set()


if __name__ == "__main__":
    main()
