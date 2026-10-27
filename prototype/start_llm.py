"""Start llama-server for the prototype with settings tuned for low first-token latency."""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import LLM_MODEL, LLM_SERVER  # noqa: E402

cmd = [
    LLM_SERVER,
    "-m", LLM_MODEL,
    "--host", "127.0.0.1", "--port", "8088",
    "-ngl", "99",            # whole model on the GPU
    "-c", "8192",            # enough for the beat prompt plus tonight's exchanges
    "-fa", "on",             # flash attention
    "--jinja",               # use the model's chat template (needed for enable_thinking=False)
    "--reasoning", "off",    # no hidden thinking pass: it would add seconds before the first line
    "--cache-reuse", "256",  # reuse the shared system-prompt prefix between turns
    "-np", "1",
]
print(" ".join(cmd))
subprocess.run(cmd)
