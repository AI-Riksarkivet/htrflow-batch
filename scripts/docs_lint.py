"""Keep the documentation site general.

The site documents htrflow-batch as it is, for any operator on any cluster:
no project history (story ids, dates, "Phase"/"PoC"), no version numbers
(they go stale on the next release), no specific hardware and no one site's
hosts or paths. Each hit prints as ``path:line: rule: text``; exit status 1
when there is any.

Usage: python3 scripts/docs_lint.py PATH [PATH ...]
  PATH is a markdown file or a directory searched for ``*.md``. Exceptions
  live in scripts/docs-lint.allow as ``path-suffix<TAB>regex``; a hit is
  allowed when its path ends with the suffix and its line matches the regex.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

RULES: list[tuple[str, re.Pattern[str]]] = [
    ("ids", re.compile(r"\b[BDTUCXGI][0-9]{1,3}\b|\bS[0-9]{2}\b|\bTask [0-9]+|\bPhase [12]\b|\bPoC\b")),
    ("dates", re.compile(r"\b20[0-9]{2}-[01][0-9]-[0-3][0-9]\b")),
    # Dotted numbers inside an IP address or CIDR (0.0.0.0/0) are not versions.
    ("versions", re.compile(r"(?<![0-9.])v?[0-9]+\.[0-9]+\.[0-9]+(?![0-9.])|\bv[0-9]+\.[0-9]+\b")),
    (
        "hardware",
        re.compile(
            r"GB10|\bAda\b|Blackwell|\b(A10|A30|A40|A100|H100|H200|L4|L40S?)\b|arm64|amd64|aarch64|x86_64|qemu|binfmt|k3s",
            re.IGNORECASE,
        ),
    ),
    (
        "site",
        re.compile(r"arkis|lbiiif|riksarkivet\.se|/home/|127\.0\.0\.1|localhost|192\.121\.", re.IGNORECASE),
    ),
]

ALLOW_FILE = Path(__file__).with_name("docs-lint.allow")


def load_allow() -> list[tuple[str, re.Pattern[str]]]:
    allow = []
    if not ALLOW_FILE.exists():
        return allow
    for raw in ALLOW_FILE.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        suffix, _, pattern = raw.partition("\t")
        pattern = pattern.strip()
        if not pattern:
            sys.exit(f"{ALLOW_FILE}: expected 'path-suffix<TAB>regex': {raw!r}")
        allow.append((suffix, re.compile(pattern)))
    return allow


def markdown_files(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for arg in paths:
        p = Path(arg)
        files.extend(sorted(p.rglob("*.md")) if p.is_dir() else [p])
    return files


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    allow = load_allow()
    hits = 0
    for path in markdown_files(argv):
        posix = path.as_posix()
        for number, text in enumerate(path.read_text().splitlines(), start=1):
            for rule, pattern in RULES:
                if not pattern.search(text):
                    continue
                if any(posix.endswith(suffix) and allowed.search(text) for suffix, allowed in allow):
                    continue
                print(f"{posix}:{number}: {rule}: {text.strip()[:160]}")
                hits += 1
    if hits:
        print(f"docs-lint: {hits} hit(s)", file=sys.stderr)
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
