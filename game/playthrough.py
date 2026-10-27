"""Play a whole evening headlessly with a scripted guest; print the transcript and beat changes.

    python game/playthrough.py [--name Joe] [--pronouns he/him]

Text only (no voices, no renderer). Needs llama-server running.
"""
import argparse
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "prototype"))

from catalog import Catalog  # noqa: E402
from director import Director  # noqa: E402
from story_arc import ARC  # noqa: E402

GUEST = [
    "Hi guys! Wow, it's been forever. *hugs Trip*",
    "Happy anniversary! I remember when you two first got together.",
    "Sure, I'd love a drink. Surprise me, Trip.",
    "Grace, what are you having?",
    "The place looks amazing, Grace. Did you pick all this out?",
    "Honestly the couch is a little stiff.",
    "*looks at the painting* Who painted this one?",
    "Is that from Italy? It looks beautiful.",
    "Grace, you don't seem to have loved the trip.",
    "Should you get the phone?",
    "Trip, why don't you want to talk to your parents?",
    "Are you two okay? You seem tense.",
    "Grace, what's really going on?",
    "I think you should tell him how you feel.",
    "Trip, I'm on your side, but you need to listen to her.",
    "What are you doing, Trip?",
    "Grace, he's trying. Give him a chance.",
    "You both need to be honest with each other.",
    "Trip, do you think Grace looks down on you?",
    "Grace, do you still paint?",
    "Why did you stop?",
    "Trip, is there something you haven't told her?",
    "It's okay. Just say it.",
    "I think you two still love each other.",
    "Grace, what do you want now?",
    "Trip, what do you want?",
    "I should probably go. Thank you for having me.",
    "Take care of each other.",
    "Goodbye.",
    "Goodbye, really.",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="Joe")
    ap.add_argument("--pronouns", default="they/them")
    args = ap.parse_args()
    d = Director(Catalog(), args.name, args.pronouns)
    d.prime()
    print(f"== {ARC[0]['title']}")
    t_all = time.perf_counter()

    def show(run):
        t0 = time.perf_counter()
        last = None
        for line in run:
            if line.part_of_previous and last == line.speaker:
                print(f" {line.text}", end="")
            else:
                print(f"\n  {line.speaker.upper():5} ({line.delivery}) {line.text}", end="")
            last = line.speaker
        print(f"\n  [{time.perf_counter() - t0:.1f}s]", end="")
        for ev in d.events:
            print(f"\n\n== {ev['title']}" if ev["type"] == "beat" else f"\n  <{ev}>", end="")
        print()
        if d.intro_pending and not d.ended:
            print("  (beat opens)", end="")
            show(d.beat_intro())

    show(d.opening())
    i = 0
    for i, text in enumerate(GUEST, 1):
        print(f"\n{args.name.upper()}> {text}")
        show(d.turn(text))
        if d.ended:
            break
    print(f"\nended={d.ended} kicked_out={d.kicked_out} beat={d.beat['id']} affinity={d.affinity} "
          f"turns={i} total {time.perf_counter() - t_all:.0f}s")


if __name__ == "__main__":
    main()
