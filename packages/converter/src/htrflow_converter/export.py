"""One volume of ``htrflow-campaigns export``: read, check, build, swap.

A volume is published whole or not at all. Files are built in a hidden
sibling folder and renamed over the real one only after every check has
passed, so the real folder name never carries a partial volume.
"""

from __future__ import annotations

import csv
import json
import secrets
import shutil
import sys
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from htrflow_converter.export_alto import AltoMismatch, empty_alto, scale_alto
from htrflow_converter.export_http import (
    Forbidden,
    Results,
    ResultsUnavailable,
    SessionLost,
    SizeUnreadable,
    image_size,
)
from htrflow_converter.export_pages import (
    PageRuleError,
    info_url,
    page_number,
    quality_mean,
)
from htrflow_converter.parse import ValidationError, load

__all__ = [
    "Options",
    "Row",
    "check_inputs",
    "export_volume",
    "run_export",
    "swap_in",
    "write_report",
]


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
    old: Path | None = final.with_name(f".{final.name}.old-{secrets.token_hex(4)}")
    try:
        final.rename(old)
    except FileNotFoundError:  # none there, or ingested just now
        old = None
    try:
        built.rename(final)
    except BaseException:
        if old is not None:
            old.rename(final)
        raise
    if old is not None:
        shutil.rmtree(old, ignore_errors=True)


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


_STATUSES = ("ok", "skipped", "failed")


def _read_manifest(raw: bytes) -> tuple[int, dict[str, dict], dict[str, str], bool]:
    """``(pages, results, page_sources, whole)``, each of the type the export
    relies on; a ``ValueError`` names what is not. ``whole`` is False for a
    volume made from an image list (``images: source``): with no source
    manifest it may be any part of a series."""
    manifest = json.loads(raw)
    if not isinstance(manifest, dict):
        raise ValueError("not a JSON object")
    pages, entries = manifest.get("pages"), manifest.get("results")
    sources = manifest.get("page_sources")
    if not isinstance(pages, int) or isinstance(pages, bool) or pages < 1:
        raise ValueError(f"pages is {pages!r}, not a page count")
    if not isinstance(entries, dict) or not all(
        isinstance(e, dict) for e in entries.values()
    ):
        raise ValueError("results is not an object of page objects")
    if not isinstance(sources, dict) or not all(
        isinstance(u, str) for u in sources.values()
    ):
        raise ValueError("page_sources is not an object of URLs")
    return pages, entries, sources, isinstance(manifest.get("source_manifest"), str)


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
        manifest = _read_manifest(raw)
    except ValueError as exc:
        return Row(volume_id, "failed", reason=f"manifest unreadable: {exc}")
    expected, entries, sources, whole = manifest

    def failed(reason: str, **kw) -> Row:
        return Row(volume_id, "failed", pages=expected, reason=reason, **kw)

    if not whole:
        return failed("not a whole volume (images: source)")

    keys = sorted(entries)
    numbers: dict[str, int] = {}
    for key in keys:
        if entries[key].get("status") not in _STATUSES:
            return failed(f"page {key}: unknown status {entries[key].get('status')!r}")
        try:
            numbers[key] = page_number(sources[key], volume_id)
        except (PageRuleError, KeyError) as exc:
            return failed(f"page {key}: {exc}")

    if len(set(numbers.values())) != len(keys):
        return failed("page numbers are not unique")
    # The publishing side takes whole image series: 1..pages, no gap.
    series, found = set(range(1, expected + 1)), set(numbers.values())
    if missing := sorted(series - found):
        return failed(f"no page numbered {missing[0]} (the volume has {expected})")
    if extra := sorted(found - series):
        return failed(f"page number {extra[0]} is past the volume's {expected}")

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
        # No mkdir of the target: check_inputs saw it, and a share that has
        # gone since must fail here, not turn into a local folder.
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
        if row.outcome != "exported":
            return row
        # A re-run of the volume deletes manifest.json before its stale
        # pages: if the manifest is not what this export read, the pages
        # just built may mix two runs.
        if results.get(f"{base}/manifest.json") != raw:
            return Row(
                volume_id,
                "failed",
                pages=expected,
                reason="volume changed during export",
                **stats,
            )
        if dry_run:
            return replace(row, outcome="would export")
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
        status = entries[key].get("status")
        if key in empty_keys:
            chosen += 1
        elif status in ("ok", "skipped"):
            # The wrapper keeps an ALTO for every ok or skipped page; none
            # means the bucket changed under this export, not a blank page.
            alto = results.get(f"{base}/alto/{key}.xml")
            if alto is None:
                article = "an" if status == "ok" else "a"
                return failed(f"page {key}: ALTO missing for {article} {status} page")
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


_OUTCOMES = (
    "exported",
    "would export",
    "below threshold",
    "not finished",
    "no scores",
    "forbidden",
    "failed",
)


_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


def _plain_text_url(url: str) -> bool:
    """The password would cross the network unencrypted to this URL."""
    parts = urlsplit(url)
    if parts.scheme == "https":
        return False
    return not (parts.scheme == "http" and parts.hostname in _LOCAL_HOSTS)


def check_inputs(
    campaign_file: Path,
    target: Path,
    report: Path | None = None,
    results_url: str | None = None,
):
    """``(campaign, config)`` for the campaign file, or the one-line reason
    the run is refused. Nothing is asked of the network and nothing is
    created: an unmounted share must not turn into a local folder."""
    if not target.is_dir():
        return f"{target} is not a directory (it is not created for you)"
    if report is not None and report.resolve().is_relative_to(target.resolve()):
        return f"the report {report} would be inside the target; put it elsewhere"
    if report is not None and not report.parent.is_dir():
        return f"the report's folder {report.parent} does not exist"
    repo = campaign_file.resolve().parent.parent
    try:
        campaigns, _, cfg = load(
            repo / "campaigns", repo / "pipelines", repo / "converter.yaml"
        )
    except ValidationError as exc:
        return f"the campaigns repo does not validate: {exc}"
    url = results_url or cfg.results_url
    if _plain_text_url(url):
        return f"the results URL {url} is not https; the password would cross in clear"
    for campaign in campaigns:
        if campaign.name == campaign_file.stem:
            return campaign, cfg
    return f"{campaign_file} is not a campaign of the repo {repo}"


def write_report(rows: list[Row], path: Path) -> str:
    """The CSV at ``path``; the aligned table and a summary line, returned."""
    names = [f.name for f in fields(Row)]

    def cells(row: Row) -> list[str]:
        out = []
        for name in names:
            value = getattr(row, name)
            if name == "quality_mean":
                value = "" if value is None else round(value, 3)
            out.append(str(value))
        return out

    body = [cells(r) for r in rows]
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(names)
        writer.writerows(body)
    widths = [max(len(h), *(len(r[i]) for r in body)) for i, h in enumerate(names)]
    lines = [
        "  ".join(c.ljust(w) for c, w in zip(line, widths, strict=True)).rstrip()
        for line in [names, *body]
    ]
    counts = [
        f"{sum(r.outcome == o for r in rows)} {o}"
        for o in _OUTCOMES
        if any(r.outcome == o for r in rows)
    ]
    return "\n".join([*lines, ", ".join(counts)])


_ALL_UNFINISHED = (
    "every volume read as not finished: check that converter.yaml's namespace "
    "and results_url point at the deployment that ran this campaign"
)


def run_export(
    campaign_file: Path,
    target: Path,
    *,
    results_url: str | None,
    user: str,
    password: str,
    opts: Options,
    report: Path,
    dry_run: bool,
    client: httpx.Client | None = None,
) -> int:
    checked = check_inputs(campaign_file, target, report, results_url)
    if isinstance(checked, str):
        print(checked, file=sys.stderr)
        return 2
    campaign, cfg = checked
    own = client is None
    if client is None:
        client = httpx.Client(
            timeout=httpx.Timeout(60.0, connect=10.0), follow_redirects=False
        )
    results = Results(results_url or cfg.results_url, user, password, client)
    prefix = f"{cfg.namespace}/{campaign.pipeline}"
    ids = [v.id for v in campaign.volumes]
    rows: list[Row] = []
    stopped = ""
    try:
        try:
            results.login()
            for volume_id in ids:
                rows.append(
                    export_volume(results, client, prefix, volume_id, opts, dry_run)
                )
        except (SessionLost, OSError) as exc:
            stopped = f"run stopped: {exc}"
    finally:
        if own:
            client.close()
    if stopped:
        print(stopped, file=sys.stderr)
        rows += [Row(v, "failed", reason=stopped) for v in ids[len(rows) :]]
    print(write_report(rows, report))
    print(f"report: {report}")
    if rows and all(r.outcome == "not finished" for r in rows):
        print(_ALL_UNFINISHED, file=sys.stderr)
    return 1 if any(r.outcome in ("failed", "forbidden") for r in rows) else 0
