"""Campaign/pipeline YAML -> domain types, with validation (spec §3)."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from pydantic import ValidationError as _PydanticValidationError

from .models import Campaign, ConverterConfig, Pipeline, Volume

_VOLUME_ID_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,61}[A-Za-z0-9])?\Z")
_NAME_RE = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,61}[a-z0-9])?\Z")
_IMAGE_RE = re.compile(r"[a-z0-9./:-]+@sha256:[0-9a-f]{64}\Z")
_REVISION_RE = re.compile(r"[0-9a-f]{40}\Z")


class ValidationError(Exception):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def _fail(problems: list[str], msg: str) -> None:
    problems.append(msg)


def _positive_int(v: object) -> bool:
    # `bool` is an `int` in Python: `window: true` is a typo, not a window of 1.
    return isinstance(v, int) and not isinstance(v, bool) and v >= 1


def _safe_name(stem: str, problems: list[str], what: str) -> str | None:
    if not _NAME_RE.match(stem):
        return _fail(problems, f"{stem}: unsafe {what}: {stem!r}")
    return stem


def _read_yaml_mapping(path: Path, problems: list[str], what: str) -> dict | None:
    try:
        doc = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        return _fail(problems, f"{path.stem}: bad {what} yaml: {e}")
    if not isinstance(doc, dict):
        return _fail(problems, f"{path.stem}: {what} file is not a mapping")
    return doc


def _repo_allowed(image: str, allowed: list[str]) -> bool:
    repo = image.split("@", 1)[0]
    return any(
        repo == e or repo.startswith(e + "/")
        for e in (a.strip().rstrip("/") for a in allowed)
        if e
    )


def _check_revisions(pid: str, steps: list, problems: list[str]) -> None:
    for step in steps:
        if not isinstance(step, dict):
            continue
        settings = step.get("settings") or {}
        ms = settings.get("model_settings") if isinstance(settings, dict) else None
        model = ms.get("model") if isinstance(ms, dict) else None
        if not isinstance(ms, dict) or not model:
            continue
        if not _REVISION_RE.match(str(ms.get("revision") or "")):
            problems.append(
                f"{pid}: model {model!r} needs a 40-hex revision "
                "(require_model_revision)"
            )


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
            return _fail(problems, f"{where}: unsafe volume id: {entry!r}")
        return Volume(id=entry, manifest=source_template.format(ref=entry))
    if not isinstance(entry, dict) or "id" not in entry:
        return _fail(problems, f"{where}: volume entry needs an id: {entry!r}")
    vid = str(entry.get("id"))
    if not _VOLUME_ID_RE.match(vid):
        return _fail(problems, f"{where}: unsafe volume id: {vid!r}")
    manifest = entry.get("manifest")
    images_raw = entry.get("images")
    images = images_raw if isinstance(images_raw, list) else []
    if (manifest is not None) == bool(images):
        return _fail(problems, f"{where}: volume {vid!r} needs manifest or images")
    if manifest is not None:
        err = _http_url(str(manifest), f"{where}: volume {vid!r} manifest")
        return _fail(problems, err) if err else Volume(id=vid, manifest=str(manifest))
    urls: list[str] = []
    for u in images:
        err = _http_url(str(u), f"{where}: volume {vid!r} images")
        if err:
            return _fail(problems, err)
        urls.append(str(u))
    return Volume(id=vid, images=urls)


def _parse_campaign(
    path: Path, source_template: str, problems: list[str]
) -> Campaign | None:
    name = _safe_name(path.stem, problems, "campaign name")
    if name is None:
        return None
    doc = _read_yaml_mapping(path, problems, "campaign")
    if doc is None:
        return None
    pipeline_id = str(doc.get("pipeline") or "")
    if not pipeline_id:
        return _fail(problems, f"{name}: campaign needs pipeline:")
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
    window = doc.get("window")
    if window is not None and not _positive_int(window):
        return _fail(problems, f"{name}: window must be a positive integer: {window!r}")
    return Campaign(
        name=name,
        pipeline=pipeline_id,
        volumes=volumes,
        priority=str(doc.get("priority") or ""),
        window=window,
        suspend=bool(doc.get("suspend")),
    )


def _parse_pipeline(
    path: Path, cfg: ConverterConfig, problems: list[str]
) -> Pipeline | None:
    pid = _safe_name(path.stem, problems, "pipeline id")
    if pid is None:
        return None
    doc = _read_yaml_mapping(path, problems, "pipeline")
    if doc is None:
        return None
    image = str(doc.get("image") or "")
    if not _IMAGE_RE.match(image):
        return _fail(problems, f"{pid}: image must be digest-pinned: {image!r}")
    if cfg.allowed_image_repos and not _repo_allowed(image, cfg.allowed_image_repos):
        return _fail(
            problems, f"{pid}: image {image!r} is not under an allowed repository"
        )
    steps = doc.get("steps")
    if not isinstance(steps, list):
        return _fail(problems, f"{pid}: missing steps")
    revision = str(doc.get("model_revision") or "")
    if revision and not _REVISION_RE.match(revision):
        return _fail(
            problems, f"{pid}: model_revision must be 40 hex chars: {revision!r}"
        )
    if cfg.require_model_revision:
        step_problems: list[str] = []
        _check_revisions(pid, steps, step_problems)
        if step_problems:
            problems.extend(step_problems)
            return None
    ms = doc.get("max_seconds")
    if ms is not None and not _positive_int(ms):
        return _fail(problems, f"{pid}: max_seconds must be a positive integer: {ms!r}")
    return Pipeline(
        id=pid, image=image, steps=steps, model_revision=revision, max_seconds=ms
    )


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
