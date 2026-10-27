"""Grace's and Trip's voices: original recordings when one fits, local synthesis otherwise.

Synthesis is conditioned on a few original recordings read from the player's own install.
The conditioning lives in memory only. The temporary reference file is deleted right after use,
and nothing derived from the voices is saved. See docs/LEGAL.md.
"""
import os
import queue
import re
import tempfile
import threading
import time

import numpy as np
import sounddevice as sd
import soundfile as sf

from config import FACADE, RECORDING_MATCH, TTS_ENGINE, VOICE_SYNTHESIS

# Map loose acting directions to Chatterbox's expressiveness controls.
INTENSE = ("angry", "furious", "yell", "shout", "excited", "exasperated", "pointed", "sharp", "frustrated")
FLAT = ("dry", "flat", "cold", "quiet", "muted", "tired", "deadpan", "awkward")


def style_for(delivery):
    d = delivery.lower()
    if any(w in d for w in INTENSE):
        return 0.8, 0.35  # exaggeration, cfg_weight
    if any(w in d for w in FLAT):
        return 0.3, 0.5
    return 0.5, 0.5


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


def speakable(text):
    """Drop *stage business* and tidy spacing; return None if nothing is left to say."""
    t = re.sub(r"\*[^*]*\*", " ", text)
    t = re.sub(r"\s+", " ", t).strip()
    return t or None


class Voices:
    def __init__(self, catalog):
        self.catalog = catalog
        self.model = None
        self.conds = {}
        self.out = queue.Queue()
        self.player = threading.Thread(target=self._play_loop, daemon=True)
        self.player.start()

    def load(self):
        quiet_engine()
        if TTS_ENGINE == "turbo":
            from chatterbox.tts_turbo import ChatterboxTurboTTS as Engine
        else:
            from chatterbox.tts import ChatterboxTTS as Engine
        self.model = Engine.from_pretrained(device="cuda")
        for ch, allowed in VOICE_SYNTHESIS.items():
            if allowed:
                self.conds[ch] = self._condition(ch)
        # One throwaway render so CUDA kernels are compiled before the first real line.
        if self.conds:
            self.model.conds = next(iter(self.conds.values()))
            self._generate("Warming up.", "")

    def _generate(self, text, delivery):
        if TTS_ENGINE == "turbo":
            return self.model.generate(text)
        ex, cfg = style_for(delivery)
        return self.model.generate(text, exaggeration=ex, cfg_weight=cfg)

    def _condition(self, ch):
        clips = self.catalog.reference_clips(ch)
        audio = []
        for p in clips:
            x, sr = sf.read(p, dtype="float32", always_2d=False)
            audio += [x, np.zeros(int(sr * 0.25), dtype="float32")]
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            sf.write(path, np.concatenate(audio), sr)
            self.model.prepare_conditionals(path, exaggeration=0.5)
            return self.model.conds
        finally:
            os.remove(path)

    def say(self, line):
        """Render one line and queue it for playback. Returns a dict of what happened and timings."""
        t0 = time.perf_counter()
        text = speakable(line.text)
        info = {"speaker": line.speaker, "text": line.text, "source": None, "render_s": 0.0}
        if text is None:
            info["source"] = "action"
            return info
        sim, rec = self.catalog.nearest(line.speaker, text)
        info["match"] = round(sim, 3)
        if sim >= RECORDING_MATCH:
            x, sr = sf.read(FACADE / rec["audio"], dtype="float32")
            info.update(source="recording", recording=rec["id"], audio_s=len(x) / sr)
        elif line.speaker in self.conds:
            self.model.conds = self.conds[line.speaker]
            wav = self._generate(text, line.delivery)
            x, sr = wav.squeeze(0).cpu().numpy(), self.model.sr
            info.update(source="synth", audio_s=len(x) / sr)
        else:
            info["source"] = "subtitle"
            return info
        info["render_s"] = time.perf_counter() - t0
        self.out.put((x, sr))
        return info

    def _play_loop(self):
        while True:
            x, sr = self.out.get()
            sd.play(x, sr)
            sd.wait()
            self.out.task_done()

    def wait(self):
        self.out.join()
