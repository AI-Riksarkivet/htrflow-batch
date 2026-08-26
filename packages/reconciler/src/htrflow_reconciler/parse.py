"""Campaign/pipeline YAML -> domain types. Campaign problems are contained
(Campaign.error); pipeline problems raise (a broken pipeline must block
submission, spec §3)."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from urllib.parse import urlsplit

import yaml

from .models import Campaign, PipelineSpec, Volume

RA_MANIFEST_TEMPLATE = "https://lbiiif.riksarkivet.se/arkis!{ref}/manifest"

# Volume ids reach Job names and label values: alphanumeric at both ends, no
# more than 63 chars. ``\Z`` (never ``$``) so a trailing newline cannot sneak
# past — ``$`` matches just before one.
_ID_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,61}[A-Za-z0-9])?\Z")

# Pipeline ids additionally become ConfigMap names (``htr-pipeline-<id>``),
# which are DNS-1123 labels: lowercase, no underscores.
_PIPELINE_ID_RE = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,61}[a-z0-9])?\Z")


# A model revision must be a full commit sha: a branch name or tag can be
# moved under the pipeline id (audit S1).
_REVISION_RE = re.compile(r"[0-9a-f]{40}\Z")


class PipelineError(ValueError):
    pass


def _http_url(value: object, what: str) -> str:
    """Only http(s) sources reach the pre-validation fetch, the wrapper and the
    browser's href/src (audit S4/S5)."""
    url = str(value)
    u = urlsplit(url)
    if u.scheme not in ("http", "https") or not u.netloc:
        raise ValueError(f"{what} must be an http(s) URL: {url!r}")
    return url


def _volume(entry: object) -> Volume:
    if isinstance(entry, str):
        if not _ID_RE.match(entry):
            raise ValueError(f"unsafe volume id: {entry!r}")
        return Volume(id=entry, manifest_url=RA_MANIFEST_TEMPLATE.format(ref=entry))
    if not isinstance(entry, dict):
        raise ValueError(f"volume entry needs an id: {entry!r}")
    fields: dict[str, object] = {str(k): v for k, v in entry.items()}
    if "id" not in fields:
        raise ValueError(f"volume entry needs an id: {entry!r}")
    vid = str(fields["id"])
    if not _ID_RE.match(vid):
        raise ValueError(f"unsafe volume id: {vid!r}")
    if "manifest" in fields:
        return Volume(id=vid, manifest_url=_http_url(fields["manifest"], "manifest:"))
    images = fields.get("images")
    if isinstance(images, list) and images:
        return Volume(id=vid, images=tuple(_http_url(u, "images:") for u in images))
    raise ValueError(f"volume {vid!r} needs manifest: or images:")


def _declared(doc: object) -> tuple[str, tuple[str, ...]]:
    """(pipeline id, volume ids) as far as a possibly-malformed file states
    them — for orphan accounting only, never for submission."""
    if not isinstance(doc, dict):
        return "", ()
    ids: list[str] = []
    entries = doc.get("volumes")
    for e in entries if isinstance(entries, list) else []:
        if isinstance(e, str):
            ids.append(e)
        elif isinstance(e, dict):
            vid = e.get("id")
            if vid is not None:
                ids.append(str(vid))
    return str(doc.get("pipeline") or ""), tuple(ids)


def parse_campaign(name: str, text: str) -> Campaign:
    doc: object = None
    try:
        doc = yaml.safe_load(text)
        if not isinstance(doc, dict):
            raise ValueError("campaign file is not a mapping")
        pipeline_id = str(doc.get("pipeline") or "")
        if not pipeline_id:
            raise ValueError("campaign needs pipeline:")
        volumes = [_volume(e) for e in doc.get("volumes") or []]
        seen: set[str] = set()
        for v in volumes:
            if v.id in seen:
                raise ValueError(f"duplicate volume id: {v.id}")
            seen.add(v.id)
        return Campaign(
            name=name,
            pipeline_id=pipeline_id,
            volumes=volumes,
            declared_ids=tuple(v.id for v in volumes),
        )
    except Exception as e:
        pipeline_id, declared = _declared(doc)
        return Campaign(
            name=name,
            pipeline_id=pipeline_id,
            volumes=[],
            error=str(e),
            declared_ids=declared,
        )


def _repo_allowed(image: str, allowed: Sequence[str]) -> bool:
    """Prefix match on a path boundary: ``ghcr.io/riksarkivet/`` admits
    ``ghcr.io/riksarkivet/x`` but not ``ghcr.io/riksarkivet-evil/x``."""
    repo = image.split("@", 1)[0]
    for entry in allowed:
        entry = entry.strip()
        if not entry:
            continue
        if repo == entry.rstrip("/") or repo.startswith(entry.rstrip("/") + "/"):
            return True
    return False


def _check_revisions(pipeline_id: str, steps: object) -> None:
    if not isinstance(steps, list):
        return
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
            raise PipelineError(
                f"pipeline {pipeline_id}: model {model!r} needs a 40-hex "
                "revision (RECONCILER_REQUIRE_MODEL_REVISION)"
            )


def parse_pipeline(
    pipeline_id: str,
    text: str,
    *,
    allowed_repos: Sequence[str] = (),
    require_revision: bool = False,
) -> PipelineSpec:
    if not _PIPELINE_ID_RE.match(pipeline_id):
        raise PipelineError(
            f"unsafe pipeline id: {pipeline_id!r} (must be a DNS-1123 label: "
            "lowercase alphanumeric ends, [a-z0-9.-] interior, <=63 chars)"
        )
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise PipelineError(f"bad pipeline yaml: {e}") from e
    if not isinstance(doc, dict):
        raise PipelineError("pipeline file is not a mapping")
    image = str(doc.get("image") or "")
    if "@sha256:" not in image:
        raise PipelineError(f"pipeline {pipeline_id}: image must be digest-pinned")
    if allowed_repos and not _repo_allowed(image, allowed_repos):
        raise PipelineError(
            f"pipeline {pipeline_id}: image {image.split('@', 1)[0]!r} is not "
            "under an allowed repository (RECONCILER_ALLOWED_IMAGE_REPOS)"
        )
    if "steps" not in doc:
        raise PipelineError(f"pipeline {pipeline_id}: missing steps")
    if require_revision:
        _check_revisions(pipeline_id, doc["steps"])
    steps_yaml = yaml.safe_dump({"steps": doc["steps"]}, sort_keys=False)
    return PipelineSpec(
        id=pipeline_id,
        image=image,
        steps_yaml=steps_yaml,
        steps_sha256=canonical_sha256({"steps": doc["steps"]}),
        legacy_sha256=hashlib.sha256(steps_yaml.encode()).hexdigest(),
    )


def canonical_sha256(steps: object) -> str:
    """Hash of the parsed recipe, not of one library's serialisation of it
    (audit R10): sorted keys, no whitespace, so a PyYAML upgrade that
    re-flows the dump cannot read as drift."""
    text = json.dumps(steps, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode()).hexdigest()


def step_summaries(steps_yaml: str) -> list[str]:
    """One display line per pipeline step: ``Step: model (weights)``.

    Display-only derivation for status.json — total over junk: anything
    unparseable yields [] rather than failing the tick.
    """
    try:
        doc = yaml.safe_load(steps_yaml)
    except yaml.YAMLError:
        return []
    if not isinstance(doc, dict) or not isinstance(doc.get("steps"), list):
        return []
    out: list[str] = []
    for step in doc["steps"]:
        if not isinstance(step, dict) or not step.get("step"):
            continue
        label = str(step["step"])
        settings = step.get("settings") or {}
        if isinstance(settings, dict) and settings.get("model"):
            label += f": {settings['model']}"
            ms = settings.get("model_settings") or {}
            if isinstance(ms, dict) and ms.get("model"):
                label += f" ({ms['model']})"
        out.append(label)
    return out
