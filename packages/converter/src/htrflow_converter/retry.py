"""``retry``: which volumes of a campaign are unfinished, and why."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Literal
from urllib.parse import quote

from htrflow_converter.export import _safe_segment
from htrflow_converter.export_http import Results
from htrflow_converter.models import parse_source_line

_PERMANENT = "permanent failure in "
_TRANSIENT = "transient failure in "
_LAST_ATTEMPT = " on the last attempt"
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")
_GROUP_MAX = 100


@dataclass(frozen=True)
class Outcome:
    id: str
    line: str  # the original ``volumes.txt`` line, unchanged
    state: Literal["done", "retry", "permanent"]
    reason: str


def classify_log(text: str | None) -> tuple[bool, str]:
    """``(permanent, reason)`` from a volume's run log. The LAST failure line
    decides, not the last line: a traceback may follow it."""
    if text is None:
        return False, "no run log: the volume never started"
    found = None
    for raw in text.splitlines():
        for marker in (_PERMANENT, _TRANSIENT):
            if marker in raw:
                found = (marker == _PERMANENT, raw.partition(marker)[2])
    if found is None:
        return False, "no failure line in the run log"
    permanent, rest = found
    return permanent, rest.partition(" — ")[0].replace(_LAST_ATTEMPT, "")


def reason_group(reason: str) -> str:
    """The reason with each quoted span folded, so that failures differing only
    in a path or a name count as one group; capped to stay readable."""
    return _QUOTED.sub("'…'", reason)[:_GROUP_MAX]


def _outcome(results: Results, namespace: str, pipeline: str, line: str) -> Outcome:
    vid, _ = parse_source_line(line)
    if not _safe_segment(vid):
        return Outcome(vid, line, "retry", "id not readable as a results path")
    seg = quote(vid, safe="")
    if results.get(f"{namespace}/{pipeline}/{seg}/manifest.json") is not None:
        return Outcome(vid, line, "done", "")
    log = results.get(f"{namespace}/status/logs/{pipeline}/{seg}.txt")
    permanent, reason = classify_log(
        log.decode("utf-8", "replace") if log is not None else None
    )
    return Outcome(vid, line, "permanent" if permanent else "retry", reason)


def find_unfinished(
    results: Results,
    namespace: str,
    pipeline: str,
    lines: list[str],
    workers: int = 16,
) -> list[Outcome]:
    """One ``Outcome`` per line, in input order. A volume is done when its
    manifest exists; otherwise its log says why not. ``Forbidden``,
    ``SessionLost`` and ``ResultsUnavailable`` end the search."""
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(
            pool.map(lambda line: _outcome(results, namespace, pipeline, line), lines)
        )
