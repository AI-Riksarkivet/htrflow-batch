"""Campaign/pipeline YAML -> domain types. Campaign problems are contained
(Campaign.error); pipeline problems raise (a broken pipeline must block
submission, spec §3)."""

from __future__ import annotations

import hashlib
import re

import yaml

from .models import Campaign, PipelineSpec, Volume

RA_MANIFEST_TEMPLATE = "https://lbiiif.riksarkivet.se/arkis!{ref}/manifest"
_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


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
