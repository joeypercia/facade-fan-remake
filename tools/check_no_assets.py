"""Fail if any tracked or staged file looks like an original Facade asset or something derived from one.

Run by the pre-commit hook (tools/install_hooks.py) and in CI. The project ships code only;
players load the game's assets from their own install at runtime.
"""
import subprocess
import sys
from pathlib import Path

BANNED_SUFFIXES = {".wav", ".spk", ".mp3", ".bmp", ".ico", ".abinary", ".sbinary", ".aindex", ".class", ".jar", ".dll"}
BANNED_DIRS = {"data", "cache"}
# Distinctive strings from the original script; a hit means dialogue text was pasted into the repo.
# Kept as fragments of beat IDs, which are structural identifiers rather than dialogue.
SCRIPT_MARKERS = ("Script_dialog_",)


def tracked_files():
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                         capture_output=True, text=True, check=True).stdout
    return [Path(p) for p in out.splitlines() if p]


def main():
    problems = []
    for path in tracked_files():
        if path.suffix.lower() in BANNED_SUFFIXES:
            problems.append(f"{path}: asset file type {path.suffix}")
        elif path.parts and path.parts[0] in BANNED_DIRS:
            problems.append(f"{path}: lives in local-only folder {path.parts[0]}/")
        elif path.is_file() and path.stat().st_size < 5_000_000 and path.name != "check_no_assets.py":
            text = path.read_text(encoding="utf-8", errors="ignore")
            for marker in SCRIPT_MARKERS:
                if marker in text and path.suffix not in {".py", ".ts", ".md"}:
                    problems.append(f"{path}: contains extracted script data ({marker})")
    if problems:
        print("Refusing: original Facade assets must not be committed.\n  " + "\n  ".join(problems))
        sys.exit(1)
    print("ok: no original assets in the repo")


if __name__ == "__main__":
    main()
