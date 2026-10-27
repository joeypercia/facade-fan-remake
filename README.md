# Façade Remastered

A non-commercial fan mod of **Façade** (2005), the interactive drama by Michael Mateas and Andrew Stern
(Procedural Arts). You spend an evening with Grace and Trip, old college friends whose marriage is
coming apart while you're in the room.

The remaster keeps the original story, characters, beats and voice performances. It replaces the
2005 natural-language parser and drama manager with a locally run language model, so Grace and Trip
respond to what you actually say and do.

> **You need your own copy of the original Façade.** This repository contains code and documentation
> only. It does not contain, and will never contain, any of the original game's audio, animation,
> textures, or script text. The remaster reads those from your installed copy at runtime.
> See [docs/LEGAL.md](docs/LEGAL.md).

## Status

Early development. The working pieces so far:

| Piece | State |
|---|---|
| Line catalog: all 8,660 lines (beat, direction, subtitle, recording, phonemes, cues) from your install | working |
| Asset guard: blocks commits that contain original assets | working |
| Animation decoding: clip index, joint-position frames, performance scripts | working |
| Apartment layout: all 127 walls and props, with positions | working |
| **Playable evening**: all twelve beats, free-text input, ending ([game/](game/README.md)) | first playable |
| Drama manager on a local LLM: beat sequencer, tonight's memory, control cues | working |
| Voices: original recordings when they fit, streamed expressive synthesis otherwise | working |
| Renderer: apartment from the original layout and textures, characters from original animation | placeholder art |
| Character models and lip sync (the engine has no mesh files) | planned |

The full plan is in [docs/DESIGN.md](docs/DESIGN.md). Notes on the original file formats are in
[docs/ASSETS.md](docs/ASSETS.md).

## Goals

- **Type anything.** They respond to what you actually said, not just a keyword match.
- **Any name, your pronouns.** The story uses the name and pronouns you choose.
- **Do things, not just say them.** Write `*hugs Grace*` and they react to it.
- **They act on what you say.** They walk over, mix you a drink, go to the window.
- **They remember tonight.** What you say and do comes back later in the same evening.
  Every evening starts completely fresh: nothing carries over between sessions.
- **True to the original.** The beat structure, backstory and tone come from the 2005 game.
  The model improvises inside that structure; it doesn't rewrite it.
- **HD and widescreen.** Fullscreen at your monitor's resolution. Starts in seconds.
- **Runs locally.** The model runs on your own GPU. No account, no server, nothing leaves your machine.

## Requirements

- Windows 10/11
- The original Façade v1.1, installed (default path `C:\Program Files (x86)\Facade`)
- Python 3.11+ for the tools
- Node 20+ for the game client (once it lands)
- An NVIDIA GPU with 12 GB VRAM is the target. Smaller cards will need a smaller model.

## Try the catalog tool

```bash
python tools/extract_catalog.py --facade "C:/Program Files (x86)/Facade"
```

This writes `data/catalog.json` on your machine. It's git-ignored; don't share it.

## Contributing

Run the asset guard before every commit (`python tools/install_hooks.py` installs it as a pre-commit hook):

```bash
python tools/check_no_assets.py
```

Pull requests that add original assets or dialogue text will be closed.

## Credits

Façade © 2005–2006 Procedural Arts. Design, engineering, writing, animation and production by
Michael Mateas and Andrew Stern. Grace was voiced by Chloe Johnston, Trip by Andy Bayiates.
Full credits are in [docs/LEGAL.md](docs/LEGAL.md). This project is not affiliated with or endorsed by Procedural Arts.

The remaster's own code is MIT-licensed ([LICENSE](LICENSE)). That license covers this repository's
code only, not Façade.
