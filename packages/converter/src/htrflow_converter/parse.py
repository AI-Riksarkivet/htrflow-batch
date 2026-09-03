"""Campaign/pipeline YAML -> domain types, with validation (spec §3)."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError as _PydanticValidationError

from .models import Campaign, ConverterConfig, Pipeline, shown


class ValidationError(Exception):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems

    @property
    def summary(self) -> str:
        """The closing line: one typo, or a bad merge?"""
        files = {p.partition(":")[0] for p in self.problems}
        n, m = len(self.problems), len(files)
        return f"{n} problem{'s'[: n != 1]} in {m} file{'s'[: m != 1]}"


def _rel(path: Path) -> str:
    """``campaigns/broken.yaml``: a problem list that names files an author
    can open is a to-do list, a list of bare stems is a puzzle."""
    return f"{path.parent.name}/{path.name}"


def _read_yaml_mapping(path: Path, problems: list[str], what: str) -> dict | None:
    rel = _rel(path)
    try:
        doc = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        problems.append(_not_yaml(rel, e))
        return None
    if not isinstance(doc, dict):
        problems.append(_not_a_mapping(rel, what))
        return None
    return doc


def _not_yaml(rel: str, e: yaml.YAMLError) -> str:
    # PyYAML names the line and column, which is the "where"; reflow it.
    return f"{rel}: this file is not valid YAML — {' '.join(str(e).split())}"


def _not_a_mapping(rel: str, what: str) -> str:
    return (
        f'{rel}: this file must be {what} settings written as "key: value" '
        "lines — a bare list or a piece of text is not one"
    )


def _load_config(path: Path, problems: list[str]) -> ConverterConfig:
    if not path.exists():
        return ConverterConfig()
    try:
        doc = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        problems.append(_not_yaml(path.name, e))
        return ConverterConfig()
    if not isinstance(doc, dict):
        problems.append(_not_a_mapping(path.name, "converter"))
        return ConverterConfig()
    try:
        return ConverterConfig(**doc)
    except _PydanticValidationError as e:
        problems.extend(_problems(path.name, e))
        return ConverterConfig()


#: Pydantic's error types as the second half of a sentence about ``_what``.
#: Its own ``msg`` is written for a Python programmer ("Field required",
#: "Input should be a valid integer") and its ``loc`` is a path into a parsed
#: object; a campaign author has neither in front of them, only a YAML file.
#: Anything not listed keeps pydantic's ``msg``, which is at least English.
_TYPE_SENTENCES = {
    "missing": 'is missing — add "{key}:" to this file',
    "extra_forbidden": "is not a setting this file has — remove it, or fix"
    " the spelling",
    "int_type": "must be a whole number (got {got})",
    "int_parsing": "must be a whole number (got {got})",
    "float_parsing": "must be a number (got {got})",
    "string_type": "must be text (got {got})",
    "bool_type": "must be true or false (got {got})",
    "list_type": "must be a list of entries (got {got})",
    "dict_type": 'must be settings written as "key: value" lines (got {got})',
    "greater_than_equal": "must be {ctx[ge]} or more (got {got})",
}


def _what(loc: tuple, value: object) -> str:
    """Who a problem is about, in the author's terms: a volume by its place in
    the list (and by its id, which is how its author knows it), or the quoted
    key of a setting. Empty for a whole-file rule, whose validator raises a
    sentence that already stands on its own."""
    if len(loc) >= 2 and loc[0] == "volumes" and isinstance(loc[1], int):
        named = f' ("{value}")' if loc[-1] == "id" and isinstance(value, str) else ""
        return f"volume {loc[1] + 1}{named}"
    if loc[-1:] in [("name",), ("id",)]:
        # Both are taken from the file name, so that is the thing to fix.
        what = "campaign name" if loc[-1] == "name" else "pipeline id"
        return f"the {what} (taken from the file name)"
    return f'"{loc[-1]}"' if loc else ""


def _problems(rel: str, exc: _PydanticValidationError) -> list[str]:
    """``file.yaml: <what is wrong> — <what to write instead>``, one line per
    error. Our own validators raise the predicate half already (each is
    written to continue ``_what``'s subject); pydantic's own error types get
    theirs from ``_TYPE_SENTENCES``."""
    out = []
    for err in exc.errors():
        loc = tuple(err["loc"])
        template = _TYPE_SENTENCES.get(err["type"])
        if template is None:
            msg = err["msg"].removeprefix("Value error, ").replace("\n", " ")
        else:
            key = loc[-1] if loc else ""
            msg = template.format(
                key=key, got=shown(err.get("input")), ctx=err.get("ctx") or {}
            )
        what = _what(loc, err.get("input"))
        out.append(f"{rel}: {what} {msg}" if what else f"{rel}: {msg}")
    return out


def _duplicate_volume_ids(doc: dict, rel: str, problems: list[str]) -> None:
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
            problems.append(
                f'{rel}: volume "{vid}" is listed twice — remove the duplicate'
            )
        seen.add(str(vid))


def _parse_campaign(path: Path, context: dict, problems: list[str]) -> Campaign | None:
    doc = _read_yaml_mapping(path, problems, "campaign")
    if doc is None:
        return None
    _duplicate_volume_ids(doc, _rel(path), problems)
    try:
        # `path.stem` always wins over a `name:` the YAML happens to carry.
        return Campaign.model_validate({**doc, "name": path.stem}, context=context)
    except _PydanticValidationError as e:
        problems.extend(_problems(_rel(path), e))
        return None


def _parse_pipeline(path: Path, context: dict, problems: list[str]) -> Pipeline | None:
    doc = _read_yaml_mapping(path, problems, "pipeline")
    if doc is None:
        return None
    try:
        # `path.stem` always wins over an `id:` the YAML happens to carry.
        return Pipeline.model_validate({**doc, "id": path.stem}, context=context)
    except _PydanticValidationError as e:
        problems.extend(_problems(_rel(path), e))
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
    broken: set[str] = set()  # a file that is there but did not load
    for path in sorted(Path(pipelines_dir).glob("*.yaml")):
        p = _parse_pipeline(path, context, problems)
        if p is not None:
            pipelines[p.id] = p
        else:
            broken.add(path.stem)

    campaigns: list[Campaign] = []
    files: dict[str, str] = {}  # campaign name -> the file it came from
    for path in sorted(Path(campaigns_dir).glob("*.yaml")):
        c = _parse_campaign(path, context, problems)
        if c is not None:
            campaigns.append(c)
            files[c.name] = _rel(path)

    for c in campaigns:
        # A campaign pointing at a pipeline whose own file is already on this
        # list is not also missing one: saying so twice sends its author
        # looking for a file that is right there.
        if c.pipeline not in pipelines and c.pipeline not in broken:
            problems.append(
                f'{files[c.name]}: pipeline "{c.pipeline}" has no file in '
                f"pipelines/ — add pipelines/{c.pipeline}.yaml, or point "
                "pipeline: at one that is there"
            )

    if problems:
        raise ValidationError(problems)
    return campaigns, pipelines, cfg
