"""Source images cached in S3 (docs: wrapper, "Image cache").

With ``IMAGE_CACHE_BUCKET`` set, a page's image is looked for at
``{ref}/{ref}_{page:05d}.jpg`` before it is downloaded, and stored there after
a download, so a volume run again -- under any pipeline or campaign -- needs
nothing from the IIIF server. The key carries no width: the image is kept as
the run that stored it fetched it.

The key does not say which source image it holds, so the object does: a PUT
records the page's ``source_identity`` as object metadata, and a GET whose
object records another source -- or none -- is a miss. Resume sends a page
whose source changed back to be fetched, and a volume id can be reused for
another volume; either way the old image must not answer for the new one.

The cache accelerates and is never a correctness dependency. Every call here
is best-effort: a miss, a cache error or a bad cached object sends the page
to the download it would have had anyway, and nothing in this module raises
into the page.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from pathlib import Path

from botocore.exceptions import BotoCoreError, ClientError

from .fetch import _SIZED, _check_pixels, _Reject, looks_like_image
from .iiif import PageRef, source_digest

log = logging.getLogger("htrflow_batch")

#: The key's page number is exactly five digits.
MAX_PAGE = 99_999

#: What a GET answers for a key that is simply not there: a miss, nothing to say.
_NOT_FOUND = frozenset({"NoSuchKey", "404", "NotFound"})

_CHUNK = 1 << 20


def source_identity(url: str) -> str:
    """Which source image a page's URL names, whatever width it asks for:
    ``source_digest`` (credentials stripped) of the URL with its IIIF size
    put to ``max``. A cached image is reused at any width (the key has none),
    so the width must not make it another source."""
    m = _SIZED.match(url)
    if m is not None:
        url = f"{m['base']}/full/max/{m['rest']}{m['query'] or ''}"
    return source_digest(url)


class ImageCache:
    """One volume's view of the cache. Shared by the download pool's
    threads, so the counts and the once-only warnings are locked."""

    def __init__(
        self, client, bucket: str, ref: str, *, max_bytes: int, max_pixels: int
    ) -> None:
        self.client, self.bucket, self.ref = client, bucket, ref
        self.max_bytes, self.max_pixels = max_bytes, max_pixels
        self.hits = self.misses = self.stored = 0
        self._lock = threading.Lock()
        self._warned: set[str] = set()

    @classmethod
    def for_volume(
        cls,
        client,
        bucket: str,
        ref: str,
        pages: Sequence[PageRef],
        *,
        max_bytes: int,
        max_pixels: int,
    ) -> ImageCache | None:
        """The cache for this volume, or None: off, or a volume whose pages
        the five-digit key cannot number (said once)."""
        if not bucket:
            return None
        if any(p.index > MAX_PAGE for p in pages):
            log.warning(
                "[%s] image cache skipped: the volume has pages past %d, which "
                "the cache key's five-digit page number cannot name",
                ref,
                MAX_PAGE,
            )
            return None
        return cls(client, bucket, ref, max_bytes=max_bytes, max_pixels=max_pixels)

    def key(self, page: PageRef) -> str:
        return f"{self.ref}/{self.ref}_{page.index:05d}.jpg"

    def _count(self, name: str) -> None:
        with self._lock:
            setattr(self, name, getattr(self, name) + 1)

    def _once(self, what: str, message: str, *args) -> None:
        """A cache-wide problem is said once per run, not once per page."""
        with self._lock:
            if what in self._warned:
                return
            self._warned.add(what)
        log.warning(message, *args)

    def _unreadable(self, e: Exception) -> None:
        code = (
            e.response.get("Error", {}).get("Code")
            if isinstance(e, ClientError)
            else ""
        )
        if code == "NoSuchBucket":
            self._once(
                "bucket",
                "image cache bucket %s does not exist; pages are downloaded "
                "and not cached",
                self.bucket,
            )
        else:
            self._once(
                "get",
                "image cache bucket %s could not be read (%s); pages are downloaded",
                self.bucket,
                e,
            )

    def get(self, page: PageRef, path: Path) -> bool:
        """Write the cached image to ``path`` and say so, or leave no file
        and return False. The object is checked as a download is."""
        key = self.key(page)
        try:
            obj = self.client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") not in _NOT_FOUND:
                self._unreadable(e)
            self._count("misses")
            return False
        except (BotoCoreError, OSError) as e:
            self._unreadable(e)
            self._count("misses")
            return False
        if obj.get("Metadata", {}).get("source") != source_identity(page.image_url):
            obj["Body"].close()
            self._once(
                "source",
                "image cache object %s holds another source image (or does not "
                "say which); refetching, and the download replaces it",
                key,
            )
            self._count("misses")
            return False
        try:
            self._save(obj["Body"], path, key)
        except (_Reject, BotoCoreError, ClientError, OSError) as e:
            path.unlink(missing_ok=True)
            log.warning(
                "image cache object %s is not a usable image (%s); refetching", key, e
            )
            self._count("misses")
            return False
        self._count("hits")
        return True

    def _save(self, body, path: Path, key: str) -> None:
        size, first = 0, True
        with path.open("wb") as f:
            for chunk in body.iter_chunks(_CHUNK):
                if first:
                    first = False
                    if not looks_like_image(chunk):
                        raise _Reject(f"body starts with {chunk[:16]!r}")
                size += len(chunk)
                if size > self.max_bytes:
                    raise _Reject(f"over {self.max_bytes} bytes")
                f.write(chunk)
        if size == 0:
            raise _Reject("empty")
        _check_pixels(path, self.max_pixels)

    def put(self, page: PageRef, path: Path) -> None:
        """Store a downloaded image; a failure is logged, never raised."""
        try:
            with path.open("rb") as f:
                self.client.put_object(
                    Bucket=self.bucket,
                    Key=self.key(page),
                    Body=f,
                    ContentType="image/jpeg",
                    Metadata={"source": source_identity(page.image_url)},
                )
        except (BotoCoreError, ClientError, OSError) as e:
            if _is_bucket_error(e):
                self._unreadable(e)
            else:
                self._once(
                    "put",
                    "image cache bucket %s could not be written (%s)",
                    self.bucket,
                    e,
                )
            return
        self._count("stored")

    def report(self) -> dict:
        with self._lock:
            return {
                "bucket": self.bucket,
                "hits": self.hits,
                "misses": self.misses,
                "stored": self.stored,
            }


def _is_bucket_error(e: Exception) -> bool:
    return (
        isinstance(e, ClientError)
        and e.response.get("Error", {}).get("Code") == "NoSuchBucket"
    )
