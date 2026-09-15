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
from .models import STATUS_SUFFIX, Campaign, parse_source_line
from .parse import ValidationError, load

_PART_RE = re.compile(r"-part(\d+)\.yaml\Z")

#: Where a campaigns repo keeps its committed render. The one source of
#: truth for the RECORD a re-render is held against: `render --out` says
#: where this render goes, and the repo's own `rendered/` is what the
#: previous one left (docs: reference/campaign-yaml.md).
RENDERED = "rendered"

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


#: A repo with no converter.yaml is not refused by ``parse.load`` -- as a
#: library, a missing file is every setting at its default. As a COMMAND it
#: has to be: the namespace, the queue, the S3 Secret and the model-cache PVC
#: all come from that file, so a repo without one silently applies one
#: cluster's campaigns to whatever ``htr-batch`` happens to be, and
#: ``--prune`` deletes what is already there.
_NO_CONVERTER_YAML = (
    "{path} is not there, and without it the namespace, the queue, the S3 "
    "Secret and the model-cache PVC would all be guessed at — copy the "
    "converter.yaml that htrflow-campaigns init writes and set it up for "
    "your cluster"
)


def _missing_config(repo: Path) -> str | None:
    return (
        None
        if (repo / "converter.yaml").is_file()
        else _NO_CONVERTER_YAML.format(path=repo / "converter.yaml")
    )


def _validate(repo_dir: str) -> int:
    repo = Path(repo_dir)
    missing = _missing_config(repo)
    if missing is not None:
        print(missing)
        return 1
    try:
        campaigns, pipelines, cfg = load(
            repo / "campaigns", repo / "pipelines", repo / "converter.yaml"
        )
    except ValidationError as e:
        return _report(e, "")
    # `rendered/` is committed, so a pull request has the previous render
    # right there to be held against -- no cluster, and no render of its own.
    edited = _edited_pipeline(campaigns, pipelines, cfg, repo / RENDERED)
    if edited is not None:
        print(edited)
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


#: A pipeline id is a permanent name for a recipe (D17): results are keyed
#: by it, and the Jobs that carry it are immutable once created. Editing a
#: pipeline a campaign already runs therefore reached the API server as
#: "field is immutable", halfway through an apply and after the pipeline
#: ConfigMap had already changed under the running campaign -- indexes that
#: had not started ran a different recipe from the ones that had.
_PIPELINE_CHANGED = (
    "pipeline {id} changed ({what}) but campaigns {campaigns} still run it — "
    "a pipeline is immutable while campaigns reference it; add a new pipeline "
    "file ({id}-2) and point new campaigns at it"
)


def _recorded_recipe(path: Path) -> dict[str, object]:
    """The recipe the previous render left in ``path``. ``rendered/`` is
    committed, so the previous render IS the record. Nothing when there is
    none to hold this render against -- a file too broken to parse included,
    since this render is about to overwrite it anyway."""
    if not path.is_file():
        return {}
    with contextlib.suppress(yaml.YAMLError):
        return render.recipe(
            [d for d in yaml.safe_load_all(path.read_text()) if isinstance(d, dict)]
        )
    return {}


def _edited_pipeline(campaigns, pipelines: dict, cfg, out: Path) -> str | None:
    """One sentence when a pipeline a live campaign runs has been edited.

    Live is: a campaign still in ``campaigns/`` that an earlier render
    already wrote to ``rendered/``. That one has been applied, its Job
    carries this recipe and its results are keyed by this pipeline id. A
    campaign whose file has been removed -- how a finished one is retired --
    holds nothing, and neither does one this render is writing for the first
    time.
    """
    for p in pipelines.values():
        before = _recorded_recipe(out / "pipelines" / f"{p.id}.yaml")
        after = render.recipe(render.pipeline_objects(p, cfg))
        moved = [k for k in sorted(after) if before and before.get(k) != after[k]]
        users = sorted(
            c.name
            for c in campaigns
            if c.pipeline == p.id and _existing_parts(out / "campaigns", c)
        )
        if moved and users:
            return _PIPELINE_CHANGED.format(
                id=p.id, what=", ".join(moved), campaigns=", ".join(users)
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


def _render(repo_dir: str, out_dir: str, record_dir: str | None = None) -> int:
    """Render ``repo_dir`` into ``out_dir``.

    ``record_dir`` is where the PREVIOUS render is, when that is not
    ``out_dir``: an ``apply`` with no ``--out`` renders into a temp directory
    that records nothing, and the repo's committed ``rendered/`` is still the
    record its pipelines and campaigns have to agree with.
    """
    repo = Path(repo_dir)
    missing = _missing_config(repo)
    if missing is not None:
        print(missing)
        return 1
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
    record = Path(record_dir) if record_dir else out
    edited = _edited_pipeline(campaigns, pipelines, cfg, record)
    if edited is not None:
        print(edited)
        return 1
    pipelines_out, campaigns_out = out / "pipelines", out / "campaigns"
    written: set[Path] = set()
    for p in pipelines.values():
        path = pipelines_out / f"{p.id}.yaml"
        _write(path, render.pipeline_objects(p, cfg))
        written.add(path)
    for c in campaigns:
        # Parsed, never byte-for-byte: a rendered file written before the
        # separator changed says the same thing with commas in it, and an
        # unchanged campaign must still re-render (models.parse_source_line).
        new_volumes = [parse_source_line(v.source_line()) for v in c.volumes]
        # Under the RECORD, never under `--out`: an apply with no --out
        # renders into a temp directory, where an earlier render of this
        # campaign cannot be, so both rules below found nothing to hold it
        # against and a swapped volume list went straight to the cluster.
        existing = _existing_parts(record / "campaigns", c)
        objects = render.campaign_objects(c, pipelines[c.pipeline], cfg)
        paths = [campaigns_out / f"{o['metadata']['name']}.yaml" for o in objects[1::2]]
        if existing:
            try:
                rendered_volumes = [
                    parse_source_line(line)
                    for p in existing
                    for line in _volumes_txt(p).splitlines()
                ]
            except _CorruptRenderedFile as e:
                print(str(e))
                return 1
            if rendered_volumes != new_volumes:
                print(f"campaign {c.name} is append-only: create a new campaign")
                return 1
            # Same volumes, different object names: the split rule itself
            # moved (a byte budget where there was only a count, one part
            # more, a shorter stem). Renaming them is not a re-render, it is
            # a delete and a restart -- `apply --prune` takes the Jobs that
            # already ran these volumes with it.
            if [p.name for p in existing] != [p.name for p in paths]:
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


def _volume_list(text: str) -> list[tuple]:
    """A ``volumes.txt`` read as what it means: per line, the volume id and
    its source, with an ``images:`` line's URLs as a list rather than as one
    string (``models.parse_source_line``). What makes two spellings of the
    same volume list compare equal."""
    return [parse_source_line(line) for line in text.splitlines() if line]


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

    The two lists are compared parsed, never byte for byte, for the reason
    ``_render``'s own check is: a campaign applied before the ``images:``
    separator changed has the comma line in its ConfigMap while this render
    writes the space line -- the same volumes, said twice. Byte for byte
    that reads as a changed campaign, and a finished one would be applied
    again, re-running every volume over a separator.
    """
    status = observed or cluster.get("ConfigMap", f"campaign-{name}{STATUS_SUFFIX}")
    data = (status or {}).get("data") or {}
    if data.get("phase") not in _FINISHED_PHASES:
        return None
    record = cluster.get("ConfigMap", f"campaign-{name}")
    stored = ((record or {}).get("data") or {}).get("volumes.txt") or ""
    if _volume_list(stored) != _volume_list(volumes):
        return None
    when = (data.get("finishedAt") or "")[:10] or "earlier"
    done, total = data.get("volumesDone", "?"), data.get("volumesTotal", "?")
    return (
        f"campaign {name} finished {when}, unchanged, left alone "
        f"({done}/{total} volumes)"
    )


_NO_RECORD = "could not record how campaign {name} ended, continuing without it: "


def _record_and_decide(cluster, cfg, name: str, volumes: str) -> str | None:
    """Write how this campaign ended, then say whether to leave it alone.

    One step, because the record this apply just wrote is what the decision
    reads. A refused WRITE is not a refused decision, though: whatever is
    already stored still says whether this campaign is over, and re-running
    a finished campaign costs the whole GPU bill over a permission the
    decision never needed. So the write is caught here and the stored
    record consulted anyway; only a refused READ (the caller's except)
    leaves the campaign to be applied as any other.
    """
    from .cluster import ClusterError  # lazy, like every other .cluster use

    live = cluster.get("Job", name)
    record = render.status_configmap(live, cfg) if live else None
    if record is not None:
        try:
            cluster.apply(record)
        except ClusterError as e:
            print(f"{_NO_RECORD.format(name=name)}{e}", file=sys.stderr)
            record = None
    return _finished(cluster, name, volumes, record)


def _campaign_of(obj: dict) -> str:
    """Which campaign a rendered object belongs to: its Job is named after
    the campaign, its ConfigMap is that name with ``campaign-`` in front."""
    name = obj["metadata"]["name"]
    return name.removeprefix("campaign-") if obj["kind"] == "ConfigMap" else name


_REPLACED = (
    "replaced: Job/{name} — its pod template changed, and a Job's template is "
    "fixed once it exists, so the Job was deleted and created again; the "
    "marker on the cache PVC survives, so the re-run is a file check"
)


def _apply_object(cluster, obj: dict, warmup: bool) -> dict:
    """Apply one rendered object, replacing a **warm-up** Job the API server
    refuses because its pod template changed.

    A Job's pod template is fixed when the Job is created. That is
    Kubernetes, not this tool, and it is met whenever the rendered template
    moves for a reason that is not the recipe: a converter release (B74 put
    the deadline on the pod and added ``runtimeClassName``), or a
    converter.yaml setting that reaches every warm-up at once (the Hub
    token's env var).

    For a warm-up Job that refusal is not a problem to report but one to
    solve. The Job is idempotent -- its completion marker sits on the cache
    PVC, so a re-run is a file check -- and it holds no state of its own, so
    deleting it and creating it again IS the same Job with the new template.
    A campaign Job is the opposite: its completed indexes and its results
    are the campaign, and deleting it would start every volume over. That
    one is reported and left exactly where it is, and a pipeline edit under
    a live campaign is refused earlier, by ``_render``.

    A warm-up that is running right now is left alone too: the delete would
    take the pod that is downloading with it, and every campaign waiting on
    its marker with it.
    """
    from .cluster import ClusterError, ImmutableField

    try:
        return cluster.apply(obj)
    except ImmutableField as e:
        if not warmup or ImmutableField.POD_TEMPLATE not in e.fields:
            raise
        name = obj["metadata"]["name"]
        live = cluster.get("Job", name)
        if ((live or {}).get("status") or {}).get("active"):
            raise ClusterError(
                f"{e} — this warm-up is running right now, so it was left "
                "alone; re-run the apply once it has finished"
            ) from e
        replaced = cluster.replace_job(obj)
        print(_REPLACED.format(name=name))
        return replaced


#: `apply` exited 1 for everything, so a CI job could not tell "nothing
#: reached the cluster" (no credentials, an unreachable API server, a render
#: that did not pass) from "all but one object is applied and the one is
#: named". They want different answers -- the first is a stop, the second a
#: change to make -- so the second has a code of its own.
REFUSED = 3
_REFUSED_SUMMARY = (
    "{n} of {total} objects were refused by the API server and are "
    "unchanged: {names} — the other {ok} were applied (exit {code})"
)


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


#: ``--prune`` deletes every converter-labelled object the render did not
#: produce, so a render that produced NOTHING cancels every campaign in the
#: namespace at once. That is a real thing to want -- deleting the last
#: campaign file is how the last campaign is retired -- and it is also what a
#: mistyped directory or a checkout that never happened looks like, so it has
#: to be said out loud.
_EMPTY_PRUNE = (
    "refusing --prune: nothing under {dir} rendered a campaign, so this would "
    "delete every campaign the converter manages in the namespace — check "
    "that this is the campaigns repo you meant, or pass --allow-empty to "
    "cancel them all on purpose"
)


def _apply(
    repo_dir: str,
    out_dir: str | None,
    prune: bool,
    pause_wait: int,
    dry_run: bool,
    allow_empty: bool = False,
) -> int:
    with contextlib.ExitStack() as stack:
        record_dir = None
        if out_dir is None:
            out_dir = stack.enter_context(tempfile.TemporaryDirectory("-htr-render"))
            record_dir = str(Path(repo_dir) / RENDERED)
        rc = _render(repo_dir, out_dir, record_dir)
        if rc:
            return rc
        repo, out = Path(repo_dir), Path(out_dir)
        # Pipelines first: a campaign's Job mounts its pipeline's ConfigMap
        # and waits on its warm-up Job's marker file.
        pipelines, campaigns = _objects(out / "pipelines"), _objects(out / "campaigns")
        if prune and not campaigns and not allow_empty:
            print(_EMPTY_PRUNE.format(dir=repo / "campaigns"), file=sys.stderr)
            return 1
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
                    print(f"{_NO_RECORD.format(name=name)}{e}", file=sys.stderr)
                    continue
                if said is not None:
                    done.add(name)
                    print(said)
            refused: list[str] = []
            applied = 0
            for objects, is_campaign in ((pipelines, False), (campaigns, True)):
                for obj in objects:
                    if is_campaign and _campaign_of(obj) in done:
                        continue
                    if is_campaign and obj["kind"] == "ConfigMap":
                        obj["metadata"].setdefault("annotations", {}).update(prov)
                    name = f"{obj['kind']}/{obj['metadata']['name']}"
                    # Per object, not per apply. One object the API server
                    # will not take used to abort the loop here, and every
                    # campaign behind it in the order was never applied at
                    # all -- a repo-wide outage over one changed Job.
                    try:
                        live = _apply_object(
                            cluster, obj, not is_campaign and obj["kind"] == "Job"
                        )
                    except ClusterError as e:
                        print(e, file=sys.stderr)
                        refused.append(name)
                        continue
                    applied += 1
                    print(f"applied: {name}")
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
            if refused:
                # Refused everything is not "some objects were refused", it
                # is the total failure the old code always reported: a Role
                # without apply, a webhook rejecting the lot. Same exit 1.
                # A pause that is not enforced is exit 1 too, and it outranks
                # a refused object: a campaign git says is paused that is
                # running anyway is burning GPU right now, while a refused
                # object is a change still to make.
                code = REFUSED if applied and not failed else 1
                print(
                    _REFUSED_SUMMARY.format(
                        n=len(refused),
                        total=applied + len(refused),
                        names=", ".join(refused),
                        ok=applied,
                        code=code,
                    ),
                    file=sys.stderr,
                )
                return code
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
        "--allow-empty",
        action="store_true",
        help="let --prune run on a render with no campaigns at all, which "
        "cancels every campaign in the namespace",
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
            args.repo_dir,
            args.out,
            args.prune,
            args.pause_wait,
            args.dry_run,
            args.allow_empty,
        )
    return _validate(args.repo_dir)


if __name__ == "__main__":
    sys.exit(main())
