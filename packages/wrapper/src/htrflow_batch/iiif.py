"""IIIF Presentation 2/3 manifest -> ordered page list (docs: wrapper)."""

from __future__ import annotations

import httpx
from pydantic import BaseModel, ConfigDict


class ManifestError(Exception):
    """Permanent: bad/empty/unreachable manifest -> exit 13."""


class PageRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    index: int  # 1-based position in manifest order
    name: str  # zero-padded, e.g. "0001" — S3 key + filename stem
    image_url: str  # width-capped IIIF image request
    canvas: dict  # raw source canvas (for viewer.build_viewer_manifest)


def fetch_manifest(url: str, client: httpx.Client) -> dict:
    try:
        resp = client.get(url, timeout=60, follow_redirects=True)
    except httpx.HTTPError as e:
        raise ManifestError(f"manifest fetch failed: {url}: {e}") from e
    if resp.status_code != 200:
        raise ManifestError(f"manifest fetch failed: {url}: HTTP {resp.status_code}")
    try:
        return resp.json()
    except ValueError as e:
        raise ManifestError(f"manifest is not JSON: {url}") from e


def _service_id(service: object) -> str | None:
    """P2 allows a bare dict, P3 a list; both use `id` or `@id`."""
    if isinstance(service, list):
        service = service[0] if service else None
    if isinstance(service, dict):
        sid = service.get("id") or service.get("@id")
        return sid if isinstance(sid, str) else None
    return None


def _sized(sid: str, canvas: dict, width: int) -> str:
    # NOTE: lbiiif rejects "!w,h" (501); "w," is the supported form.
    # Level1 servers also reject upscaling (400), so a canvas narrower
    # than the cap must ask for max instead.
    cw = canvas.get("width")
    size = "max" if cw and cw <= width else f"{width},"
    return f"{sid.rstrip('/')}/full/{size}/0/default.jpg"


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


def pages_from_manifest(manifest: dict, width: int) -> list[PageRef]:
    canvases = manifest.get("items") or []
    if not canvases:  # P2: sequences[0].canvases
        seqs = manifest.get("sequences") or []
        canvases = (seqs[0].get("canvases") or []) if seqs else []
    pages: list[PageRef] = []
    for i, canvas in enumerate(canvases, start=1):
        url = _image_url(canvas, width)
        if url is None:
            raise ManifestError(f"canvas {i} has no image")
        pages.append(PageRef(index=i, name=f"{i:04d}", image_url=url, canvas=canvas))
    if not pages:
        raise ManifestError("manifest has no canvases")
    return pages
