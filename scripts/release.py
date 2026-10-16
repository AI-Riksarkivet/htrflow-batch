#!/usr/bin/env python3
"""The release's file edits, and what main needs next.

`state` tells CI which step a release is at; `bump` is the PR that starts one.
Standard library only: CI runs this with the runner's plain `python3`.

    python scripts/release.py state
    python scripts/release.py bump <version> --chart <chart-version>

States, from the files on main and the `v*` tags:

  released  the version already has its tag
  publish   the images for the version are not yet built and pinned
  tag       the hook pins the version's converter by digest; only the tag is left
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Literal

import tomllib

ROOT = Path(__file__).parents[1]
HOOK = "packages/converter/src/htrflow_converter/template/argocd/apply.yaml"
EXAMPLE_HOOK = "examples/campaigns/argocd/apply.yaml"
PYPROJECTS = (
    "packages/wrapper/pyproject.toml",
    "packages/web/pyproject.toml",
    "packages/converter/pyproject.toml",
)
CHART = "charts/htrflow-batch/Chart.yaml"
CONVERTER_IMAGE = "docker.io/riksarkivet/htrflow-campaigns"

_HOOK_IMAGE = re.compile(
    rf"^(?P<head>\s*image: ){re.escape(CONVERTER_IMAGE)}(?P<ref>\S*)(?P<tail>.*)$"
)
_PIN = re.compile(r"^@sha256:[0-9a-f]{64}$")


def _key(v: str) -> tuple[int, ...]:
    if not re.fullmatch(r"\d+\.\d+\.\d+", v):
        sys.exit(f"release: {v!r} is not a x.y.z version")
    return tuple(int(p) for p in v.split("."))


def version(root: Path) -> str:
    data = tomllib.loads((root / PYPROJECTS[0]).read_text())
    return data["project"]["version"]


def state(root: Path, tags: set[str]) -> Literal["released", "publish", "tag"]:
    v = version(root)
    if "v" + v in tags:
        return "released"
    images = [
        m
        for line in (root / HOOK).read_text().splitlines()
        if (m := _HOOK_IMAGE.match(line))
    ]
    pinned = [
        m for m in images if _PIN.match(m["ref"]) and m["tail"].strip() == f"# v{v}"
    ]
    return "tag" if len(images) == 3 and len(pinned) == 3 else "publish"


def _sub_lines(path: Path, pattern: str, repl: str, count: int) -> None:
    """Replace `count` lines matching `pattern` (multiline), keeping the rest."""
    text, n = re.subn(pattern, repl, path.read_text(), flags=re.M)
    if n != count:
        sys.exit(
            f"release: expected {count} match(es) of {pattern!r} in {path}, found {n}"
        )
    path.write_text(text)


def bump(root: Path, new: str, chart_version: str, run=subprocess.run) -> list[Path]:
    if _key(new) <= _key(version(root)):
        sys.exit(f"release: {new} is not greater than the current {version(root)}")
    _key(chart_version)
    changed = []
    for rel in PYPROJECTS:
        _sub_lines(root / rel, r'^(version = )"[^"]*"$', rf'\1"{new}"', 1)
        changed.append(root / rel)
    chart = root / CHART
    _sub_lines(chart, r'^(appVersion: )"[^"]*"$', rf'\1"{new}"', 1)
    _sub_lines(chart, r"^(version: )\S+$", rf"\g<1>{chart_version}", 1)
    changed.append(chart)
    for rel in (HOOK, EXAMPLE_HOOK):
        _sub_lines(
            root / rel,
            rf"^(\s*image: {re.escape(CONVERTER_IMAGE)})\S*.*$",
            rf"\1:v{new}",
            3,
        )
        changed.append(root / rel)
    run(["uv", "lock"], cwd=root, check=True)
    changed.append(root / "uv.lock")
    return changed


def _tags(root: Path) -> set[str]:
    out = subprocess.run(
        ["git", "tag", "--list", "v*"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return set(out.stdout.split())


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("state", help="print released, publish or tag")
    b = sub.add_parser("bump", help="set the version everywhere")
    b.add_argument("version")
    b.add_argument("--chart", required=True, help="the chart's new version")
    args = parser.parse_args(argv)
    if args.command == "state":
        print(state(ROOT, _tags(ROOT)))
    else:
        for path in bump(ROOT, args.version, args.chart):
            print(path.relative_to(ROOT))


if __name__ == "__main__":
    main()
