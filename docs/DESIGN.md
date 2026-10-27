# Design

## Principles

1. **The original is the source of truth.** Beat structure, backstory, characters' goals and the
   tone come from Façade's own data, read from the player's install. The model improvises
   *within* a beat. It doesn't invent a different story.
2. **Real performances first.** If an original recording fits what a character means to say, play
   it. Synthesize a new line only when nothing recorded fits.
3. **One evening, then gone.** All state lives in memory for one session. Nothing is written that a
   later session reads back (the stageplay transcript is an export for the player, never an input).
4. **Local and private.** The model, voice synthesis and assets all stay on the player's machine.
5. **Ship code, not content.** See [LEGAL.md](LEGAL.md).

## Architecture

```
┌─────────────── game client (Electron + three.js, TypeScript) ───────────────┐
│ renderer: apartment, Grace & Trip rigs, drinks/props, HD fullscreen         │
│ input: free text, *actions* in asterisks, mouse pick-up/use, movement       │
│ audio: original recordings, synthesized lines, music, sfx                   │
│ performance player: drives body/gesture/gaze/mouth from a line + direction  │
└──────────────▲────────────────────────────────────────────┬─────────────────┘
               │ performances (who says what, how, what they do)  │ player events
┌──────────────┴────────────────────────────────────────────▼─────────────────┐
│ drama manager (TypeScript)                                                  │
│  story state: tension arc, affinity, revealed topics, current beat          │
│  beat sequencer: original beat graph + preconditions, picks the next beat   │
│  evening memory: what the player said/did tonight (session only)            │
│  character minds: Grace and Trip, each prompted with the beat's goals       │
└──────────────▲──────────────────────────────────┬───────────────────────────┘
               │                                  │
     local LLM (OpenAI-compatible        line selector: embedding search over
     endpoint: Ollama / llama.cpp /      the player's local catalog, picks the
     LM Studio)                          closest original recording
                                                  │ no good match
                                                  ▼
                                   voice service (Python, local): synthesis
                                   from reference clips in the player's install
```

### Each turn

1. The player types or acts. The input goes to the drama manager, with no keyword parser in between.
2. The current beat's context (goals, what each character is hiding, what must happen before the
   beat ends) and tonight's memory go to the model. It returns a structured performance plan:
   speaker, intent, wording, delivery direction, gesture, movement or object action, and any
   story-state changes.
3. The line selector looks for an original recording that matches the planned wording and intent
   (an embedding similarity threshold, checked against the beat and direction). On a match, that
   recording plays and its original lip-sync track is used.
4. Otherwise the wording is synthesized in that character's voice. The mouth is driven from
   synthesized phonemes, and the performance player picks a gesture to fit the direction.
5. The beat sequencer checks whether the beat's goal is met and, using the original beat graph,
   moves the story toward one of its endings.

### Why the model is structured, not free-form

Façade works because of its dramatic arc. A free-running chatbot drifts. The model only ever fills
in *one beat at a time*, through a JSON schema, and the sequencer keeps the story on track. That's
what keeps it "the original story" while still answering what you actually said.

## Model

- Target: a ~12–14B instruction model, 4-bit quantized, on 12 GB VRAM, served by any
  OpenAI-compatible local server. Configurable in settings.
- Latency budget: first audio within ~1.5 s of the player pressing Enter. Recorded lines play
  immediately; synthesized lines stream.
- Smaller cards: a 7–8B model at reduced quality.

## Roadmap

| Phase | Deliverable | Done when |
|---|---|---|
| 0 | Groundwork | Line catalog, format notes, asset guard, repo |
| 1 | Asset decoding | Loads the `.aindex`/`.abinary`/`.sbinary` rigs and clips from an install; meshes/apartment recovered from the engine or rebuilt to match |
| 2 | Renderer | The apartment and both characters render in HD widescreen; clips play; movement and camera feel like the original |
| 3 | Performance player | Any catalog line plays with its original body, gesture and mouth tracks, in sync |
| 4 | Drama manager | Original beat graph extracted; the LLM drives beats; a full evening plays through to an ending using recordings only |
| 5 | Voice | Local synthesis for new lines, consent rules enforced in code; the mouth follows synthesized speech |
| 6 | Polish | Name and pronoun choice, `*actions*`, skippable intro, fast start, stageplay export, settings |

## Open questions

- How the engine draws characters from their joints (no mesh files exist; see ASSETS.md).
  Recover the drawing rules, or build a new character renderer driven by the same skeleton.
- Whether a local LLM and local voice synthesis together can hit the latency budget on 12 GB.
  This is worth prototyping early, in parallel with the renderer.
- How much of the beat graph can be recovered automatically from the compiled ABL classes,
  versus hand-annotated from play.
- Which local TTS gives the best quality per VRAM while sharing the GPU with the LLM.
  It may need to run on CPU or swap in and out.
