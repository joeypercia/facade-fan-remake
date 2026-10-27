# Playable build

A first playable version of the whole evening: you arrive at Grace and Trip's apartment, talk to
them in free text, and the night plays out across twelve beats to an ending. Every evening is new,
and nothing carries over between evenings.

## Run it

You need the original Façade installed, the line catalog built (`python tools/extract_catalog.py`),
and the prototype's environment set up (see [prototype/README.md](../prototype/README.md)).

```bash
python prototype/start_llm.py
%USERPROFILE%\.venvs\facade-remastered\Scripts\python game\server.py
```

Then open http://127.0.0.1:8090, pick your name and pronouns, and knock.

- Type to talk at any time. Put actions in asterisks: `*hugs Grace*`, `*picks up the photo*`.
- Arrow keys or WASD to move, drag to look around.
- `FACADE_VOICE=0` runs subtitles-only (no voice model), which helps when the GPU is busy with something else.
- `python game/playthrough.py` plays a whole evening with a scripted guest, text only, for testing.

## How it fits together

| File | Role |
|---|---|
| `story_arc.py` | The evening as twelve beat cards (situation, each character's goal, end condition, guest-turn range, staging, opening cue, sound effects), in this project's own words |
| `director.py` | Walks the arc. Asks the local model for each exchange, reads its control lines (beat done, siding, exits, kicking the guest out), skips repeated lines, retries hollow replies, remembers tonight |
| `assets.py` | Reads the apartment layout, textures, sound effects and animation from your install at runtime |
| `server.py` | Local web server (127.0.0.1 only): serves the client and assets, runs the evening on one worker thread, streams lines and voice audio to the browser |
| `client/` | three.js renderer: the apartment from the original layout and textures, Grace and Trip from their original animation frames, subtitles, streamed audio, input |

## Known gaps

- Characters are drawn as ball-and-bone figures over the original skeleton, not real models yet.
  Furniture (couch, bar, armoire) is placeholder boxes; the original draws it outside the sprite system.
- Gestures are chosen from the original clips by name; recorded lines don't yet use their original
  performance tracks, and there's no lip sync.
- The model decides when the guest has "taken a side" and leans heavily one way; the ending doesn't use it yet.
- With voices on, first audio is about 2 s after you press Enter. The LLM and the voice model slow each
  other down when both run at once on one GPU. The fix (pause the LLM after the first sentence,
  voice it, then resume) is next.
