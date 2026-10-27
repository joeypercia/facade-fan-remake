"""Talk to Grace and Trip in one beat, with live voices, and measure latency.

    python prototype/play.py                 interactive: type to talk, empty line to quit
    python prototype/play.py --bench         scripted guest inputs, prints a latency table
    python prototype/play.py --mute          no audio playback (still renders voices)

Needs llama-server running (see prototype/README.md).
"""
import argparse
import queue
import re
import statistics
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from brain import Brain, Line
from catalog import Catalog
from voice import Voices

BENCH_INPUTS = [
    "Hey guys! Happy anniversary!",
    "Sure, I'd love a drink. What do you recommend?",
    "Grace, what are you having?",
    "Honestly I'll just have whatever Grace is having.",
    "*looks at the bar* That's a lot of bottles, Trip.",
    "Actually, can I just get a glass of water?",
]


def split_sentences(line):
    """Split a line into sentence-sized Lines, keeping *stage business* attached to its sentence."""
    parts = [p for p in re.split(r"(?<=[.!?])(?<!\.\.\.)\s+(?=[A-Z*\"'])", line.text) if p.strip()]
    if len(parts) <= 1:
        return [line]
    return [Line(line.speaker, line.delivery, p, line.t_ready) for p in parts]


def run_turn(brain, voices, text, verbose=True):
    """One exchange. Lines are voiced as they stream in, so the first one plays while the rest generate."""
    t0 = time.perf_counter()
    results, first_audio = [], [None]
    todo = queue.Queue()

    def voice_worker():
        # One worker, in order, so playback order matches the script.
        while (line := todo.get()) is not None:
            info = voices.say(line)
            if info["source"] in ("recording", "synth") and first_audio[0] is None:
                first_audio[0] = time.perf_counter() - t0
            results.append((line, info))

    worker = threading.Thread(target=voice_worker)
    worker.start()
    for line in brain.turn(text):
        if verbose:
            print(f"  {line.speaker.upper():5} ({line.delivery}) {line.text}")
        # Voice sentence by sentence: a short first sentence gets audio out sooner.
        for part in split_sentences(line):
            todo.put(part)
    todo.put(None)
    worker.join()
    llm_first_line = min((l.t_ready for l, _ in results), default=None)
    return {
        "llm_first_token": brain.t_first_token,
        "llm_first_line": llm_first_line,
        "first_audio": first_audio[0],
        "lines": len(results),
        "sources": [i["source"] for _, i in results],
        "infos": [i for _, i in results],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--beat", default="FASKDRINKT1")
    ap.add_argument("--name", default="Joe")
    ap.add_argument("--pronouns", default="they/them", help="e.g. she/her, he/him, they/them")
    ap.add_argument("--bench", action="store_true")
    ap.add_argument("--mute", action="store_true")
    args = ap.parse_args()

    catalog = Catalog()
    voices = Voices(catalog)
    t = time.perf_counter()
    voices.load()
    catalog.nearest("trip", "warm up")
    print(f"voices ready in {time.perf_counter() - t:.1f}s")
    if args.mute:
        import sounddevice as sd
        sd.play = lambda *a, **k: None
        sd.wait = lambda: None

    brain = Brain(catalog, args.beat, args.name, args.pronouns)
    inputs = BENCH_INPUTS if args.bench else iter(lambda: input(f"\n{args.name.upper()}> ").strip(), "")
    stats = []
    for text in inputs:
        if args.bench:
            print(f"\n{args.name.upper()}> {text}")
        s = run_turn(brain, voices, text)
        stats.append(s)
        print(f"  [first token {s['llm_first_token']:.2f}s | first sentence {s['llm_first_line']:.2f}s"
              f" | first audio {s['first_audio'] or float('nan'):.2f}s | {s['sources']}]")
        voices.wait()
        if getattr(brain, "beat_done", False):
            print("  -- model says the beat is complete --")
            s["beat_done"] = True
            brain.beat_done = False
            if not args.bench:
                break

    fa = [s["first_audio"] for s in stats if s["first_audio"]]
    if fa:
        print(f"\nfirst audio: median {statistics.median(fa):.2f}s, worst {max(fa):.2f}s over {len(fa)} turns")
        rtf = [i["render_s"] / i["audio_s"] for s in stats for i in s["infos"] if i["source"] == "synth"]
        if rtf:
            print(f"synthesis real-time factor: median {statistics.median(rtf):.2f} (below 1.0 = faster than real time)")


if __name__ == "__main__":
    main()
