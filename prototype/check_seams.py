"""Does chunked (streaming) vocoding change the audio? Same speech tokens, vocoded whole vs in chunks.

Reports the length difference and the spectral difference near chunk seams vs elsewhere.
"""
import sys
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).parent))
import streaming as st  # noqa: E402
from catalog import Catalog  # noqa: E402
from chatterbox.models.s3gen.const import S3GEN_SIL  # noqa: E402
from chatterbox.tts_turbo import punc_norm  # noqa: E402
from voice import Voices  # noqa: E402


def logmel(x):
    import librosa
    return np.log(librosa.feature.melspectrogram(y=x, sr=24000, n_fft=1024, hop_length=240, n_mels=80) + 1e-5)


def main():
    v = Voices(Catalog(), mute=True)
    v.load()
    m = v.model
    m.conds = v.conds[("trip", "neutral")]
    for text in ["Well, if you ask me, the best drink in the house is the one I make myself.",
                 "Grace, honey, come on. It's our anniversary, let's not do this tonight, okay?"]:
        toks = m.tokenizer(punc_norm(text), return_tensors="pt").input_ids.to(m.device)
        tokens = [t for t in st.speech_tokens(m.t3, m.conds.t3, toks) if t < st.SPEECH_VOCAB]
        with torch.inference_mode():
            whole, _ = m.s3gen.inference(speech_tokens=torch.tensor(tokens + [S3GEN_SIL] * 3, device=m.device),
                                         ref_dict=m.conds.gen, n_cfm_timesteps=2)
        whole = whole.squeeze(0).cpu().numpy()

        c = st._Chunker(m)
        c.tokens = tokens
        m.watermarker.apply_watermark = lambda x, sample_rate: x  # compare raw audio
        chunks, seams = [], []
        target = st.FIRST_CHUNK
        while target + st.LOOKAHEAD <= len(tokens):
            chunks.append(c.vocode(target, final=False))
            seams.append(sum(map(len, chunks)))
            target = c.done + st.CHUNK
        chunks.append(c.vocode(len(tokens), final=True))
        streamed = np.concatenate(chunks)

        n = min(len(whole), len(streamed))
        A, B = logmel(whole[:n]), logmel(streamed[:n])
        diff = np.abs(A - B).mean(axis=0)            # per 10 ms frame
        near = np.zeros_like(diff, dtype=bool)
        for s in seams:
            f = s // 240
            near[max(0, f - 5):f + 5] = True
        print(f"{len(tokens)} tokens | whole {len(whole) / 24000:.2f}s vs streamed {len(streamed) / 24000:.2f}s "
              f"| spectral diff near seams {diff[near].mean():.2f}, elsewhere {diff[~near].mean():.2f} "
              f"| {len(seams)} seams")


if __name__ == "__main__":
    main()
