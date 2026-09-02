"""IIIF Presentation 2/3 manifest -> ordered page list (docs: wrapper)."""

from __future__ import annotations

import json
import re

import httpx
from pydantic import BaseModel, ConfigDict

#: Default cap on manifest bytes (env ``MANIFEST_MAX_BYTES``; docs: wrapper).
MANIFEST_MAX_BYTES = 16 * 1024 * 1024

#: Status codes that mean "this URL will not work tomorrow either".
#: Everything else non-200 (5xx, 429, odd 4xx) is retried by Kubernetes.
PERMANENT_STATUSES = frozenset({400, 401, 403, 404, 410})


class ManifestError(Exception):
    """Permanent: bad/empty/oversized manifest -> exit 13."""


class TransientManifestError(Exception):
    """Retryable: network error, 5xx, 429 on the manifest fetch -> exit 1.
    Deliberately NOT a ManifestError subclass: main.py's permanent branch
    catches ManifestError by type."""


class PageRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    index: int  # 1-based position in manifest order
    name: str  # zero-padded, e.g. "0001" — S3 key + filename stem
    image_url: str  # width-capped IIIF image request
    canvas: dict  # raw source canvas (for viewer.build_viewer_manifest)


def redact_url(url: str) -> str:
    """URL as it may appear in logs/errors: no userinfo, no query (S6).
    Tokenised private IIIF URLs would otherwise land in the world-readable
    run log and termination message."""
    try:
        u = httpx.URL(url)
    except Exception:
        return url.split("?", 1)[0].split("#", 1)[0]
    if not u.scheme:
        return url.split("?", 1)[0].split("#", 1)[0]
    host = u.host
    if u.port is not None:
        host = f"{host}:{u.port}"
    return f"{u.scheme}://{host}{u.path}"


_URL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s\"'<>)\]]+")


def redact_urls(text: str) -> str:
    """Apply redact_url to every URL inside free text (log lines, error
    messages, tracebacks)."""
    return _URL_RE.sub(lambda m: redact_url(m.group(0)), text)


def check_http_url(url: str, what: str) -> None:
    """S5: only http(s) URLs may be fetched; campaign data is untrusted."""
    scheme = url.split(":", 1)[0].lower() if ":" in url else ""
    if scheme not in ("http", "https"):
        raise ManifestError(f"{what} must be an http(s) URL: {redact_url(url)}")


def fetch_manifest(
    url: str, client: httpx.Client, max_bytes: int = MANIFEST_MAX_BYTES
) -> dict:
    """GET a IIIF manifest as a JSON object, bounded by ``max_bytes``.

    Permanent (ManifestError): non-http(s) URL, 400/401/403/404/410, body
    over the cap, non-JSON or non-object JSON. Transient
    (TransientManifestError): connection/timeout errors, 5xx, 429 and any
    other non-200 status.
    """
    check_http_url(url, "manifest URL")
    shown = redact_url(url)
    try:
        with client.stream("GET", url, timeout=60, follow_redirects=True) as resp:
            if resp.status_code in PERMANENT_STATUSES:
                raise ManifestError(
                    f"manifest fetch failed: {shown}: HTTP {resp.status_code}"
                )
            if resp.status_code != 200:
                raise TransientManifestError(
                    f"manifest fetch failed: {shown}: HTTP {resp.status_code}"
                )
            declared = resp.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise ManifestError(
                    f"manifest too large: {shown}: {declared} bytes > {max_bytes}"
                )
            chunks: list[bytes] = []
            size = 0
            for chunk in resp.iter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    raise ManifestError(
                        f"manifest too large: {shown}: > {max_bytes} bytes"
                    )
                chunks.append(chunk)
    except httpx.HTTPError as e:
        raise TransientManifestError(
            f"manifest fetch failed: {shown}: {type(e).__name__}: {e}"
        ) from e
    try:
        data = json.loads(b"".join(chunks))
    except ValueError as e:
        raise ManifestError(f"manifest is not JSON: {shown}") from e
    if not isinstance(data, dict):
        raise ManifestError(f"manifest is not a JSON object: {shown}")
    return data


def _service_id(service: object) -> str | None:
    """P2 allows a bare dict, P3 a list; both use `id` or `@id`."""
    if isinstance(service, list):
        service = service[0] if service else None
    if isinstance(service, dict):
        sid = service.get("id") or service.get("@id")
        return sid if isinstance(sid, str) else None
    return None


def _sized(sid: str, canvas: dict, width: int) -> str:
    # lbiiif rejects "!w,h" (501), "w," is supported; Level1 servers reject
    # upscaling (400), so a canvas narrower than the cap must ask for max.
    cw = _int_or_none(canvas.get("width"))
    size = "max" if cw and cw <= width else f"{width},"
    return f"{sid.rstrip('/')}/full/{size}/0/default.jpg"


def _int_or_none(value: object) -> int | None:
    """W11: manifests in the wild carry widths as strings (or junk); a
    TypeError here used to fail the whole volume, retried to the cap."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value.strip()))
        except ValueError:
            return None
    return None


def _image_url(canvas: dict, width: int) -> str | None:
    for ap in canvas.get("items", []):  # P3
        for anno in ap.get("items", []):
            body = anno.get("body") or {}
            sid = _service_id(body.get("service"))
            if sid:
                return _sized(sid, canvas, width)
            if body.get("id"):
                return body["id"]
    for img in canvas.get("images", []):  # P2
        res = img.get("resource") or {}
        sid = _service_id(res.get("service"))
        if sid:
            return _sized(sid, canvas, width)
        rid = res.get("@id") or res.get("id")
        if rid:
            return rid
    return None


def painting_body(canvas: dict) -> dict:
    """P3-style annotation body for a P3 or P2 canvas. P2 services are
    emitted with v2-style keys (@id/@type/profile) — UV silently shows no
    image otherwise (docs: wrapper)."""
    for ap in canvas.get("items", []):
        for anno in ap.get("items", []):
            if anno.get("body"):
                return anno["body"]
    for img in canvas.get("images", []):
        res = img.get("resource") or {}
        rid = res.get("@id") or res.get("id")
        if not rid:
            continue
        body: dict = {
            "id": rid,
            "type": "Image",
            "format": res.get("format", "image/jpeg"),
        }
        sid = _service_id(res.get("service"))
        if sid:
            body["service"] = [
                {
                    "@id": sid,
                    "@type": "ImageService2",
                    "profile": "http://iiif.io/api/image/2/level2.json",
                }
            ]
        return body
    return {}


def _p2_canvases(manifest: dict) -> list:
    """P2: sequences[0].canvases, tolerant of a manifest that is not shaped
    like one."""
    seqs = manifest.get("sequences")
    first = seqs[0] if isinstance(seqs, list) and seqs else None
    canvases = first.get("canvases") if isinstance(first, dict) else None
    return canvases if isinstance(canvases, list) else []


def pages_from_manifest(manifest: dict, width: int) -> list[PageRef]:
    canvases = manifest.get("items") or _p2_canvases(manifest)
    if not isinstance(canvases, list):
        raise ManifestError("manifest items are not a list of canvases")
    pages: list[PageRef] = []
    for i, canvas in enumerate(canvases, start=1):
        # A junk canvas shape (items that is not a list, an annotation whose
        # body is a bare URL string) used to raise AttributeError/TypeError
        # -> exit 1 and three retries of a condition that cannot change.
        # It is the manifest that is wrong: permanent, like a missing image.
        try:
            url = _image_url(canvas, width) if isinstance(canvas, dict) else None
        except (AttributeError, TypeError, KeyError, IndexError) as e:
            raise ManifestError(
                f"canvas {i} is malformed: {type(e).__name__}: {e}"
            ) from e
        if not isinstance(url, str) or not url:
            raise ManifestError(f"canvas {i} has no image")
        pages.append(PageRef(index=i, name=f"{i:04d}", image_url=url, canvas=canvas))
    if not pages:
        raise ManifestError("manifest has no canvases")
    return pages
