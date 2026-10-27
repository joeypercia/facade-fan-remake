"""Time each stage of one Chatterbox Turbo render: speech tokens, vocoder, watermark."""
import sys
import time
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).parent))
from catalog import Catalog  # noqa: E402
from voice import Voices  # noqa: E402
from chatterbox.models.s3gen.const import S3GEN_SIL  # noqa: E402
from chatterbox.models.tokenizers import tokenizer as _tok  # noqa: E402,F401
from chatterbox.tts_turbo import punc_norm  # noqa: E402

SENTENCES = ["Oh, hey!", "Thanks, Joe! Come on in.", "Just a simple glass of Chardonnay, thank you.",
             "Well, I like to think I'm setting the tone for the evening."]


def sync():
    torch.cuda.synchronize()
    return time.perf_counter()


def main():
    v = Voices(Catalog())
    v.load()
    m = v.model
    m.conds = v.conds["trip"]
    for text in SENTENCES * 2:
        t0 = sync()
        toks = m.tokenizer(punc_norm(text), return_tensors="pt").input_ids.to(m.device)
        speech = m.t3.inference_turbo(t3_cond=m.conds.t3, text_tokens=toks, temperature=0.8, top_k=1000,
                                      top_p=0.95, repetition_penalty=1.2)
        t1 = sync()
        speech = speech[speech < 6561].to(m.device)
        speech = torch.cat([speech, torch.tensor([S3GEN_SIL] * 3, device=m.device)])
        wav, _ = m.s3gen.inference(speech_tokens=speech, ref_dict=m.conds.gen, n_cfm_timesteps=2)
        t2 = sync()
        x = wav.squeeze(0).cpu().numpy()
        m.watermarker.apply_watermark(x, sample_rate=m.sr)
        t3 = time.perf_counter()
        print(f"{len(speech):4d} tokens {len(x) / m.sr:4.2f}s audio | tokens {t1 - t0:.3f}s "
              f"({len(speech) / (t1 - t0):.0f}/s) | vocoder {t2 - t1:.3f}s | watermark {t3 - t2:.3f}s | {text[:40]}")


if __name__ == "__main__":
    main()
