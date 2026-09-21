"""IIIF Presentation 2/3 manifest -> ordered page list (docs: wrapper)."""

from __future__ import annotations

import hashlib
import json
import re

import httpx
from pydantic import BaseModel, ConfigDict

from .bounded import ACCEPT_ENCODING, BadEncoding, TooLarge, body_chunks

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


#: Query parameters that carry a credential rather than name the image; they
#: rotate, so they must not reach the digest below. ``X-Amz-*`` (presigned S3)
#: is matched by prefix.
_CREDENTIAL_PARAMS = frozenset({"token", "sig", "signature", "key"})


def source_digest(url: str) -> str:
    """A stable identity for a page's source image, for the resume comparison
    (W5, 2026-09-14 audit).

    ``redact_url`` drops the whole query, and that is the only form of the URL
    the public manifest may carry (S6) -- so on a host that selects the image
    with ``?id=`` every page of a volume looked identical and an edited
    manifest never triggered a reprocess. A digest keeps the query without
    publishing it. The credentials come out first: userinfo, the ``X-Amz-*``
    presign parameters and the token/sig/signature/key families rotate, and a
    re-signed URL is not a new source image."""
    try:
        parsed = httpx.URL(url)
        # a tuple, not a list: httpx types the parameter pairs as invariant
        kept = tuple(
            (name, value)
            for name, value in parsed.params.multi_items()
            if name.lower() not in _CREDENTIAL_PARAMS
            and not name.lower().startswith("x-amz-")
        )
        text = str(parsed.copy_with(userinfo=b"", params=httpx.QueryParams(kept)))
    except Exception:
        text = url.split("?", 1)[0]  # not a URL we can parse: path only
    return hashlib.sha256(text.encode()).hexdigest()


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
    over the cap once decoded, a Content-Encoding other than gzip, non-JSON
    or non-object JSON. Transient
    (TransientManifestError): connection/timeout errors, 5xx, 429 and any
    other non-200 status.
    """
    check_http_url(url, "manifest URL")
    shown = redact_url(url)
    try:
        with client.stream(
            "GET",
            url,
            headers={"Accept-Encoding": ACCEPT_ENCODING},
            timeout=60,
            follow_redirects=True,
        ) as resp:
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
            chunks = list(body_chunks(resp, max_bytes))  # 3062: decoded bytes
    except TooLarge as e:
        raise ManifestError(f"manifest too large: {shown}: {e}") from e
    except BadEncoding as e:
        raise ManifestError(f"manifest refused: {shown}: {e}") from e
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


def _publishable(body: dict) -> bool:
    """Whether a painting body may be copied into the manifest we publish
    (W6, 2026-09-14 audit).

    The body comes out of a third-party manifest and goes, verbatim, into an
    iiif.json served from our own domain -- so every URL in it is a viewer's
    idea of what to load. Only the URL the wrapper FETCHES was ever
    scheme-checked, and on a canvas with an image service that is the
    service's: a body whose ``id`` reads ``javascript:`` sailed past it and
    was published. Both ids are checked here; a body that fails is dropped,
    which costs that canvas its image and nothing else."""
    ids = [body.get("id") or body.get("@id")]
    service = body.get("service")
    for entry in service if isinstance(service, list) else [service]:
        if isinstance(entry, dict):
            ids.append(entry.get("id") or entry.get("@id"))
    for value in ids:
        if not isinstance(value, str) or not value:
            return False
        try:
            check_http_url(value, "canvas image")
        except ManifestError:
            return False
    return True


def _body_candidates(body: object) -> list[dict]:
    """The bodies one painting annotation offers, in order.

    Usually one. P3 also allows a ``Choice`` -- several representations of the
    same page, the client picking one -- and manifests in the wild put a bare
    list there as well. Both are flattened so the first body that may be
    published wins, rather than the annotation being dropped for a shape."""
    if isinstance(body, list):
        candidates = body
    elif isinstance(body, dict) and body.get("type") == "Choice":
        items = body.get("items")
        candidates = items if isinstance(items, list) else []
    else:
        candidates = [body]
    return [item for item in candidates if isinstance(item, dict)]


def _p2_body(res: dict) -> dict:
    """A P2 image resource as a P3-style body. The service keeps v2-style
    keys (@id/@type/profile): UV silently shows no image otherwise (docs:
    wrapper)."""
    body = {
        "id": res.get("@id") or res.get("id"),
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


def _fetch_url(body: dict, canvas: dict, width: int) -> str | None:
    """Where a body's image is downloaded from: its image service, sized, or
    the body itself when it has none."""
    sid = _service_id(body.get("service"))
    if sid:
        return _sized(sid, canvas, width)
    return _body_id(body)


def _body_id(body: dict) -> str | None:
    return body.get("id") or body.get("@id")


def _canvas_image(canvas: dict) -> tuple[dict, bool]:
    """THE image of a canvas, and whether it may be published (3097).

    The one rule both halves use: ``pages_from_manifest`` fetches this body
    and ``painting_body`` publishes it, so the ALTO drawn from the fetched
    image always lands on the image the viewer shows. They were two rules
    once, and a Choice or list body failed the volume, while a canvas whose
    first body was unpublishable fetched one image and published another.
    The first publishable body wins; if there is none the first one with a
    URL is still fetched and transcribed (W6: an unpublishable body costs the
    canvas its image in the viewer and nothing else)."""
    bodies: list[dict] = []
    for ap in canvas.get("items", []):  # P3
        for anno in ap.get("items", []):
            bodies += _body_candidates(anno.get("body"))
    for img in canvas.get("images", []):  # P2
        bodies.append(_p2_body(img.get("resource") or {}))
    for body in bodies:
        if _publishable(body):
            return body, True
    fetchable = (b for b in bodies if _service_id(b.get("service")) or _body_id(b))
    return next(fetchable, {}), False


def painting_body(canvas: dict) -> dict:
    """P3-style annotation body to publish for a P3 or P2 canvas: the image
    ``_canvas_image`` chose, or nothing when it may not be published."""
    body, publishable = _canvas_image(canvas)
    return body if publishable else {}


def pages_from_manifest(manifest: dict, width: int) -> list[PageRef]:
    canvases = manifest.get("items")
    if not canvases:  # P2: sequences[0].canvases, in a manifest of any shape
        seqs = manifest.get("sequences")
        first = seqs[0] if isinstance(seqs, list) and seqs else {}
        canvases = first.get("canvases") if isinstance(first, dict) else []
        # Neither shape carries canvases at all: that is an empty manifest,
        # which the "no canvases" error below names accurately. Reserve
        # "items are not a list" for an `items` that really is not one.
        if canvases is None:
            canvases = []
    if not isinstance(canvases, list):
        raise ManifestError("manifest items are not a list of canvases")
    pages: list[PageRef] = []
    for i, canvas in enumerate(canvases, start=1):
        # A junk shape (canvas items not a list, a body that is a bare URL)
        # raised AttributeError/TypeError: exit 1 and three retries of a
        # condition that cannot change. Permanent, like a missing image.
        try:
            body = _canvas_image(canvas)[0] if isinstance(canvas, dict) else {}
            url = _fetch_url(body, canvas, width) if body else None
        except (AttributeError, TypeError, KeyError, IndexError) as e:
            raise ManifestError(f"canvas {i} is malformed: {e!r}") from e
        if not isinstance(url, str) or not url:
            raise ManifestError(f"canvas {i} has no image")
        # S5: the manifest is campaign data. A file:/ftp: body id used to
        # reach httpx per page (UnsupportedProtocol) and be retried; it is
        # permanent, and named as such, here.
        check_http_url(url, f"canvas {i} image URL")
        pages.append(PageRef(index=i, name=f"{i:04d}", image_url=url, canvas=canvas))
    if not pages:
        raise ManifestError("manifest has no canvases")
    return pages
