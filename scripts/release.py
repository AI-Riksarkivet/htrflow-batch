#!/usr/bin/env python3
"""The release's file edits, and what main needs next.

`state` tells CI which step a release is at; `bump` is the PR that starts one.
Standard library only: CI runs this with the runner's plain `python3`.

    python scripts/release.py state
    python scripts/release.py bump <version> --chart <chart-version>
    python scripts/release.py pin <version> --wrapper sha256:... --web sha256:...
        --campaigns sha256:... --converter-ref <sha>

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

COMPOSE = ".docker/docker-compose.yml"
# The hand-pinned files of each image; `.dagger/published.go` names the same
# ones for the checks.
PINNED: dict[str, tuple[str, ...]] = {
    "web": ("charts/htrflow-batch/values.yaml", COMPOSE),
    "wrapper": (
        "packages/converter/src/htrflow_converter/template/pipelines/demo-v1.yaml",
        COMPOSE,
    ),
    "campaigns": (HOOK,),
}
CONVERTER_REF_FILES = (
    "packages/converter/src/htrflow_converter/ci/github/.github/workflows/render.yml",
    "packages/converter/src/htrflow_converter/ci/azure/azure-pipelines.yml",
)
# Regenerated from the pinned files by `pin`, never edited by hand.
GENERATED: tuple[str, ...] = ("examples/campaigns/", "docs/reference/configuration.md")
# The image repository of each pin, and the files whose pins carry `# v<version>`.
_REPOS = {
    "wrapper": "riksarkivet/htrflow-batch",
    "web": "riksarkivet/htrflow-web",
    "campaigns": "riksarkivet/htrflow-campaigns",
}
_COMMENTED = (COMPOSE, HOOK)
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_SHA = re.compile(r"[0-9a-f]{40}")

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


def _sub_lines(
    pending: dict[Path, str], path: Path, pattern: str, repl: str, count: int
) -> None:
    """Replace `count` lines matching `pattern` in the pending text of `path`.

    Nothing is written here: bump writes once every count has held.
    """
    text, n = re.subn(pattern, repl, pending.get(path, path.read_text()), flags=re.M)
    if n != count:
        sys.exit(
            f"release: expected {count} match(es) of {pattern!r} in {path}, found {n}"
        )
    pending[path] = text


def bump(root: Path, new: str, chart_version: str, run=subprocess.run) -> list[Path]:
    if _key(new) <= _key(version(root)):
        sys.exit(f"release: {new} is not greater than the current {version(root)}")
    _key(chart_version)
    pending: dict[Path, str] = {}
    for rel in PYPROJECTS:
        _sub_lines(pending, root / rel, r'^(version = )"[^"]*"$', rf'\1"{new}"', 1)
    chart = root / CHART
    _sub_lines(pending, chart, r'^(appVersion: )"[^"]*"$', rf'\1"{new}"', 1)
    _sub_lines(pending, chart, r"^(version: )\S+$", rf"\g<1>{chart_version}", 1)
    for rel in (HOOK, EXAMPLE_HOOK):
        _sub_lines(
            pending,
            root / rel,
            rf"^(\s*image: {re.escape(CONVERTER_IMAGE)})\S*.*$",
            rf"\1:v{new}",
            3,
        )
    for path, text in pending.items():
        path.write_text(text)
    run(["uv", "lock"], cwd=root, check=True)
    return [*pending, root / "uv.lock"]


def pin(
    root: Path,
    version: str,
    digests: dict[str, str],
    converter_ref: str,
    run=subprocess.run,
) -> list[Path]:
    """Pin the published images and the converter ref, then regenerate."""
    _key(version)
    if set(digests) != set(_REPOS):
        sys.exit(f"release: digests must be exactly {sorted(_REPOS)}")
    for name, digest in digests.items():
        if not _DIGEST.fullmatch(digest):
            sys.exit(f"release: {name} digest {digest!r} is not sha256:<64 hex>")
    if not _SHA.fullmatch(converter_ref):
        sys.exit(f"release: converter ref {converter_ref!r} is not a 40-hex SHA")
    pending: dict[Path, str] = {}
    for image, files in PINNED.items():
        repo = re.escape(_REPOS[image])
        for rel in files:
            tail = f"  # v{version}" if rel in _COMMENTED else ""
            count = 3 if rel == HOOK else 2 if (image, rel) == ("web", COMPOSE) else 1
            _sub_lines(
                pending,
                root / rel,
                rf"^(.*?{repo})(?:@sha256:[0-9a-f]{{64}}|:v\d[^\s}}]*)"
                r"(\}?)(?:  # v\S+)?$",
                rf"\g<1>@{digests[image]}\g<2>{tail}",
                count,
            )
    _sub_lines(
        pending,
        root / PINNED["web"][0],
        r"^(\s*# default is the published v)\d\S*( web image.*)$",
        rf"\g<1>{version}\g<2>",
        1,
    )
    for rel in CONVERTER_REF_FILES:
        _sub_lines(
            pending,
            root / rel,
            r"^(\s*CONVERTER_REF: )\S+.*$",
            rf"\g<1>{converter_ref}   # v{version}",
            1,
        )
    for path, text in pending.items():
        path.write_text(text)
    run(
        [
            "uv",
            "run",
            "--no-sync",
            "htrflow-campaigns",
            "init",
            "--force",
            "examples/campaigns",
        ],
        cwd=root,
        check=True,
    )
    run(
        ["uv", "run", "--no-sync", "python", "scripts/config_reference.py"],
        cwd=root,
        check=True,
    )
    return [*pending, *(root / g for g in GENERATED)]


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
    p = sub.add_parser("pin", help="pin the published images and the converter ref")
    p.add_argument("version")
    for name in _REPOS:
        p.add_argument(f"--{name}", required=True, help="sha256:<digest>")
    p.add_argument("--converter-ref", required=True, help="40-hex commit SHA")
    args = parser.parse_args(argv)
    if args.command == "state":
        print(state(ROOT, _tags(ROOT)))
    elif args.command == "pin":
        digests = {name: getattr(args, name) for name in _REPOS}
        for path in pin(ROOT, args.version, digests, args.converter_ref):
            print(path.relative_to(ROOT))
    else:
        for path in bump(ROOT, args.version, args.chart):
            print(path.relative_to(ROOT))


if __name__ == "__main__":
    main()
