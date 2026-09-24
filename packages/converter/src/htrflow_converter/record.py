"""What an earlier render left in a campaigns repo's ``rendered/``: the
record every rule about a campaign that has already run is held against
(``cli``), and the record that decides which authoring rules a campaign is
held to at all (``parse``: see ``unchanged``)."""

from __future__ import annotations

import contextlib
import re
from pathlib import Path

import yaml

from . import render
from .models import Campaign, parse_source_line

#: Where a campaigns repo keeps its committed render. The one source of
#: truth for the RECORD a re-render is held against: `render --out` says
#: where this render goes, and the repo's own `rendered/` is what the
#: previous one left (docs: reference/cli.md).
RENDERED = "rendered"

_PART_RE = re.compile(r"-part(\d+)\.yaml\Z")


class CorruptRenderedFile(Exception):
    def __init__(self, path: Path, reason: object) -> None:
        super().__init__(f"{path}: cannot read existing campaign: {reason}")


def rendered(path: Path, kind: str) -> dict:
    """The object of ``kind`` in one rendered campaign file."""
    try:
        docs = yaml.load_all(path.read_text(), Loader=FAST_LOADER)
        return next(d for d in docs if isinstance(d, dict) and d.get("kind") == kind)
    except (yaml.YAMLError, StopIteration) as e:
        raise CorruptRenderedFile(path, e) from e


def volumes_txt(path: Path) -> str:
    try:
        return rendered(path, "ConfigMap")["data"]["volumes.txt"].rstrip("\n")
    except (KeyError, TypeError) as e:
        raise CorruptRenderedFile(path, e) from e


def _part_number(path: Path) -> int:
    m = _PART_RE.search(path.name)
    return int(m.group(1)) if m else 0


#: libyaml where the platform has it: a part's ConfigMap is up to 900 KiB
#: of ``volumes.txt``, read here once more than the append-only check does.
FAST_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def _rendered_campaign(path: Path) -> str | None:
    """The campaign a rendered file says it belongs to: the converter's
    campaign label on its objects. ``None`` when the file does not say --
    unreadable, or written without the label -- which the caller counts
    against every campaign it could be, so the check that reads it next
    reports it rather than passing it by."""
    try:
        for doc in yaml.load_all(path.read_text(), Loader=FAST_LOADER):
            labels = ((doc or {}).get("metadata") or {}).get("labels") or {}
            if render.CAMPAIGN_LABEL in labels:
                return labels[render.CAMPAIGN_LABEL]
    except (yaml.YAMLError, OSError, AttributeError):
        pass
    return None


def existing_parts(campaigns_out: Path, name: str) -> list[Path]:
    """Every file an earlier render of this campaign left in ``out``, in the
    order it wrote them. A campaign that splits renders under a name cut
    short of its own (see ``render.split_stem``), so the ``-partN`` files are
    looked up under that stem, not under the campaign's own name. Matched
    whole, never globbed: ``loc-part*`` also finds the campaign ``loc-partner``
    (3088).

    A stem is not an owner, though: two long names that agree on their first
    50 characters share one, and ``<stem>-b`` rendered as a single Job used to
    be handed ``<stem>-a``'s parts as its own -- "append-only", with nothing
    changed (audit 0923 C-4). A part is this campaign's when its label says
    so. ``<name>.yaml`` needs no such check: no other campaign renders there,
    since a campaign name may not end in ``-part<number>``."""
    part = re.compile(re.escape(render.split_stem(name)) + r"-part\d+\.yaml\Z")
    paths = sorted(campaigns_out.glob(f"{name}.yaml"))
    mine = render.label_value(name)
    parts = [
        p
        for p in campaigns_out.glob("*.yaml")
        if part.match(p.name) and _rendered_campaign(p) in (mine, None)
    ]
    return paths + sorted(parts, key=_part_number)


def recorded_lines(campaigns_out: Path, name: str) -> list[str] | None:
    """The ``volumes.txt`` lines an earlier render recorded for campaign
    ``name``, as written, or ``None`` when it has none. Raises
    ``CorruptRenderedFile`` for a record it cannot read."""
    parts = existing_parts(campaigns_out, name)
    if not parts:
        return None
    return [line for p in parts for line in volumes_txt(p).splitlines()]


def recorded_manifests(lines: list[str]) -> dict[str, str]:
    """The manifest URL each recorded volume was rendered with, by id: what a
    bare reference code expanded to, for a campaign kept as rendered after
    converter.yaml stopped setting the template it was expanded with."""
    return {
        vid: source
        for vid, _, source in (line.partition("\t") for line in lines)
        if not source.startswith("images:")
    }


def unchanged(c: Campaign, recorded: list[tuple] | None) -> bool:
    """Whether ``c`` is a campaign an earlier render already recorded, with
    the volume list it recorded. THE test for which rules a campaign is held
    to: an authoring rule added later applies to a campaign that is new or
    whose record changes, never to one already rendered and unchanged. Its
    list is append-only, so a rule it now fails would be a rule it could
    never be brought to pass -- the old rendering stays authoritative
    (audit 0923 review)."""
    return recorded is not None and recorded == [
        parse_source_line(v.source_line()) for v in c.volumes
    ]


def recorded_recipe(path: Path) -> dict[str, object]:
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
