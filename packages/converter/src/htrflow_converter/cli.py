"""``htrflow-campaigns`` CLI: validate/render/apply a campaigns repo (spec §3)."""

from __future__ import annotations

import argparse
import contextlib
import getpass
import hashlib
import os
import signal
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
from .record import (
    FAST_LOADER,
    RENDERED,
    CorruptRenderedFile,
    existing_parts,
    rendered,
    volumes_txt,
)

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


def _init(dir_: str, force: bool, ci: str = "github") -> int:
    dest = Path(dir_)
    if dest.exists():
        if not dest.is_dir():
            print(f"{dest} exists and is not a directory", file=sys.stderr)
            return 2
        if any(dest.iterdir()) and not force:
            print(f"{dest} is not empty: pass --force to overwrite it", file=sys.stderr)
            return 2
    # The repo itself, then the CI that renders it: GitHub Actions or Azure
    # Pipelines, one flavour per repo, so the template carries none.
    root = resources.files("htrflow_converter")
    _copy_tree(root / "template", dest)
    _copy_tree(root / "ci" / ci, dest)
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


#: What the Argo CD hook checks before it applies (audit 0923 S-9). It
#: clones the branch's HEAD, not the commit CI rendered -- a hook cannot
#: learn the Application's revision -- so a push that landed after CI's
#: render commit would otherwise be applied unrendered, and unchecked by
#: the campaigns repo's Policy job.
_NOT_RENDERED = (
    "{rendered} is not what this checkout renders: CI has not rendered this "
    "commit yet (its render commit starts another sync), or CI renders with "
    "a different converter release from this one (CONVERTER_REF against "
    "this image) — refusing, so that nothing CI did not render and check is "
    "applied"
)


@contextlib.contextmanager
def _quiet_stderr():
    with open(os.devnull, "w") as sink, contextlib.redirect_stderr(sink):
        yield


def _rendered_objects(out: Path) -> dict[str, list]:
    """Every rendered file under ``out``, parsed: what the render SAYS. Its
    bytes are not the point -- a checkout with CRLF line endings, or a
    PyYAML release that spells the same objects differently, says the same
    thing -- and neither is ``sync.yaml``, which is a digest of them."""
    return {
        p.relative_to(out).as_posix(): list(
            yaml.load_all(p.read_text(), Loader=FAST_LOADER)
        )
        for sub in _OWNED[:2]
        for p in sorted((out / sub).glob("*.yaml"))
    }


def _unrendered(repo: Path) -> str | None:
    """One sentence unless ``rendered/`` holds exactly this checkout's
    render."""
    committed = repo / RENDERED
    with tempfile.TemporaryDirectory(prefix="htr-check-") as t:
        # Its refusals to stderr; its warnings validate has already said.
        with contextlib.redirect_stdout(sys.stderr), _quiet_stderr():
            if _render(str(repo), str(Path(t) / RENDERED)):
                return _NOT_RENDERED.format(rendered=committed)
        fresh = _rendered_objects(Path(t) / RENDERED)
    try:
        same = _rendered_objects(committed) == fresh
    except (yaml.YAMLError, OSError):
        same = False
    return None if same else _NOT_RENDERED.format(rendered=committed)


def _load(repo: Path):
    """``parse.load`` of the repo, saying on stderr what a campaign kept as
    its earlier render recorded would be refused for if it were new."""
    warnings: list[str] = []
    try:
        return load(
            repo / "campaigns", repo / "pipelines", repo / "converter.yaml", warnings
        )
    finally:
        for warning in warnings:
            print(f"warning: {warning}", file=sys.stderr)


def _validate(repo_dir: str, rendered: bool = False) -> int:
    repo = Path(repo_dir)
    missing = _missing_config(repo)
    if missing is not None:
        print(missing)
        return 1
    try:
        campaigns, pipelines, cfg = _load(repo)
    except ValidationError as e:
        return _report(e, "")
    # `rendered/` is committed, so a pull request has the previous render
    # right there to be held against -- no cluster, and no render of its own.
    refused = _refused(campaigns, pipelines, cfg, repo / RENDERED)
    if refused is not None:
        print(refused)
        return 1
    _window_warnings(campaigns, cfg, repo / RENDERED)
    unrendered = _unrendered(repo) if rendered else None
    if unrendered is not None:
        print(unrendered)
        return 1
    return 0


def _write(path: Path, docs: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump_all(docs, sort_keys=False))


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


def _recorded_pipelines(paths: list[Path]) -> set[object]:
    """The pipeline an earlier render recorded on each of these campaign
    files' ConfigMaps. What the campaign's live Job runs, whatever its file
    says now: one moved to another pipeline in the same change as an edit
    to this one is still running this one (3084)."""
    ids: set[object] = set()
    for path in paths:
        with contextlib.suppress(yaml.YAMLError, OSError):
            for d in yaml.safe_load_all(path.read_text()):
                if isinstance(d, dict) and d.get("kind") == "ConfigMap":
                    ids.add(render.campaign_record(d)["pipeline"])
    return ids


def _edited_pipeline(campaigns, pipelines: dict, cfg, out: Path) -> str | None:
    """One sentence when a pipeline a live campaign runs has been edited.

    Live is: a campaign still in ``campaigns/`` that an earlier render
    already wrote to ``rendered/``. That one has been applied, its Job
    carries the recipe that render recorded and its results are keyed by
    that pipeline id. A campaign whose file has been removed -- how a
    finished one is retired -- holds nothing, and neither does one this
    render is writing for the first time.
    """
    parts = {c.name: existing_parts(out / "campaigns", c.name) for c in campaigns}
    recorded = {c.name: _recorded_pipelines(parts[c.name]) for c in campaigns}
    for p in pipelines.values():
        before = _recorded_recipe(out / "pipelines" / f"{p.id}.yaml")
        after = render.recipe(render.pipeline_objects(p, cfg))
        moved = [k for k in sorted(after) if before and before.get(k) != after[k]]
        users = sorted(
            c.name
            for c in campaigns
            if parts[c.name]
            and (c.pipeline == p.id or render.label_value(p.id) in recorded[c.name])
        )
        if moved and users:
            return _PIPELINE_CHANGED.format(
                id=p.id, what=", ".join(moved), campaigns=", ".join(users)
            )
    return None


#: Kueue compares a running Job's pod count with its admitted Workload's and,
#: when they differ, suspends the Job -- every running pod stopped --
#: deletes the Workload and queues the campaign again (jobframework
#: ``ensureOneWorkload``, "No matching Workload"; Kueue v0.19). A Workload
#: holding no quota, a paused campaign's, is updated in place instead
#: (audit 0923 C-11). Whether the campaign is still running is the
#: cluster's to say: offline this is a warning, and the apply, which sees
#: the Job, holds a running one to its count.
_WINDOW_MOVED = (
    "campaign {name} runs {before} pods at a time and would now run {after}: "
    "if it is still running, Kueue stops every running pod of a Job whose "
    "parallelism changes and queues the campaign again — to change it safely, "
    "pause it first (suspend: true), change the window once the pause is "
    "applied, then resume it"
)


def _moved_window(c: Campaign, record: Path, cfg) -> str | None:
    """A warning when a rendered campaign's pod count moves. Held against the
    record part by part: each Job there is what was last applied. A part the
    record paused runs no pods, so its count may move without one; so may a
    campaign whose parts are not the ones recorded (the append-only rules
    report that)."""
    existing = existing_parts(record / "campaigns", c.name)
    for path, volumes in zip(existing, render.split(c.volumes)):
        try:
            job = rendered(path, "Job")
        except CorruptRenderedFile:
            return None  # the append-only check has said so
        before = _pod_count(job)
        paused = bool((job.get("spec") or {}).get("suspend"))
        after = min(render.parallelism(c, cfg), len(volumes))
        if before != after and not paused:
            return _WINDOW_MOVED.format(name=c.name, before=before, after=after)
    return None


def _window_warnings(campaigns, cfg, record: Path) -> None:
    for c in campaigns:
        if (said := _moved_window(c, record, cfg)) is not None:
            print(f"warning: {said}", file=sys.stderr)


def _moved_campaign(c: Campaign, record: Path) -> str | None:
    """One sentence when ``c`` no longer renders as the record says it did."""
    existing = existing_parts(record / "campaigns", c.name)
    if not existing:
        return None
    # Parsed, never byte-for-byte: a rendered file written before the
    # separator changed says the same thing with commas in it, and an
    # unchanged campaign must still re-render (models.parse_source_line).
    try:
        before = [
            parse_source_line(line)
            for p in existing
            for line in volumes_txt(p).splitlines()
        ]
    except CorruptRenderedFile as e:
        return str(e)
    if before != [parse_source_line(v.source_line()) for v in c.volumes]:
        return f"campaign {c.name} is append-only: create a new campaign"
    # Same volumes, different object names: the split rule itself moved (a
    # byte budget where there was only a count, one part more, a shorter
    # stem). Renaming them is not a re-render, it is a delete and a restart
    # -- `apply --prune` takes the Jobs that already ran these volumes with it.
    names = render.campaign_names(c, render.split(c.volumes))
    paths = [record / "campaigns" / f"{n}.yaml" for n in names]
    if [p.name for p in existing] != [p.name for p in paths]:
        return (
            f"campaign {c.name} was rendered as {_shape(existing)} and now "
            f"renders as {_shape(paths)}: applying that would delete the Jobs "
            "that have already run it and start every volume over — create a "
            "new campaign instead"
        )
    return None


def _refused(campaigns, pipelines: dict, cfg, record: Path) -> str | None:
    """Every rule a repo is held to beyond its own files' shape, in one
    place: ``validate`` (the pull-request gate) and ``render`` (on main) call
    this, and nothing else. When only ``render`` held a rule, a change went
    green in review and then stopped every render on main (3086)."""
    for problem in (
        *(render.last_pod_problem(c) for c in campaigns),
        _colliding_names(campaigns),
        _edited_pipeline(campaigns, pipelines, cfg, record),
        *(_moved_campaign(c, record) for c in campaigns),
    ):
        if problem is not None:
            return problem
    return None


def _shape(paths: list[Path]) -> str:
    """``kyrk.yaml``, or ``8 parts, kyrk-part1.yaml … kyrk-part8.yaml``."""
    if len(paths) == 1:
        return paths[0].name
    return f"{len(paths)} parts, {paths[0].name} … {paths[-1].name}"


#: What a render owns in ``--out``. It replaces these whole, so a file
#: nothing rendered this time -- a deleted campaign's, a split that shrank
#: -- goes with them: a leftover would be applied again, and would keep a
#: cancelled Job alive through every ``apply --prune``. Anything else in
#: ``--out`` is left alone.
_OWNED = ("pipelines", "campaigns", "sync.yaml")


def _swap_in(new: Path, out: Path, old: Path) -> None:
    """Move the render in ``new`` into ``out``, the one before it to ``old``.

    Renames only, inside one directory's filesystem: every rule and every
    write happened before this, so a refused or crashed render never
    reaches ``out`` at all (3089)."""
    for sub in _OWNED[:2]:
        kept = {p.name for p in (new / sub).iterdir()}
        for path in sorted((out / sub).glob("*.y*ml")):
            if path.name not in kept:
                print(f"removed: {path}", file=sys.stderr)
    out.mkdir(exist_ok=True)
    for name in _OWNED:
        if (out / name).exists():
            (out / name).rename(old / name)
        (new / name).rename(out / name)


def _unsafe_out(repo: Path, out: Path) -> str | None:
    """`--out` is a directory this command *deletes from*. Pointing it at the
    campaigns repo itself would delete the sources it just read."""
    out_r = out.resolve()
    for src in (repo.resolve(), *(repo / d for d in ("campaigns", "pipelines"))):
        if out_r == src.resolve() or src.resolve().is_relative_to(out_r):
            return f"--out {out} would delete {src}: render into a directory of its own"
    return None


def _render(repo_dir: str, out_dir: str) -> int:
    """Render ``repo_dir`` into ``out_dir``.

    ``--out`` says where this render is WRITTEN. What it is held against is
    the repo's own committed ``rendered/``, always: that is the record of
    what has been applied (B78), and a render directory that is empty --
    ``apply``'s temp directory, a fresh ``--out`` an operator reached for
    when a render looked wrong -- is not evidence that a campaign has never
    run.
    """
    repo = Path(repo_dir)
    missing = _missing_config(repo)
    if missing is not None:
        print(missing)
        return 1
    try:
        campaigns, pipelines, cfg = _load(repo)
    except ValidationError as e:
        return _report(e, " — nothing was rendered")
    out = Path(out_dir)
    unsafe = _unsafe_out(repo, out)
    if unsafe is not None:
        print(unsafe, file=sys.stderr)
        return 1
    # Held against the RECORD, never against `--out`: an apply with no --out
    # renders into a temp directory, where an earlier render of a campaign
    # cannot be, so the rules found nothing to hold it against and a swapped
    # volume list went straight to the cluster.
    refused = _refused(campaigns, pipelines, cfg, repo / RENDERED)
    if refused is not None:
        print(refused)
        return 1
    _window_warnings(campaigns, cfg, repo / RENDERED)
    # Written in full beside `--out` first (same filesystem, so the swap is
    # renames), and swapped in only once the whole render exists.
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{out.name}-", dir=out.parent) as t:
        new, old = Path(t) / "new", Path(t) / "old"
        for sub in _OWNED[:2]:
            (new / sub).mkdir(parents=True)
        old.mkdir()
        written: set[Path] = set()
        for p in pipelines.values():
            path = new / "pipelines" / f"{p.id}.yaml"
            _write(path, render.pipeline_objects(p, cfg))
            written.add(path)
        for c in campaigns:
            objects = render.campaign_objects(c, pipelines[c.pipeline], cfg)
            for i in range(0, len(objects), 2):
                path = new / "campaigns" / f"{objects[i + 1]['metadata']['name']}.yaml"
                _write(path, objects[i : i + 2])
                written.add(path)
        digest = hashlib.sha256()
        for path in sorted(written):
            digest.update(
                f"{path.relative_to(new).as_posix()}\0".encode() + path.read_bytes()
            )
        _write(new / "sync.yaml", [render.sync_configmap(cfg, digest.hexdigest())])
        _swap_in(new, out, old)
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
    tarball, a test's tmp_path) -- the record says so rather than guessing.
    Where there is no git binary, dulwich reads it (``_dulwich_head``)."""
    try:
        done = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except OSError:
        return _dulwich_head(repo)
    except subprocess.SubprocessError:
        return "unknown"
    return done.stdout.strip() if done.returncode == 0 else "unknown"


def _dulwich_head(repo: Path) -> str:
    """HEAD by dulwich, the pure-Python git the Argo CD hook's clone already
    uses (the ``hook`` extra). Its image is distroless, with no git binary,
    so without this every campaign the hook applied -- the production path
    -- recorded its commit as ``unknown`` (audit 0923 C-10)."""
    try:
        from dulwich.errors import NotGitRepository
        from dulwich.repo import Repo
    except ImportError:
        return "unknown"
    try:
        with Repo.discover(str(repo)) as found:
            return found.head().decode()
    except (NotGitRepository, KeyError, OSError):  # KeyError: no commit yet
        return "unknown"


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
    A volume list that has MOVED is not this function's business, it is
    the append-only rule's, which ``_render`` and ``_moved_live`` already
    ran. There is deliberately no override flag: a campaign that should run
    again is a new campaign.

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
    if observed is None and not _believed(name, record, data):
        return None
    when = (data.get("finishedAt") or "")[:10] or "earlier"
    done, total = data.get("volumesDone", "?"), data.get("volumesTotal", "?")
    return (
        f"campaign {name} finished {when}, unchanged, left alone "
        f"({done}/{total} volumes)"
    )


_NO_RECORD = "could not record how campaign {name} ended, continuing without it: "
#: Whether a campaign has finished is what keeps a reaped one from running
#: again, so a read that fails is a check not made, never one passed (3093).
_UNREAD = (
    "could not tell whether campaign {name} has finished, so it was left as it was: {e}"
)
#: A campaign is rendered as a pair: the ConfigMap that carries its
#: volumes.txt and the Job that mounts it. A directory where the pair has
#: come apart -- a half-finished write, a hand edit, a bad merge of
#: `rendered/` -- used to raise a bare KeyError on the Job's name, from
#: inside the loop that was about to apply it.
_INCOMPLETE_RENDER = (
    "{dir} has a Job for campaign {name} but no ConfigMap carrying its "
    "volumes.txt, so this render is incomplete — re-render the repo and "
    "apply that"
)


#: Which Job the apply made for a campaign, on the campaign ConfigMap (S-11).
#: A status record whose Job is gone can be about some other run: an earlier
#: Job under the same name, or none at all when the ConfigMap went out and
#: its Job never did. It is believed only when it names the Job recorded
#: here. Not a trust boundary: whatever can read that Job's uid and write
#: the record (the read API's ServiceAccount can do both) can still name
#: it. ``""``: the Job has not been created. A ConfigMap without the key was
#: applied before it existed, and its record is believed as it always was.
_JOB_UID = "htrflow.riksarkivet.se/job-uid"
_UNBELIEVED = (
    "campaign {name}: its status record says {phase} of Job {theirs}, not the "
    "Job this apply created ({ours}), so it is not believed and the campaign "
    "is applied"
)
_NO_UID = "could not record which Job campaign {name} runs, continuing without it: "


def _uid(job: dict | None) -> str:
    return ((job or {}).get("metadata") or {}).get("uid", "")


def _believed(name: str, record: dict | None, data: dict) -> bool:
    ours = ((record or {}).get("metadata") or {}).get("annotations") or {}
    if _JOB_UID not in ours:
        return True
    if ours[_JOB_UID] and data.get("jobUid") == ours[_JOB_UID]:
        return True
    print(
        _UNBELIEVED.format(
            name=name,
            phase=data.get("phase"),
            theirs=data.get("jobUid") or "(none)",
            ours=ours[_JOB_UID] or "none yet",
        ),
        file=sys.stderr,
    )
    return False


def _restamp(cluster, record: dict | None, live: dict) -> None:
    """Put the uid of the Job just applied on its campaign ConfigMap, when
    the ConfigMap went out before that Job existed. A failure costs a
    re-run once the Job is reaped, never a record believed wrongly."""
    from .cluster import ClusterError, Unreachable

    if record is None or record["metadata"]["annotations"].get(_JOB_UID) == _uid(live):
        return
    record["metadata"]["annotations"][_JOB_UID] = _uid(live)
    try:
        cluster.apply(record)
    except Unreachable:
        raise
    except ClusterError as e:
        print(f"{_NO_UID.format(name=_campaign_of(record))}{e}", file=sys.stderr)


def _repair_stamp(cluster, name: str, job: dict | None) -> None:
    """A finished campaign is left alone, so its ConfigMap is not re-applied
    -- and a uid ``_restamp`` failed to write (a refused write, a killed
    apply) would stay missing, and the record would not be believed once
    the Job is reaped: the whole campaign run again. While the Job is still
    there, the live ConfigMap is sent back as it is, with the uid added."""
    from .cluster import ClusterError, Unreachable

    if job is None:
        return
    try:
        live = cluster.get("ConfigMap", f"campaign-{name}")
    except Unreachable:
        raise
    except ClusterError as e:
        print(f"{_NO_UID.format(name=name)}{e}", file=sys.stderr)
        return
    if live is None:
        return
    meta = live["metadata"]
    keep = {k: meta.get(k) or {} for k in ("labels", "annotations")}
    body = {"apiVersion": "v1", "kind": "ConfigMap", "data": live.get("data") or {},
            "metadata": {"name": meta["name"], "namespace": meta.get("namespace"),
                         **keep}}  # fmt: skip
    _restamp(cluster, body, job)


def _record_and_decide(
    cluster, cfg, name: str, volumes: str
) -> tuple[str | None, dict | None]:
    """Write how this campaign ended, then say whether to leave it alone --
    and hand back the live Job it read, which the apply needs again.

    A live Job is the truth about its campaign, and outranks any stored
    record: a ``Succeeded`` record beside a Job that is still running is
    left over from something else -- a reused name, an Argo CD prune that
    never tracks ``-status``, a Job re-created by hand -- and trusting it
    left a running campaign alone and its pause unenforced (3083). Only
    when there is no Job does the stored record speak, which is the case it
    exists for (B76). A refused WRITE is not a refused decision: the record
    derived from the Job still decides, and re-running a finished campaign
    costs the whole GPU bill over a permission the decision never needed.
    """
    from .cluster import ClusterError, Unreachable

    live = cluster.get("Job", name)
    if live is None:
        return _finished(cluster, name, volumes, None), None
    record = render.status_configmap(live, cfg)
    if record is None:
        return None, live
    try:
        cluster.apply(record)
    except Unreachable:
        raise
    except ClusterError as e:
        print(f"{_NO_RECORD.format(name=name)}{e}", file=sys.stderr)
    return _finished(cluster, name, volumes, record), live


#: `rendered/` is the record a RENDER is held against, and it can be absent,
#: fresh or behind the cluster: a checkout whose render was never committed,
#: an `--out` somewhere new. The live campaign ConfigMap is the record the
#: cluster keeps, so apply holds every campaign against it as well, before
#: anything is sent (3084).
_LIVE_MOVED = (
    "campaign {name} is in the cluster with different {what}: a campaign is "
    "append-only and runs one pipeline, so create a new campaign instead — "
    "nothing was applied"
)
_KEPT_PAIR = "{name}: left as it was, since Job/{job} was refused"
#: `validate` holds a window change against `rendered/`, which can lag the
#: cluster. Kueue v0.19 (``ensureOneWorkload``) stops every pod of an
#: admitted Job whose pod count no longer matches its Workload and queues it
#: again; a suspended Job's Workload is updated in place.
_LIVE_WINDOW = (
    "campaign {name} runs {before} pods at a time and would now run {after}: "
    "Kueue stops every running pod of an admitted Job whose parallelism "
    "changes and queues the campaign again — put its window back (the "
    "campaign's window:, or the window cap in converter.yaml), or pause the "
    "campaign first (suspend: true), change the window once it is paused, "
    "then resume it — nothing was applied"
)
#: The pipeline half of the same backstop (C-7). A pipeline ConfigMap is
#: mutable, and a live campaign Job mounts it by name: an edited recipe
#: applied under one runs every index not yet started on different steps,
#: under the same results id.
_LIVE_RECIPE = (
    "pipeline {id} is in the cluster with different steps and campaigns "
    "{jobs} still run it: a pipeline id is a permanent name for a recipe, so "
    "add a new pipeline file instead — nothing was applied"
)


def _running(cluster) -> list[dict]:
    """The live campaign Jobs that have not ended: warm-ups are not
    campaigns, and a Job that has ended runs nothing more."""
    return [
        j
        for j in cluster.labelled("Job")
        if not j["metadata"]["name"].startswith(render.WARMUP_PREFIX)
        and not _condition(j, "Complete", "Failed")
    ]


def _moved_live(
    cluster, pipelines: list[dict], campaigns: list[dict], running: list[dict]
) -> str | None:
    """One sentence when a rendered campaign disagrees with its live record,
    or a rendered recipe with the live one a running campaign mounts.

    A key the live object does not carry (a record written before the key
    existed) is not held against. A refused READ is not caught here: a
    check that cannot be made is not a check that passed.
    """
    moved = _moved_recipe(cluster, pipelines, running)
    if moved is not None:
        return moved
    live_jobs = {j["metadata"]["name"]: j for j in running}
    for obj in campaigns:
        live_job = live_jobs.get(obj["metadata"]["name"])
        if obj["kind"] == "Job" and live_job is not None:
            before, after = _pod_count(live_job), _pod_count(obj)
            if before != after and not live_job["spec"].get("suspend"):
                return _LIVE_WINDOW.format(
                    name=obj["metadata"]["name"], before=before, after=after
                )
        if obj["kind"] != "ConfigMap":
            continue
        live = cluster.get("ConfigMap", obj["metadata"]["name"])
        if live is None:
            continue
        before, after = render.campaign_record(live), render.campaign_record(obj)
        moved = [k for k, v in before.items() if v is not None and v != after[k]]
        if moved:
            return _LIVE_MOVED.format(name=_campaign_of(obj), what=", ".join(moved))
    return None


#: Results are keyed ``<pipeline>/<volume>/``: two campaigns running one
#: volume on one pipeline race on its progress and manifest objects (C-13).
#: `validate` cannot see what is running, and a new campaign on the same
#: pipeline is how a failed volume is run again -- once the old one is over.
_SHARED = (
    "campaign {name} shares {volumes} with {others} on pipeline {pipeline}, "
    "still running or starting in this apply: both would write the same "
    "results — let it finish first, or take the volumes out of one of them"
)
_UNCHECKED = (
    "could not tell whether campaign {name} shares a volume with a running "
    "campaign, so it was left as it was: {e}"
)


def _claim_volumes(cluster, campaigns, volumes_of, done, blocked, running) -> None:
    """Block each campaign Job that would run a volume another campaign on
    its pipeline runs: a live one that has not ended, or one this apply is
    about to start (the first in the render keeps the volume)."""
    from .cluster import ClusterError, Unreachable

    claimed: dict[str, dict[str, set[str]]] = {}  # pipeline -> campaign -> ids

    def ids(text: str) -> set[str]:
        return {vid for vid, _ in _volume_list(text)}

    try:
        for job in running:
            labels = job["metadata"].get("labels") or {}
            cm = cluster.get("ConfigMap", f"campaign-{job['metadata']['name']}")
            owner = claimed.setdefault(labels.get(render._PIPELINE_LABEL, ""), {})
            owner.setdefault(labels.get(render.CAMPAIGN_LABEL, ""), set()).update(
                ids(((cm or {}).get("data") or {}).get("volumes.txt") or "")
            )
    except Unreachable:
        raise
    except ClusterError as e:
        for obj in campaigns:
            if obj["kind"] == "Job" and _campaign_of(obj) not in done:
                blocked.setdefault(
                    _campaign_of(obj),
                    ClusterError(_UNCHECKED.format(name=obj["metadata"]["name"], e=e)),
                )
        return
    for obj in campaigns:
        name = _campaign_of(obj)
        if obj["kind"] != "Job" or name in done or name in blocked:
            continue
        labels = obj["metadata"]["labels"]
        pipeline = labels.get(render._PIPELINE_LABEL, "")
        campaign = labels.get(render.CAMPAIGN_LABEL, "")
        owners = claimed.setdefault(pipeline, {})
        mine = ids(volumes_of[name])
        others = {c: mine & v for c, v in owners.items() if c != campaign and mine & v}
        if not others:
            owners.setdefault(campaign, set()).update(mine)
            continue
        shared = sorted(set().union(*others.values()))
        listed = ", ".join(shared[:3]) + (
            f" and {len(shared) - 3} more" if len(shared) > 3 else ""
        )
        blocked[name] = ClusterError(
            _SHARED.format(
                name=name,
                volumes=("volume " if len(shared) == 1 else "volumes ") + listed,
                others=("campaign " if len(others) == 1 else "campaigns ")
                + ", ".join(sorted(others)),
                pipeline=pipeline,
            )
        )


def _pod_count(job: dict) -> int:
    """How many pods a campaign Job runs at once, as Kueue counts them for
    its Workload: ``min(parallelism, completions)``."""
    spec = job.get("spec") or {}
    parallelism = spec.get("parallelism", 1)
    return min(parallelism, spec.get("completions", parallelism))


def _moved_recipe(cluster, pipelines: list[dict], running: list[dict]) -> str | None:
    """The render's own rule -- an id is held while a campaign runs it --
    held against the cluster: which campaign Jobs that have not ended still
    mount each pipeline ConfigMap is read off ``running``. The steps are
    compared parsed, as ``render.recipe`` reads them, so a YAML spelling
    change is not an edit."""
    rendered = [o for o in pipelines if o["kind"] == "ConfigMap"]
    users: dict[str, list[str]] = {}
    for job in running if rendered else []:
        name = job["metadata"]["name"]
        pod = ((job.get("spec") or {}).get("template") or {}).get("spec") or {}
        for volume in pod.get("volumes") or []:
            mounted = (volume.get("configMap") or {}).get("name")
            if mounted:
                users.setdefault(mounted, []).append(name)
    for obj in rendered:
        cm = obj["metadata"]["name"]
        live = cluster.get("ConfigMap", cm) if cm in users else None
        if live is not None and (
            render.recipe([live])["steps"] != render.recipe([obj])["steps"]
        ):
            return _LIVE_RECIPE.format(
                id=cm.removeprefix("htr-pipeline-"), jobs=", ".join(sorted(users[cm]))
            )
    return None


def _campaign_of(obj: dict) -> str:
    """Which campaign a rendered object belongs to: its Job is named after
    the campaign, its ConfigMap is that name with ``campaign-`` in front."""
    name = obj["metadata"]["name"]
    return name.removeprefix("campaign-") if obj["kind"] == "ConfigMap" else name


_REPLACED = (
    "replaced: Job/{name} — its pod template changed, and a Job's template is "
    "fixed once it exists, so the Job was deleted and created again; a recipe "
    "already warmed keeps its marker on the cache PVC and the re-run is a file "
    "check, while a changed recipe downloads into a cache directory of its own"
)


#: A warm-up that spent its backoffLimit (a Secret not there yet, a Hub
#: outage) stays Failed, and an unchanged apply is a no-op on it: every
#: campaign on the pipeline fails each index on the missing marker, for ever
#: (3092). Nothing but its marker is state, so the apply runs it again.
_RETRIED = (
    "replaced: Job/{name} — it had failed, and a failed Job never runs "
    "again, so it was deleted and created again to retry the warm-up"
)


def _condition(job: dict, *types: str) -> bool:
    conditions = (job.get("status") or {}).get("conditions") or []
    return any(c.get("type") in types and c.get("status") == "True" for c in conditions)


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
    its marker with it. One that has FAILED is replaced even when the apply
    went through, since the API server will never run it again.
    """
    from .cluster import ClusterError, ImmutableField

    try:
        live = cluster.apply(obj)
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
    if warmup and _condition(live, "Failed"):
        live = cluster.replace_job(obj)
        print(_RETRIED.format(name=obj["metadata"]["name"]))
    return live


def _before_resume(cluster, obj: dict, live: dict | None) -> None:
    """Keep a campaign Job suspended through the apply that resumes it.

    A campaign paused before Kueue ever admitted it -- a full queue, the
    case a queue exists for -- has ``spec.suspend: true`` owned by this
    tool's field manager alone. The resuming render drops the field, the
    server-side apply releases it, and the API server puts the default back:
    ``false``, a Job the Job controller starts at once, with no admission
    and no quota, until Kueue's reconciler stops it again mid-volume. Handed
    to a manager of its own first, the field stays ``true`` until Kueue
    admits the Workload the pause sync reactivates, and flips it itself.
    """
    if live is not None and not obj["spec"].get("suspend"):
        cluster.hold_suspend(live)


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
#: A pause the sync could not reach: the refused Job's live one could not be
#: read (no uid to find its Workload by), or the Workload patch failed. Git
#: says stopped, the Job may be running, and nothing in this apply is going
#: to stop it. Exit 1, like a Workload that never appeared.
_UNSYNCED_PAUSE = (
    "{name}: paused in git, but its Kueue Workload was not deactivated, so the "
    "pause is NOT enforced; fix what the error above says and re-run the apply"
)
#: Not objects this apply renders, so not in the refused count (C-9).
_UNSYNCED = (
    "the pause sync did not reach the Kueue Workload of {names}; see above "
    "(exit {code})"
)

#: The API server stopped answering part-way through: what came before is
#: applied, the object it stopped on may or may not be, and nothing after it
#: was sent. Not "refused and unchanged" -- that would be a guess (3091).
_STOPPED = (
    "{e} — the apply stopped at {name}: everything before it was applied, "
    "it may or may not have been, and nothing after it was sent; re-run the "
    "apply"
)


def _terminated(signum: int, frame: object) -> None:
    raise SystemExit(128 + signum)


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


#: The repo names its namespace, and every policy the chart ships matches
#: the release namespace alone: from a kubeconfig with wider rights than the
#: chart's Role, a repo naming another namespace would put Jobs where none
#: applies (S-7). ``--namespace`` is the caller saying which one it means.
_OTHER_NAMESPACE = (
    "{path} says namespace: {ns}, but this apply was run with --namespace "
    "{want} — nothing was applied; apply this repo where it says, or fix its "
    "converter.yaml"
)


def _apply(
    repo_dir: str,
    out_dir: str | None,
    prune: bool,
    pause_wait: int,
    dry_run: bool,
    allow_empty: bool = False,
    namespace: str | None = None,
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
        empty_prune = prune and not campaigns and not allow_empty
        # The namespace comes from converter.yaml, not from the rendered
        # objects: a repo whose last campaign was deleted renders nothing at
        # all, which is exactly when --prune has work to do. (_render just
        # loaded this, so it cannot fail here.)
        cfg = load(repo / "campaigns", repo / "pipelines", repo / "converter.yaml")[2]
        if namespace is not None and namespace != cfg.namespace:
            print(
                _OTHER_NAMESPACE.format(
                    path=repo / "converter.yaml", ns=cfg.namespace, want=namespace
                ),
                file=sys.stderr,
            )
            return 1
        if dry_run:
            for obj in pipelines + campaigns:
                print(f"would apply: {obj['kind']}/{obj['metadata']['name']}")
            if prune:
                print(
                    f"would prune: every {render.CAMPAIGN_SELECTOR} Job/ConfigMap "
                    "in the namespace that is not listed above"
                )
            # Printed AFTER the preview, not instead of it: an empty prune is
            # the one an operator most wants to see the shape of first.
            if empty_prune:
                print(_EMPTY_PRUNE.format(dir=repo / "campaigns"), file=sys.stderr)
            print("(--dry-run: nothing was sent to the API server)")
            return 0
        if empty_prune:
            print(_EMPTY_PRUNE.format(dir=repo / "campaigns"), file=sys.stderr)
            return 1
        # Imported here, not at module level, for the same reason `_cluster`
        # imports `.cluster` lazily: `validate`/`render` must never pay for
        # importing `kubernetes`.
        from .cluster import ClusterError, LeaseLost, Unreachable

        try:
            # SIGTERM -- how Argo CD stops a hook -- would end the process
            # without a single `finally`, and leave the Lease held for its
            # whole duration: the next hook fails on it. Turned into an exit
            # that unwinds, with the code the shell gives it.
            with contextlib.suppress(ValueError):  # not the main thread
                previous = signal.signal(signal.SIGTERM, _terminated)
                stack.callback(signal.signal, signal.SIGTERM, previous)
            cluster = _cluster(cfg.namespace)
            # One apply at a time, for the whole run: released when this
            # function returns, however it returns (C-12).
            stack.enter_context(cluster.lease())
            # (live object, declared pause) per campaign Job: the live one
            # has the uid Kueue labels the Workload with, the rendered one
            # has what git says. Warm-up Jobs are not campaigns and get no
            # pause sync.
            jobs: list[tuple[dict, bool]] = []
            running = _running(cluster)
            moved = _moved_live(cluster, pipelines, campaigns, running)
            if moved is not None:
                print(moved, file=sys.stderr)
                return 1
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
            # The live campaign Jobs, as read before anything was sent, and
            # the campaign ConfigMaps as this apply sent them.
            lives: dict[str, dict | None] = {}
            records: dict[str, dict] = {}
            # Campaigns whose Job must not be sent, with the sentence why.
            blocked: dict[str, ClusterError] = {}
            for obj in campaigns:
                if obj["kind"] != "Job":
                    continue
                name = obj["metadata"]["name"]
                # This Job's own ConfigMap is missing from the render: not a
                # permission or a cluster problem but a broken directory, and
                # applying a Job whose volumes.txt is not there would start a
                # campaign that cannot read its own work list. Nothing has
                # been sent yet, so stop here.
                if name not in volumes_of:
                    print(
                        _INCOMPLETE_RENDER.format(name=name, dir=out / "campaigns"),
                        file=sys.stderr,
                    )
                    return 1
                # A read that fails skips this campaign, not the apply: the
                # other campaigns and the pipelines still go out, and the
                # summary names the pair left as it was.
                try:
                    said, lives[name] = _record_and_decide(
                        cluster, cfg, name, volumes_of[name]
                    )
                except Unreachable:
                    raise
                except ClusterError as e:
                    blocked[name] = ClusterError(_UNREAD.format(name=name, e=e))
                    continue
                if said is not None:
                    done.add(name)
                    print(said)
                    _repair_stamp(cluster, name, lives[name])
            _claim_volumes(cluster, campaigns, volumes_of, done, blocked, running)
            # Each campaign Job is tried with dryRun=All before its pair is
            # sent: a ConfigMap applied under a Job the API server then
            # refuses is a volumes.txt the Job's unstarted indexes read (3084).
            for obj in campaigns:
                campaign = _campaign_of(obj)
                if obj["kind"] != "Job" or campaign in done or campaign in blocked:
                    continue
                try:
                    cluster.apply(obj, dry_run=True)
                except Unreachable:
                    raise
                except ClusterError as e:
                    blocked[campaign] = e
            refused: list[str] = []
            # Campaign Jobs the API server refused (or this apply could not
            # look at), which the pause sync still reaches -- see below.
            held: list[dict] = []
            unsynced: list[str] = []
            applied = failed = 0
            for objects, is_campaign in ((pipelines, False), (campaigns, True)):
                for obj in objects:
                    campaign = _campaign_of(obj) if is_campaign else None
                    if campaign in done:
                        continue
                    if is_campaign and obj["kind"] == "ConfigMap":
                        ann = obj["metadata"].setdefault("annotations", {})
                        ann.update(prov)
                        ann[_JOB_UID] = _uid(lives.get(campaign))
                        records[_campaign_of(obj)] = obj
                    name = f"{obj['kind']}/{obj['metadata']['name']}"
                    if campaign in blocked and obj["kind"] == "ConfigMap":
                        print(
                            _KEPT_PAIR.format(name=name, job=campaign), file=sys.stderr
                        )
                        refused.append(name)
                        continue
                    # Per object, not per apply. One object the API server
                    # will not take used to abort the loop here, and every
                    # campaign behind it in the order was never applied at
                    # all -- a repo-wide outage over one changed Job.
                    try:
                        if campaign in blocked:
                            raise blocked[campaign]
                        if is_campaign and obj["kind"] == "Job":
                            _before_resume(cluster, obj, lives.get(campaign))
                        live = _apply_object(
                            cluster, obj, not is_campaign and obj["kind"] == "Job"
                        )
                    except LeaseLost:
                        raise
                    except Unreachable as e:
                        raise Unreachable(_STOPPED.format(e=e, name=name)) from e
                    except ClusterError as e:
                        print(e, file=sys.stderr)
                        refused.append(name)
                        if is_campaign and obj["kind"] == "Job":
                            held.append(obj)
                        continue
                    applied += 1
                    print(f"applied: {name}")
                    if is_campaign and obj["kind"] == "Job":
                        jobs.append((live, obj["spec"].get("suspend", False)))
                        _restamp(cluster, records.get(campaign), live)
            # The pause first: it is the one step whose absence burns GPU
            # right now, and a prune problem used to end the apply before it
            # ran for any campaign (3090).
            # A refused Job is still a live Job with a Workload, and pausing
            # needs nothing else (C-2). A converter release or a
            # converter.yaml change refuses every live campaign Job at once
            # (its pod template is fixed), and a campaign git pauses then
            # must stop all the same -- or resume, since a paused one could
            # otherwise never finish, which is what the refusal asks for.
            for obj in held:
                job, suspended = (
                    obj["metadata"]["name"],
                    obj["spec"].get("suspend", False),
                )
                try:
                    live = cluster.get("Job", job)
                except Unreachable:
                    raise
                except ClusterError as e:
                    print(e, file=sys.stderr)
                    unsynced.append(f"Job/{job}")
                    if suspended:
                        print(_UNSYNCED_PAUSE.format(name=job), file=sys.stderr)
                        failed = 1
                    continue
                # No Job: nothing runs, so a pause holds and an unpause has
                # nothing to start.
                if live is not None:
                    jobs.append((live, suspended))
            # One Workload's problem is one campaign's (C-9): a Workload
            # deleted between the list and the patch, a patch the Role does
            # not allow. It used to leave through the outer handler, with
            # every later pause unsynced and the prune never run.
            for live, suspended in jobs:
                try:
                    failed |= cluster.sync_pause(live, suspended, pause_wait)
                except Unreachable:
                    raise
                except ClusterError as e:
                    job = live["metadata"]["name"]
                    print(e, file=sys.stderr)
                    unsynced.append(f"Job/{job}")
                    if suspended:
                        print(_UNSYNCED_PAUSE.format(name=job), file=sys.stderr)
                        failed = 1
            if prune:
                # What makes deleting a campaign file cancel the campaign.
                # Both directories: see Cluster.prune.
                for what, problem in cluster.prune(
                    {(o["kind"], o["metadata"]["name"]) for o in pipelines + campaigns}
                ):
                    print(problem, file=sys.stderr)
                    refused.append(what)
            if not (refused or unsynced):
                return failed
            # Refused everything is not "some objects were refused", it is
            # the total failure the old code always reported: a Role without
            # apply, a webhook rejecting the lot. Same exit 1. A pause that
            # is not enforced is exit 1 too, and it outranks a refused
            # object: a campaign git says is paused that is running anyway is
            # burning GPU right now, while a refused object is a change still
            # to make.
            code = 1 if failed or (refused and not applied) else REFUSED
            if unsynced:
                names = ", ".join(unsynced)
                print(_UNSYNCED.format(names=names, code=code), file=sys.stderr)
            if refused:
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
    init_p.add_argument(
        "--ci",
        choices=["github", "azure"],
        default="github",
        help="which CI the repo gets: GitHub Actions or Azure Pipelines",
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
    validate_p.add_argument(
        "--rendered",
        action="store_true",
        help="also refuse unless rendered/ is exactly what this checkout "
        "renders: what the Argo CD hook checks before it applies",
    )
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
        "--namespace",
        help="the namespace this apply is meant for; refused unless the "
        "repo's converter.yaml names the same one",
    )
    apply_p.add_argument(
        "--dry-run",
        action="store_true",
        help="render and print what would be applied, without a cluster",
    )
    args = parser.parse_args(argv)
    if args.command == "init":
        return _init(args.dir, args.force, args.ci)
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
            args.namespace,
        )
    return _validate(args.repo_dir, args.rendered)


if __name__ == "__main__":
    sys.exit(main())
