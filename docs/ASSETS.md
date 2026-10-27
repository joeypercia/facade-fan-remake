# Original asset formats

Notes on how Façade v1.1 lays out its data, so the remaster can load it from a player's install.
Paths are relative to the install root (default `C:\Program Files (x86)\Facade`).
**Confirmed** means verified by tooling in this repo; **hypothesis** means it still needs checking.

## Layout

```
Facade/
  stageplays/                 transcripts the game writes after each play (plain text)
  util/
    classes/                  compiled Java: drama manager (ABL), NLU (Jess rules), character behaviors
    j2re1.4.2_06/             bundled Java 1.4.2 runtime
    sources/facade/
      animEngineStarter.exe   launcher; loads animEngineDLL.dll (renderer + animation engine)
      animEngineDLL.dll       C++ engine: OpenGL rendering, audio, stageplay writer, starts the JVM
      nativeaniminterface.dll JNI bridge between the Java drama manager and the engine (shared memory)
      animation/<char>/       .abinary clips, <char>.aindex, <char>Script.sbinary
      Sounds/<char>/NN/       NNNNN.wav voice lines (+ .spk Speex copies), global/ sfx, mp3_*/ music
      textures/               146 .bmp textures (props, drinks, doors, eightball...)
      nlu/                    Jess rule bases (.rul), maps, WordNet-style .bin data
```

## Voice lines (confirmed)

- `Sounds/grace/NN/NNNNN.wav` and `Sounds/trip/NN/NNNNN.wav`: the five-digit file number is the
  line's **script index**. Folders `01`–`10` just bucket the numbers.
- `.spk` files are Speex-compressed copies of the same audio, not lip-sync data
  (`speexdecode.bat` in the install regenerates the `.wav` files from them).
- `Sounds/<char>/<char>snd.txt` lists the recordings the engine preloads.

## Script (confirmed)

The dialogue isn't stored as text. Each voiced line is a `static final int` constant in
`util/classes/facade/util/GraceScript.class` / `TripScript.class`. The constant's value is the
script index, and its name encodes the line:

```
e<char>Script_dialog_<BEAT>_<DIRECTIONS>_<spoken_words>
```

- `<BEAT>`: the beat ID (for example `FASKDRINKT1`, `CRISISP1`, `THERAPYGAMEP2`, `ENDING`).
  Beats are the story units the drama manager sequences.
- `<DIRECTIONS>`: ALL-CAPS performance notes (`GENTLY`, `ACCUSATION`, `MUTED OFFSCREEN`...).
  These can also trail the words, or be glued onto the first word.
- `<spoken_words>`: lowercase words, underscores for spaces, punctuation dropped. A digit suffix
  disambiguates otherwise identical names (`simply1`).
- Beat `player` holds the lines where Trip or Grace say the player's name. The text is empty because
  the original recorded one take per supported first name.

`tools/extract_catalog.py` turns this into `data/catalog.json` (local only): 8,660 lines across
517 beat/character pairs, 7,964 of them with a recording. Lines without a recording were written
but never recorded or shipped.

The same classes also define non-dialogue constants (`egraceScript_layer_*`, `egraceScript_stand*`...),
which name animation scripts and body layers.

## Animation

Readers for all three formats are in `tools/facade_formats.py`.
`tools/preview_skeleton.py <char> <clip>` renders a clip as a stick figure for checking by eye.

### `<char>.aindex` (confirmed)

Fixed 259-byte records, one per clip, in clip order:

| Offset | Size | Field |
|---|---|---|
| 0 | 255 | clip name, NUL-padded (`gr_all_neutrals`, `gr_armL_sip`, `tr_armL_eightballShake`...) |
| 255 | 4 | int32 LE: **cumulative** end frame (the clip spans previous end → this end) |

Clip *i* is stored in `<char>{i:03}.abinary`. Grace has 97 clips (4,533 frames), Trip 136 (7,616).
Clips are concatenated in index order into one **global frame space**, which `.sbinary` refers to.

### `<char>NNN.abinary` (confirmed layout, partly interpreted)

Raw little-endian float32 frames, no header. Every clip's size matches its `.aindex` frame count.

- Grace: 243 floats per frame = 81 vec3s. Trip: 234 floats = 78 vec3s.
- vec3 0–1: always zero (Trip's vec 0 has a tiny y value in a few clips).
- vec3 2: small offsets, probably a root translation (hypothesis).
- vec3 3–4: angles in degrees, range ±180 (hypothesis: root and head orientation).
- vec3 5 onward: **body-space joint positions**, not rotations (confirmed). Y is up; feet are near
  y = −25 and the head near y = +17. Left and right joints come in mirrored pairs (x ≈ ±1.7),
  and two blocks of 16–18 joints are the fingers of each hand.

Pose data is positional, and bone lengths stay constant: 64 of Grace's 75 inferred bones vary by
less than 1% across every frame. So the rig is a fixed skeleton, sampled as joint positions.
The real joint hierarchy and joint names are still to be recovered from the engine.

### `<char>Script.sbinary` (confirmed)

A stream of int32 LE tokens, starting with the header `0, 2, 2, 0`:

```
199998, <script index>, h0, h1, h2,      record start; script index = constant value in <Char>Script.class
200000,                                  body start
  <segments>
200007                                   body end
...
199998, 199999                           end of file
```

The body is a run of segments. Each opcode (≥ 200000) takes the int arguments that follow it,
up to the next opcode:

| Opcode | Arguments | Meaning |
|---|---|---|
| (none) | frame numbers | explicit frame list at the start of a body |
| 200001 | a, b | play global frames a → b |
| 200004 | 1+ frames | hold frame(s) |
| 200006 | 1 (rarely 3) | segment boundary / transition flag (hypothesis) |
| 200002, 200003 | 2, 3 | rare; meaning unknown |
| 200005 | see below | a voiced line |

A voiced line (200005) carries:

```
base frame, 0, flag,
[phoneme codes..., 0]           first phrase, starting at 0 ms (if any)
entries until (t, 0):
  t, phoneme codes..., 0        a phrase starting at t ms
  t, cue id                     a timed cue (ids seen: 1, 2, 7, 9, 10, 11, 12, 20, 21, 22)
-1, <script index>, <subtitle as one char code per int>, -1
```

- **Phoneme codes** are 31–70, 32 of them in use. That matches a phoneme set (ARPAbet has 39
  plus silence); the exact code-to-phoneme table is still unknown. Code 70 opens most phrases.
  Their timing isn't a fixed rate, so the engine must spread them over the phrase somehow.
- The final entry `(t, 0)` closes the cue list. t is **not** the audio length (median 450 ms
  shorter, with a wide spread).
- The **subtitle** is the exact line as written, with capitals and punctuation.
  `extract_catalog.py` uses it in preference to the constant name.

Grace has 4,334 records (4,171 voiced); Trip has 4,825 (4,563 voiced).

## Scene (confirmed positions, partly interpreted)

The apartment isn't stored as files. The engine builds it in code: one constructor call per
object, with its arguments pushed as immediates. `tools/scan_scene.py` finds every such call
(127 in v1.1) and writes `data/scene.json` (local only).

- Walls are named panels per area: `FD_` (front door), `FY_` (foyer), `HW_` (hallway), `KI_`
  (kitchen), `LR_` (living room, including `LR_backwindow*`). Props have plain names: `couch`,
  `rug`, `wedding photo`, `martini glass 1`... The second argument is 1 for walls and 0 for props.
- Arguments 2–4 are the object's position, pushed in (z, y, x) order. Walls are 160 units tall;
  the living room's back wall is at z = −500.
- Arguments 12–14 hold sizes (for props, height and width).
- The constructor is at `0x10002310` in `animEngineDLL.dll` v1.1. A different engine build would
  need that address re-found.
- Still unknown: textures per panel (the 146 `.bmp`s are props, drinks and doors; wall surfaces
  may be flat-shaded), and the collision and walk paths ("fattened walls").

## Characters

There is no character mesh in the files. The engine draws Grace and Trip from their joint positions
("sprites" made of "model points", with face parameters for brows, eyelids, mouth and gaze set
through the API). That fits Andrew Stern's earlier PF.Magic "Ballz" engine (Petz, Babyz), where
characters were 2D shapes drawn at 3D skeleton points. The drawing rules are inside
`animEngineDLL.dll`. Recovering them, or designing a faithful new character renderer driven by
the same skeleton, is the main open problem for phase 2.

## Engine notes

- The engine starts the drama manager as
  `java -Xms70m -Xmx150m ... -classpath ../../classes;../../classes/jess.jar facade.Main`
  and talks to it through shared memory via `nativeaniminterface.dll`.
- The frame limiter sleeps 10–30 ms per frame based on measured fps (`GetTickCount`).
- On modern Windows the original menu can stop redrawing: only the cursor moves, inside a black box.
  This is a rendering stall, not a crash. The remaster's new renderer avoids the old engine entirely.
