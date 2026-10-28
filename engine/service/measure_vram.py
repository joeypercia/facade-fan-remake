"""Measure GPU memory for the LLM server (a few settings) and the voice model (fp32 vs fp16).

Starts each piece on its own, reads nvidia-smi, stops it. Run with the GPU otherwise idle.
"""
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "prototype"))
from config import LLM_MODEL, LLM_SERVER  # noqa: E402


def gpu_used():
    out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True).stdout
    return int(out.strip().splitlines()[0])


def llm(extra, label):
    base = gpu_used()
    p = subprocess.Popen([LLM_SERVER, "-m", LLM_MODEL, "--port", "8099", "-ngl", "99", "-fa", "on", "--jinja",
                          "--reasoning", "off", "-np", "1"] + extra,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(120):
            try:
                if requests.get("http://127.0.0.1:8099/health", timeout=1).ok:
                    break
            except requests.RequestException:
                pass
            time.sleep(1)
        t = time.perf_counter()
        r = requests.post("http://127.0.0.1:8099/v1/chat/completions", timeout=60, json={
            "messages": [{"role": "user", "content": "Write one line of dialogue for a host offering a drink."}],
            "max_tokens": 40, "chat_template_kwargs": {"enable_thinking": False}})
        took = time.perf_counter() - t
        print(f"LLM {label:34} +{gpu_used() - base:5d} MB   one line in {took:.2f}s: "
              f"{r.json()['choices'][0]['message']['content'][:50]!r}", flush=True)
    finally:
        p.terminate()
        p.wait()
        time.sleep(2)


def voice(half):
    code = f"""
import torch, time, sys
sys.path.insert(0, r'{ROOT / "prototype"}')
from chatterbox.tts_turbo import ChatterboxTurboTTS
m = ChatterboxTurboTTS.from_pretrained(device='cuda')
if {half}:
    m.t3 = m.t3.half(); m.s3gen = m.s3gen.half()
torch.cuda.synchronize()
t = time.perf_counter()
try:
    with torch.autocast('cuda', dtype=torch.float16, enabled={half}):
        wav = m.generate("Come on, Grace. It's our anniversary.", audio_prompt_path=None) if m.conds else None
    ok = 'ok'
except Exception as e:
    ok = repr(e)[:80]
print(f"allocated {{torch.cuda.memory_allocated()/2**20:.0f}} MB, reserved {{torch.cuda.memory_reserved()/2**20:.0f}} MB, "
      f"peak {{torch.cuda.max_memory_allocated()/2**20:.0f}} MB, render {{time.perf_counter()-t:.2f}}s {{ok}}")
"""
    base = gpu_used()
    py = r"C:\Users\joeyp\.venvs\facade-remastered\Scripts\python.exe"
    p = subprocess.Popen([py, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    peak = base
    while p.poll() is None:
        peak = max(peak, gpu_used())
        time.sleep(0.5)
    print(f"voice {'fp16' if half else 'fp32':4} process peak +{peak - base:5d} MB | {p.stdout.read().strip()}", flush=True)


if __name__ == "__main__":
    llm(["-c", "8192"], "now (8K context, fp16 cache)")
    llm(["-c", "4096", "-ctk", "q8_0", "-ctv", "q8_0"], "4K context, 8-bit cache")
    voice(False)
    voice(True)
