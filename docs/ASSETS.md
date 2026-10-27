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

## Animation (hypothesis, in progress)

- `<char>.aindex`: fixed 259-byte records. Each holds a NUL-padded clip name (`gr_all_neutrals`,
  `gr_armL_sip`...) followed by integers that look like a clip number and a frame count.
- `<char>NNN.abinary`: clip data. Starts with little-endian floats that look like per-joint
  rotations and translations per frame, which points to a skeletal rig.
- `<char>Script.sbinary`: per-script-index performance tracks: which animation layers, gestures
  and (likely) mouth shapes play over a voice line. The repeating 0x3xxxx-range integers look like
  references into the clip table.
- Character meshes and the apartment geometry don't appear as separate files. They are probably
  built into `animEngineDLL.dll`; this needs reverse-engineering.

## Engine notes

- The engine starts the drama manager as
  `java -Xms70m -Xmx150m ... -classpath ../../classes;../../classes/jess.jar facade.Main`
  and talks to it through shared memory via `nativeaniminterface.dll`.
- The frame limiter sleeps 10–30 ms per frame based on measured fps (`GetTickCount`).
- On modern Windows the original menu can stop redrawing: only the cursor moves, inside a black box.
  This is a rendering stall, not a crash. The remaster's new renderer avoids the old engine entirely.
