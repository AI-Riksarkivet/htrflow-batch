"""``htrflow-campaigns`` CLI: validate/render/apply a campaigns repo (spec §3)."""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import shlex
import subprocess
import sys
import tempfile
import time
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


class _Kubectl:
    """``kubectl`` as a subprocess -- deliberately no Kubernetes client
    dependency: this command runs from CI images and from ``uvx``, where the
    only thing that can be assumed is the binary. Every command is echoed to
    stderr, so an apply is auditable from a CI log alone."""

    def __init__(self, binary: str, dry_run: bool = False) -> None:
        self.binary, self.dry_run = binary, dry_run

    def _echo(self, args: tuple[str, ...]) -> None:
        print(shlex.join([self.binary, *args]), file=sys.stderr)

    def run(self, *args: str) -> int:
        self._echo(args)
        if self.dry_run:
            return 0
        return subprocess.run([self.binary, *args]).returncode

    def read(self, *args: str) -> str:
        """stdout of a read-only query; empty when the object is absent."""
        self._echo(args)
        p = subprocess.run([self.binary, *args], capture_output=True, text=True)
        return p.stdout.strip() if p.returncode == 0 else ""


def _campaign_jobs(campaigns_out: Path) -> list[dict]:
    """The rendered campaign Jobs, in apply order. The pause intent is read
    from these dicts -- the same documents that were just applied."""
    jobs: list[dict] = []
    for path in sorted(campaigns_out.glob("*.yaml")):
        jobs += [
            d
            for d in yaml.safe_load_all(path.read_text())
            if isinstance(d, dict) and d.get("kind") == "Job"
        ]
    return jobs


def _workload(kubectl: _Kubectl, ns: str, job: str) -> str:
    """The Kueue Workload of ``job``, or empty. Kueue labels it with the
    Job's uid, which is the only link that survives a delete/recreate."""
    uid = kubectl.read("-n", ns, "get", "job", job, "-o", "jsonpath={.metadata.uid}")
    if not uid:
        return ""
    found = kubectl.read(
        "-n", ns, "get", "workload", "-l", f"kueue.x-k8s.io/job-uid={uid}", "-o", "name"
    )
    return found.splitlines()[0] if found else ""


def _pause_sync(kubectl: _Kubectl, campaigns_out: Path, pause_wait: int) -> int:
    """Put each campaign's declared pause on its Kueue Workload.

    ``suspend: true`` renders ``spec.suspend: true`` on the Job, but Kueue
    OWNS that field for a Workload it has admitted and flips it back within
    seconds -- the rendered field is intent, not enforcement. The lever that
    holds is ``spec.active`` on the Workload. Idempotent: a Workload that
    already agrees is left alone.

    A Workload appears a moment AFTER its Job, and for a campaign that is
    paused in git that moment is exactly the window in which Kueue would
    admit and start it -- so a paused campaign waits, and fails loudly if the
    Workload never turns up. A campaign that is NOT paused needs no wait: a
    Workload that does not exist is not admitted either, and the next apply
    catches it. See docs/development/e2e-indexed-jobs.md (Task 14) for why
    the render-time alternative -- dropping the queue-name label -- was not
    taken.
    """
    failed = 0
    for job in _campaign_jobs(campaigns_out):
        name = job["metadata"]["name"]
        ns = job["metadata"]["namespace"]
        want = not job["spec"].get("suspend", False)
        wl = _workload(kubectl, ns, name)
        if not wl and not want:
            for _ in range(pause_wait):
                time.sleep(1)
                wl = _workload(kubectl, ns, name)
                if wl:
                    break
        if not wl:
            if want:
                print(f"{name}: no Workload yet, skipping", file=sys.stderr)
                continue
            print(
                f"{name}: paused in git, but no Kueue Workload appeared within "
                f"{pause_wait}s — the pause is NOT enforced; re-run the apply",
                file=sys.stderr,
            )
            failed = 1
            continue
        current = kubectl.read("-n", ns, "get", wl, "-o", "jsonpath={.spec.active}")
        if (current or "true") == str(want).lower():
            continue
        print(f"{name}: {wl} active={str(want).lower()}", file=sys.stderr)
        patch = json.dumps({"spec": {"active": want}})
        failed |= min(
            kubectl.run("-n", ns, "patch", wl, "--type", "merge", "-p", patch), 1
        )
    return failed


def _apply(
    repo_dir: str,
    out_dir: str | None,
    prune: bool,
    binary: str,
    pause_wait: int,
    dry_run: bool,
) -> int:
    with contextlib.ExitStack() as stack:
        if out_dir is None:
            out_dir = stack.enter_context(tempfile.TemporaryDirectory("-htr-render"))
        rc = _render(repo_dir, out_dir)
        if rc:
            return rc
        out = Path(out_dir)
        pipelines, campaigns = str(out / "pipelines"), str(out / "campaigns")
        kubectl = _Kubectl(binary, dry_run)
        # Pipelines first: a campaign's Job mounts its pipeline's ConfigMap
        # and waits on its warm-up Job's marker file.
        if kubectl.run("apply", "-f", pipelines):
            return 1
        # --prune deletes every object carrying CAMPAIGN_SELECTOR that is not
        # in THIS apply -- which is what makes deleting a campaign file cancel
        # the campaign. It therefore has to see the pipelines too: pruning
        # against campaigns/ alone would delete the pipeline ConfigMaps and
        # warm-up Jobs the previous command just applied.
        second = ["apply", "-f", campaigns]
        if prune:
            second = [
                "apply",
                "--prune",
                "-l",
                render.CAMPAIGN_SELECTOR,
                "-f",
                pipelines,
                "-f",
                campaigns,
            ]
        if kubectl.run(*second):
            return 1
        if dry_run:
            # The pause sync's commands depend on what the cluster answers,
            # so there is nothing truthful to print for it here.
            print("(--dry-run: the Kueue pause sync was skipped)", file=sys.stderr)
            return 0
        return _pause_sync(kubectl, out / "campaigns", pause_wait)


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
    apply_p = sub.add_parser("apply", help="render, then kubectl apply (and prune)")
    apply_p.add_argument("repo_dir")
    apply_p.add_argument("--out", help="render here instead of a temp directory")
    apply_p.add_argument(
        "--prune",
        action="store_true",
        help="delete the objects a previous render left behind (opt-in: it "
        "deletes every converter-labelled object not in this apply)",
    )
    apply_p.add_argument("--kubectl", default="kubectl")
    apply_p.add_argument(
        "--pause-wait",
        type=int,
        default=10,
        help="seconds to wait for a new paused campaign's Kueue Workload",
    )
    apply_p.add_argument(
        "--dry-run", action="store_true", help="print the kubectl commands only"
    )
    args = parser.parse_args(argv)
    if args.command == "init":
        return _init(args.dir, args.force)
    if args.command == "render":
        return _render(args.repo_dir, args.out)
    if args.command == "apply":
        return _apply(
            args.repo_dir,
            args.out,
            args.prune,
            args.kubectl,
            args.pause_wait,
            args.dry_run,
        )
    return _validate(args.repo_dir)


if __name__ == "__main__":
    sys.exit(main())
