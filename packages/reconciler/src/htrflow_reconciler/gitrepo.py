"""Shallow clone/pull of the campaigns repo via subprocess git."""

from __future__ import annotations

import subprocess
from pathlib import Path


def checkout(url: str, dest: Path) -> Path:
    dest = Path(dest)
    if (dest / ".git").exists():
        subprocess.run(
            ["git", "-C", str(dest), "pull", "--ff-only"], check=True, timeout=120
        )
    else:
        subprocess.run(
            ["git", "clone", "--depth", "1", url, str(dest)], check=True, timeout=300
        )
    return dest
