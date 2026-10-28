"""Where does the voice side's GPU memory go? PyTorch's own counters and nvidia-smi after each step."""
import subprocess
import sys
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "prototype"))


def smi():
    return int(subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                              capture_output=True, text=True).stdout.split()[0])


BASE = smi()


def report(step):
    print(f"{step:34} process +{smi() - BASE:5d} MB | torch allocated {torch.cuda.memory_allocated() / 2**20:6.0f} MB, "
          f"reserved {torch.cuda.memory_reserved() / 2**20:6.0f} MB, peak {torch.cuda.max_memory_allocated() / 2**20:6.0f} MB",
          flush=True)


torch.zeros(1, device="cuda")
report("CUDA context only")
from catalog import Catalog  # noqa: E402
from voice import EMOTIONS, Voices, load_fp16  # noqa: E402
from chatterbox.tts_turbo import ChatterboxTurboTTS  # noqa: E402

v = Voices(Catalog(), mute=True)
v.model = load_fp16(ChatterboxTurboTTS)
report("model loaded (fp16)")
from voice import BAD_REFERENCE, quiet_engine  # noqa: E402
quiet_engine()
n = 0
for ch in ("grace", "trip"):
    for emotion, (directions, _) in EMOTIONS.items():
        clips = v.catalog.reference_clips(ch, directions=directions, avoid=BAD_REFERENCE)
        if clips:
            v.conds[(ch, emotion)] = v._condition(clips)
            n += 1
report(f"{n} voices conditioned")
torch.cuda.empty_cache()
report("after empty_cache")
v.model.conds = v.conds[("trip", "neutral")]
v.model.generate("Come on, Grace. It's our anniversary, can we not do this tonight?")
report("after one line")
torch.cuda.empty_cache()
report("after one line + empty_cache")
