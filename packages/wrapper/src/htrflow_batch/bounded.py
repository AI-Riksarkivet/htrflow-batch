"""What one HTTP body may cost the pod (docs: wrapper).

Every body the wrapper reads -- the manifest, each page image -- comes from a
host named in campaign data, so its size is bounded on the bytes the wrapper
ends up HOLDING, not on the bytes the host sends (3062). httpx inflates each
network chunk whole before the caller sees it, and its brotli decoder has no
output limit at all: 3 KB of body became 2 GiB, and the pod was OOM-killed
before the byte cap it had long since crossed was ever counted. So the raw
bytes are read here and inflated at most ``_CHUNK`` at a time, and only
gzip -- which the standard library inflates with an output bound -- is asked
for. Anything else a host sends anyway is refused undecoded.
"""

from __future__ import annotations

import zlib
from typing import Iterator

import httpx

#: The only Content-Encoding the wrapper asks for, and so the only one it
#: accepts besides none at all.
ACCEPT_ENCODING = "gzip"

_CHUNK = 256 * 1024


class TooLarge(Exception):
    """The decoded body passed the cap. Permanent: it is the same body tomorrow."""


class BadEncoding(Exception):
    """A Content-Encoding not asked for, or gzip that does not inflate."""


def body_chunks(resp: httpx.Response, max_bytes: int) -> Iterator[bytes]:
    """The decoded body of ``resp``, in chunks of at most ``_CHUNK`` bytes;
    raises ``TooLarge`` the moment more than ``max_bytes`` have come out."""
    encoding = resp.headers.get("Content-Encoding", "").strip().lower()
    if encoding in ("", "identity"):
        inflate = None
    elif encoding in ("gzip", "x-gzip"):
        inflate = zlib.decompressobj(16 + zlib.MAX_WBITS)
    else:
        raise BadEncoding(f"Content-Encoding {encoding!r} was not asked for")
    if resp.is_stream_consumed:
        # Built in memory (``Response(content=)``, as a mock transport returns
        # one): read and decoded when it was made, so there is no raw stream
        # left, and only the cap still has anything to decide.
        inflate, raws = None, resp.iter_bytes(_CHUNK)
    else:
        raws = resp.iter_raw(_CHUNK)
    size = 0
    for raw in raws:
        for chunk in _inflated(inflate, raw) if inflate else (raw,):
            size += len(chunk)
            if size > max_bytes:
                raise TooLarge(f"> {max_bytes} bytes")
            yield chunk


def _inflated(inflate, data: bytes) -> Iterator[bytes]:
    """``data`` inflated ``_CHUNK`` at a time: ``max_length`` keeps the rest
    of the input in ``unconsumed_tail`` instead of expanding it all at once.
    A full chunk with no tail left may still have output pending inside the
    decompressor, so the loop only ends on a short one."""
    try:
        while True:
            out = inflate.decompress(data, _CHUNK)
            data = inflate.unconsumed_tail
            if out:
                yield out
            if not data and len(out) < _CHUNK:
                return
    except zlib.error as e:
        raise BadEncoding(f"gzip body does not inflate: {e}") from e
