# Legal, consent and asset policy

Façade Remastered is a free, non-commercial fan project made for fun. It will never be sold,
monetized, or bundled with ads or donations-for-access.

## What this repository contains

Only original code and documentation written for this project. That code is MIT-licensed.

## What it never contains

Façade is freeware, but it is not public domain: its audio, animation, textures, models, music and
script are © 2005–2006 Procedural Arts. Freeware means you can play it and give copies to friends,
as the original readme says. It does not mean anyone may re-host or repackage its parts.
So this project never commits, uploads, or redistributes:

- voice recordings (`.wav` / `.spk`), music (`.mp3`), sound effects
- animation data (`.abinary`, `.sbinary`, `.aindex`), textures (`.bmp`), cursors
- the original program files (`.dll`, `.exe`, `.class`, `.jar`)
- the script: dialogue text, whether pasted in or extracted (for example `data/catalog.json`)
- anything derived from those files: converted audio, decoded meshes, voice models or voice embeddings

The remaster reads everything it needs from the player's own installed copy, at runtime, on the
player's machine. `tools/check_no_assets.py` enforces this on every commit.

Docs may describe file *formats* (field layouts, how files relate to each other) and may name beat
IDs, which are structural labels. They never quote dialogue.

## Voices

New lines are spoken in Grace's and Trip's voices, synthesized locally from the original
recordings.

- Voice synthesis runs only inside the game, for lines Grace or Trip speak in a scene. There is no
  general "say anything in this voice" tool, command or API.
- Reference audio is read from the player's own install. Nothing derived from it is cached in a
  shareable form, uploaded, or committed.
- No voice models, fine-tunes or speaker embeddings are ever published.

## Original credits

Façade, a one-act interactive drama, v1.1, © 2005–2006 Procedural Arts, www.interactivestory.net

- Design, engineering, writing, animation, production: Michael Mateas and Andrew Stern
- Voice acting: Chloe Johnston (Grace), Andy Bayiates (Trip)
- Music: Billy Gomberg, Matt Ganucheau, Jim Doran, Aaron Acosta, Leo Caruso
- Additional engineering: Mehmet Fidanboylu
- Additional animation and art: John Rines, Andy Webster
- Additional voice acting: Mary McCann, Joel Bruner
- Story consultant: John Lavin

This project is not affiliated with or endorsed by Procedural Arts. If you are a rights holder and
want something changed or taken down, open an issue and it will be handled promptly.
