"""Shallow clone/pull of the campaigns repo via subprocess git.

Errors never carry the repo URL: a campaigns URL may embed a token and this
runs in a CronJob whose stderr lands in cluster logs. Git's own stderr is
passed through (minus any ``user:token@`` userinfo) so failures stay
diagnosable without leaking the credential. ``GIT_TERMINAL_PROMPT=0`` makes an
auth-required URL fail fast instead of blocking on a prompt until the timeout.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

_USERINFO = re.compile(r"(://)[^/\s@]*@")


def _redact(text: str) -> str:
    return _USERINFO.sub(r"\1", text)


def _run(argv: list[str], op: str, timeout: int) -> None:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, env=env, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"git {op} timed out after {timeout}s") from None
    if proc.returncode != 0:
        raise RuntimeError(
            f"git {op} failed (exit {proc.returncode}): {_redact(proc.stderr).strip()}"
        )


def checkout(url: str, dest: Path) -> Path:
    dest = Path(dest)
    if (dest / ".git").exists():
        _run(["git", "-C", str(dest), "pull", "--ff-only"], "pull", 120)
    else:
        _run(["git", "clone", "--depth", "1", url, str(dest)], "clone", 300)
    return dest
