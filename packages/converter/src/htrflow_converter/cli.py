"""``htrflow-campaigns`` CLI: validate/render/apply a campaigns repo (spec §3)."""

from __future__ import annotations

import argparse
import contextlib
import re
import sys
import tempfile
from importlib import resources
from pathlib import Path

import yaml

from . import render
from .parse import ValidationError, load

_PART_RE = re.compile(r"-part(\d+)\.yaml\Z")

_NEXT_STEPS = """\
Your campaigns repo is ready at {dir}.

Next steps:
  1. Open converter.yaml and set it up for your cluster (namespace, queue,
     which image registries are allowed, and so on).
  2. Replace the demo pipeline and campaign with your own, or edit them in
     place to get started.
  3. Check your work: htrflow-campaigns validate {dir}
  4. Commit and push. Every pull request gets checked automatically, and a
     change on the main branch is turned into the files a cluster applies.
     See this repo's README.md for what "pausing" and "deleting" a running
     campaign mean.
"""


def _copy_tree(src, dst: Path) -> None:
    """Copy an ``importlib.resources`` traversable directory onto a real
    filesystem path. ``shutil.copytree`` cannot take a traversable -- the
    template ships inside the installed wheel, not as a plain directory."""
    dst.mkdir(parents=True, exist_ok=True)
    for entry in src.iterdir():
        target = dst / entry.name
        if entry.is_dir():
            _copy_tree(entry, target)
        else:
            target.write_bytes(entry.read_bytes())


def _init(dir_: str, force: bool) -> int:
    dest = Path(dir_)
    if dest.exists():
        if not dest.is_dir():
            print(f"{dest} exists and is not a directory", file=sys.stderr)
            return 2
        if any(dest.iterdir()) and not force:
            print(f"{dest} is not empty: pass --force to overwrite it", file=sys.stderr)
            return 2
    template = resources.files("htrflow_converter") / "template"
    _copy_tree(template, dest)
    print(_NEXT_STEPS.format(dir=dir_))
    return 0


def _report(e: ValidationError, tail: str) -> int:
    """The problems, then a closing line that counts them. The count is the
    part a reader acts on first: one problem in one file is a typo, nine in
    four files is a bad merge, and either way nothing downstream ran."""
    for problem in e.problems:
        print(problem)
    print(e.summary + tail)
    return 1


def _validate(repo_dir: str) -> int:
    repo = Path(repo_dir)
    try:
        load(repo / "campaigns", repo / "pipelines", repo / "converter.yaml")
    except ValidationError as e:
        return _report(e, "")
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
        return _report(e, " — nothing was rendered")
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


def _cluster(namespace: str):
    """The API-server adapter, behind a function: `validate` and `render`
    never touch a cluster (nor pay the client's import), and a test swaps
    the whole cluster out here."""
    from .cluster import Cluster

    return Cluster(namespace)


def _objects(dir_: Path) -> list[dict]:
    """Every rendered document under ``dir_``, in file order."""
    objects: list[dict] = []
    for path in sorted(dir_.glob("*.yaml")):
        objects += [
            d for d in yaml.safe_load_all(path.read_text()) if isinstance(d, dict)
        ]
    return objects


def _apply(
    repo_dir: str, out_dir: str | None, prune: bool, pause_wait: int, dry_run: bool
) -> int:
    with contextlib.ExitStack() as stack:
        if out_dir is None:
            out_dir = stack.enter_context(tempfile.TemporaryDirectory("-htr-render"))
        rc = _render(repo_dir, out_dir)
        if rc:
            return rc
        repo, out = Path(repo_dir), Path(out_dir)
        # Pipelines first: a campaign's Job mounts its pipeline's ConfigMap
        # and waits on its warm-up Job's marker file.
        pipelines, campaigns = _objects(out / "pipelines"), _objects(out / "campaigns")
        if dry_run:
            for obj in pipelines + campaigns:
                print(f"would apply: {obj['kind']}/{obj['metadata']['name']}")
            if prune:
                print(
                    f"would prune: every {render.CAMPAIGN_SELECTOR} Job/ConfigMap "
                    "in the namespace that is not listed above"
                )
            print("(--dry-run: nothing was sent to the API server)")
            return 0
        # The namespace comes from converter.yaml, not from the rendered
        # objects: a repo whose last campaign was deleted renders nothing at
        # all, which is exactly when --prune has work to do. (_render just
        # loaded this, so it cannot fail here.)
        cfg = load(repo / "campaigns", repo / "pipelines", repo / "converter.yaml")[2]
        cluster = _cluster(cfg.namespace)
        # (live object, declared pause) per campaign Job: the live one has the
        # uid Kueue labels the Workload with, the rendered one has what git
        # says. Warm-up Jobs are not campaigns and get no pause sync.
        jobs: list[tuple[dict, bool]] = []
        for objects, is_campaign in ((pipelines, False), (campaigns, True)):
            for obj in objects:
                live = cluster.apply(obj)
                print(f"applied: {obj['kind']}/{obj['metadata']['name']}")
                if is_campaign and obj["kind"] == "Job":
                    jobs.append((live, obj["spec"].get("suspend", False)))
        if prune:
            # What makes deleting a campaign file cancel the campaign. Both
            # directories: see Cluster.prune.
            cluster.prune(
                {(o["kind"], o["metadata"]["name"]) for o in pipelines + campaigns}
            )
        failed = 0
        for live, suspended in jobs:
            failed |= cluster.sync_pause(live, suspended, pause_wait)
        return failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="htrflow-campaigns")
    sub = parser.add_subparsers(dest="command", required=True)
    init_p = sub.add_parser("init", help="write a new campaigns repo from the template")
    init_p.add_argument("dir")
    init_p.add_argument(
        "--force", action="store_true", help="overwrite a non-empty directory"
    )
    validate_p = sub.add_parser("validate", help="validate campaigns/ and pipelines/")
    validate_p.add_argument("repo_dir")
    render_p = sub.add_parser("render", help="render ConfigMaps and Jobs")
    render_p.add_argument("repo_dir")
    render_p.add_argument("--out", required=True)
    apply_p = sub.add_parser("apply", help="render, then apply (and prune)")
    apply_p.add_argument("repo_dir")
    apply_p.add_argument("--out", help="render here instead of a temp directory")
    apply_p.add_argument(
        "--prune",
        action="store_true",
        help="delete the objects a previous render left behind (opt-in: it "
        "deletes every converter-labelled object not in this apply)",
    )
    apply_p.add_argument(
        "--pause-wait",
        type=int,
        default=10,
        help="seconds to wait for a new paused campaign's Kueue Workload",
    )
    apply_p.add_argument(
        "--dry-run",
        action="store_true",
        help="render and print what would be applied, without a cluster",
    )
    args = parser.parse_args(argv)
    if args.command == "init":
        return _init(args.dir, args.force)
    if args.command == "render":
        return _render(args.repo_dir, args.out)
    if args.command == "apply":
        return _apply(
            args.repo_dir, args.out, args.prune, args.pause_wait, args.dry_run
        )
    return _validate(args.repo_dir)


if __name__ == "__main__":
    sys.exit(main())
