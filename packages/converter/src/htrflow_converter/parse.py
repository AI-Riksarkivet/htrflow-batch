"""Campaign/pipeline YAML -> domain types, with validation (spec §3)."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError as _PydanticValidationError

from .models import (
    Campaign,
    ConverterConfig,
    Pipeline,
    _not_text,
    _shown_url,
    _unopenable,
    shown,
)
from .record import RENDERED, CorruptRenderedFile, recorded_volumes, unchanged


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
    Only the keys the mapping itself writes are compared, ``<<`` among them:
    what a merge supplies is defaults that an explicit key is meant to
    override."""

    def construct_mapping(self, node, deep=False):
        if isinstance(node, yaml.MappingNode):
            seen: dict[object, yaml.Node] = {}
            for key_node, _ in node.value:
                # `<<` is compared as written: two of them are two keys.
                key = (
                    "<<"
                    if key_node.tag == "tag:yaml.org,2002:merge"
                    else self.construct_object(key_node, deep=True)
                )
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


def _kept(rel: str, doc: dict, c: Campaign) -> list[str]:
    """What a campaign kept under ``record.unchanged`` would be refused for
    if it were new: said, not enforced -- its volumes cannot change."""
    said = []
    raw = [e for e in doc.get("volumes") or []]
    for n, (entry, v) in enumerate(zip(raw, c.volumes), start=1):
        if isinstance(entry, dict) and (kind := _not_text(entry.get("id"))):
            said.append(
                f"{rel}: volume {n} has an id YAML reads as {kind}, and it was "
                f'rendered as "{v.id}", which stays its id — write id: "{v.id}" '
                "so the file says what it is"
            )
        for what, url in [
            ("a manifest", v.manifest),
            *(("an image", u) for u in v.images),
        ]:
            if url is not None and (why := _unopenable(url)):
                said.append(
                    f'{rel}: volume {n} has {what} a browser cannot open ("'
                    f'{_shown_url(url)}"): {why} — kept, since this campaign '
                    "was rendered with it and its volumes cannot change"
                )
    return said


def _parse_campaign(
    path: Path, context: dict, problems: list[str], warnings: list[str]
) -> Campaign | None:
    doc = _read_yaml_mapping(path, problems, "campaign")
    if doc is None:
        return None
    rel = _rel(path)
    _duplicate_volume_ids(doc, rel, problems)
    # `path.stem` always wins over a `name:` the YAML happens to carry.
    data = {**doc, "name": path.stem}
    try:
        return Campaign.model_validate(data, context=context)
    except _PydanticValidationError as e:
        refused = e
    # An authoring rule added since the campaign was rendered does not reach
    # it while its record is unchanged: it could never be brought to pass.
    recorded = _recorded(context.get("record"), path.stem)
    if recorded is not None:
        try:
            c = Campaign.model_validate(data, context={**context, "as_recorded": True})
        except _PydanticValidationError:
            c = None
        if c is not None and unchanged(c, recorded):
            warnings.extend(_kept(rel, doc, c))
            return c
    problems.extend(_problems(rel, refused))
    return None


def _recorded(record: Path | None, name: str) -> list[tuple] | None:
    """The volumes ``record`` (a ``rendered/`` directory) recorded for
    ``name``; ``None`` for none, and for a record it cannot read -- the
    append-only check reports that one."""
    if record is None or not (record / "campaigns").is_dir():
        return None
    try:
        return recorded_volumes(record / "campaigns", name)
    except CorruptRenderedFile:
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
    campaigns_dir: Path,
    pipelines_dir: Path,
    config_path: Path,
    warnings: list[str] | None = None,
) -> tuple[list[Campaign], dict[str, Pipeline], ConverterConfig]:
    """The repo, validated. Its earlier render is read from ``rendered/``
    beside ``campaigns_dir`` (``record.unchanged``); ``warnings`` collects
    what a campaign kept that way would be refused for if it were new."""
    problems: list[str] = []
    warnings = [] if warnings is None else warnings
    cfg = _load_config(Path(config_path), problems)
    context = {
        "source_template": cfg.source_template,
        "record": Path(campaigns_dir).parent / RENDERED,
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
        c = _parse_campaign(path, context, problems, warnings)
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
