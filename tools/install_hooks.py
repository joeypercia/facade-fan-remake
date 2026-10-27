"""Install a git pre-commit hook that runs the asset guard."""
from pathlib import Path

hook = Path(".git/hooks/pre-commit")
hook.write_text("#!/bin/sh\nexec python tools/check_no_assets.py\n", encoding="utf-8", newline="\n")
hook.chmod(0o755)
print(f"installed {hook}")
