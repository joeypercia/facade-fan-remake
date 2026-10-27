# LLM + voice prototype

A throwaway console prototype that answers one question: can a local model write fresh
Grace and Trip lines inside one of the original beats, and can they be heard in their voices
fast enough to feel like conversation?

There's no renderer here. You type as the guest; Grace and Trip answer out loud.

## Pipeline

1. **Brain** (`brain.py`): builds a prompt from the setting and characters (in this project's own
   words), the current beat's situation and goals, and a few original lines from that beat taken
   from your local catalog as tone references. It streams the model's reply and hands over each
   line the moment it's complete.
2. **Voice** (`voice.py`), for each line in order:
   - If an original recording is close enough in meaning (`RECORDING_MATCH`, cosine similarity
     with bge-small embeddings), play it.
   - Otherwise synthesize it with Chatterbox, conditioned on a few original recordings from your
     install. The acting direction sets the expressiveness.
   - If synthesis is switched off for that character (`VOICE_SYNTHESIS` in `config.py`),
     show a subtitle instead.
3. The first line starts playing while the model is still writing the rest.

Nothing is written to disk except the console output. Each run is a fresh evening.

## Setup (Windows, NVIDIA GPU)

```bash
python tools/extract_catalog.py                       # builds data/catalog.json from your install
python -m venv %USERPROFILE%\.venvs\facade-remastered
%USERPROFILE%\.venvs\facade-remastered\Scripts\pip install chatterbox-tts sentence-transformers requests --extra-index-url https://download.pytorch.org/whl/cu124
```

Download a GGUF chat model (the default is Qwen3-8B Q4_K_M) and set `FACADE_LLM_MODEL`
if it isn't at `G:\AI\models\Qwen3-8B-Q4_K_M.gguf`. Then, in two terminals:

```bash
python prototype/start_llm.py
%USERPROFILE%\.venvs\facade-remastered\Scripts\python prototype/play.py --name Joe
```

`--bench` runs a fixed set of guest inputs and prints a latency summary.

## Results

2026-10-27 · RTX 4070 Ti (12 GB) · Qwen3-8B Q4_K_M on llama.cpp (thinking off) · Chatterbox Turbo ·
beat FASKDRINKT1 · six scripted guest turns · `play.py --bench --mute`

| Measure | Result |
|---|---|
| LLM first token | 0.02–0.21 s |
| First complete sentence from the LLM | 0.26–0.36 s |
| **First audio** (median / worst) | **1.48 s / 1.63 s** |
| First audio when an original recording matched | ~0.45 s |
| Synthesis speed (render time ÷ audio length) | 0.35 (about 3× faster than real time) |
| VRAM | ~4.7 GB LLM + ~3.8 GB voice |
| Startup (voices conditioned, warmed up) | ~20 s |

How it got there:

| Change | First audio, median / worst |
|---|---|
| Chatterbox standard, whole lines | not viable: synthesis ran at 1.5× real time |
| Chatterbox Turbo, whole lines | 1.95 s / 4.82 s |
| + sentence-sized chunks, warm-up render | 1.64 s / 1.86 s |
| + sentences handed over while the LLM is still streaming | 1.48 s / 1.63 s |

What's left in the 1.5 s is almost all voice rendering of the first sentence (~1.1 s).
Next levers: a shorter first chunk (split at the first comma), streaming the voice model's
output in chunks instead of whole sentences, and `torch.compile`.

Quality notes from the transcripts:

- In character and on-beat: Trip's showmanship and wounded pride, Grace's polite undercutting,
  both trying to recruit the guest. Callbacks to the original's backstory appear unprompted.
- The guest's pronouns are respected once stated in the prompt (default they/them).
- Repetition across turns was a problem until the prompt forbade reusing lines and presence and
  frequency penalties were added.
- The model sometimes declares the beat over too early. The beat sequencer, not the model, should
  decide that.
- Original recordings are reused only for short, common lines at the 0.92 threshold.
  Most lines are new, so most are synthesized.
- Turbo has no expressiveness control, so delivery directions currently don't change the voice.
