# S3 Image Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an optional S3 cache of source page images. A campaign pod looks for
`{ref}/{ref}_{page:05d}.jpg` in the cache bucket before downloading from IIIF, and stores the image there on
a miss.

**Architecture:**

- A new wrapper module, `imagecache.py`, owns the key, the checked GET, the best-effort PUT and the counts.
- `fetch_page` asks it first and stores after a successful download. `PageStream` and `main` carry it
  through, and `publish` records its counts.
- The converter renders `IMAGE_CACHE_BUCKET` from `converter.yaml`'s `image_cache.bucket`.
- The dev stacks create the bucket.
- The docs, README and generated diagrams show it.

**Tech Stack:** Python 3.12, boto3 (moto in tests), httpx, pydantic, Helm, the repo's SVG diagram generator
(`docs/slides/diagrams/build_docs.py`).

**Spec:** `docs/superpowers/specs/2026-09-25-image-cache-design.md`

## Global Constraints

- **Key:** `{ref}/{ref}_{page:05d}.jpg`.
  - `ref` is `VOLUME_REF`.
  - `page` is `PageRef.index` (1-based, manifest order), zero-padded to exactly 5 digits. Example:
    `R0001203/R0001203_00001.jpg`.
  - No `S3_PREFIX` and no pipeline id in the key.
- **A volume with any page index above 99 999:** the cache is off for the whole volume, with one log line.
- **The cache never fails or defers a page.** On any cache error the page is downloaded as today. A failed
  PUT is logged, and the page stays ok.
- **Logging:** GET errors other than "not found" are logged once per run. A missing or unreadable bucket is
  one sentence that names the bucket.
- **A hit is checked** exactly like a download: a known raster signature, `FETCH_MAX_BYTES` and
  `MAX_IMAGE_PIXELS`. A hit that fails the checks is a miss, and its object is overwritten after the
  download.
- **Stored image:** the image as fetched (width-capped). No width in the key.
- **Unchanged fields:** `bytes_fetched` keeps meaning bytes downloaded from IIIF. `page_sources` and
  `page_source_digests` are unchanged.
- **With the cache on:**
  - `manifest.json` gets `"image_cache": {"bucket": str, "hits": int, "misses": int, "stored": int}`;
  - the run log gets one line with the same counts.
- **With the cache off:**
  - no `image_cache` key;
  - no `IMAGE_CACHE_BUCKET` env var;
  - rendered golden files are byte-identical.
- **Bucket name:** 3–63 characters of `[a-z0-9.-]`, starting and ending with a letter or digit, no `..`.
  Otherwise the converter refuses it with a one-line reason.
- **The cache bucket is private.** It is never in a public policy and never linked.
- **Docs, README and diagrams ship with the code.**
  - Diagrams change only in `docs/slides/diagrams/build_docs.py`, then are regenerated. SVGs are never
    hand-edited.
- **Repo rules:**
  - Site docs (`docs/` minus `docs/superpowers`) carry no names, dates, versions, hosts or hardware;
    `scripts/docs-site.sh build --clean --strict` must pass.
  - One logical step per commit.
  - No `Co-Authored-By` trailers.
  - Before any push, run all of these: `make ci`, `scripts/loc-budget.sh` (NOT part of `make ci`), and, if
    the frontend is touched, `cd frontend && bun run test && bun run check`.
  - Never read `.env` files.

## Review Focus

1. **The cache bucket does not exist or the credentials cannot read it.** The run completes uncached, with
   exactly one log line naming the bucket, not one per page. Pinned in Task 1.
2. **A cached object is truncated or is an HTML error page** (someone uploaded junk). The page downloads
   fresh, the object is overwritten, and a warning names the key. Pinned in Task 1.
3. **The same volume runs in two campaigns at once.** Both PUT the same key and neither page fails. A
   second PUT of an existing key is simply an overwrite. Pinned in Task 1 (`put` twice).
4. **Resume after a partial run.** Pages done earlier are skipped before any fetch, so they neither read nor
   write the cache, and the counts cover only this run's pages. Pinned in Task 2.
5. **A hit must not count as downloaded bytes but must still count against the lookahead memory budget.**
   A fully cached rerun reports `bytes_fetched: 0` while the workdir budget still sees each image's size.
   Pinned in Task 2.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `packages/wrapper/src/htrflow_batch/imagecache.py` (new) | The key, the checked GET into the page's file, the best-effort PUT, the counts, the report block |
| `packages/wrapper/src/htrflow_batch/config.py` | `image_cache_bucket` (`IMAGE_CACHE_BUCKET`) |
| `packages/wrapper/src/htrflow_batch/fetch.py` | `fetch_page(cache=...)`: look first, store after; `FetchResult.from_cache` |
| `packages/wrapper/src/htrflow_batch/stream.py` | `PageStream(cache=...)`; `bytes_fetched` skips hits |
| `packages/wrapper/src/htrflow_batch/main.py` | Build the cache for the volume, pass it to the stream, log the counts, hand them to publish |
| `packages/wrapper/src/htrflow_batch/publish.py` | `run_manifest(..., image_cache=None)` adds the block |
| `packages/converter/src/htrflow_converter/models.py` | `ImageCacheSettings` and `ConverterConfig.image_cache` |
| `packages/converter/src/htrflow_converter/render.py` | Append `IMAGE_CACHE_BUCKET` to the campaign container when set |
| `charts/htrflow-devstack/values.yaml`, `templates/rustfs.yaml` | `s3.imageCacheBucket`, created privately by the init hook |
| `scripts/compose_init.py` | Creates `IMAGE_CACHE_BUCKET` when set |
| `docs/slides/diagrams/build_docs.py` → `docs/assets/diagrams/*.svg` | Diagrams |
| `README.md`, `docs/…` | Docs |

---

### Task 1: The cache module

**Files:**
- Create: `packages/wrapper/src/htrflow_batch/imagecache.py`
- Modify: `packages/wrapper/src/htrflow_batch/config.py`. Add a field beside `fetch_max_bytes`.
- Test: `packages/wrapper/tests/test_imagecache.py` (new)

**Interfaces:**
- Produces:
  - `imagecache.ImageCache(client, bucket: str, ref: str, *, max_bytes: int, max_pixels: int)`;
  - `ImageCache.for_volume(client, bucket: str, ref: str, pages: Sequence[PageRef], *, max_bytes: int, max_pixels: int) -> ImageCache | None`
    returns `None` when `bucket` is empty, or when any page index exceeds `MAX_PAGE`, which it logs;
  - `ImageCache.key(page: PageRef) -> str`;
  - `ImageCache.get(page: PageRef, path: Path) -> bool` (True = a valid hit written to `path`; never raises);
  - `ImageCache.put(page: PageRef, path: Path) -> None` (never raises);
  - `ImageCache.report() -> dict` (`{"bucket", "hits", "misses", "stored"}`);
  - `imagecache.MAX_PAGE = 99_999`;
  - `Config.image_cache_bucket: str` (alias `IMAGE_CACHE_BUCKET`, default `""`).
- Consumes: `fetch.looks_like_image(head: bytes) -> bool`, `fetch._check_pixels(path, max_pixels)` (raises
  `fetch._Reject`), and `iiif.PageRef`.

- [ ] **Step 1: Write the failing tests** (`packages/wrapper/tests/test_imagecache.py`)

```python
import logging

import boto3
import pytest
from moto import mock_aws

from htrflow_batch.iiif import PageRef
from htrflow_batch.imagecache import MAX_PAGE, ImageCache

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
BUCKET = "images-batch"


def _page(i: int) -> PageRef:
    return PageRef(index=i, name=f"{i:04d}", image_url=f"https://iiif.example/p{i}", canvas={})


@pytest.fixture
def client():
    with mock_aws():
        c = boto3.client("s3", region_name="us-east-1")
        c.create_bucket(Bucket=BUCKET)
        yield c


def _cache(client, **kw) -> ImageCache:
    return ImageCache(
        client, BUCKET, "R0001203", max_bytes=kw.get("max_bytes", 1 << 20), max_pixels=0
    )


def test_the_key_is_ref_slash_ref_underscore_five_digit_page(client):
    assert _cache(client).key(_page(1)) == "R0001203/R0001203_00001.jpg"
    assert _cache(client).key(_page(637)) == "R0001203/R0001203_00637.jpg"
    assert _cache(client).key(_page(MAX_PAGE)) == "R0001203/R0001203_99999.jpg"


def test_a_miss_then_a_store_then_a_hit(client, tmp_path):
    cache, path = _cache(client), tmp_path / "0001.jpg"
    assert cache.get(_page(1), path) is False
    path.write_bytes(JPEG)
    cache.put(_page(1), path)
    body = client.get_object(Bucket=BUCKET, Key="R0001203/R0001203_00001.jpg")["Body"].read()
    assert body == JPEG
    path.unlink()
    assert cache.get(_page(1), path) is True
    assert path.read_bytes() == JPEG
    assert cache.report() == {"bucket": BUCKET, "hits": 1, "misses": 1, "stored": 1}


@pytest.mark.parametrize(
    "junk", [b"<html>login</html>", b"", b"\xff\xd8"], ids=["html", "empty", "short"]
)
def test_a_cached_object_that_is_not_an_image_is_a_miss(client, tmp_path, caplog, junk):
    client.put_object(Bucket=BUCKET, Key="R0001203/R0001203_00002.jpg", Body=junk)
    path = tmp_path / "0002.jpg"
    with caplog.at_level(logging.WARNING):
        assert _cache(client).get(_page(2), path) is False
    assert not path.exists()
    assert "R0001203/R0001203_00002.jpg" in caplog.text


def test_a_cached_object_over_the_byte_cap_is_a_miss(client, tmp_path):
    client.put_object(Bucket=BUCKET, Key="R0001203/R0001203_00003.jpg", Body=JPEG * 100)
    path = tmp_path / "0003.jpg"
    assert _cache(client, max_bytes=100).get(_page(3), path) is False
    assert not path.exists()


def test_a_missing_bucket_is_one_sentence_naming_it_and_every_page_a_miss(tmp_path, caplog):
    with mock_aws():
        c = boto3.client("s3", region_name="us-east-1")
        cache = ImageCache(c, "no-such-bucket", "R0001203", max_bytes=1 << 20, max_pixels=0)
        with caplog.at_level(logging.WARNING):
            for i in range(1, 4):
                assert cache.get(_page(i), tmp_path / f"{i}.jpg") is False
            (tmp_path / "x.jpg").write_bytes(JPEG)
            cache.put(_page(1), tmp_path / "x.jpg")  # never raises
    lines = [r for r in caplog.records if "no-such-bucket" in r.getMessage()]
    assert len(lines) == 1
    assert cache.report()["misses"] == 3 and cache.report()["stored"] == 0


def test_a_second_put_of_the_same_key_overwrites(client, tmp_path):
    cache, path = _cache(client), tmp_path / "p.jpg"
    path.write_bytes(JPEG)
    cache.put(_page(4), path)
    path.write_bytes(JPEG + b"\x01")
    cache.put(_page(4), path)
    body = client.get_object(Bucket=BUCKET, Key="R0001203/R0001203_00004.jpg")["Body"].read()
    assert body == JPEG + b"\x01"


def test_for_volume_is_none_without_a_bucket(client):
    pages = [_page(1)]
    assert ImageCache.for_volume(client, "", "R1", pages, max_bytes=1, max_pixels=0) is None


def test_for_volume_is_none_past_the_five_digit_limit_and_says_so_once(client, caplog):
    pages = [_page(1), _page(MAX_PAGE + 1)]
    with caplog.at_level(logging.WARNING):
        got = ImageCache.for_volume(client, BUCKET, "R1", pages, max_bytes=1, max_pixels=0)
    assert got is None
    assert sum("99999" in r.getMessage() for r in caplog.records) == 1


def test_for_volume_builds_one_otherwise(client):
    got = ImageCache.for_volume(client, BUCKET, "R1", [_page(1)], max_bytes=1, max_pixels=0)
    assert isinstance(got, ImageCache)
```

Also add to `packages/wrapper/tests/test_config.py`, following that file's pattern for building a Config from
env:

```python
def test_the_image_cache_bucket_is_off_unless_set(cfg):
    assert cfg.image_cache_bucket == ""
```

and one test showing that setting `IMAGE_CACHE_BUCKET=images-batch` in the env gives
`cfg.image_cache_bucket == "images-batch"`.

- [ ] **Step 2: Run them and see them fail**

Run: `uv run --no-sync pytest packages/wrapper/tests/test_imagecache.py packages/wrapper/tests/test_config.py -k "cache" -q`
Expected: FAIL with `ModuleNotFoundError: htrflow_batch.imagecache` and a missing Config field.

- [ ] **Step 3: Implement.** In `config.py`, beside `fetch_max_bytes`:

```python
    #: The S3 bucket source images are cached in (docs: wrapper, "Image
    #: cache"); empty is off. Same endpoint and credentials as the results.
    image_cache_bucket: str = Field("", alias="IMAGE_CACHE_BUCKET")
```

Create `packages/wrapper/src/htrflow_batch/imagecache.py`:

```python
"""Source images cached in S3 (docs: wrapper, "Image cache").

With ``IMAGE_CACHE_BUCKET`` set, a page's image is looked for at
``{ref}/{ref}_{page:05d}.jpg`` before it is downloaded, and stored there after
a download, so a volume run again -- under any pipeline or campaign -- needs
nothing from the IIIF server. The key carries no width: the image is kept as
the run that stored it fetched it.

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

from .fetch import _check_pixels, _Reject, looks_like_image
from .iiif import PageRef

log = logging.getLogger("htrflow_batch")

#: The key's page number is exactly five digits.
MAX_PAGE = 99_999

#: What a GET answers for a key that is simply not there: a miss, nothing to say.
_NOT_FOUND = frozenset({"NoSuchKey", "404", "NotFound"})

_CHUNK = 1 << 20


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
        code = e.response.get("Error", {}).get("Code") if isinstance(e, ClientError) else ""
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
        try:
            self._save(obj["Body"], path, key)
        except (_Reject, BotoCoreError, ClientError, OSError) as e:
            path.unlink(missing_ok=True)
            log.warning("image cache object %s is not a usable image (%s); refetching", key, e)
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
                    Bucket=self.bucket, Key=self.key(page), Body=f, ContentType="image/jpeg"
                )
        except (BotoCoreError, ClientError, OSError) as e:
            self._unreadable(e) if _is_bucket_error(e) else self._once(
                "put", "image cache bucket %s could not be written (%s)", self.bucket, e
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
    return isinstance(e, ClientError) and e.response.get("Error", {}).get("Code") == "NoSuchBucket"
```

Replace the conditional-expression statement in `put` with a plain `if/else` if ruff objects.
`test_a_missing_bucket_is_one_sentence_naming_it_and_every_page_a_miss` requires the missing-bucket sentence
exactly once across both GETs and PUTs. `_once("bucket", ...)` gives that, since GET and PUT share the key.

- [ ] **Step 4: Run the tests and see them pass**

Run: `uv run --no-sync pytest packages/wrapper/tests/test_imagecache.py packages/wrapper/tests/test_config.py -q`
Expected: PASS.

Run: `uv run --no-sync ruff check packages/wrapper && uv run --no-sync ruff format --check packages/wrapper && make typecheck`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add packages/wrapper/src/htrflow_batch/imagecache.py packages/wrapper/src/htrflow_batch/config.py \
  packages/wrapper/tests/test_imagecache.py packages/wrapper/tests/test_config.py
git commit -m "feat(wrapper): an S3 image cache keyed {ref}/{ref}_{page:05d}.jpg that never fails a page"
```

---

### Task 2: The fetch uses it, and the manifest counts it

**Files:**
- Modify: `packages/wrapper/src/htrflow_batch/fetch.py`: `FetchResult` (line 89) and `fetch_page` (line 320)
- Modify: `packages/wrapper/src/htrflow_batch/stream.py`: `PageStream.__init__` (the `partial`) and
  `__iter__` (`bytes_fetched`)
- Modify: `packages/wrapper/src/htrflow_batch/main.py`: `_stream` (line 456) and the `publish.run` call
  (line 333)
- Modify: `packages/wrapper/src/htrflow_batch/publish.py`: `run_manifest` and `run`
- Test: `packages/wrapper/tests/test_fetch.py`, `test_stream.py`, `test_main.py`, `test_publish.py`

**Interfaces:**
- Consumes: `ImageCache` (Task 1).
- Produces:
  - `FetchResult.from_cache: bool = False`;
  - `fetch_page(..., cache: ImageCache | None = None)`;
  - `PageStream(..., cache: ImageCache | None = None)`;
  - `_stream(...)` returns `(stats, bytes_fetched, cache_report: dict | None)`;
  - `publish.run(..., image_cache: dict | None = None)`;
  - `publish.run_manifest(..., image_cache: dict | None = None)`.

- [ ] **Step 1: Write the failing tests**

`test_fetch.py` (it has `JPEG` and `_client(handler)`; add a moto-backed cache):

```python
import boto3
from moto import mock_aws

from htrflow_batch.imagecache import ImageCache


def _cache(c):
    return ImageCache(c, "images-batch", "R0001203", max_bytes=1 << 20, max_pixels=0)


def test_a_cache_hit_never_calls_the_iiif_server(tmp_path, page):
    calls = []

    def handler(req):
        calls.append(req.url)
        return httpx.Response(200, content=JPEG)

    with mock_aws():
        c = boto3.client("s3", region_name="us-east-1")
        c.create_bucket(Bucket="images-batch")
        c.put_object(Bucket="images-batch", Key=_cache(c).key(page), Body=JPEG)
        result = fetch_page(page, tmp_path, _client(handler), cache=_cache(c))
    assert result.error is None and result.from_cache and calls == []
    assert result.size == len(JPEG)


def test_a_miss_downloads_and_stores(tmp_path, page):
    with mock_aws():
        c = boto3.client("s3", region_name="us-east-1")
        c.create_bucket(Bucket="images-batch")
        cache = _cache(c)
        result = fetch_page(
            page, tmp_path, _client(lambda r: httpx.Response(200, content=JPEG)), cache=cache
        )
        stored = c.get_object(Bucket="images-batch", Key=cache.key(page))["Body"].read()
    assert result.error is None and not result.from_cache
    assert stored == JPEG
    assert cache.report()["stored"] == 1


def test_a_failed_download_stores_nothing(tmp_path, page):
    with mock_aws():
        c = boto3.client("s3", region_name="us-east-1")
        c.create_bucket(Bucket="images-batch")
        cache = _cache(c)
        result = fetch_page(
            page, tmp_path, _client(lambda r: httpx.Response(404)), retries=1, cache=cache
        )
    assert result.error and cache.report()["stored"] == 0
```

If `test_fetch.py` has no `page` fixture, define one next to these tests:
`PageRef(index=1, name="0001", image_url="https://iiif.example/p1/full/2500,/0/default.jpg", canvas={})`.

`test_stream.py`: a stream over two pages whose fetch is stubbed to return one `from_cache=True` result
(size 100) and one downloaded result (size 50) must report `bytes_fetched == 50`. Use the file's existing
approach for stubbing `fetch_page` or the client.

`test_main.py` (it has `env`, `s3`, `cfg`, `fake_factory` and `_keys`; the `env` fixture's HTTP handler is
set in conftest). Add a counting handler locally:

```python
def _counting_http(monkeypatch, sample_manifest):
    calls = []

    def handler(req):
        if req.url.path.endswith("manifest.json"):
            return httpx.Response(200, json=sample_manifest)
        calls.append(str(req.url))
        return httpx.Response(200, content=b"\xff\xd8\xff\xe0JPEGDATA")

    monkeypatch.setattr(
        main_mod, "_http_client", lambda: httpx.Client(transport=httpx.MockTransport(handler))
    )
    return calls


def test_a_volume_run_twice_with_the_cache_fetches_nothing_the_second_time(
    env, cfg, s3, monkeypatch, sample_manifest
):
    s3.create_bucket(Bucket="images-batch")
    cached = {**env, "IMAGE_CACHE_BUCKET": "images-batch", "RESUME": "false"}
    first = _counting_http(monkeypatch, sample_manifest)
    assert main(cached, process_page_factory=fake_factory) == EXIT_OK
    keys = sorted(
        o["Key"] for o in s3.list_objects_v2(Bucket="images-batch").get("Contents", [])
    )
    assert keys == [f"SE-RA-1234/SE-RA-1234_{i:05d}.jpg" for i in (1, 2, 3)]
    m1 = json.loads(
        s3.get_object(Bucket=cfg.s3_bucket, Key="demo-v1/SE-RA-1234/manifest.json")["Body"].read()
    )
    assert m1["image_cache"] == {"bucket": "images-batch", "hits": 0, "misses": 3, "stored": 3}
    assert len(first) == 3

    second = _counting_http(monkeypatch, sample_manifest)
    assert main(cached, process_page_factory=fake_factory) == EXIT_OK
    m2 = json.loads(
        s3.get_object(Bucket=cfg.s3_bucket, Key="demo-v1/SE-RA-1234/manifest.json")["Body"].read()
    )
    assert second == []
    assert m2["bytes_fetched"] == 0
    assert m2["image_cache"] == {"bucket": "images-batch", "hits": 3, "misses": 0, "stored": 0}


def test_without_the_cache_the_manifest_has_no_image_cache_key(env, cfg, s3):
    assert main(env, process_page_factory=fake_factory) == EXIT_OK
    body = json.loads(
        s3.get_object(Bucket=cfg.s3_bucket, Key="demo-v1/SE-RA-1234/manifest.json")["Body"].read()
    )
    assert "image_cache" not in body


def test_a_resumed_page_touches_no_cache(env, cfg, s3, monkeypatch, sample_manifest):
    s3.create_bucket(Bucket="images-batch")
    _put_done(s3, cfg, "0001")
    _counting_http(monkeypatch, sample_manifest)
    assert main({**env, "IMAGE_CACHE_BUCKET": "images-batch"}, process_page_factory=fake_factory) == EXIT_OK
    body = json.loads(
        s3.get_object(Bucket=cfg.s3_bucket, Key="demo-v1/SE-RA-1234/manifest.json")["Body"].read()
    )
    assert body["image_cache"]["misses"] == 2
    keys = [o["Key"] for o in s3.list_objects_v2(Bucket="images-batch").get("Contents", [])]
    assert "SE-RA-1234/SE-RA-1234_00001.jpg" not in keys


def test_a_missing_cache_bucket_still_completes_the_volume(env, cfg, s3):
    assert main({**env, "IMAGE_CACHE_BUCKET": "images-batch"}, process_page_factory=fake_factory) == EXIT_OK
    body = json.loads(
        s3.get_object(Bucket=cfg.s3_bucket, Key="demo-v1/SE-RA-1234/manifest.json")["Body"].read()
    )
    assert body["pages_ok"] == 3 and body["image_cache"]["stored"] == 0
```

The page names in the sample manifest are `0001`–`0003` (indexes 1–3), and `VOLUME_REF` is `SE-RA-1234`. If
the fixture differs, adapt the expected keys, not the key rule. `_put_done` is the module's existing helper.
The second run in the first test uses `RESUME=false` so that every page is fetched again.

`test_publish.py`:

```python
def test_the_manifest_carries_the_cache_counts_only_when_given(cfg, monkeypatch):
    monkeypatch.setattr(publish, "_htrflow_version", lambda: "0.2.3")
    stats = StreamStats(results={"0001": PageOutcome(status="ok", seconds=1.0)})
    args = (cfg, _pages()[:1], stats, "https://m", PIPELINE, 1.0, 1)
    assert "image_cache" not in publish.run_manifest(*args)
    counts = {"bucket": "images-batch", "hits": 1, "misses": 0, "stored": 0}
    assert publish.run_manifest(*args, image_cache=counts)["image_cache"] == counts
```

- [ ] **Step 2: Run them and see them fail**

Run: `uv run --no-sync pytest packages/wrapper/tests/test_fetch.py packages/wrapper/tests/test_stream.py packages/wrapper/tests/test_main.py packages/wrapper/tests/test_publish.py -k "cache" -q`
Expected: FAIL (`unexpected keyword argument 'cache'`, a missing `image_cache` key).

- [ ] **Step 3: Implement**

`fetch.py`:
- `FetchResult` gains the field:

```python
    #: Read from the image cache, not downloaded: not in ``bytes_fetched``,
    #: which counts what the source served (docs: wrapper, "Image cache").
    from_cache: bool = False
```

- `fetch_page` gains `cache: "ImageCache | None" = None` as its last parameter, under a `TYPE_CHECKING`
  import. This avoids an import cycle, since `imagecache` imports `fetch`.
- Right after `path = dest_dir / f"{page.name}.jpg"`:

```python
    if cache is not None and cache.get(page, path):
        return FetchResult(
            page=page, path=path, error=None, size=path.stat().st_size, from_cache=True
        )
```

- In the success branch, after `_check_pixels(path, max_pixels)`:

```python
                    if cache is not None:
                        cache.put(page, path)  # best-effort; never fails the page
```

`stream.py`:
- `PageStream.__init__` gains `cache: "ImageCache | None" = None` and passes `cache=cache` into the
  `partial(fetch_page, …)`.
- In `__iter__`, `self.bytes_fetched += result.size` becomes:

```python
                if not result.from_cache:
                    self.bytes_fetched += result.size
```

- `_held` keeps using `result.size`. A cached image occupies the workdir like a downloaded one.

`main.py`, in `_stream`:

```python
    cache = ImageCache.for_volume(
        store.client,
        cfg.image_cache_bucket,
        cfg.volume_ref,
        todo,
        max_bytes=cfg.fetch_max_bytes,
        max_pixels=cfg.max_image_pixels,
    )
```

- Pass `cache=cache` to `PageStream(...)`.
- After the `finally`:

```python
    report = cache.report() if cache is not None else None
    if report is not None:
        log.info(
            "[%s] image cache %s: %d hits, %d misses, %d stored",
            cfg.volume_ref, report["bucket"], report["hits"], report["misses"], report["stored"],
        )
    return stats, stream.bytes_fetched, report
```

- Update the return annotation and docstring, and the one caller (`stats, nbytes = _stream(...)` →
  `stats, nbytes, cache_report = _stream(...)`).
- `cache_report` must exist on every path that reaches publish. Initialise `cache_report = None` before the
  branch that calls `_stream`, if one exists.
- Pass `image_cache=cache_report` to `publish.run`.

`publish.py`:
- `run(..., image_cache: dict | None = None)` passes `image_cache=image_cache` to `run_manifest`.
- `run_manifest(..., image_cache: dict | None = None)`: after building `body`, add
  `if image_cache is not None: body["image_cache"] = image_cache`.
- Keep `run_manifest`'s existing parameter order; add the new one last. Check it against the current
  signature, which also has `quality`, `canvases` and `summary`.

- [ ] **Step 4: Run the wrapper suite**

Run: `uv run --no-sync pytest packages/wrapper -q`
Expected: all PASS.

Run: `uv run --no-sync ruff check . && uv run --no-sync ruff format --check . && make typecheck && scripts/loc-budget.sh`
Expected: clean. If the wrapper budget is exceeded, raise it in `scripts/loc-budget.sh` with an itemised
comment block above its `check wrapper` line, in that file's style: what each file gained and why.

- [ ] **Step 5: Document** in `docs/reference/s3-layout.md`:
  - the `image_cache` field in the `manifest.json` table;
  - a short "Image cache bucket" subsection with the key layout, and a note that it is private and separate
    from the results bucket.

  In `docs/reference/configuration.md` (or wherever the wrapper's env vars are listed), add
  `IMAGE_CACHE_BUCKET`. If that page is generated, run `make config-reference`.

  Run: `ZENSICAL=<a zensical binary> scripts/docs-site.sh build --clean --strict`, after
  `uv sync --locked --only-group docs` in a SEPARATE worktree or venv. That sync strips the main venv.
  Expected: "No issues found".

- [ ] **Step 6: Commit** (two commits)

```bash
git add packages/wrapper/src packages/wrapper/tests scripts/loc-budget.sh
git commit -m "feat(wrapper): each page looks in the image cache first and stores what it downloads"
git add docs/reference
git commit -m "docs(reference): the image cache bucket, its key and its counts in manifest.json"
```

---

### Task 3: The converter renders the switch

**Files:**
- Modify: `packages/converter/src/htrflow_converter/models.py`: a new `ImageCacheSettings` model near
  `Size`/`Flavor`, and a field on `ConverterConfig` after `fetch_max_bytes`
- Modify: `packages/converter/src/htrflow_converter/render.py`: the campaign job, after the `dynamic_env`
  loop (around line 358)
- Modify: `packages/converter/src/htrflow_converter/template/converter.yaml` and
  `examples/campaigns/converter.yaml`: a commented example block
- Test: `packages/converter/tests/test_models.py`, `packages/converter/tests/test_render.py`
- Docs: `docs/reference/campaign-yaml.md`, the `converter.yaml` section

**Interfaces:**
- Produces:
  - `models.ImageCacheSettings(bucket: str)`;
  - `ConverterConfig.image_cache: ImageCacheSettings | None = None`;
  - the rendered campaign container env var `IMAGE_CACHE_BUCKET`.

- [ ] **Step 1: Write the failing tests**

`test_models.py`:

```python
BASE = {"namespace": "htr-test", "public_results_base": "https://results.example.org"}


@pytest.mark.parametrize("bucket", ["images-batch", "img.cache-01", "abc"])
def test_a_valid_image_cache_bucket_is_kept(bucket):
    cfg = ConverterConfig.model_validate({**BASE, "image_cache": {"bucket": bucket}})
    assert cfg.image_cache.bucket == bucket


@pytest.mark.parametrize(
    "bucket",
    ["", "ab", "Images", "-images", "images-", "im..ages", "im_ages", "a" * 64, "images/batch"],
)
def test_an_invalid_image_cache_bucket_is_refused_in_one_sentence(bucket):
    with pytest.raises(ValidationError) as exc_info:
        ConverterConfig.model_validate({**BASE, "image_cache": {"bucket": bucket}})
    assert "S3 bucket name" in str(exc_info.value)


def test_an_unknown_image_cache_key_is_refused():
    with pytest.raises(ValidationError):
        ConverterConfig.model_validate({**BASE, "image_cache": {"bucket": "abc", "ttl": 3}})


def test_no_image_cache_by_default():
    assert ConverterConfig.model_validate(BASE).image_cache is None
```

(import `ConverterConfig` from `htrflow_converter.models` if the file does not already)

`test_render.py` (uses its `_kyrk()` helper, which loads `fixtures/good`):

```python
def _env(job: dict) -> dict:
    return {
        e["name"]: e.get("value")
        for e in job["spec"]["template"]["spec"]["containers"][0]["env"]
    }


def test_the_image_cache_bucket_reaches_the_campaign_pod():
    kyrk, demo, cfg = _kyrk()
    cached = cfg.model_copy(update={"image_cache": ImageCacheSettings(bucket="images-batch")})
    job = render.campaign_objects(kyrk, demo, cached)[1]
    assert _env(job)["IMAGE_CACHE_BUCKET"] == "images-batch"


def test_without_the_image_cache_the_pod_has_no_such_env():
    kyrk, demo, cfg = _kyrk()
    job = render.campaign_objects(kyrk, demo, cfg)[1]
    assert "IMAGE_CACHE_BUCKET" not in _env(job)
```

(add `ImageCacheSettings` to the file's `from htrflow_converter.models import ...` line)

- [ ] **Step 2: Run them and see them fail**

Run: `uv run --no-sync pytest packages/converter/tests/test_models.py packages/converter/tests/test_render.py -k "image_cache" -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

`models.py`:

```python
#: An S3 bucket name as the S3 API accepts it (docs: reference/campaign-yaml).
_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]\Z")


class ImageCacheSettings(BaseModel):
    """``image_cache:`` in converter.yaml: where campaign pods cache source
    images (docs: how-it-works/wrapper, "Image cache"). The same S3 store as
    the results; only the bucket differs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    bucket: str

    @field_validator("bucket")
    @classmethod
    def _check_bucket(cls, v: str) -> str:
        if not _BUCKET_RE.match(v) or ".." in v:
            raise ValueError(
                f"is not an S3 bucket name (got {shown(v)}) — 3 to 63 lowercase "
                "letters, digits, dots and hyphens, starting and ending with a "
                "letter or digit"
            )
        return v
```

`ConverterConfig`, after `fetch_max_bytes`:

```python
    #: Cache source images in this S3 bucket (``ImageCacheSettings``).
    #: Absent: off, and campaign pods render exactly as without it.
    image_cache: ImageCacheSettings | None = None
```

`render.py`, right after the `for e in job["spec"]["template"]["spec"]["containers"][0]["env"]:` loop that
fills `dynamic_env`:

```python
    if cfg.image_cache is not None:
        # Appended, not a skeleton entry: a converter.yaml without the block
        # renders byte-for-byte what it always did.
        job["spec"]["template"]["spec"]["containers"][0]["env"].append(
            {"name": "IMAGE_CACHE_BUCKET", "value": cfg.image_cache.bucket}
        )
```

In both example `converter.yaml` files, add a commented block after `fetch_max_bytes` (or at the end if
that key is not present):

```yaml
# Cache source page images in this S3 bucket, on the same S3 store as the
# results: a volume run again needs nothing from the IIIF server. Private;
# create the bucket first (docs: getting-started/deploy.md).
# image_cache:
#   bucket: images-batch
```

- [ ] **Step 4: Run the converter suite, including goldens**

Run: `uv run --no-sync pytest packages/converter -q`
Expected: all PASS, with golden files unchanged (`git status packages/converter/tests/golden` is clean). A
changed golden means the "off" path changed output; fix the code, not the golden.

- [ ] **Step 5: Document** `image_cache` in `docs/reference/campaign-yaml.md`'s `converter.yaml` section: the
  key, what it does, that it is private and off when absent, and a link to the key layout in
  `s3-layout.md`. Run the docs gate as in Task 2.

- [ ] **Step 6: Commit** (two commits)

```bash
git add packages/converter examples/campaigns/converter.yaml
git commit -m "feat(converter): converter.yaml's image_cache.bucket reaches every campaign pod"
git add docs/reference/campaign-yaml.md
git commit -m "docs(reference): image_cache in converter.yaml"
```

---

### Task 4: The dev stacks create the bucket

**Files:**
- Modify: `charts/htrflow-devstack/values.yaml` (under `s3:`)
- Modify: `charts/htrflow-devstack/templates/rustfs.yaml` (the init ConfigMap's `init.sh`, and the init
  Job's env)
- Modify: `scripts/compose_init.py`
- Test: the devstack chart render tests. Find them with
  `grep -rln "htrflow-devstack" packages/*/tests`, likely `packages/converter/tests/test_chart_render.py`.
  Also a compose_init test if one exists (`grep -rln compose_init packages/*/tests`).

**Interfaces:**
- Produces: devstack value `s3.imageCacheBucket: ""`, and compose env `IMAGE_CACHE_BUCKET`.

- [ ] **Step 1: Write the failing tests**
  - Rendering the devstack chart with `rustfs.enabled=true`, `rustfs.init.enabled=true`,
    `devStack.insecureDefaults=true` and `s3.imageCacheBucket=images-batch` gives an init script that runs
    `ensure_bucket "$IMAGE_CACHE_BUCKET"`, and an init Job env with `IMAGE_CACHE_BUCKET=images-batch`.
  - No bucket policy or CORS is applied to it: the script has no `put-bucket-policy --bucket "$IMAGE_CACHE_BUCKET"`.
  - Without the value, the rendered init ConfigMap and Job are byte-identical to today.

  Use the file's existing render helper and its golden/CI values.

- [ ] **Step 2: Run and see them fail.**

- [ ] **Step 3: Implement**
  - `values.yaml`, under `s3:`:

```yaml
  # A private bucket for the image cache (converter.yaml's image_cache.bucket):
  # created by the init hook with no public policy. Empty: none.
  imageCacheBucket: ""
```

  - `rustfs.yaml` `init.sh`, after `ensure_bucket "$S3_BUCKET"`, with the policy and CORS lines left
    untouched:

```sh
    if [ -n "${IMAGE_CACHE_BUCKET:-}" ]; then
      ensure_bucket "$IMAGE_CACHE_BUCKET"   # private: no policy, no CORS
    fi
```

  - The init Job's container env gets the following, only when set:

```yaml
            {{- with .Values.s3.imageCacheBucket }}
            - name: IMAGE_CACHE_BUCKET
              value: {{ . | quote }}
            {{- end }}
```

    Put it beside the existing env entries, matching their indentation.
  - `compose_init.py`: `IMAGE_CACHE_BUCKET = os.environ.get("IMAGE_CACHE_BUCKET", "")`. In `main()`, create
    it the way the loop creates the other buckets, and apply no policy to it. Document the env var in the
    module docstring's "Env:" list.

- [ ] **Step 4: Run** `uv run --no-sync pytest packages/converter/tests -q -k "chart or devstack or compose"`
  and `make helm-template`.
  Expected: PASS; the templates render.

- [ ] **Step 5: Commit**

```bash
git add charts/htrflow-devstack scripts/compose_init.py packages/converter/tests
git commit -m "feat(devstack): the dev stacks create a private image cache bucket when asked"
```

---

### Task 5: Docs, README and diagrams

**Files:**
- Modify: `docs/slides/diagrams/build_docs.py`, then regenerate `docs/assets/diagrams/*.svg`
- Modify:
  - `README.md`;
  - `docs/how-it-works/page-flow.md`;
  - `docs/how-it-works/wrapper.md`;
  - `docs/getting-started/deploy.md`;
  - `docs/roadmap/cache-layer.md`.

**Interfaces:** none. This task consumes only the behaviour of Tasks 1–4.

- [ ] **Step 1: Find how the diagrams are built.** Read the header of `build_docs.py` and how it is run. It
  writes to `OUT`. Check the Makefile, `scripts/`, or its `__main__` (`grep -rn build_docs Makefile scripts docs`).
  Run it once unchanged. `git status docs/assets/diagrams` must be clean. If it is not, the generator and the
  committed SVGs have drifted; stop and report that, rather than committing unrelated diagram changes.

- [ ] **Step 2: Edit the generator.** Add the cache as an optional stop between the pod and the IIIF server,
  and label it clearly as optional. Use the generator's existing shapes, icons (`L(...)`) and `outside=`
  convention; the S3 bucket is inside, IIIF is outside.
  - **`overview.svg`** (around line 79): an "Image cache (optional)" box beside the S3 bucket, e.g.
    "source images, private", with the IIIF arrow passing through it.
  - **`architecture.svg`** (around line 115): the same box. The S3 bucket's subtitle gains "quality" where
    it lists manifest contents.
  - **`htrflow-in-a-pod.svg`** (around line 132): the fetch step reads "cache, else IIIF".
  - **`page-flow.svg`** (around line 181): the fetch path is "image cache hit → use; miss → IIIF → store".
    "After the last page" mentions `quality` where it names `manifest.json`/`iiif.json`.
  - **`seq-campaign.svg`** (around line 287): the message "fetch page N+k …" becomes a look in the image
    cache, then IIIF on a miss, then a store. Put the cache in the participants list, marked optional.
  - **`seq-signals-index.svg`** (around line 324): the same fetch change, and the ALTO carries the page
    score (`PC`).

  Keep labels short, as the existing ones are. The generator's layout helpers fix the box widths.

- [ ] **Step 3: Regenerate and look.** Run the generator. For each changed SVG, render a PNG and inspect it:
  `rsvg-convert`, or headless Chromium via playwright as used elsewhere in the repo. Check:
  - no overlapping text;
  - no label cut off;
  - the "optional" marking readable;
  - arrows pointing the right way.

  Save the PNGs outside the repo, in a scratch directory.

- [ ] **Step 4: Update the pages.**
  - **`README.md` overview:** one sentence that source images can be cached in S3, linking
    `docs/getting-started/deploy.md#cache-source-images`.
  - **`page-flow.md` and `wrapper.md`:** a short "Image cache" section covering the lookup, the checks on a
    hit, the miss path and store, "the cache never fails a page", and `bytes_fetched` counting only IIIF
    bytes.
  - **`deploy.md`:** a "Cache source images" section covering:
    - creating the private bucket, on the same S3 store as the results;
    - adding `image_cache: {bucket: …}` to `converter.yaml`;
    - the run-log line: `image cache images-batch: 12 hits, 3 misses, 3 stored`;
    - the devstack's `s3.imageCacheBucket`.
  - **`roadmap/cache-layer.md`:**
    - "What happens today" says an S3 image cache can be switched on, and links it;
    - the two variants stay, as the next step for GPU-idle or origin-load evidence;
    - add a sentence under "What both variants keep" / "Width in the key": the built S3 cache keys by
      `{ref}/{ref}_{page:05d}.jpg` with no width, a deliberate operator choice. A cached image is reused at
      whatever width first stored it.
  - Site-docs rules: no names, dates, versions, hosts or hardware.

- [ ] **Step 5: Gates.**

  Run: `scripts/docs-site.sh build --clean --strict`, set up as in Task 2.
  Expected: "No issues found".

  `README.md` is linted by the same script.

- [ ] **Step 6: Commit** (two commits)

```bash
git add docs/slides/diagrams/build_docs.py docs/assets/diagrams
git commit -m "docs(diagrams): the optional image cache on the fetch path, and the page score in the ALTO"
git add README.md docs/how-it-works docs/getting-started docs/roadmap
git commit -m "docs: the image cache in the README, the how-it-works pages, Deploy and the roadmap"
```

---

### Task 6: Live proof on the local cluster, volume R0001203 only

This task proves the feature on the real local k3s cluster. It changes nothing in the repo except, possibly,
fixes the run turns up (each with its own test and commit).

**Constraints:**
- Namespace `htr-batch` on the local cluster.
- Volume **R0001203 only**.
- Never modify the operator's campaigns repo at `~/htr-test`. Copy it to a scratch directory and work there.
- Never overwrite a registry tag the cluster already uses (`:dev`, or existing digests). Push a new tag.

- [ ] **Step 1: Image.** Build the wrapper image from this branch natively on the arm64 node.
  - Follow the Makefile's `build-wrapper` recipe and args, but with a unique tag:
    `127.0.0.1:30500/htrflow-batch:image-cache-<short-sha>`.
  - Push it, and record the pushed digest (`docker inspect --format '{{index .RepoDigests 0}}'`).
- [ ] **Step 2: Bucket.** Create `images-batch` on the cluster's RustFS. Use a one-off `aws` CLI pod in
  `htr-batch`, with the `htr-batch-s3` Secret's credentials and endpoint (the devstack init Job's image and
  pattern), and no bucket policy. Confirm with `head-bucket`.
- [ ] **Step 3: Campaign.** In a scratch copy of `~/htr-test`:
  - add `image_cache: {bucket: images-batch}` to `converter.yaml`;
  - add a pipeline, `image-cache-test-v1`, copied from an existing working one, with `image:` set to the
    digest from Step 1;
  - add a campaign with one volume. To keep the run short, list the first 10 page images of R0001203 as an
    `images:` volume with `id: R0001203`, taking the width-capped image URLs from its IIIF manifest
    (`https://lbiiif.riksarkivet.se/arkis!R0001203/manifest`, in canvas order). The key is then
    `R0001203/R0001203_0000N.jpg` for pages 1–10.
  - Render and apply with the CLI and flow the repo documents (`htrflow-campaigns render` / `apply`, or the
    Makefile's `campaigns-apply`).
- [ ] **Step 4: Run one.** Wait for the index to finish. Check:
  - `manifest.json` has `image_cache: {"bucket": "images-batch", "hits": 0, "misses": 10, "stored": 10}`;
  - `images-batch/R0001203/R0001203_00001.jpg` exists;
  - the run log has the one cache line.
- [ ] **Step 5: Run two.** Run the same campaign again under a new campaign name, so the rendered Job is new
  and the pipeline is the same. `RESUME` would skip done pages, so use a new campaign name, or delete that
  volume's results prefix first; say which you did. Check:
  - `hits: 10, misses: 0, stored: 0` and `bytes_fetched: 0`;
  - the run log shows no IIIF image fetch;
  - each page's ALTO is identical to run one's apart from the provenance block's timestamp: diff with the
    `<Processing ID="htrflow-batch">` timestamp stripped.
- [ ] **Step 6: Report.** Write the counts, the diff result, the image tag and digest, and every command to
  a scratch report. Leave the cluster as follows:
  - delete the two test campaigns' Jobs, but keep their results and the bucket;
  - leave everything else untouched.
- [ ] **Step 7: Whole-branch gates and PR.**
  - Run all of: `make ci`, `scripts/loc-budget.sh`, and `scripts/docs-site.sh build --clean --strict`.
  - Push the branch and open a PR. The body covers:
    - what it does;
    - "off by default, nothing changes";
    - the live R0001203 proof (the counts, the ALTO diff);
    - the diagram changes, with the regenerated SVGs shown in the diff.
