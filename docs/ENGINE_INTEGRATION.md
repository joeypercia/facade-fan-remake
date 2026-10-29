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

## LLM performance (step 4, built)

With each generated line, the line service also asks the LLM how it's performed
(`engine/service/performance.py`), and the bridge plays that on top of the drama manager's behaviours
(`engine/bridge/perform.cpp`).

- **What the LLM picks:** a facial expression (`doFullExpressionBaseAnimation`, `eFEBase_*`). One
  gesture, landing on the line's most stressed word:
  - arm gestures (`doGestureAnimation(char, part, script)`): `part` is the arm (0 left, 1 right,
    2 both) and `script` is the gesture's `_hold` script from `GraceScript`/`TripScript`;
  - body gestures (nod, sigh): a one-shot `pushMultipleAnimationScripts` on layer 3.

  And where to look (`setGazeTracking` at a `SpriteID`: the guest, the partner, the bar, the
  paintings and so on). Each character has a menu of about 15 gestures, each with a short "when to
  use" hint.
- **Format:** one line, `face | gesture | word | look`, checked against the menus. A JSON schema made
  the model spend ~40 tokens on syntax (0.7-0.9 s); the one-line answer takes 0.2-0.3 s. It runs
  before the voice renders: running both at once on the GPU slowed both.
- **Playing it safely:** calls go out from the drama manager's own threads (inside its frequent calls
  into the bridge), each with a fresh action object (`DoGestureAnimation` and the like).
  `FacadeAsynchronousAction` completion only sets a status on that object, so nothing in the drama
  manager waits on it.
  - A gesture only goes on an arm the drama manager has left at rest (`layer_arm_default`), never
    over a drink, the door or one of its own gestures.
  - At the line's end, whatever is still ours is undone: arms back to rest, and the drama manager's
    last expression and gaze re-issued. Anything it changed meanwhile is left alone.
- **Tested** unattended (`engine/autopilot/perform_test.txt`, hidden desktop, 24 generated lines in
  5 minutes). Trip's "arms spread", "arms crossed" and "point", and Grace's "welcoming hand",
  "small emphasis" and "sigh", played on their words. Busy arms were skipped. Arms returned to rest
  after the line. No errors.
- **Not yet:** walking somewhere or sitting down. These move the character through the drama
  manager's staging (where each character stands for each beat), and its walk behaviours would
  immediately walk them back. They need a hook into the staging, not just the calls above.

## Story lines: as written, with an answer first (revised 2026-10-29)

Rewording every drama-manager line made the dialogue drift into nonsense: "Well come on in..."
became "Well come on in, you're freezing", and fragments of a sentence the drama manager plays
across several lines ("You think I'm...") got invented endings ("...a failure?"). Now:

- A story line plays as written, in the actor's own take, unless the guest has said something no
  line has answered yet (within 25 s), or a scene just ended.
- Then the character first says a short generated reply (at most 12 words: answer the question,
  return the greeting, or dodge in character). The reply is checked against echoing the line,
  repeating anything said tonight, starting with the line's own first word ("Hey, ... Hey!"), and
  meta talk. In testing these lines took 1.9-3.1 s from the push to the audio, against 2-4 s before.
- Only the reply is generated speech (second play test: voicing the whole line took 2-4 s and lost
  the actor's delivery). The actor's take(s) follow it in one audio file. The speech block puts the
  reply's phonemes first, then each take's own phrases and cues moved, not stretched, to where the
  take starts, so the drama manager's gestures still land on the actor's words. No acting call is
  made for these lines: the drama manager performs them as usual. A line whose parts lack a
  recording (the per-name fragments) is still voiced whole.
- Fragments and bare reactions ("Uh...", "get divorced...") always play as written; the guest's
  words then wait for the next whole line.
- **The parser's trait words.** Façade's parser treats some words as the guest's verdict on a host,
  and the drama manager answers "You think I'm... communicative..." (`general.rul` in the install,
  zlib-compressed: rules `ipExplainAdviceParam_*`). "heard", "hear", "listen" and "talk" count as
  *communicative*, "fight" and "argue" as *angry*, "scared" and "hide" as *afraid*, and so on. "I
  heard all this arguing" drew exactly that in the second play test. Rewording around them didn't
  hold up ("I noticed arguing" still reads as *angry*; "let's talk about something else" as advice
  to communicate), hence the act menu below.

## What the guest meant: Façade's own reactions (2026-10-29)

The guest's words used to be reworded by the model into plainer English for the 2005 parser. That
was a second free-form sentence going through the same keyword rules, and it drifted. Now the model
only picks what the guest meant from the reactions the parser knows, and the parser is handed fixed
phrases that are known to read that way (`engine/service/acts.py`).

- **The parser's reactions** are discourse acts: 33 types (Agree, Disagree, Greet, Praise,
  Criticize, ReferTo a thing or topic, Explain "you are ...", Advice "you should ...", ExplBig
  "Trip is cheating"...) with parameters (17 objects, 7 topics, 23 traits and advice, 20 drinks).
  One line usually yields several; the drama manager's reaction decider chooses among them.
- **The menu** (`acts.py`) has an entry per act the guest can mean, each with a phrase: agree →
  "Yes.", about:paintings → "The paintings.", you_are:lying → "You are lying.", drink:red_wine → "I'll
  have a red wine."... The model answers with one or two entries and @grace or @trip, kept only when
  the guest named that host. A near miss of an entry's name is mended ("reveal:trip_cheating").
- **Nothing fits** (small talk, a story, "let's talk about something else"): the drama manager hears
  nothing, and the next story line opens with a reply to the words. This replaces the parser's
  "didn't understand" reactions, which read as the hosts being shocked by ordinary remarks.
- **Checked against the real parser.** `tools/nlu_probe` runs Façade's own parser (its
  `TemplateCompiler` classes and rules, read from the install) outside the game, in 3 s, and prints
  the acts it reads. `verify_acts.py` runs every entry alone, addressed to Trip and paired with
  another: 265/265 read as intended. The game's own `java.log` shows the same acts in play.
- **Speed and accuracy:** picking takes 0.09 s (median) against 0.3-1 s for the rewording. On 40
  typical guest lines (greetings, the argument, confusion, comments, drinks, advice, small talk) 39
  matched what the guest meant.
- **Repeats.** Generated lines are checked against everything generated tonight: much the same
  words, the same first four words, any shared run of five words, or a whole sentence again ("Want
  a drink?"). A rejected scene line starts the request over with the rejected text named, instead
  of showing the model its own attempt (it rewrote the same line three times). llama.cpp's DRY
  penalty was tried and dropped: it garbled words ("a lot_of", "butI"). In a scene, a host chimes in
  at most once (it used to after every answer, which sounded like a rerun). Scene lines ask for at
  most 16 words and are cut to the whole sentences that fit 18, for speed.
- **Who answers in a scene.** A named host answers; a follow-up ("what do you mean") goes to whoever
  just spoke; otherwise they take turns. The prompt says which host said the last line, after Trip
  answered for Grace's joke.
- **A cut-off line.** When the guest types, the drama manager often aborts the line being spoken
  (`abortAnimationScripts`, layer 7), and no `ScriptFinished` follows. The bridge counted that host as
  mid-line for 30 s, so the other one took every scene line. An abort now ends the line.

## Subtitles (2026-10-29)

The engine times subtitles by their text, not their audio (`animEngineDLL` v1.1, `0x1009DD90`). It
types a line out word by word at 0.75 frames per character, holds it 60 frames, then clears it. That
fits the actors' pacing; generated speech often runs longer, and its subtitle went before the words.
The engine side of the bridge (`hold_subtitles` in `inject.cpp`) now notes how long each voice file
lasts when the engine opens it, and while it plays keeps the subtitle's hold from running out. The
character objects are at `0x1097947C` (Grace) and `0x10979480` (Trip); in each, the text is at
`+0x13E1C`, the shown range at `+0x1411C`/`+0x14120`, and the frame the hold began at `+0x14124`. The
frame counter is `0x1083F62C`, and the menu's Subtitles flag is the byte at `0x10979471` (the
autopilot's `subtitles on`). Tested with frames every 0.25 s: a 6.8 s answer kept its subtitle to the
end and cleared after.

Fully generated lines (scenes) also get their phonemes split into phrases at punctuation, each
starting at its share of the voiced audio, as in the actors' records, instead of one phrase for the
whole line.

## Off-script scenes and following (built 2026-10-28)

Façade's drama manager has no room for a guest who takes the evening somewhere new. It deflects what
it doesn't understand. It treats a guest in the hallway as leaving, and escalates to throwing them
out. Nobody ever follows the guest.

- **Scenes** (`engine/service/improv.py`). A scene starts in three ways:
  - something the guest types takes the evening somewhere new (an LLM check, NEW or USUAL, with
    examples and a USUAL default; questions about the fighting, confusion and "that's not what I
    said" are USUAL);
  - the drama manager picks one of its off-script reactions (`DEFLECT*`, `*UNCOOP`, `*LEAVEAPARTMENT`,
    `*LEAVEFORKITCHEN` beats);
  - the guest walks into the hallway after coming in.

  **A scene pauses the story** (`engine/bridge/scene.cpp`, revised after the first play test). The
  first version swapped each story line for an improvised one while the drama manager carried on
  underneath. Its actions then contradicted the words (Trip shut the door while saying "I'm
  coming!"), and whole beats, the divorce question among them, went by improvised away.
  - Now the line service answers the next story push with `hold`. That character's drama-manager
    thread stays inside its push, so its behaviours and its beat can't move on. Grace and Trip are
    separate threads; the other one is held at their own next line.
  - Meanwhile the held thread asks `/scene` what happens next and pushes the scene lines itself,
    with fresh action objects. The hosts answer each thing the guest says, and chime in once after
    12 s of quiet.
  - A scene line never cuts off a line still playing. The engine side records when speech finishes,
    in a small shared memory block.
  - The guest's words during a scene go to the scene only (`textSensor` returns nothing).
  - The scene ends when the guest's words turn back to the evening (the classifier, with examples
    such as "anyway, I'd love a glass of wine"), after 20 s with nothing added, after 4 minutes, or
    when a hallway scene's guest comes back in. The held story line then plays, bridged from the
    scene.
  - Scenes don't start before the guest has come in: the greeting stays the original's.
  - In testing, the drama manager survived scenes of 35 to 131 s and resumed at the held line.
    Its "MISSED THE DIALOG COMMIT CUE" and expression-timeout warnings occur at the same rate
    without scenes.
- **Positions** (`engine/bridge/world.cpp`). Every line request carries everyone's floor position
  (`getObjectPosition`). The service describes where the guest is: by the bar, in the hallway, next
  to the speaker.
- **Following, only when asked** (revised after the 10-29 play test: hosts trailing the guest
  unasked felt wrong). `/understand` also asks the LLM whether the guest's words ask a host to come
  along ("follow me", "trip, come out here", "can I talk to you in private"); a keyword pre-check
  keeps the call off ordinary lines. Its answer rides back as `"follow"`: 0 Grace, 1 Trip, 2
  whoever is nearer, -1 "stay there". Only that host follows, for 3 minutes or until told to stay.
- Once asked and once the guest has come in (seen in the hallway, then well inside for 3 s), the
  host walks after them whenever they are more than 170 units away. The host stops 70 units short.
  The same `walkStep` is re-issued every 0.5 s, as the drama manager does.
  - Following only starts when that host's last walk from the drama manager has completed (its
    `Walk` action's `completionStatus`).
  - Replacing a walk the drama manager was still waiting on killed its Java process in testing.
    There was no exception and no log; the process just exited.
- **The doorway.** While the guest is in the hallway within 250 units of the door, the drama
  manager's position reads (`getObjectPosition`, `getAllObjectPositions`) put them just inside the
  doorway. Its "leaving the apartment" test never fires, so there's no warning, no escalation and no
  eviction. Walking on toward the elevator shows the real position, so leaving still ends the evening.
  - The catch, found in the first play test: believing the guest was inside, Trip shut the front
    door on them. The game has no way back in after that.
  - Trip shuts the door with a right-arm gesture (script 42, which plays `closedoor2.wav`; 39
    opens it). While the guest isn't inside, `door_guard` swaps that gesture for his resting arm,
    so the door stays open.
- **Following into the hallway.** With the guest outside, following starts at 100 units instead of
  170 and stops 55 units short. A host still in the room walks to the doorway first.
- **Typing.** The engine's typing filter (`+0xa19b0`) now accepts `*`, for actions. Subtitles are
  capped at 140 characters, because longer ones overran the engine's buffer.
- `FFR_NO_FOLLOW=1` and `FFR_NO_DISGUISE=1` turn the two bridge behaviours off. The drama manager's
  console output goes to `%LOCALAPPDATA%\FacadeFanRemake\java.log`.
- **Tested** with `engine/autopilot/improv_test.txt` on the hidden desktop, over five runs. In the
  last run:
  - scenes lasted 23-49 s, with 2-6 story lines held back;
  - they returned with bridging lines;
  - Grace followed the guest across the room;
  - the drama manager ran to the end.

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

**Correction (2026-10-28):** the fp16 audio decoder (S3Gen) garbled the speech in play. Over six lines
in Trip's voice, similarity to the actor's voice was 0.865, against 0.899 in fp32. Only the text model
(T3) is fp16 now, under autocast. The decoder is fp32 again: 0.898, and 1.9 GB instead of 1.4 GB
(`load_fp16` in `prototype/voice.py`). The stack peaks around +7.6 GB.

`FACADE_TIMING=1` logs the engine's voice-file reads. Each file is read whole, not streamed. Automated
runs also log frames slower than 100 ms. Neither showed a playback problem.

## Unknowns

- How the in-memory script table is laid out, and whether adding a record at runtime is safe.
- Sitting down, and walks the LLM directs (to the bar, the window). Following works by walking only
  when the drama manager's own walk is done; directed moves would need the same care.
- Which discourse acts the original NLU emits (the Jess rule files in `util/sources/facade/nlu`).
- The modern-Windows render stall in the menus (cursor in a black box, 0% CPU).

## Tools

The JDK's `javap` reads the Java side. Visual Studio 2022 (installed) builds the 32-bit DLLs.
