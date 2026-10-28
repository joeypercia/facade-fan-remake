# Integrating with the original engine

Direction (2026-10-27): keep Façade's original engine, rendering and art style untouched. Add the
LLM on top of the original drama manager, and fix the engine's bugs on modern Windows.

## Division of labour

| Layer | Controlled by | Decides |
|---|---|---|
| Story | the original drama manager (Java, ABL) | beat sequencing, tension, beat goals, who has the floor, when to move on, the ending |
| Performance | the LLM, every exchange | the words; expression and mood; gestures and when they land; gaze; small moves (to the bar, picking up the photo) |
| Body | the original engine and its low-level behaviours | rendering, blinking, breathing, idle shifts, walking paths, held objects; lip sync from the generated speech |

The drama manager's choice of a line becomes a *brief* (beat, speaker, intent, direction, the
original wording as an example). The LLM writes new words for that brief in the context of what the
player actually said tonight, and directs the performance using the engine's own vocabulary. Every
movement is therefore still original Façade animation; only the choice and timing come from the LLM.

## What the engine exposes (confirmed)

Java and the engine are separate processes. The Java process loads `nativeaniminterface.dll`
(the JNI bridge), which links `animEngineDLL.dll` and calls 48 `AnimAPI_*` functions that write into
shared memory (`CreateFileMappingA` + mutexes); the engine process reads it.

`facade.nativeinterface.NativeAnimationInterface` declares the whole vocabulary:

| Area | Calls |
|---|---|
| Scripts (dialogue lines, poses) | `pushAnimationUScript(char, script, layer)`, `pushMultipleAnimationScripts(...)`, `pushPoseScript`, `abortAnimationScripts` |
| Gestures | `doGestureAnimation(char, gesture, ...)`, `doPickupObjGestureAnimation`, `doPutdownObjGestureAnimation`, `miscLittleAction` |
| Face | `doFullExpressionBaseAnimation`, `doFullExpressionMoodAnimation`, `setBrowShapeL/R`, `setEyelidHeightL/R`, `setMouthShape` |
| Gaze | `setGazeTracking(char, target, x, y, z, ...)` |
| Movement | `walkStep`, `reactiveWalkStep`, `turnInPlace`, `setObjectToHold`, `setObjectPosition/Rotation` |
| Senses | `textSensor()` (what the player typed; the engine owns the text box), `getPlayerGesture`, `getMyCopyOfAllAnimCues` (timed cues inside a playing script, used to sync gestures to speech), object positions and states |
| Other | `music`, `setPerformanceInfo`, `getTickCount` |

Each call is wrapped by exactly one class in `facade.primact` (`PushMultipleAnimationScripts`,
`DoGestureAnimation`, `SetGazeTracking`...), and the compiled character behaviours only go through
those. `PushMultipleAnimationScripts.execute` is the single path for every script a character
performs, dialogue included: it receives (character, script index(es), layer).

Voice files are read through one call site of `mmioOpenA` in `animEngineDLL.dll` (`0x100a1d10` in v1.1)
and played with DirectSound. Lip-sync and cue tracks come from `<char>Script.sbinary`, loaded into
the engine process at startup (format in [ASSETS.md](ASSETS.md)).

## Observed in a real session (step 1, bridge log)

A 4-minute session with the observe-only bridge (991 calls) showed:

- **Dialogue is `pushMultipleAnimationScripts(char, s1, s2, s3, s4, layer=7)`.** Layer 7 is speech.
  `pushAnimationUScript` was never called. A spoken line is up to four script indices played
  back to back. This is how the original fits the player's name into a sentence: Trip's
  "Oh, / *Player,* / take a look at this photo…" is `[1473, 306, 1474]`, where 306 is one of the
  per-name recordings (or a generic one for names it doesn't know). Generated lines can simply
  say the player's real name.
- Indices with no catalog entry on layer 7 (171-175, 271, 297…) are short non-dialogue scripts
  (reactions, breaths).
- **`ScriptFinished(char, x)` reports the layer, not the script** (always 7 here). It arrives when
  the audio ends (1-6 s after the push), and the drama manager waits for it before the next line.
  A replacement line therefore has to really play for its own length, and its end is reported by
  the engine as usual.
- **Typed text arrives twice:** the engine calls `PlayerEnteredText(text)` when Enter is pressed,
  and the Java side reads the same text through `textSensor()` a few ms later. `" "` arrives
  when the player starts typing (the characters react to "about to speak"), and `"warm up nlu"`
  is an internal warm-up of the original parser.
- Mouse actions (hug, kiss) reach the drama manager through `getPlayerGesture`, not text.
- `doFullExpressionMoodAnimation` is re-issued every ~0.5 s (583 calls); `setGazeTracking`,
  `walkStep`, `doGestureAnimation` and `miscLittleAction` are called dozens of times a minute by
  the low-level behaviours. The LLM's performance choices must coexist with these.
- `music(0, n)` switches the soundtrack cue as tension rises.

## Plan

1. **Proxy bridge (observe).** A 32-bit `nativeaniminterface.dll` proxy that forwards every JNI
   export to the original and logs `pushAnimationUScript`, `textSensor`, gesture, expression and
   gaze calls. Installed by renaming the original (a mod installer, with uninstall). Confirms the
   intercept points against a real session, without changing behaviour.
2. **Engine hook (observe).** A small DLL loaded into the engine process (via a proxy of one of its
   system DLLs) that logs `mmioOpenA` paths and locates the in-memory script table. Confirms whether
   voice files load on demand and where a script's phoneme and cue records live.
3. **Swap one line.** At a dialogue `pushAnimationUScript`: generate new words, synthesize audio,
   build a phoneme and cue track in the engine's format, put them in a reserved script slot (patched
   in memory, audio served through the `mmioOpenA` hook) and push that slot instead.
4. **LLM performance.** The proxy issues the LLM's chosen expression, gesture, gaze and movement
   calls around each line, timed by the speech, alongside the original low-level behaviours.
5. **LLM understanding.** Replace `textSensor`'s raw text with the LLM's reading of it, in the
   form the original NLU produces (discourse acts), so the drama manager reacts correctly.
6. **Bug fixes** in the engine hook: the menu rendering stall and fullscreen at desktop resolution.
7. **Latency.** The engine plays a line's audio as one file, so the next line is generated while
   the current one plays.

## GPU memory budget (12 GB card)

Measured 2026-10-27 with `engine/service/measure_vram.py`, each piece alone:

| Piece | Now | Cheaper setting | After |
|---|---|---|---|
| LLM, Qwen3-8B Q4_K_M | 6.1 GB (8K context, fp16 KV cache) | 4K context, 8-bit KV cache (`-c 4096 -ctk q8_0 -ctv q8_0`); same output, 0.33 s vs 0.40 s per line | 5.4 GB |
| Voice, Chatterbox Turbo | 3.0 GB (fp32) | fp16 weights (1.4 GB allocated vs 2.7; render 2.1 s vs 1.9 s). Convert on the CPU before moving to the GPU, or the fp32 load sets the peak. | ~1.6 GB |
| Line matcher (bge-small) and per-thread CUDA setup | ~1 GB | embedder on the CPU | ~0.3 GB |
| **Total** | **~10 GB** | | **~7.3 GB** |

Façade and everyday desktop apps (browser, chat, audio tools) take another 1-2 GB, so at ~10 GB the card
was full: Windows spilled GPU memory into system RAM and every LLM call timed out (the "delays, no
answers" session). If the cheaper settings aren't enough, Qwen3-4B (~2.5 GB download) would bring the
total to ~4.5 GB at the cost of weaker writing.

**Applied, measured with the whole stack running** (LLM server plus `check_service.py` and
`check_tone.py` generating and voicing lines): peak **+7.1 GB** over the desktop, with the LLM at 5.0 GB.
`engine/service/measure_voice_steps.py` breaks down the voice side. The first fp16 attempt still peaked
at 9.1 GB: the model had gradients enabled, so each of the 12 stored voice conditionings kept its
autograd history (~1.6 GB in all). `load_fp16` now turns gradients off and runs under
`torch.inference_mode`, and the 12 voices cost almost nothing. Lines took 1.3-2.8 s, and tone matching
was unchanged (offscreen loudness 0.017 vs. the original take's 0.018).

## Unknowns

- How the in-memory script table is laid out, and whether adding a record at runtime is safe.
- Whether the original behaviours fight LLM-issued gestures and gaze, or can be told to yield.
- Which discourse acts the original NLU emits (the Jess rule files in `util/sources/facade/nlu`).
- The modern-Windows render stall in the menus (cursor in a black box, 0% CPU).

## Tools

The JDK's `javap` reads the Java side. Visual Studio 2022 (installed) builds the 32-bit DLLs.
