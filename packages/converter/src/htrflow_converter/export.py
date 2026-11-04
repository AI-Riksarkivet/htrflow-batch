"""One volume of ``htrflow-campaigns export``: read, check, build, swap.

A volume is published whole or not at all. Files are built in a hidden
sibling folder and renamed over the real one only after every check has
passed, so the real folder name never carries a partial volume.
"""

from __future__ import annotations

import json
import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from htrflow_converter.export_alto import AltoMismatch, empty_alto, scale_alto
from htrflow_converter.export_http import (
    Forbidden,
    Results,
    ResultsUnavailable,
    SizeUnreadable,
    image_size,
)
from htrflow_converter.export_pages import (
    PageRuleError,
    info_url,
    page_number,
    quality_mean,
)

__all__ = ["Options", "Row", "export_volume", "swap_in"]


@dataclass(frozen=True)
class Row:
    volume: str
    outcome: str
    pages: int = 0
    pages_with_text: int = 0
    pages_empty_filled: int = 0
    pages_empty_chosen: int = 0
    quality_mean: float | None = None
    quality_pages: int = 0
    reason: str = ""


@dataclass(frozen=True)
class Options:
    target: Path
    min_quality: float | None  # None = quality filter off
    empty_pages: frozenset[int]  # published page numbers to blank


def swap_in(built: Path, final: Path) -> None:
    """Make ``built`` the folder ``final``; an existing one goes aside first."""
    old = None
    if final.exists():
        old = final.with_name(f".{final.name}.old-{secrets.token_hex(4)}")
        final.rename(old)
    built.rename(final)
    if old is not None:
        shutil.rmtree(old)


def _clear_leftovers(target: Path, volume_id: str) -> None:
    for kind in ("tmp", "old"):
        for path in target.glob(f".{glob_escape(volume_id)}.{kind}-*"):
            shutil.rmtree(path, ignore_errors=True)


def glob_escape(text: str) -> str:
    return "".join(f"[{c}]" if c in "*?[" else c for c in text)


def _safe_segment(volume_id: str) -> bool:
    return bool(volume_id) and not (
        "/" in volume_id or "\\" in volume_id or volume_id.startswith(".")
    )


def export_volume(
    results: Results,
    client: httpx.Client,
    prefix: str,
    volume_id: str,
    opts: Options,
    dry_run: bool,
) -> Row:
    if not _safe_segment(volume_id):
        return Row(volume_id, "failed", reason="not a safe folder name")
    base = f"{prefix}/{quote(volume_id, safe='')}"
    try:
        return _export(results, client, base, volume_id, opts, dry_run)
    except Forbidden:
        return Row(
            volume_id, "forbidden", reason="your account may not read this volume"
        )
    except ResultsUnavailable as exc:
        return Row(volume_id, "failed", reason=str(exc))


def _export(
    results: Results,
    client: httpx.Client,
    base: str,
    volume_id: str,
    opts: Options,
    dry_run: bool,
) -> Row:
    raw = results.get(f"{base}/manifest.json")
    if raw is None:
        return Row(volume_id, "not finished")
    try:
        manifest = json.loads(raw)
        expected = int(manifest["pages"])
        entries: dict[str, dict] = manifest["results"]
        sources: dict[str, str] = manifest["page_sources"]
    except (ValueError, KeyError, TypeError) as exc:
        return Row(volume_id, "failed", reason=f"manifest unreadable: {exc!r}")

    def failed(reason: str, **kw) -> Row:
        return Row(volume_id, "failed", pages=expected, reason=reason, **kw)

    keys = sorted(entries)
    numbers: dict[str, int] = {}
    for key in keys:
        try:
            numbers[key] = page_number(sources[key], volume_id)
        except (PageRuleError, KeyError) as exc:
            return failed(f"page {key}: {exc}")

    if len(set(numbers.values())) != len(keys):
        return failed("page numbers are not unique")

    empty_keys = {k for k in keys if numbers[k] in opts.empty_pages}
    mean, counted = quality_mean(entries, empty_keys)
    stats: dict[str, Any] = {"quality_mean": mean, "quality_pages": counted}
    if opts.min_quality is not None:
        if mean is None:
            return Row(volume_id, "no scores", pages=expected, **stats)
        if mean < opts.min_quality:
            return Row(volume_id, "below threshold", pages=expected, **stats)

    sizes: dict[str, tuple[int, int]] = {}
    for key in keys:
        try:
            sizes[key] = image_size(client, info_url(sources[key]))
        except (SizeUnreadable, PageRuleError) as exc:
            return failed(f"page {key}: {exc}", **stats)

    tmp = opts.target / f".{volume_id}.tmp-{secrets.token_hex(4)}"
    if not dry_run:
        opts.target.mkdir(parents=True, exist_ok=True)
        _clear_leftovers(opts.target, volume_id)
        tmp.mkdir()
    try:
        row = _build(
            results,
            base,
            volume_id,
            entries,
            keys,
            numbers,
            sizes,
            empty_keys,
            expected,
            stats,
            tmp,
            dry_run,
        )
        if row.outcome == "exported" and not dry_run:
            swap_in(tmp, opts.target / volume_id)
        return row
    finally:
        if not dry_run:
            shutil.rmtree(tmp, ignore_errors=True)


def _build(
    results,
    base,
    volume_id,
    entries,
    keys,
    numbers,
    sizes,
    empty_keys,
    expected,
    stats,
    tmp,
    dry_run,
) -> Row:  # noqa: PLR0913
    def failed(reason: str) -> Row:
        return Row(volume_id, "failed", pages=expected, reason=reason, **stats)

    with_text = filled = chosen = built = 0
    for key in keys:
        number, native = numbers[key], sizes[key]
        stem = f"{volume_id}_{number:05d}"
        alto = None
        if key in empty_keys:
            chosen += 1
        elif entries[key].get("status") in ("ok", "skipped"):
            alto = results.get(f"{base}/alto/{key}.xml")
        if alto is None:
            if key not in empty_keys:
                filled += 1
            data = empty_alto(native, stem, number, key)
        else:
            try:
                data = scale_alto(alto, native, stem, number)
            except AltoMismatch as exc:
                return failed(f"page {key}: {exc}")
            with_text += 1
        if not dry_run:
            (tmp / f"{stem}.xml").write_bytes(data)
        built += 1

    if not len(entries) == expected == built:
        return failed(f"{len(entries)} results, {expected} pages, {built} files built")
    return Row(
        volume_id,
        "exported",
        pages=expected,
        pages_with_text=with_text,
        pages_empty_filled=filled,
        pages_empty_chosen=chosen,
        **stats,
    )
