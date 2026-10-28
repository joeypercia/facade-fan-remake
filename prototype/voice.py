"""Grace's and Trip's voices: original recordings when one fits, local synthesis otherwise.

Synthesis (Chatterbox Turbo) is expressive in two ways:
- each character has one conditioning per emotion, built from original takes the script marks
  with that emotion (angry takes for angry lines, warm takes for warm lines...), so the actors'
  own performances carry the tone;
- the line's delivery adds one of Turbo's style tags ([sarcastic], [angry]...), and the writer
  may put nonverbal tags ([sigh], [laugh]...) in the text.

Audio streams: the first ~0.5 s of a line plays while the rest is still being generated.

Conditioning lives in memory only. Temporary reference files are deleted right after use, and
nothing derived from the voices is saved. Every synthesized chunk carries Chatterbox's Perth
watermark. See docs/LEGAL.md.
"""
import collections
import os
import re
import tempfile
import threading
import time

import numpy as np
import soundfile as sf

from config import FACADE, RECORDING_MATCH, VOICE_SYNTHESIS

SR = 24000  # Chatterbox's output rate; recordings (22.05 kHz) are resampled to it

# Emotion buckets: original directions that feed each one, and delivery words that select it.
EMOTIONS = {
    "warm": ({"WARM", "SMILING", "SMILE", "PLEASED", "SWEET", "CHEERY", "UPBEAT", "EXCITED", "HAPPY",
              "JOKING", "GREET"},
             ("warm", "cheer", "happy", "excited", "enthusias", "friendly", "joking", "playful", "bright",
              "proud", "eager", "flirt", "delighted", "smiling")),
    "annoyed": ({"ANNOYED", "FRUSTRATED", "SARCASTIC", "COLD", "IMPATIENT", "RILED", "TENSE", "DEFENSIVE"},
                ("annoyed", "irritat", "frustrat", "sarcas", "dry", "pointed", "cold", "tense", "defensive",
                 "impatient", "curt", "clipped", "sharp", "mocking", "tight", "icy", "snippy")),
    "angry": ({"ANGRY", "FURIOUS", "ACCUSATION", "ADMONISH"},
              ("angry", "furious", "yell", "shout", "rage", "accus", "explod", "livid", "seething")),
    "sad": ({"UPSET", "SAD", "DOWN", "DOWNBEAT", "DEFEATED", "REJECTED", "DESPERATE", "DISMAYED"},
            ("sad", "hurt", "upset", "defeated", "wounded", "tearful", "crying", "desperate", "quiet",
             "resigned", "dejected", "broken", "pleading")),
    "anxious": ({"CONCERNED", "ANXIOUS", "NERVOUS", "UNSURE"},
                ("anxious", "nervous", "concerned", "worried", "unsure", "awkward", "uneasy", "hesitant",
                 "uncomfortable", "flustered")),
    "neutral": ({"NEUTRAL"}, ()),
}
# Takes that make poor references: cut off, muffled, or not the character's normal voice.
BAD_REFERENCE = {"INTERRUPTED", "INTERRUPTING", "OFFSCREEN", "MUTED", "UNDERBREATH", "UNDER", "WALKING"}

# Turbo style tags by delivery word. Nonverbal tags the writer may use inline.
STYLE_TAGS = (
    (("sarcas", "dry", "mocking", "pointed", "snide", "wry"), "[sarcastic]"),
    (("angry", "furious", "yell", "shout", "livid", "seething"), "[angry]"),
    (("happy", "cheer", "delighted", "excited", "enthusias"), "[happy]"),
    (("surprised", "shocked", "startled", "astonish"), "[surprised]"),
    (("scared", "afraid", "fear", "panic"), "[fear]"),
    (("crying", "tearful", "sobbing"), "[crying]"),
    (("whisper", "hushed", "under breath", "under her breath", "under his breath"), "[whispering]"),
)
NONVERBAL = ("[sigh]", "[laugh]", "[chuckle]", "[gasp]", "[groan]", "[sniff]", "[clear throat]")


def emotion_for(delivery):
    d = delivery.lower()
    for name, (_, words) in EMOTIONS.items():
        if any(w in d for w in words):
            return name
    return "neutral"


def style_tag(delivery):
    d = delivery.lower()
    return next((tag for words, tag in STYLE_TAGS if any(w in d for w in words)), "")


NEGATIONS = {"no", "not", "never", "don't", "dont", "can't", "cant", "won't", "isn't", "wasn't", "didn't",
             "doesn't", "nothing", "nobody", "none", "aren't", "shouldn't", "wouldn't", "couldn't"}


def same_words(a, b, threshold=0.9):
    """True if two lines say essentially the same words: near-identical wording and the same
    negations. (Embedding similarity alone treats "I love you" and "I don't love you" as a match.)"""
    import difflib
    wa = re.findall(r"[a-z']+", a.lower())
    wb = re.findall(r"[a-z']+", b.lower())
    if set(wa) & NEGATIONS != set(wb) & NEGATIONS:
        return False
    return difflib.SequenceMatcher(None, wa, wb).ratio() >= threshold


def quiet_engine():
    """Silence Chatterbox's per-call print() and tqdm bars inside its own modules only.
    (Redirecting sys.stdout would also swallow the game's output from other threads.)"""
    import importlib
    from functools import partial
    for name in ("models.s3gen.flow_matching", "models.s3gen.s3gen", "models.t3.t3",
                 "models.s3gen.hifigan", "tts", "tts_turbo"):
        mod = importlib.import_module(f"chatterbox.{name}")
        mod.print = lambda *a, **k: None
        if hasattr(mod, "tqdm"):
            mod.tqdm = partial(mod.tqdm, disable=True)


def load_fp16(engine_cls):
    """Load Chatterbox Turbo in half precision: its two big parts are converted on the CPU before
    they reach the GPU (loading straight to the GPU in fp32 would set the memory peak). About
    1.4 GB of weights instead of 2.7. Generation runs under autocast so fp32 inputs meet fp16
    weights; the small voice encoder stays fp32."""
    import functools
    import torch
    m = engine_cls.from_pretrained(device="cpu")
    m.t3 = m.t3.half().to("cuda").eval()
    m.s3gen = m.s3gen.half().to("cuda").eval()
    m.ve = m.ve.to("cuda").eval()
    # Inference only. With gradients on, every stored voice conditioning kept its whole autograd
    # history: ~136 MB each, 1.6 GB for the 12 voices.
    for part in (m.t3, m.s3gen, m.ve):
        part.requires_grad_(False)
    m.device = "cuda"
    if getattr(m, "conds", None) is not None:
        m.conds = m.conds.to("cuda")
    torch.cuda.empty_cache()

    def amp(fn):
        @functools.wraps(fn)
        def run(*a, **k):
            with torch.inference_mode(), torch.autocast("cuda", dtype=torch.float16):
                out = fn(*a, **k)
            return out.float() if torch.is_tensor(out) else out
        return run

    m.generate = amp(m.generate)
    m.prepare_conditionals = amp(m.prepare_conditionals)
    return m


def speakable(text):
    """Drop *stage business* and any bracket tag Turbo doesn't know; None if nothing is left to say."""
    t = re.sub(r"\*[^*]*\*", " ", text)
    t = re.sub(r"\[[^\]]*\]", lambda m: m.group(0) if m.group(0).lower() in NONVERBAL else " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t if re.search(r"[A-Za-z]", re.sub(r"\[[^\]]*\]", "", t)) else None


class Player:
    """One continuous output stream fed from a queue, so streamed chunks play back to back."""

    def __init__(self, mute=False):
        self.chunks = collections.deque()
        self.lock = threading.Lock()
        self.pending = 0
        self.mute = mute
        if not mute:
            import sounddevice as sd
            self.stream = sd.OutputStream(samplerate=SR, channels=1, dtype="float32",
                                          blocksize=480, callback=self._fill)
            self.stream.start()

    def push(self, x):
        if self.mute:
            return
        with self.lock:
            self.chunks.append(np.asarray(x, dtype="float32"))
            self.pending += len(x)

    def _fill(self, out, frames, _time, _status):
        out.fill(0)
        filled = 0
        with self.lock:
            while filled < frames and self.chunks:
                c = self.chunks[0]
                n = min(frames - filled, len(c))
                out[filled:filled + n, 0] = c[:n]
                filled += n
                if n == len(c):
                    self.chunks.popleft()
                else:
                    self.chunks[0] = c[n:]
            self.pending -= filled

    def wait(self):
        while self.pending > 0:
            time.sleep(0.02)


class Voices:
    def __init__(self, catalog, mute=False):
        self.catalog = catalog
        self.model = None
        self.conds = {}  # (character, emotion) -> conditionals
        self.player = Player(mute)

    def load(self):
        quiet_engine()
        from chatterbox.tts_turbo import ChatterboxTurboTTS
        self.model = load_fp16(ChatterboxTurboTTS)
        for ch, allowed in VOICE_SYNTHESIS.items():
            if not allowed:
                continue
            for emotion, (directions, _) in EMOTIONS.items():
                clips = self.catalog.reference_clips(ch, directions=directions, avoid=BAD_REFERENCE)
                if clips:
                    self.conds[(ch, emotion)] = self._condition(clips)
        # One throwaway render so CUDA kernels are compiled before the first real line.
        if self.conds:
            from streaming import stream
            self.model.conds = next(iter(self.conds.values()))
            for _ in stream(self.model, "Warming up the voices now."):
                pass

    def warm_thread(self):
        """Run the GPU paths once on the calling thread. PyTorch sets up CUDA handles per thread,
        so the thread that voices lines should call this before the first real one (~1 s saved)."""
        from streaming import stream
        self.catalog.nearest("trip", "warm up")
        if self.conds:
            self.model.conds = next(iter(self.conds.values()))
            for _ in stream(self.model, "Warming up."):
                pass

    def lab_render(self, ch, text, delivery, variant):
        """Render one line a particular way for side-by-side listening. Returns float32 audio at SR.
        Variants: current, no_tag, single_take, whole, recording."""
        from streaming import stream
        import soxr
        emotion = emotion_for(delivery)
        if variant == "recording":
            _, rec = self.catalog.nearest(ch, text)
            x, sr = sf.read(FACADE / rec["audio"], dtype="float32")
            return soxr.resample(x, sr, SR), rec["text"]
        cond = self.conds.get((ch, emotion)) or self.conds[(ch, "neutral")]
        if variant == "single_take":
            key = (ch, emotion, "single")
            if key not in self.conds:
                directions = EMOTIONS[emotion][0]
                clips = self.catalog.reference_clips(ch, directions=directions, avoid=BAD_REFERENCE,
                                                     seconds=6.0, min_len=6.0, max_len=12.0, count=1)
                self.conds[key] = self._condition(clips[:1]) if clips else cond
            cond = self.conds[key]
        self.model.conds = cond
        tagged = text if variant in ("no_tag", "single_take") else f"{style_tag(delivery)} {text}".strip()
        if variant == "whole":
            return self.model.generate(tagged).squeeze(0).cpu().numpy(), tagged
        return np.concatenate(list(stream(self.model, tagged))), tagged

    def _condition(self, clips):
        import soxr
        audio = []
        for p in clips:
            x, sr = sf.read(p, dtype="float32", always_2d=False)
            audio += [soxr.resample(x, sr, SR), np.zeros(SR // 4, dtype="float32")]
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            sf.write(path, np.concatenate(audio), SR)
            self.model.prepare_conditionals(path)
            return self.model.conds
        finally:
            os.remove(path)

    def say(self, line, on_text=None):
        """Render one line and stream it to the player. Returns what happened, with timings.
        info["t_first_audio"] is the perf_counter time the first audio reached the player.
        If an original recording is used, line.text becomes its wording and on_text(text) is
        called before its audio, so subtitles can match."""
        from streaming import stream
        import soxr
        t0 = time.perf_counter()
        text = speakable(line.text)
        info = {"speaker": line.speaker, "text": line.text, "source": None, "render_s": 0.0}
        if text is None:
            info["source"] = "action"
            return info
        plain = re.sub(r"\[[^\]]*\]", "", text).strip()
        sim, rec = self.catalog.nearest(line.speaker, plain)
        info["match"] = round(sim, 3)
        emotion = emotion_for(line.delivery)
        cond = self.conds.get((line.speaker, emotion)) or self.conds.get((line.speaker, "neutral"))
        if sim >= RECORDING_MATCH and same_words(plain, rec["text"]):
            x, sr = sf.read(FACADE / rec["audio"], dtype="float32")
            x = soxr.resample(x, sr, SR)
            # Say what the recording says: the subtitle and tonight's history follow the audio.
            line.text = rec["text"]
            if on_text:
                on_text(rec["text"])
            info["t_first_audio"] = time.perf_counter()
            self.player.push(x)
            info.update(source="recording", recording=rec["id"], audio_s=len(x) / SR)
        elif cond is not None:
            self.model.conds = cond
            tagged = f"{style_tag(line.delivery)} {text}".strip()
            total = 0
            for chunk in stream(self.model, tagged):
                if "t_first_audio" not in info:
                    info["t_first_audio"] = time.perf_counter()
                self.player.push(chunk)
                total += len(chunk)
            info.update(source="synth", emotion=emotion, tagged=tagged, audio_s=total / SR)
        else:
            info["source"] = "subtitle"
            return info
        info["render_s"] = time.perf_counter() - t0
        return info

    def wait(self):
        self.player.wait()
