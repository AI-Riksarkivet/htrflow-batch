"""IIIF Presentation 3 manifest -> ordered page list (docs: wrapper)."""

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


def _image_url(canvas: dict, width: int) -> str | None:
    for ap in canvas.get("items", []):
        for anno in ap.get("items", []):
            body = anno.get("body") or {}
            services = body.get("service") or []
            if services:
                sid = services[0].get("id") or services[0].get("@id")
                if sid:
                    # NOTE: lbiiif rejects "!w,h" (501); "w," is the supported
                    # form. Level1 servers also reject upscaling (400), so a
                    # canvas narrower than the cap must ask for max instead.
                    cw = canvas.get("width")
                    size = "max" if cw and cw <= width else f"{width},"
                    return f"{sid.rstrip('/')}/full/{size}/0/default.jpg"
            if body.get("id"):
                return body["id"]
    return None


def pages_from_manifest(manifest: dict, width: int) -> list[PageRef]:
    canvases = manifest.get("items") or []
    pages: list[PageRef] = []
    for i, canvas in enumerate(canvases, start=1):
        url = _image_url(canvas, width)
        if url is None:
            raise ManifestError(f"canvas {i} has no image")
        pages.append(PageRef(index=i, name=f"{i:04d}", image_url=url, canvas=canvas))
    if not pages:
        raise ManifestError("manifest has no canvases")
    return pages
