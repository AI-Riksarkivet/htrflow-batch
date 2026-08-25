"""Campaign/pipeline YAML -> domain types. Campaign problems are contained
(Campaign.error); pipeline problems raise (a broken pipeline must block
submission, spec §3)."""

from __future__ import annotations

import hashlib
import re

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


class PipelineError(ValueError):
    pass


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
    manifest = fields.get("manifest")
    if manifest:
        return Volume(id=vid, manifest_url=str(manifest))
    images = fields.get("images")
    if isinstance(images, list) and images:
        return Volume(id=vid, images=tuple(str(u) for u in images))
    raise ValueError(f"volume {vid!r} needs manifest: or images:")


def parse_campaign(name: str, text: str) -> Campaign:
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
        return Campaign(name=name, pipeline_id=pipeline_id, volumes=volumes)
    except Exception as e:
        return Campaign(name=name, pipeline_id="", volumes=[], error=str(e))


def parse_pipeline(pipeline_id: str, text: str) -> PipelineSpec:
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
    if "steps" not in doc:
        raise PipelineError(f"pipeline {pipeline_id}: missing steps")
    steps_yaml = yaml.safe_dump({"steps": doc["steps"]}, sort_keys=False)
    return PipelineSpec(
        id=pipeline_id,
        image=image,
        steps_yaml=steps_yaml,
        steps_sha256=hashlib.sha256(steps_yaml.encode()).hexdigest(),
    )


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
