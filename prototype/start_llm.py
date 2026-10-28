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
    "-c", "4096",            # enough for one line's prompt plus tonight's recent exchanges
    "-ctk", "q8_0", "-ctv", "q8_0",  # 8-bit KV cache: with the 4K context, 6.1 -> 5.4 GB of GPU memory
    "-fa", "on",             # flash attention
    "--jinja",               # use the model's chat template (needed for enable_thinking=False)
    "--reasoning", "off",    # no hidden thinking pass: it would add seconds before the first line
    # Plain prefix caching (on by default) reuses the system prompt between turns. Don't add
    # --cache-reuse: its KV shifting replayed stale chunks and made replies repeat verbatim.
    "-np", "1",
]
print(" ".join(cmd))
subprocess.run(cmd)
