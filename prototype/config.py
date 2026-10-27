"""Prototype settings. Paths default to this machine's layout; override with environment variables."""
import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FACADE = Path(os.environ.get("FACADE_DIR", r"C:\Program Files (x86)\Facade"))
CATALOG = REPO / "data" / "catalog.json"

# Local LLM: any OpenAI-compatible server. Default is llama.cpp's llama-server.
LLM_URL = os.environ.get("FACADE_LLM_URL", "http://127.0.0.1:8088/v1/chat/completions")
LLM_SERVER = os.environ.get("FACADE_LLM_SERVER", r"C:\llama.cpp\llama-server.exe")
LLM_MODEL = os.environ.get("FACADE_LLM_MODEL", r"G:\AI\models\Qwen3-8B-Q4_K_M.gguf")

# Voice synthesis per character. Set a character to False if their actor withdraws consent:
# they then speak only in original recordings, with subtitles for lines that weren't recorded.
VOICE_SYNTHESIS = {"grace": True, "trip": True}

# Play an original recording instead of synthesizing when one is at least this similar (cosine)
# to the planned line. 1.0 disables reuse (always synthesize).
RECORDING_MATCH = float(os.environ.get("FACADE_RECORDING_MATCH", "0.92"))
