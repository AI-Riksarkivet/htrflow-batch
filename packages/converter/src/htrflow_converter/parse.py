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


class _KeyWrittenTwice(yaml.YAMLError):
    def __init__(self, key: object, first: yaml.Mark, again: yaml.Mark) -> None:
        super().__init__(
            f'"{key}" is written twice, on lines {first.line + 1} and '
            f"{again.line + 1} — YAML would keep only the last one, so remove "
            "one or merge them"
        )


class _StrictLoader(yaml.SafeLoader):
    """``yaml.safe_load``, except that a mapping may not name a key twice.

    PyYAML keeps the last of two equal keys without a word, so ``volumes:
    [R1]`` followed by ``volumes: [R2]`` rendered R2 alone (audit 0923 C-6).
    Only the keys the mapping itself writes are compared: a ``<<:`` merge
    supplies defaults that an explicit key is meant to override."""

    def construct_mapping(self, node, deep=False):
        if isinstance(node, yaml.MappingNode):
            seen: dict[object, yaml.Node] = {}
            for key_node, _ in node.value:
                if key_node.tag == "tag:yaml.org,2002:merge":
                    continue
                key = self.construct_object(key_node, deep=True)
                try:
                    first = seen.setdefault(key, key_node)
                except TypeError:  # an unhashable key: SafeLoader refuses it
                    continue
                if first is not key_node:
                    raise _KeyWrittenTwice(key, first.start_mark, key_node.start_mark)
        return super().construct_mapping(node, deep=deep)


def _safe_load(path: Path) -> object:
    """Every YAML file the converter reads from a campaigns repo."""
    return yaml.load(path.read_text(), Loader=_StrictLoader)  # a SafeLoader


def _read_yaml_mapping(path: Path, problems: list[str], what: str) -> dict | None:
    rel = _rel(path)
    try:
        doc = _safe_load(path)
    except yaml.YAMLError as e:
        problems.append(_not_yaml(rel, e))
        return None
    if not isinstance(doc, dict):
        problems.append(_not_a_mapping(rel, what))
        return None
    return doc


def _not_yaml(rel: str, e: yaml.YAMLError) -> str:
    if isinstance(e, _KeyWrittenTwice):
        return f"{rel}: {e}"
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
        doc = _safe_load(path) or {}
    except yaml.YAMLError as e:
        problems.append(_not_yaml(path.name, e))
        return ConverterConfig()
    if not isinstance(doc, dict):
        problems.append(_not_a_mapping(path.name, "converter"))
        return ConverterConfig()
    try:
        return ConverterConfig.model_validate(doc)
    except _PydanticValidationError as e:
        problems.extend(_problems(path.name, e))
        return ConverterConfig()


#: Pydantic's error types as the second half of a sentence about ``_what``.
#: Its own ``msg`` is written for a Python programmer ("Field required",
#: "Input should be a valid integer") and its ``loc`` is a path into a parsed
#: object; a campaign author has neither in front of them, only a YAML file.
#: Anything not listed keeps pydantic's ``msg``, which is at least English.
#: The list is the audited one: feeding every field of every model a wrong
#: value emits exactly these types plus ``value_error`` (our own
#: validators, which raise their sentence directly). ``float_parsing`` was
#: dropped with that audit -- no model has a float field to reach it. The
#: typed tolerations added ``model_type`` and ``literal_error`` (audit 0923 S-1).
_TYPE_SENTENCES = {
    "missing": 'is missing — add "{key}:" to this file',
    "extra_forbidden": "is not a setting this file has — remove it, or fix"
    " the spelling",
    "int_type": "must be a whole number (got {got})",
    "int_parsing": "must be a whole number (got {got})",
    "int_from_float": "must be a whole number (got {got})",
    "string_type": "must be text (got {got})",
    "bool_type": "must be true or false (got {got})",
    "bool_parsing": "must be true or false (got {got})",
    "list_type": "must be a list of entries (got {got})",
    "dict_type": 'must be settings written as "key: value" lines (got {got})',
    # A settings block with a model of its own (a toleration) is refused as
    # this, not as `dict_type`; to its author the two are one mistake.
    "model_type": 'must be settings written as "key: value" lines (got {got})',
    "literal_error": "must be one of {expected} (got {got})",
    "greater_than_equal": "must be {ctx[ge]} or more (got {got})",
    "less_than_equal": "must be {ctx[le]} or less (got {got})",
}


_ONE_LINE = {ord(c): " " for c in "\t\r\n"}


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
    if not loc:
        return ""
    # A nested loc is a key path the author can see in their own file
    # (`node_selector.a`); a list index in it is not, so it is counted from 1
    # and spelled out. `loc[-1]` alone would name a bare `0` for `steps.0`.
    keys = ".".join(str(p) for p in loc if not isinstance(p, int))
    nth = next((f" entry {p + 1}" for p in loc if isinstance(p, int)), "")
    return f'"{keys}"{nth}'


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
            # One problem is one line: a tab, CR or LF out of the author's
            # own YAML would otherwise split it in a CI log.
            msg = err["msg"].removeprefix("Value error, ").translate(_ONE_LINE)
        elif err["type"] == "extra_forbidden" and loc[:1] == ("volumes",):
            # `_what` names the volume, so the key is the object here.
            msg = (
                f'has "{loc[-1]}", which is not a setting a volume has — '
                "remove it, or fix the spelling"
            )
        else:
            key = loc[-1] if loc else ""
            ctx = err.get("ctx") or {}
            msg = template.format(
                key=key,
                got=shown(err.get("input")),
                ctx=ctx,
                # pydantic quotes each choice as a repr: 'Exists' or 'Equal'
                expected=str(ctx.get("expected", "")).replace("'", ""),
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


def _shared_volumes(
    campaigns: list[Campaign], files: dict[str, str], problems: list[str]
) -> None:
    """A volume two campaigns on one pipeline both list (audit 0923 C-13).

    Results are keyed ``<pipeline>/<volume>/``, not by campaign, so the two
    campaigns' pods would run the same volume at once and write over each
    other's pages. One line per pair of campaigns -- two copies of one big
    list would otherwise be thousands -- blamed on the later file."""
    first: dict[tuple[str, str], str] = {}
    for c in campaigns:
        shared: dict[str, list[str]] = {}
        for v in c.volumes:
            owner = first.setdefault((c.pipeline, v.id), c.name)
            if owner != c.name:
                shared.setdefault(owner, []).append(v.id)
        for owner, ids in shared.items():
            quoted = [f'"{i}"' for i in ids]
            if len(ids) == 1:
                named = f"volume {quoted[0]} is"
            elif len(ids) <= 3:
                named = f"volumes {', '.join(quoted[:-1])} and {quoted[-1]} are"
            else:
                named = f"volumes {', '.join(quoted[:3])} and {len(ids) - 3} more are"
            problems.append(
                f"{files[c.name]}: {named} also in {files[owner]}, and both "
                f"campaigns run pipeline {c.pipeline} — their pods would write "
                "the same results at once; list each volume in one of them only"
            )


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
    context = {"source_template": cfg.source_template}

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

    _shared_volumes(campaigns, files, problems)
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
        # Kueue never refuses an unknown class: the Job just stays suspended
        # with no event, so the class list has to be checked here.
        if c.priority and c.priority not in cfg.priority_classes:
            offered = ", ".join(cfg.priority_classes) or "none"
            problems.append(
                f'{files[c.name]}: priority "{c.priority}" is not one of the '
                f"cluster's classes ({offered}) — set converter.yaml "
                "priority_classes to what the chart's queue.priorityClasses ships"
            )

    if problems:
        raise ValidationError(problems)
    return campaigns, pipelines, cfg
