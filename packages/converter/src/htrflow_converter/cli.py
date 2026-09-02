"""``htrflow-campaigns`` CLI: validate/render a campaigns repo (spec §3)."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

from . import render
from .parse import ValidationError, load

_PART_RE = re.compile(r"-part(\d+)\.yaml\Z")


def _validate(repo_dir: str) -> int:
    repo = Path(repo_dir)
    try:
        load(repo / "campaigns", repo / "pipelines", repo / "converter.yaml")
    except ValidationError as e:
        for problem in e.problems:
            print(problem)
        return 1
    return 0


def _write(path: Path, docs: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump_all(docs, sort_keys=False))


class _CorruptRenderedFile(Exception):
    def __init__(self, path: Path, reason: object) -> None:
        super().__init__(f"{path}: cannot read existing campaign: {reason}")


def _volumes_txt(path: Path) -> str:
    try:
        docs = list(yaml.safe_load_all(path.read_text()))
        cm = next(
            d for d in docs if isinstance(d, dict) and d.get("kind") == "ConfigMap"
        )
        return cm["data"]["volumes.txt"].rstrip("\n")
    except (yaml.YAMLError, StopIteration, KeyError, TypeError) as e:
        raise _CorruptRenderedFile(path, e) from e


def _part_number(path: Path) -> int:
    m = _PART_RE.search(path.name)
    return int(m.group(1)) if m else 0


def _existing_campaign_text(campaigns_out: Path, name: str) -> str | None:
    paths = sorted(campaigns_out.glob(f"{name}.yaml"))
    paths += sorted(campaigns_out.glob(f"{name}-part*.yaml"), key=_part_number)
    if not paths:
        return None
    return "\n".join(_volumes_txt(p) for p in paths)


def _prune(out: Path, written: set[Path]) -> None:
    """Deleting a campaign (or pipeline) file must take its rendered manifest
    with it: that manifest is what an apply --prune / Argo CD compares the
    cluster against, so a leftover would keep resurrecting a cancelled Job.
    A shrinking `-partN` split is the same case."""
    for path in sorted([*out.glob("*.yaml"), *out.glob("*.yml")]):
        if path not in written:
            path.unlink()
            print(f"removed: {path}", file=sys.stderr)


def _unsafe_out(repo: Path, out: Path) -> str | None:
    """`--out` is a directory this command *deletes from*. Pointing it at the
    campaigns repo itself would delete the sources it just read."""
    out_r = out.resolve()
    for src in (repo.resolve(), *(repo / d for d in ("campaigns", "pipelines"))):
        if out_r == src.resolve() or src.resolve().is_relative_to(out_r):
            return f"--out {out} would delete {src}: render into a directory of its own"
    return None


def _render(repo_dir: str, out_dir: str) -> int:
    repo = Path(repo_dir)
    try:
        campaigns, pipelines, cfg = load(
            repo / "campaigns", repo / "pipelines", repo / "converter.yaml"
        )
    except ValidationError as e:
        for problem in e.problems:
            print(problem)
        return 1
    out = Path(out_dir)
    unsafe = _unsafe_out(repo, out)
    if unsafe is not None:
        print(unsafe, file=sys.stderr)
        return 1
    pipelines_out, campaigns_out = out / "pipelines", out / "campaigns"
    written: set[Path] = set()
    for p in pipelines.values():
        path = pipelines_out / f"{p.id}.yaml"
        _write(path, render.pipeline_objects(p, cfg))
        written.add(path)
    for c in campaigns:
        new_text = "\n".join(v.source_line() for v in c.volumes)
        try:
            existing = _existing_campaign_text(campaigns_out, c.name)
        except _CorruptRenderedFile as e:
            print(str(e))
            return 1
        if existing is not None and existing != new_text:
            print(f"campaign {c.name} is append-only: create a new campaign")
            return 1
        objects = render.campaign_objects(c, pipelines[c.pipeline], cfg)
        for i in range(0, len(objects), 2):
            job = objects[i + 1]
            path = campaigns_out / f"{job['metadata']['name']}.yaml"
            _write(path, objects[i : i + 2])
            written.add(path)
    _prune(pipelines_out, written)
    _prune(campaigns_out, written)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="htrflow-campaigns")
    sub = parser.add_subparsers(dest="command", required=True)
    validate_p = sub.add_parser("validate", help="validate campaigns/ and pipelines/")
    validate_p.add_argument("repo_dir")
    render_p = sub.add_parser("render", help="render ConfigMaps and Jobs")
    render_p.add_argument("repo_dir")
    render_p.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    if args.command == "render":
        return _render(args.repo_dir, args.out)
    return _validate(args.repo_dir)


if __name__ == "__main__":
    sys.exit(main())
