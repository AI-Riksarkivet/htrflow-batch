"""Campaign/pipeline YAML -> domain types, with validation (spec §3, docs/
superpowers/specs/2026-09-01-indexed-jobs-design.md). Ported (not imported —
that package is deleted in Task 6) from
``packages/reconciler/src/htrflow_reconciler/parse.py``. Every problem
across every file is collected before raising once, so fixing one file never
hides another's problem."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from pydantic import ValidationError as _PydanticValidationError

from .models import Campaign, ConverterConfig, Pipeline, Volume

# Volume ids reach Job names and ConfigMap lines: alphanumeric at both ends,
# no more than 63 chars.
_VOLUME_ID_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,61}[A-Za-z0-9])?\Z")

# Pipeline ids and campaign names become ConfigMap/Job names: DNS-1123
# labels (lowercase, no underscores).
_NAME_RE = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,61}[a-z0-9])?\Z")

_IMAGE_RE = re.compile(r"[a-z0-9./:-]+@sha256:[0-9a-f]{64}\Z")

_REVISION_RE = re.compile(r"[0-9a-f]{40}\Z")


def _repo_allowed(image: str, allowed: list[str]) -> bool:
    """Path-boundary prefix match: ``ghcr.io/riksarkivet`` admits
    ``ghcr.io/riksarkivet/x`` but not ``ghcr.io/riksarkivet-evil/x``."""
    repo = image.split("@", 1)[0]
    for entry in allowed:
        entry = entry.strip().rstrip("/")
        if not entry:
            continue
        if repo == entry or repo.startswith(entry + "/"):
            return True
    return False


def _check_revisions(pid: str, steps: list, problems: list[str]) -> None:
    """One problem per model missing a 40-hex revision (ported from the
    reconciler's ``_check_revisions``)."""
    for step in steps:
        if not isinstance(step, dict):
            continue
        settings = step.get("settings") or {}
        ms = settings.get("model_settings") if isinstance(settings, dict) else None
        model = ms.get("model") if isinstance(ms, dict) else None
        if not isinstance(ms, dict) or not model:
            continue
        rev = str(ms.get("revision") or "")
        if not _REVISION_RE.match(rev):
            problems.append(
                f"{pid}: model {model!r} needs a 40-hex revision "
                "(require_model_revision)"
            )


class ValidationError(Exception):
    """Every problem found while loading a campaigns repo, not just the first."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def _http_url(value: str, what: str) -> str | None:
    u = urlsplit(value)
    if u.scheme not in ("http", "https") or not u.netloc:
        return f"{what} must be an http(s) URL: {value!r}"
    return None


def _volume(
    entry: object, source_template: str, problems: list[str], where: str
) -> Volume | None:
    if isinstance(entry, str):
        if not _VOLUME_ID_RE.match(entry):
            problems.append(f"{where}: unsafe volume id: {entry!r}")
            return None
        return Volume(id=entry, manifest=source_template.format(ref=entry))
    if not isinstance(entry, dict):
        problems.append(f"{where}: volume entry needs an id: {entry!r}")
        return None
    if "id" not in entry:
        problems.append(f"{where}: volume entry needs an id: {entry!r}")
        return None
    vid = str(entry.get("id"))
    if not _VOLUME_ID_RE.match(vid):
        problems.append(f"{where}: unsafe volume id: {vid!r}")
        return None
    manifest = entry.get("manifest")
    images_raw = entry.get("images")
    images = images_raw if isinstance(images_raw, list) else []
    has_images = len(images) > 0
    if (manifest is not None) == has_images:
        problems.append(f"{where}: volume {vid!r} needs manifest or images")
        return None
    if manifest is not None:
        err = _http_url(str(manifest), f"{where}: volume {vid!r} manifest")
        if err:
            problems.append(err)
            return None
        return Volume(id=vid, manifest=str(manifest))
    urls: list[str] = []
    for u in images:
        err = _http_url(str(u), f"{where}: volume {vid!r} images")
        if err:
            problems.append(err)
            return None
        urls.append(str(u))
    return Volume(id=vid, images=urls)


def _parse_campaign(
    path: Path, source_template: str, problems: list[str]
) -> Campaign | None:
    name = path.stem
    if not _NAME_RE.match(name):
        problems.append(f"{name}: unsafe campaign name: {name!r}")
        return None
    try:
        doc = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        problems.append(f"{name}: bad campaign yaml: {e}")
        return None
    if not isinstance(doc, dict):
        problems.append(f"{name}: campaign file is not a mapping")
        return None
    pipeline_id = str(doc.get("pipeline") or "")
    if not pipeline_id:
        problems.append(f"{name}: campaign needs pipeline:")
        return None
    local: list[str] = []
    volumes: list[Volume] = []
    seen: set[str] = set()
    for entry in doc.get("volumes") or []:
        v = _volume(entry, source_template, local, name)
        if v is None:
            continue
        if v.id in seen:
            local.append(f"{name}: duplicate volume id: {v.id}")
            continue
        seen.add(v.id)
        volumes.append(v)
    if local:
        problems.extend(local)
        return None
    window_raw = doc.get("window")
    window: int | None = None
    if window_raw is not None:
        try:
            window = int(window_raw)
        except (TypeError, ValueError):
            window = None
        if window is None or window < 1:
            problems.append(
                f"{name}: window must be a positive integer: {window_raw!r}"
            )
            return None
    return Campaign(
        name=name,
        pipeline=pipeline_id,
        volumes=volumes,
        priority=str(doc.get("priority") or ""),
        window=window,
    )


def _parse_pipeline(
    path: Path, cfg: ConverterConfig, problems: list[str]
) -> Pipeline | None:
    pid = path.stem
    if not _NAME_RE.match(pid):
        problems.append(f"{pid}: unsafe pipeline id: {pid!r}")
        return None
    try:
        doc = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        problems.append(f"{pid}: bad pipeline yaml: {e}")
        return None
    if not isinstance(doc, dict):
        problems.append(f"{pid}: pipeline file is not a mapping")
        return None
    image = str(doc.get("image") or "")
    if not _IMAGE_RE.match(image):
        problems.append(f"{pid}: image must be digest-pinned: {image!r}")
        return None
    if cfg.allowed_image_repos and not _repo_allowed(image, cfg.allowed_image_repos):
        problems.append(f"{pid}: image {image!r} is not under an allowed repository")
        return None
    steps = doc.get("steps")
    if not isinstance(steps, list):
        problems.append(f"{pid}: missing steps")
        return None
    revision = str(doc.get("model_revision") or "")
    if revision and not _REVISION_RE.match(revision):
        problems.append(f"{pid}: model_revision must be 40 hex chars: {revision!r}")
        return None
    if cfg.require_model_revision:
        step_problems: list[str] = []
        _check_revisions(pid, steps, step_problems)
        if step_problems:
            problems.extend(step_problems)
            return None
    return Pipeline(id=pid, image=image, steps=steps, model_revision=revision)


def _load_config(path: Path, problems: list[str]) -> ConverterConfig:
    if not path.exists():
        return ConverterConfig()
    try:
        doc = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        problems.append(f"{path.name}: bad yaml: {e}")
        return ConverterConfig()
    if not isinstance(doc, dict):
        problems.append(f"{path.name}: converter config is not a mapping")
        return ConverterConfig()
    try:
        return ConverterConfig(**doc)
    except _PydanticValidationError as e:
        for err in e.errors():
            loc = ".".join(str(p) for p in err["loc"])
            problems.append(f"{path.name}: {loc}: {err['msg']}")
        return ConverterConfig()


def load(
    campaigns_dir: Path, pipelines_dir: Path, config_path: Path
) -> tuple[list[Campaign], dict[str, Pipeline], ConverterConfig]:
    """Load and validate a campaigns repo. Raises :class:`ValidationError`
    with every problem found, else returns (campaigns, pipelines by id, cfg)."""
    problems: list[str] = []
    cfg = _load_config(Path(config_path), problems)

    pipelines: dict[str, Pipeline] = {}
    for path in sorted(Path(pipelines_dir).glob("*.yaml")):
        p = _parse_pipeline(path, cfg, problems)
        if p is not None:
            pipelines[p.id] = p

    campaigns: list[Campaign] = []
    for path in sorted(Path(campaigns_dir).glob("*.yaml")):
        c = _parse_campaign(path, cfg.source_template, problems)
        if c is not None:
            campaigns.append(c)

    for c in campaigns:
        if c.pipeline not in pipelines:
            problems.append(f"{c.name}: unknown pipeline: {c.pipeline!r}")

    if problems:
        raise ValidationError(problems)
    return campaigns, pipelines, cfg
