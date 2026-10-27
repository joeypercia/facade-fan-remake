"""Streaming synthesis for Chatterbox Turbo.

Turbo normally generates every speech token for a sentence, then vocodes them all at once.
Token generation runs ~3.5x faster than real time, so the first audio can be vocoded long before
the sentence is finished. This module yields audio in chunks as tokens arrive:

- each chunk is vocoded with a few tokens of left context (so it starts mid-flow, not cold) and a
  few tokens of lookahead (so its end isn't treated as the end of the utterance);
- only the chunk's own span is kept, and neighbouring chunks are crossfaded over 20 ms;
- every chunk is watermarked, like the non-streaming path.
"""
import torch
import torch.nn.functional as F
from transformers.generation.logits_process import (LogitsProcessorList, RepetitionPenaltyLogitsProcessor,
                                                    TemperatureLogitsWarper, TopKLogitsWarper, TopPLogitsWarper)

from chatterbox.models.s3gen.const import S3GEN_SIL, S3GEN_SR
from chatterbox.tts_turbo import punc_norm

SPEECH_VOCAB = 6561                    # token ids at or above this are control tokens
SAMPLES_PER_TOKEN = S3GEN_SR // 25     # 25 speech tokens per second of audio
FIRST_CHUNK = 12                       # tokens in the first chunk (~0.5 s of audio)
CHUNK = 30                             # tokens in later chunks (~1.2 s)
CONTEXT = 10                           # left-context tokens re-vocoded and discarded
LOOKAHEAD = 4                          # right-context tokens vocoded and discarded
FADE = S3GEN_SR // 50                  # 20 ms crossfade between chunks


@torch.inference_mode()
def speech_tokens(t3, t3_cond, text_tokens, temperature=0.8, top_k=1000, top_p=0.95,
                  repetition_penalty=1.2, max_len=1000):
    """Same sampling loop as T3.inference_turbo, but yields each speech token as it's sampled."""
    procs = LogitsProcessorList([TemperatureLogitsWarper(temperature), TopKLogitsWarper(top_k),
                                 TopPLogitsWarper(top_p), RepetitionPenaltyLogitsProcessor(repetition_penalty)])
    start = t3.hp.start_speech_token * torch.ones_like(text_tokens[:, :1])
    embeds, _ = t3.prepare_input_embeds(t3_cond=t3_cond, text_tokens=text_tokens, speech_tokens=start, cfg_weight=0.0)
    out = t3.tfmr(inputs_embeds=embeds, use_cache=True)
    past, logits = out.past_key_values, t3.speech_head(out[0][:, -1:])
    history = start
    for _ in range(max_len):
        probs = F.softmax(procs(history, logits[:, -1, :]), dim=-1)
        tok = torch.multinomial(probs, num_samples=1)
        if int(tok) == t3.hp.stop_speech_token:
            return
        history = torch.cat([history, tok], dim=1)
        yield int(tok)
        out = t3.tfmr(inputs_embeds=t3.speech_emb(tok), past_key_values=past, use_cache=True)
        past, logits = out.past_key_values, t3.speech_head(out[0])


class _Chunker:
    def __init__(self, model):
        self.model = model
        self.tokens = []
        self.done = 0      # tokens whose audio has been emitted
        self.tail = None   # last FADE samples of the previous chunk, held back for the crossfade

    def vocode(self, end, final):
        m, a = self.model, self.done
        start = max(0, a - CONTEXT)
        span = self.tokens[start:end + (0 if final else LOOKAHEAD)]
        if final:
            span = span + [S3GEN_SIL] * 3
        wav, _ = m.s3gen.inference(speech_tokens=torch.tensor(span, device=m.device), ref_dict=m.conds.gen,
                                   n_cfm_timesteps=2)
        wav = wav.squeeze(0)
        lead = FADE if self.tail is not None else 0
        lo = (a - start) * SAMPLES_PER_TOKEN - lead
        hi = len(wav) if final else (end - start) * SAMPLES_PER_TOKEN
        seg = wav[lo:hi]
        if self.tail is not None:
            w = torch.linspace(0, 1, FADE, device=seg.device)
            seg = torch.cat([self.tail * (1 - w) + seg[:FADE] * w, seg[FADE:]])
        if not final:
            seg, self.tail = seg[:-FADE], seg[-FADE:]
        self.done = end
        x = seg.cpu().numpy()
        return m.watermarker.apply_watermark(x, sample_rate=m.sr)


def stream(model, text):
    """Yield float32 numpy chunks of speech at model.sr for `text`, using model.conds."""
    toks = model.tokenizer(punc_norm(text), return_tensors="pt").input_ids.to(model.device)
    c = _Chunker(model)
    for tok in speech_tokens(model.t3, model.conds.t3, toks):
        if tok >= SPEECH_VOCAB:
            continue
        c.tokens.append(tok)
        target = FIRST_CHUNK if c.done == 0 else c.done + CHUNK
        if len(c.tokens) >= target + LOOKAHEAD:
            yield c.vocode(target, final=False)
    if c.tokens[c.done:] or c.tail is not None:
        yield c.vocode(len(c.tokens), final=True)
