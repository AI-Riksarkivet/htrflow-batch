"""``htrflow-campaigns`` CLI: validate/render/apply a campaigns repo (spec §3)."""

from __future__ import annotations

import argparse
import contextlib
import getpass
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

import yaml

from . import render
from .models import STATUS_SUFFIX, Campaign
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


def _existing_parts(campaigns_out: Path, c: Campaign) -> list[Path]:
    """Every file an earlier render of this campaign left in ``out``, in the
    order it wrote them. A campaign that splits renders under a name cut
    short of its own (see ``render.split_stem``), so the ``-partN`` files are
    looked up under that stem, not under the campaign's own name."""
    stem = render.split_stem(c.name)
    paths = sorted(campaigns_out.glob(f"{c.name}.yaml"))
    return paths + sorted(campaigns_out.glob(f"{stem}-part*.yaml"), key=_part_number)


def _colliding_names(campaigns: list[Campaign]) -> str | None:
    """Two campaigns that are the same up to the stem a split cuts them to
    render into the same files. Looked for across every campaign BEFORE any
    of them is held against an earlier render: the second one would
    otherwise find the first one's parts sitting in ``rendered/`` and be
    reported as append-only -- a change its author never made."""
    owners: dict[str, str] = {}
    for c in campaigns:
        for name in render.campaign_names(c, render.split(c.volumes)):
            other = owners.setdefault(name, c.name)
            if other != c.name:
                return (
                    f"campaigns/{other}.yaml and campaigns/{c.name}.yaml both "
                    f"render as {name}.yaml: a split cuts a campaign name to "
                    f"its first {len(render.split_stem(c.name))} characters, "
                    "and these two are the same up to there — rename one of them"
                )
    return None


def _shape(paths: list[Path]) -> str:
    """``kyrk.yaml``, or ``8 parts, kyrk-part1.yaml … kyrk-part8.yaml``."""
    if len(paths) == 1:
        return paths[0].name
    return f"{len(paths)} parts, {paths[0].name} … {paths[-1].name}"


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
    clash = _colliding_names(campaigns)
    if clash is not None:
        print(clash)
        return 1
    pipelines_out, campaigns_out = out / "pipelines", out / "campaigns"
    written: set[Path] = set()
    for p in pipelines.values():
        path = pipelines_out / f"{p.id}.yaml"
        _write(path, render.pipeline_objects(p, cfg))
        written.add(path)
    for c in campaigns:
        new_text = "\n".join(v.source_line() for v in c.volumes)
        existing = _existing_parts(campaigns_out, c)
        objects = render.campaign_objects(c, pipelines[c.pipeline], cfg)
        paths = [campaigns_out / f"{o['metadata']['name']}.yaml" for o in objects[1::2]]
        if existing:
            try:
                rendered_text = "\n".join(_volumes_txt(p) for p in existing)
            except _CorruptRenderedFile as e:
                print(str(e))
                return 1
            if rendered_text != new_text:
                print(f"campaign {c.name} is append-only: create a new campaign")
                return 1
            # Same volumes, different object names: the split rule itself
            # moved (a byte budget where there was only a count, one part
            # more, a shorter stem). Renaming them is not a re-render, it is
            # a delete and a restart -- `apply --prune` takes the Jobs that
            # already ran these volumes with it.
            if existing != paths:
                print(
                    f"campaign {c.name} was rendered as {_shape(existing)} and "
                    f"now renders as {_shape(paths)}: applying that would delete "
                    "the Jobs that have already run it and start every volume "
                    "over — create a new campaign instead"
                )
                return 1
        for path, i in zip(paths, range(0, len(objects), 2)):
            _write(path, objects[i : i + 2])
            written.add(path)
    _prune(pipelines_out, written)
    _prune(campaigns_out, written)
    return 0


#: Provenance keys on the campaign ConfigMap. The ConfigMap has no TTL and
#: is pruned only when the campaign file leaves git, so it -- not the Job --
#: is where the record of a campaign lives (B76, the product owner
#: 2026-09-08: the record is a ConfigMap, not a database).
_COMMIT_ANNOTATION = "htrflow.riksarkivet.se/campaigns-commit"
#: Who ran THIS apply -- not `htrflow.riksarkivet.se/submitter`, which the
#: multi-tenant design reserves (D10, B94) for a LABEL stamped at render
#: time by CI from an authenticated forge login. That is evidence; this is
#: the account the command happened to run under, which pairs with
#: `applied-at` and claims no more than it can prove.
_APPLIED_BY_ANNOTATION = "htrflow.riksarkivet.se/applied-by"
_APPLIED_AT_ANNOTATION = "htrflow.riksarkivet.se/applied-at"


def _git_head(repo: Path) -> str:
    """The campaigns repo's commit, or ``unknown`` outside a checkout (a
    tarball, a test's tmp_path) -- the record says so rather than guessing."""
    try:
        done = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return done.stdout.strip() if done.returncode == 0 else "unknown"


def _applied_by() -> str:
    """``HTRFLOW_APPLIED_BY`` (what CI sets from the actor that triggered
    it), else the OS user. Lower-cased: one person, one spelling."""
    name = os.environ.get("HTRFLOW_APPLIED_BY", "").strip()
    if not name:
        with contextlib.suppress(Exception):
            name = getpass.getuser()
    return (name or "unknown").lower()


def _provenance(repo: Path) -> dict[str, str]:
    """What this apply adds to every campaign ConfigMap it writes. Stamped
    here and not in ``render``: ``rendered/`` has to be a pure function of
    the repo (B78), and which commit, which person and which minute are
    not. The image digest, which *is* one, is rendered (``render.py``)."""
    return {
        _COMMIT_ANNOTATION: _git_head(repo),
        _APPLIED_BY_ANNOTATION: _applied_by(),
        _APPLIED_AT_ANNOTATION: datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
    }


#: A campaign in one of these phases is over -- the read API will write
#: nothing more about it (packages/web ``projection.FINISHED_PHASES``).
_FINISHED_PHASES = ("Succeeded", "Failed", "PartiallyFailed")


def _finished(cluster, name: str, volumes: str, observed: dict | None) -> str | None:
    """One sentence when this campaign is over and unchanged, else ``None``.

    The record outlives the Job (B76): past ``ttlSecondsAfterFinished``
    there is no Job left to compare against, and an apply that simply
    recreated it would re-run every volume and pay the whole GPU bill
    again -- resume makes that cheap, not right. The status ConfigMap the
    read API wrote -- or, when the Job outlived every visit to the status
    page, the one THIS apply just wrote from it (``observed``) -- says how
    the campaign ended, and the campaign ConfigMap says on which volumes.
    A volume list that has MOVED is not this
    function's business, it is the append-only rule's, which ``_render``
    already ran. There is deliberately no override flag: a campaign that
    should run again is a new campaign.
    """
    status = observed or cluster.get("ConfigMap", f"campaign-{name}{STATUS_SUFFIX}")
    data = (status or {}).get("data") or {}
    if data.get("phase") not in _FINISHED_PHASES:
        return None
    record = cluster.get("ConfigMap", f"campaign-{name}")
    if ((record or {}).get("data") or {}).get("volumes.txt") != volumes:
        return None
    when = (data.get("finishedAt") or "")[:10] or "earlier"
    done, total = data.get("volumesDone", "?"), data.get("volumesTotal", "?")
    return (
        f"campaign {name} finished {when}, unchanged, left alone "
        f"({done}/{total} volumes)"
    )


def _record_and_decide(cluster, cfg, name: str, volumes: str) -> str | None:
    """Write how this campaign ended, then say whether to leave it alone.

    One step, because the record this apply just wrote is what the decision
    reads -- and because both halves want the same answer when the cluster
    refuses them: one sentence, and the campaign applied as any other.
    """
    live = cluster.get("Job", name)
    record = render.status_configmap(live, cfg) if live else None
    if record is not None:
        cluster.apply(record)
    return _finished(cluster, name, volumes, record)


def _campaign_of(obj: dict) -> str:
    """Which campaign a rendered object belongs to: its Job is named after
    the campaign, its ConfigMap is that name with ``campaign-`` in front."""
    name = obj["metadata"]["name"]
    return name.removeprefix("campaign-") if obj["kind"] == "ConfigMap" else name


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
        # Imported here, not at module level, for the same reason `_cluster`
        # imports `.cluster` lazily: `validate`/`render` must never pay for
        # importing `kubernetes`.
        from .cluster import ClusterError

        try:
            cluster = _cluster(cfg.namespace)
            # (live object, declared pause) per campaign Job: the live one
            # has the uid Kueue labels the Workload with, the rendered one
            # has what git says. Warm-up Jobs are not campaigns and get no
            # pause sync.
            jobs: list[tuple[dict, bool]] = []
            prov = _provenance(repo)
            # Asked before anything is sent, so a finished campaign's
            # ConfigMap is not re-stamped with a new apply time either.
            # Observe before deciding. The read API writes this record in
            # more detail, but only while a person has the status page
            # open; a campaign that finished unwatched would otherwise
            # reach its TTL with no terminal record at all, and this apply
            # would recreate the Job and run every volume again.
            volumes_of = {
                _campaign_of(o): o["data"]["volumes.txt"]
                for o in campaigns
                if o["kind"] == "ConfigMap"
            }
            done: set[str] = set()
            for obj in campaigns:
                if obj["kind"] != "Job":
                    continue
                name = obj["metadata"]["name"]
                # Never a precondition for the apply. An identity whose Role
                # predates this needs a `get` on Jobs that nothing needed
                # before, and a human may be on a restricted kubeconfig --
                # refusing to apply anything over that would take the
                # campaigns repo offline for a permission it never had. Warn
                # once, and apply this campaign as any other.
                try:
                    said = _record_and_decide(cluster, cfg, name, volumes_of[name])
                except ClusterError as e:
                    print(
                        f"could not record how campaign {name} ended, "
                        f"continuing without it: {e}",
                        file=sys.stderr,
                    )
                    continue
                if said is not None:
                    done.add(name)
                    print(said)
            for objects, is_campaign in ((pipelines, False), (campaigns, True)):
                for obj in objects:
                    if is_campaign and _campaign_of(obj) in done:
                        continue
                    if is_campaign and obj["kind"] == "ConfigMap":
                        obj["metadata"].setdefault("annotations", {}).update(prov)
                    live = cluster.apply(obj)
                    print(f"applied: {obj['kind']}/{obj['metadata']['name']}")
                    if is_campaign and obj["kind"] == "Job":
                        jobs.append((live, obj["spec"].get("suspend", False)))
            if prune:
                # What makes deleting a campaign file cancel the campaign.
                # Both directories: see Cluster.prune.
                cluster.prune(
                    {(o["kind"], o["metadata"]["name"]) for o in pipelines + campaigns}
                )
            failed = 0
            for live, suspended in jobs:
                failed |= cluster.sync_pause(live, suspended, pause_wait)
            return failed
        except ClusterError as e:
            print(e, file=sys.stderr)
            return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="htrflow-campaigns")
    sub = parser.add_subparsers(dest="command", required=True)
    init_p = sub.add_parser("init", help="write a new campaigns repo from the template")
    init_p.add_argument("dir")
    init_p.add_argument(
        "--force", action="store_true", help="overwrite a non-empty directory"
    )
    validate_p = sub.add_parser(
        "validate",
        help="validate campaigns/ and pipelines/",
        description="Checks the shape of converter.yaml, campaigns/ and "
        "pipelines/ — including that every pipeline image is digest-pinned, "
        "which the renderer needs. NOT the cluster's policy: which "
        "registries an image may come from, and whether every model carries "
        "a revision, are Kyverno ClusterPolicies the htrflow-batch chart "
        "ships (security.allowedImageRepos, security.requireModelRevision). "
        "The Kyverno CLI runs them over rendered/ in this repo's CI.",
    )
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
