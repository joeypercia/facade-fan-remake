"""Voice a few lines through the full say() path (muted) and report timing and audio length per line."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from brain import Line  # noqa: E402
from catalog import Catalog  # noqa: E402
from voice import Voices  # noqa: E402

LINES = [
    ("trip", "forced cheer", "So, anyhow, Joe, you're just in time!"),
    ("grace", "pointed", "Oh, nothing for me."),
    ("grace", "warm", "Just water, thank you."),
    ("trip", "dry", "I think you'll find it's not the drink that matters, Joe."),
    ("trip", "neutral", "I think you'll find it's not the drink that matters, Joe."),
]


def main():
    c = Catalog()
    v = Voices(c, mute=True)
    v.load()
    c.nearest("trip", "warm up")
    for ch, delivery, text in LINES:
        t0 = time.perf_counter()
        i = v.say(Line(ch, delivery, text, 0))
        print(f"{ch:5} {i['source']:9} first audio {i.get('t_first_audio', t0) - t0:5.2f}s | total "
              f"{time.perf_counter() - t0:5.2f}s | audio {i.get('audio_s', 0):5.2f}s | "
              f"{i.get('emotion')} | {i.get('tagged', text)}", flush=True)


if __name__ == "__main__":
    main()
