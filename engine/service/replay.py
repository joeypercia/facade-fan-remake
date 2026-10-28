"""Replay a logged session through the line service offline: the player's text and every speech push,
in order, as the bridge saw them. Shows what the parser would now hear and what each line becomes.

    python engine/service/replay.py [bridge log]   (default: %LOCALAPPDATA%\\FacadeFanRemake\\bridge-test2.log)
"""
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import line_service as ls  # noqa: E402


def events(path):
    for line in open(path, encoding="latin-1"):
        m = re.search(r'java\s+textSensor "(.*)"$', line.rstrip())
        if m and m[1] not in (" ", "warm up nlu"):
            yield "text", m[1]
        m = re.search(r"java\s+pushMultipleAnimationScripts ch=(\d) scripts=([-\d,]+) layer=7", line)
        if m:
            yield "push", (int(m[1]), [int(x) for x in m[2].split(",")])


def main():
    path = Path(sys.argv[1] if len(sys.argv) > 1 else
                os.path.expandvars(r"%LOCALAPPDATA%\FacadeFanRemake\bridge-test2.log"))
    lines = ls.Lines()
    lines.voices.warm_thread()
    replaced = kept = 0
    for kind, ev in events(path):
        if kind == "text":
            print(f"\nGUEST typed: {ev}\n  parser now hears: {lines.understand(ev)}")
        else:
            out = lines.line(*ev)
            if out.get("replace"):
                replaced += 1
            else:
                kept += 1
                why = out.get("why", "not dialogue")
                if why not in ("not dialogue", "short reaction"):
                    print(f"  kept original {ev[1]}: {why}")
    print(f"\n{replaced} lines rewritten, {kept} kept")


if __name__ == "__main__":
    main()
