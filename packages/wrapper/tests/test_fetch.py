import threading
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from htrflow_batch.fetch import FetchResult, fetch_page
from htrflow_batch.iiif import PageRef

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 12  # JPEG SOI + APP0 marker


def _pages(n):
    return [
        PageRef(index=i, name=f"{i:04d}", image_url=f"https://img/{i}", canvas={})
        for i in range(1, n + 1)
    ]


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _fetch_all(pages, tmp_path, handler, **kw):
    """What stream.PageStream's pool does, one page at a time."""
    client = _client(handler)
    return {p.name: fetch_page(p, tmp_path, client, **kw) for p in pages}


def test_downloads_every_page_to_its_own_file(tmp_path):
    def handler(req):
        return httpx.Response(200, content=JPEG + req.url.path.encode())

    results = _fetch_all(_pages(3), tmp_path, handler, retries=3, backoff=0.0)
    assert all(isinstance(r, FetchResult) and r.path for r in results.values())
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "0001.jpg",
        "0002.jpg",
        "0003.jpg",
    ]
    assert all(r.size == len(JPEG) + 2 for r in results.values())


def test_failed_page_reports_error_not_exception(tmp_path):
    def handler(req):
        if req.url.path == "/2":
            return httpx.Response(500)
        return httpx.Response(200, content=JPEG)

    results = _fetch_all(_pages(3), tmp_path, handler, retries=2, backoff=0.0)
    assert results["0002"].path is None
    assert "500" in results["0002"].error
    assert results["0001"].path and results["0003"].path


def test_non_httpx_exception_caught(tmp_path):
    """Non-httpx exceptions (e.g. OSError opening the file) are caught and reported."""

    def handler(req):
        return httpx.Response(200, content=JPEG)

    original_open = Path.open

    def patched_open(self, *a, **k):
        if self.name == "0002.jpg":
            raise OSError("Disk full")
        return original_open(self, *a, **k)

    with patch.object(Path, "open", patched_open):
        results = _fetch_all(_pages(3), tmp_path, handler, retries=1, backoff=0.0)

    assert len(results) == 3
    assert all(isinstance(r, FetchResult) for r in results.values())
    for name in ("0001", "0003"):
        assert results[name].path is not None and results[name].error is None
    assert results["0002"].path is None
    # A sentence with the type in front, never repr(): this text reaches
    # the status page's notice chip through progress.json.
    assert results["0002"].error == "OSError: Disk full"


def test_upscale_400_falls_back_to_max(tmp_path):
    """lbiiif (IIIF level1) returns 400 for sized requests wider than the
    original image (no upscaling). On 400 the fetcher retries with full/max
    instead of failing the page (R0001203 page 0002, 1281px wide)."""

    def handler(req):
        if "/full/2500,/" in req.url.path:
            return httpx.Response(400)
        if "/full/max/" in req.url.path:
            return httpx.Response(200, content=JPEG + b"narrow-image")
        return httpx.Response(404)

    page = PageRef(
        index=1,
        name="0001",
        image_url="https://img/iiif/full/2500,/0/default.jpg",
        canvas={},
    )
    r = fetch_page(page, tmp_path, _client(handler), 3, 0.0)
    assert r.error is None
    assert r.path is not None and r.path.read_bytes() == JPEG + b"narrow-image"


def _one(tmp_path, handler, retries=3, max_bytes=None, stop=None):
    kw = {} if max_bytes is None else {"max_bytes": max_bytes}
    return fetch_page(
        _pages(1)[0], tmp_path, _client(handler), retries, 0.0, stop=stop, **kw
    )


def test_html_200_is_retried_then_failed(tmp_path):
    """W4: a 200 with an HTML body (login page, error page) used to be saved
    as the JPEG and burn a whole attempt in htrflow. It is a failed fetch."""
    calls = []

    def handler(req):
        calls.append(req.url.path)
        return httpx.Response(
            200, headers={"Content-Type": "text/html"}, content=b"<html>login</html>"
        )

    r = _one(tmp_path, handler, retries=2)
    assert r.path is None
    assert "text/html" in r.error
    assert len(calls) == 2  # retryable
    assert list(tmp_path.iterdir()) == []


def test_non_image_bytes_rejected_even_with_image_content_type(tmp_path):
    def handler(req):
        return httpx.Response(
            200, headers={"Content-Type": "image/jpeg"}, content=b"<html>nope</html>"
        )

    r = _one(tmp_path, handler, retries=1)
    assert r.path is None and "not an image" in r.error
    assert list(tmp_path.iterdir()) == []


def test_empty_body_rejected(tmp_path):
    def handler(req):
        return httpx.Response(200, headers={"Content-Type": "image/jpeg"}, content=b"")

    r = _one(tmp_path, handler, retries=1)
    assert r.path is None and "empty" in r.error
    assert list(tmp_path.iterdir()) == []


def test_octet_stream_with_image_bytes_accepted(tmp_path):
    """Static hosts (S3 without a content type) serve octet-stream; the
    magic bytes decide."""

    def handler(req):
        return httpx.Response(
            200, headers={"Content-Type": "application/octet-stream"}, content=JPEG
        )

    r = _one(tmp_path, handler, retries=1)
    assert r.path is not None and r.error is None


def test_body_over_cap_fails_without_retry_and_leaves_no_file(tmp_path):
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(200, content=JPEG + b"x" * 1000)

    r = _one(tmp_path, handler, retries=3, max_bytes=100)
    assert r.path is None and "too large" in r.error
    assert len(calls) == 1  # a bigger image tomorrow is not a thing
    assert list(tmp_path.iterdir()) == []


def test_content_length_over_cap_rejected_before_reading(tmp_path):
    def handler(req):
        return httpx.Response(200, headers={"Content-Length": "999999"}, content=b"")

    r = _one(tmp_path, handler, retries=1, max_bytes=100)
    assert r.path is None and "too large" in r.error


def test_partial_file_unlinked_on_write_failure(tmp_path):
    """W5: ENOSPC mid-write must not leave a truncated JPEG for htrflow."""

    def handler(req):
        return httpx.Response(200, content=JPEG + b"y" * 10)

    original_open = Path.open

    class _Broken:
        def __init__(self, f):
            self._f = f

        def write(self, data):
            raise OSError(28, "No space left on device")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self._f.close()

    def patched_open(self, *a, **k):
        f = original_open(self, *a, **k)
        return _Broken(f) if self.name == "0001.jpg" else f

    with patch.object(Path, "open", patched_open):
        r = _one(tmp_path, handler, retries=1)
    assert r.path is None and "No space left" in r.error
    assert not (tmp_path / "0001.jpg").exists()


def test_stop_event_short_circuits_a_page_without_touching_the_network(tmp_path):
    """W10, per page: once the run has failed a queued page must not spend
    its retries/timeouts — it comes back "stopped" without a request."""
    started = []

    def handler(req):
        started.append(req.url.path)
        return httpx.Response(200, content=JPEG)

    stop = threading.Event()
    stop.set()
    r = _one(tmp_path, handler, retries=3, stop=stop)
    assert r.path is None and "stopped" in r.error
    assert started == []


def test_the_unscaled_fallback_does_not_spend_an_attempt(tmp_path):
    """W13: the fallback asks a DIFFERENT URL, so it is not another go at the
    one that failed -- and taking one of the page's attempts meant a single
    retry (or a flaky server after the switch) lost the page for a reason the
    fetcher had already worked out."""
    seen = []

    def handler(req):
        seen.append(str(req.url))
        if "/full/2500,/" in req.url.path:
            return httpx.Response(400)
        return httpx.Response(200, content=JPEG + b"narrow-image")

    page = PageRef(
        index=1,
        name="0001",
        image_url="https://img/iiif/full/2500,/0/default.jpg",
        canvas={},
    )
    r = fetch_page(page, tmp_path, _client(handler), 1, 0.0)
    assert r.error is None
    assert [u.rsplit("/full/", 1)[1] for u in seen] == [
        "2500,/0/default.jpg",
        "max/0/default.jpg",
    ]


def test_a_400_that_is_not_a_size_still_spends_its_attempts(tmp_path):
    """The fallback is a one-off substitution; a 400 it cannot change is an
    ordinary failure and must not loop."""
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(400)

    page = PageRef(index=1, name="0001", image_url="https://img/a.jpg", canvas={})
    r = fetch_page(page, tmp_path, _client(handler), 3, 0.0)
    assert r.error == "HTTP 400"
    assert len(calls) == 3


def test_a_400_after_the_fallback_does_not_loop(tmp_path):
    """Once the URL is unscaled the substitution is a no-op, so the second
    400 falls through and spends the attempt like any other."""
    calls = []

    def handler(req):
        calls.append(str(req.url))
        return httpx.Response(400)

    page = PageRef(
        index=1,
        name="0001",
        image_url="https://img/iiif/full/2500,/0/default.jpg",
        canvas={},
    )
    r = fetch_page(page, tmp_path, _client(handler), 2, 0.0)
    assert r.error == "HTTP 400"
    assert len(calls) == 3  # the sized one, then the unscaled one twice


def _real_jpeg(width: int, height: int) -> bytes:
    """A decodable image of a named size -- the byte cap cannot tell one of
    these from the other, which is the point of MAX_IMAGE_PIXELS."""
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height)).save(buffer, format="JPEG")
    return buffer.getvalue()


def test_an_image_over_the_pixel_cap_is_refused(tmp_path):
    """W14: FETCH_MAX_BYTES bounds the DOWNLOAD, not what decoding it costs.
    A few MB of JPEG can carry a gigapixel image, and htrflow decodes every
    page into memory -- so the pod is OOM-killed by a file that passed every
    check the fetcher had."""
    body = _real_jpeg(40, 40)

    def handler(req):
        return httpx.Response(200, content=body)

    page = PageRef(index=1, name="0001", image_url="https://img/1.jpg", canvas={})
    r = fetch_page(page, tmp_path, _client(handler), 3, 0.0, max_pixels=100)
    assert r.path is None
    assert r.error == "too large: 40x40 = 1600 pixels > 100"
    assert not (tmp_path / "0001.jpg").exists()  # nothing left in the workdir


def test_an_image_inside_the_pixel_cap_is_kept(tmp_path):
    body = _real_jpeg(40, 40)

    def handler(req):
        return httpx.Response(200, content=body)

    page = PageRef(index=1, name="0001", image_url="https://img/1.jpg", canvas={})
    r = fetch_page(page, tmp_path, _client(handler), 3, 0.0, max_pixels=10_000)
    assert r.error is None and r.path is not None


def test_the_pixel_cap_is_not_a_decodability_gate(tmp_path):
    """A file Pillow cannot read is left to htrflow, which is where a page
    that will not decode has always failed. This guard is about SIZE."""

    def handler(req):
        return httpx.Response(200, content=JPEG + b"not really a jpeg")

    page = PageRef(index=1, name="0001", image_url="https://img/1.jpg", canvas={})
    assert fetch_page(page, tmp_path, _client(handler), 3, 0.0).error is None


def _png_declaring(width: int, height: int) -> bytes:
    """A one-pixel PNG whose IHDR claims a size it does not carry -- what a
    decompression bomb looks like to a header read, without the gigabytes."""
    import io
    import struct
    import zlib

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (1, 1)).save(buffer, format="PNG")
    data = bytearray(buffer.getvalue())
    ihdr = data.index(b"IHDR")
    struct.pack_into(">II", data, ihdr + 4, width, height)
    crc = zlib.crc32(bytes(data[ihdr : ihdr + 17])) & 0xFFFFFFFF
    struct.pack_into(">I", data, ihdr + 17, crc)
    return bytes(data)


def test_a_real_decompression_bomb_is_rejected(tmp_path):
    """W14 review, BLOCKING: Pillow raises DecompressionBombError of its own
    above ~179 MP, and the guard's `except Exception: return` swallowed it --
    so a 40000x40000 image sailed past the very check it exists for. The
    wrapper's cap has to be the only gate."""
    body = _png_declaring(40000, 40000)

    def handler(req):
        return httpx.Response(200, content=body)

    page = PageRef(index=1, name="0001", image_url="https://img/1.png", canvas={})
    r = fetch_page(page, tmp_path, _client(handler), 3, 0.0, max_pixels=100_000_000)
    assert r.path is None
    assert r.error == "too large: 40000x40000 = 1600000000 pixels > 100000000"
    assert not (tmp_path / "0001.jpg").exists()


def test_a_page_under_the_cap_passes_without_a_warning(tmp_path, recwarn):
    """Pillow warns above ~89 MP, which is BELOW the wrapper's own default
    cap -- so a legitimate 90-100 MP page printed a DecompressionBombWarning
    into the run log for a page nothing was wrong with."""
    import warnings

    body = _png_declaring(10_000, 10_000)  # 100 MP: over Pillow's warn line

    def handler(req):
        return httpx.Response(200, content=body)

    page = PageRef(index=1, name="0001", image_url="https://img/1.png", canvas={})
    warnings.simplefilter("always")  # record them rather than raise them
    r = fetch_page(page, tmp_path, _client(handler), 3, 0.0, max_pixels=150_000_000)
    assert r.error is None and r.path is not None
    assert [w for w in recwarn if "decompression" in str(w.message).lower()] == []


def test_a_gzip_bomb_is_capped_on_its_decoded_size_in_bounded_memory(
    tmp_path, gzip_bomb, peak_mib
):
    """3062: httpx inflated each network chunk whole before the cap was
    counted, so ~250 KB of gzip became 256 MiB in one allocation -- and
    brotli has no output limit at all. The cap is on DECODED bytes, counted
    as they are inflated, a chunk at a time."""
    bomb = gzip_bomb(256, head=JPEG)
    calls = []

    def handler(req):
        calls.append(req.headers.get("Accept-Encoding"))
        return httpx.Response(
            200, headers={"Content-Encoding": "gzip"}, stream=httpx.ByteStream(bomb)
        )

    results: list = []
    peak = peak_mib(lambda: results.append(_one(tmp_path, handler, max_bytes=1 << 20)))
    r = results[0]
    assert r.path is None and "too large" in r.error
    assert len(calls) == 1  # permanent: the same bomb tomorrow
    assert calls[0] == "gzip"  # nothing advertised that is not decoded bounded
    assert peak < 16, f"peak {peak:.0f} MiB"
    assert list(tmp_path.iterdir()) == []


def test_a_gzip_image_under_the_cap_is_decoded(tmp_path):
    import gzip

    def handler(req):
        return httpx.Response(
            200,
            headers={"Content-Encoding": "gzip"},
            stream=httpx.ByteStream(gzip.compress(JPEG)),
        )

    r = _one(tmp_path, handler)
    assert r.error is None and r.path is not None
    assert r.path.read_bytes() == JPEG and r.size == len(JPEG)


@pytest.mark.parametrize("encoding", ["br", "zstd", "deflate", "gzip, br"])
def test_an_encoding_we_did_not_ask_for_is_refused_undecoded(tmp_path, encoding):
    """3062: brotli (and zstd) decoders have no output bound; only gzip is
    asked for, and anything else is refused before a byte is decoded."""
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(
            200,
            headers={"Content-Encoding": encoding},
            stream=httpx.ByteStream(b"\x8b" * 64),
        )

    r = _one(tmp_path, handler)
    assert r.path is None and "Content-Encoding" in r.error
    assert len(calls) == 1
