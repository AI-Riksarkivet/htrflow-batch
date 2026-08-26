"""Retry budgets and terminal verdicts, persisted in ``status/attempts.json``.

v2 shape (audit R1): ``{"<pid>/<vid>": {"n": int, "terminal": str | null}}``.
``terminal`` is the sticky verdict — ``"exit-13"`` or ``"capped"`` — that keeps
a volume ``needs-attention`` after its Job has been TTL-reaped; clearing it is
an operator action (delete the key or bump the pipeline id). v1 stored bare
ints and is migrated on read.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, ValidationError


class Attempt(BaseModel):
    model_config = ConfigDict(frozen=True)

    n: int = 0
    terminal: str | None = None
    #: ``pages_done`` when the current Job was submitted (audit O2): a Job
    #: that hit its deadline but made progress is not charged an attempt.
    pages_at_submit: int | None = None


def load_attempts(raw: object) -> dict[str, Attempt]:
    """Total over junk: a corrupt record loses only itself, never the tick."""
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, Attempt] = {}
    for key, value in raw.items():
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            out[str(key)] = Attempt(n=value)
            continue
        if not isinstance(value, Mapping):
            continue
        try:
            out[str(key)] = Attempt.model_validate(dict(value))
        except ValidationError:
            continue
    return out


def dump_attempts(records: Mapping[str, Attempt]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for key, rec in records.items():
        row: dict = {"n": rec.n, "terminal": rec.terminal}
        if rec.pages_at_submit is not None:
            row["pages_at_submit"] = rec.pages_at_submit
        out[key] = row
    return out
