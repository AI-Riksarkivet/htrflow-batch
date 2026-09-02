"""Campaign/pipeline YAML -> domain types, with validation (spec §3)."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError as _PydanticValidationError

from .models import Campaign, ConverterConfig, Pipeline


class ValidationError(Exception):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def _read_yaml_mapping(path: Path, problems: list[str], what: str) -> dict | None:
    try:
        doc = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        problems.append(f"{path.stem}: bad {what} yaml: {e}")
        return None
    if not isinstance(doc, dict):
        problems.append(f"{path.stem}: {what} file is not a mapping")
        return None
    return doc


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


def _problems(stem: str, exc: _PydanticValidationError) -> list[str]:
    out = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"])
        msg = err["msg"]
        if msg.startswith("Value error, "):
            msg = msg[len("Value error, ") :]
        msg = msg.replace("\n", " ")
        out.append(f"{stem}: {loc}: {msg}" if loc else f"{stem}: {msg}")
    return out


def _duplicate_volume_ids(doc: dict, stem: str, problems: list[str]) -> None:
    seen: set[str] = set()
    for entry in doc.get("volumes") or []:
        vid = (
            entry
            if isinstance(entry, str)
            else entry.get("id")
            if isinstance(entry, dict)
            else None
        )
        if vid is None:
            continue  # pydantic reports the missing id; nothing to compare
        if str(vid) in seen:
            problems.append(f"{stem}: duplicate volume id: {vid}")
        seen.add(str(vid))


def _parse_campaign(path: Path, context: dict, problems: list[str]) -> Campaign | None:
    doc = _read_yaml_mapping(path, problems, "campaign")
    if doc is None:
        return None
    _duplicate_volume_ids(doc, path.stem, problems)
    try:
        # `path.stem` always wins over a `name:` the YAML happens to carry.
        return Campaign.model_validate({**doc, "name": path.stem}, context=context)
    except _PydanticValidationError as e:
        problems.extend(_problems(path.stem, e))
        return None


def _parse_pipeline(path: Path, context: dict, problems: list[str]) -> Pipeline | None:
    doc = _read_yaml_mapping(path, problems, "pipeline")
    if doc is None:
        return None
    try:
        # `path.stem` always wins over an `id:` the YAML happens to carry.
        return Pipeline.model_validate({**doc, "id": path.stem}, context=context)
    except _PydanticValidationError as e:
        problems.extend(_problems(path.stem, e))
        return None


def load(
    campaigns_dir: Path, pipelines_dir: Path, config_path: Path
) -> tuple[list[Campaign], dict[str, Pipeline], ConverterConfig]:
    problems: list[str] = []
    cfg = _load_config(Path(config_path), problems)
    context = {
        "source_template": cfg.source_template,
        "allowed_image_repos": cfg.allowed_image_repos,
        "require_model_revision": cfg.require_model_revision,
    }

    pipelines: dict[str, Pipeline] = {}
    for path in sorted(Path(pipelines_dir).glob("*.yaml")):
        p = _parse_pipeline(path, context, problems)
        if p is not None:
            pipelines[p.id] = p

    campaigns: list[Campaign] = []
    for path in sorted(Path(campaigns_dir).glob("*.yaml")):
        c = _parse_campaign(path, context, problems)
        if c is not None:
            campaigns.append(c)

    for c in campaigns:
        if c.pipeline not in pipelines:
            problems.append(f"{c.name}: unknown pipeline: {c.pipeline!r}")

    if problems:
        raise ValidationError(problems)
    return campaigns, pipelines, cfg
