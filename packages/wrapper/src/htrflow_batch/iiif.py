"""IIIF Presentation 2/3 manifest -> ordered page list (docs: wrapper)."""

from __future__ import annotations

import hashlib
import json
import re

import httpx
from pydantic import BaseModel, ConfigDict

from .bounded import (
    DOWNLOAD_DEADLINE_SECONDS,
    BadEncoding,
    Deadline,
    TooLarge,
    body_chunks,
    get,
)

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


#: Query parameters that authorise a request rather than name what it asks
#: for; they rotate, so they must not reach the digest below. Each belongs to
#: a signing scheme that defines it (audit 0923 W-2): the generic token
#: names, Akamai's EdgeAuth, and the S3 SigV4 / GCS V4 prefixes.
_CREDENTIAL_PARAMS = frozenset(
    {"token", "access_token", "sig", "signature", "key", "__token__", "hdnts"}
)
_CREDENTIAL_PREFIXES = ("x-amz-", "x-goog-")

#: Signing schemes whose other parameters have names too ordinary to drop on
#: sight (`st`, `se`, `Expires`, `Policy` may name an image elsewhere): they
#: go only beside the scheme's own marker. Azure SAS, CloudFront signed
#: URLs, S3 SigV2 and GCS V2 query authentication.
_SIGNED_FAMILIES = (
    (
        "sig",
        frozenset(
            "sv ss srt sp se st spr sr si sdd skoid sktid skt ske sks "
            "skv saoid suoid scid ses rscc rscd rsce rscl rsct".split()
        ),
    ),
    ("key-pair-id", frozenset({"expires", "policy", "signature"})),
    ("awsaccesskeyid", frozenset({"expires", "signature"})),
    ("googleaccessid", frozenset({"expires", "signature"})),
)


def _credentials(names: set[str]) -> set[str]:
    """The lower-cased parameter names among ``names`` that authorise."""
    drop = {n for n in names if n in _CREDENTIAL_PARAMS}
    drop |= {n for n in names if n.startswith(_CREDENTIAL_PREFIXES)}
    for marker, members in _SIGNED_FAMILIES:
        if marker in names:
            drop |= names & (members | {marker})
    return drop


def source_digest(url: str) -> str:
    """A stable identity for a page's source image, for the resume comparison
    (W5, 2026-09-14 audit).

    ``redact_url`` drops the whole query, and that is the only form of the URL
    the public manifest may carry (S6) -- so on a host that selects the image
    with ``?id=`` every page of a volume looked identical and an edited
    manifest never triggered a reprocess. A digest keeps the query without
    publishing it.

    What names the image is scheme, host, path and every query parameter
    that is not a credential; a credential authorises the request, rotates
    with every signing, and a re-signed URL is not a new source image. It is
    the credentials that are enumerated, not the naming parameters: those
    are the host's own (``?id=``, IIPImage's ``?IIIF=``, ...), while a
    credential belongs to one of a handful of signing schemes, each of which
    defines its names (``_credentials``). Userinfo goes too."""
    try:
        parsed = httpx.URL(url)
        pairs = parsed.params.multi_items()
        drop = _credentials({name.lower() for name, _ in pairs})
        # a tuple, not a list: httpx types the parameter pairs as invariant
        kept = tuple((n, v) for n, v in pairs if n.lower() not in drop)
        text = str(parsed.copy_with(userinfo=b"", params=httpx.QueryParams(kept)))
    except Exception:
        text = url.split("?", 1)[0]  # not a URL we can parse: path only
    return hashlib.sha256(text.encode()).hexdigest()


#: A URL in free text (audit 0923 W-3, review M-3). It never holds what RFC
#: 3986 leaves out of a URL unencoded -- whitespace, `"<>` and backtick,
#: `{}|^`. A `'`, `]`, `)` or `,` may be inside one (`img(1).jpg`,
#: IIIF's `/full/2500,/`), so each ends it only where it closes it:
#: followed by whitespace, a separator, another closer, the end, or the
#: next URL. Stopping at any of them left the query after `img(1).jpg`
#: unredacted; running to whitespace swallowed the rest of a list.
_SCHEME = r"[A-Za-z][A-Za-z0-9+.-]*://"
_URL_RE = re.compile(
    _SCHEME + r"(?:[^\s\"<>`{}|^'\]),]"
    r"|['\]),](?![\s,;'\"\])}]|$|" + _SCHEME + r"))+"
)
_TRAILING = ")]}'.,;:!?"


def _redact_match(match: re.Match) -> str:
    url = match.group(0)
    core = url.rstrip(_TRAILING)
    return redact_url(core) + url[len(core) :]


def redact_urls(text: str) -> str:
    """Apply redact_url to every URL inside free text (log lines, error
    messages, tracebacks)."""
    return _URL_RE.sub(_redact_match, text)


def check_http_url(url: str, what: str) -> None:
    """S5: only http(s) URLs may be fetched; campaign data is untrusted."""
    scheme = url.split(":", 1)[0].lower() if ":" in url else ""
    if scheme not in ("http", "https"):
        raise ManifestError(f"{what} must be an http(s) URL: {redact_url(url)}")


def fetch_manifest(
    url: str,
    client: httpx.Client,
    max_bytes: int = MANIFEST_MAX_BYTES,
    deadline: float = DOWNLOAD_DEADLINE_SECONDS,
) -> dict:
    """GET a IIIF manifest as a JSON object, bounded by ``max_bytes``.

    Permanent (ManifestError): non-http(s) URL, 400/401/403/404/410, body
    over the cap once decoded, a Content-Encoding other than gzip, non-JSON
    or non-object JSON. Transient (TransientManifestError): connection and
    timeout errors, the download deadline, 5xx, 429 and any other non-200
    status.
    """
    check_http_url(url, "manifest URL")
    shown = redact_url(url)
    clock = Deadline(deadline)  # 3063
    try:
        with get(client, url, 60, clock) as resp:
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
        if clock.expired:  # a connection-delimited body cut short
            raise TransientManifestError(
                f"manifest fetch failed: {shown}: {clock.reason}"
            )
    except TooLarge as e:
        raise ManifestError(f"manifest too large: {shown}: {e}") from e
    except BadEncoding as e:
        raise ManifestError(f"manifest refused: {shown}: {e}") from e
    except httpx.HTTPError as e:
        why = clock.reason if clock.expired else f"{type(e).__name__}: {e}"
        raise TransientManifestError(f"manifest fetch failed: {shown}: {why}") from e
    try:
        data = json.loads(b"".join(chunks))
    except ValueError as e:
        raise ManifestError(f"manifest is not JSON: {shown}") from e
    if not isinstance(data, dict):
        raise ManifestError(f"manifest is not a JSON object: {shown}")
    return data


def _image_service(service: object) -> dict | None:
    """The first image service: P2 allows a bare dict, P3 a list, which may
    lead with an auth or search service. An entry with no type, as P2's
    usually have, is one unless its context or profile names another IIIF
    API (``/api/auth/``, ``/api/search/``)."""
    for entry in service if isinstance(service, list) else [service]:
        if isinstance(entry, dict):
            kind = entry.get("type") or entry.get("@type")
            if isinstance(kind, str):
                if kind.startswith("ImageService"):
                    return entry
            elif not _OTHER_API.search(_marks(entry)):
                return entry
    return None


def _service_id(service: object) -> str | None:
    """The image service's `id` (P3) or `@id` (P2)."""
    entry = _image_service(service) or {}
    sid = entry.get("id") or entry.get("@id")
    return sid if isinstance(sid, str) else None


_V1_OR_V2 = re.compile(r"ImageService[12]\b|/api/image/[12]/")
_V3 = re.compile(r"ImageService3\b|/api/image/3/")
_OTHER_API = re.compile(r"iiif\.io/api/(?!image/)")


def _marks(service: dict) -> str:
    """What says which API, and which version, a service is."""
    return json.dumps(
        [service.get(k) for k in ("type", "@type", "@context", "profile")]
    )


def full_size(service: dict) -> str:
    """The size that names the whole image, for a service or an info.json:
    Image API 3 requires `max` at every level and drops `full`; 2 requires
    `full` (2.1 made `max` optional, 2.0 has none). A service that names no
    version is taken for 3."""
    return "full" if _V1_OR_V2.search(_marks(service)) else "max"


def _sized(sid: str, image_width: int | None, width: int, whole: str) -> str:
    # Some IIIF servers reject "!w,h" (501) but take "w,"; Level1 servers reject
    # upscaling (400), so an image narrower than the cap must ask for it whole.
    size = whole if image_width and image_width <= width else f"{width},"
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
    wrapper). Its version and profile are the service's own -- a level it
    does not claim would have the viewer ask for sizes it 400s -- or 2 and
    level 0: P2 predates Image API 3, though manifests in the wild pair them."""
    body = {
        "id": res.get("@id") or res.get("id"),
        "type": "Image",
        "format": res.get("format", "image/jpeg"),
    }
    for key in ("width", "height"):
        if (value := _int_or_none(res.get(key))) is not None:
            body[key] = value
    if sid := _service_id(res.get("service")):
        service = _image_service(res["service"]) or {}
        v3 = bool(_V3.search(_marks(service)))
        body["service"] = [
            {
                "@id": sid,
                "@type": "ImageService3" if v3 else "ImageService2",
                "profile": service.get("profile")
                or ("level0" if v3 else "http://iiif.io/api/image/2/level0.json"),
            }
        ]
    return body


def _fetch_url(body: dict, canvas: dict, width: int) -> str | None:
    """Where a body's image is downloaded from: its image service, sized, or
    the body itself when it has none."""
    if sid := _service_id(body.get("service")):
        # Whole only when every width known fits: the canvas is a coordinate
        # space and the body is often a sized derivative, so neither alone is
        # the service image's. A wrong `w,` costs a refusal and the info.json
        # fallback; a wrong whole image costs a master in the lookahead.
        known = [_int_or_none(body.get("width")), _int_or_none(canvas.get("width"))]
        widest = max((w for w in known if w), default=None)
        return _sized(
            sid, widest, width, full_size(_image_service(body["service"]) or {})
        )
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
