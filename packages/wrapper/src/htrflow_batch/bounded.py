"""What one HTTP download may cost the pod, in bytes and in time (docs: wrapper).

Every body the wrapper reads -- the manifest, each page image -- comes from a
host named in campaign data, so its size is bounded on the bytes the wrapper
ends up HOLDING, not on the bytes the host sends (3062). httpx inflates each
network chunk whole before the caller sees it, and its brotli decoder has no
output limit at all: 3 KB of body became 2 GiB, and the pod was OOM-killed
before the byte cap it had long since crossed was ever counted. So the raw
bytes are read here and inflated at most ``_CHUNK`` at a time, and only
gzip -- which the standard library inflates with an output bound -- is asked
for. Anything else a host sends anyway is refused undecoded.

Time is bounded by a ``Deadline`` per download (3063). httpx's timeouts are
per read, and each byte a host sends starts a new one: a host dripping a byte
at a time held the pod, and its GPU, until the pod's own deadline hours
later. A timer cuts the download's connection when its wall-clock budget is
spent, whichever phase it is in -- status line, headers or body.
"""

from __future__ import annotations

import socket
import threading
import zlib
from contextlib import contextmanager
from typing import Any, Iterator

import httpcore
import httpx

#: The only Content-Encoding the wrapper asks for, and so the only one it
#: accepts besides none at all.
ACCEPT_ENCODING = "gzip"

_CHUNK = 256 * 1024

#: Default wall-clock budget of one download -- the manifest, or one attempt
#: at a page (env ``DOWNLOAD_DEADLINE_SECONDS``; docs: wrapper).
DOWNLOAD_DEADLINE_SECONDS = 300.0


def http_client() -> httpx.Client:
    """The client every fetch driven by campaign data uses. S5: redirect
    chains are bounded. No keep-alive (3063): a ``Deadline`` learns a
    connection's socket as the connection is opened, and a pooled one being
    reused never is -- so its headers could drip past the deadline unseen."""
    limits = httpx.Limits(max_keepalive_connections=0)
    return httpx.Client(max_redirects=5, limits=limits)


@contextmanager
def get(
    client: httpx.Client, url: str, timeout: float, clock: Deadline
) -> Iterator[httpx.Response]:
    """A streamed GET under ``clock``, asking only for what ``body_chunks``
    can decode with a bound. ``timeout`` is httpx's, per read."""
    with (
        clock,
        client.stream(
            "GET",
            url,
            headers={"Accept-Encoding": ACCEPT_ENCODING},
            timeout=timeout,
            follow_redirects=True,
            extensions=clock.extensions,
        ) as resp,
    ):
        yield resp


class Deadline:
    """A wall-clock budget for one download, across its redirects.

    Used through ``get``: httpcore's ``trace`` extension reports each connection
    the request opens, and when the timer fires every one of them is shut
    down, so whatever read is blocked on it returns at once and the request
    fails. ``expired`` then says why -- and must be checked after a body that
    seemed to end cleanly too, since a body delimited by the connection
    closing ends the same way when it is cut."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.expired = False
        self._lock = threading.Lock()
        self._sockets: list[socket.socket] = []
        self._timer = threading.Timer(seconds, self._expire)
        self._timer.daemon = True

    @property
    def extensions(self) -> dict[str, Any]:
        return {"trace": self._trace}

    @property
    def reason(self) -> str:
        return f"deadline: not downloaded within {self.seconds:g} s"

    def __enter__(self) -> Deadline:
        self._timer.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._timer.cancel()

    def _trace(self, event: str, info: dict[str, Any]) -> None:
        # connect_tcp and start_tls both hand back the stream they opened;
        # the TLS one wraps (and detaches) the TCP one's socket.
        stream = info.get("return_value")
        if not isinstance(stream, httpcore.NetworkStream):
            return
        sock = stream.get_extra_info("socket")
        if isinstance(sock, socket.socket):
            with self._lock:
                self._sockets.append(sock)
                if self.expired:
                    _cut(sock)

    def _expire(self) -> None:
        with self._lock:
            self.expired = True
            for sock in self._sockets:
                _cut(sock)


def _cut(sock: socket.socket) -> None:
    """Shut the connection down under the thread blocked reading it. The
    base-class call on purpose: ``SSLSocket.shutdown`` also drops the TLS
    object that thread is reading through."""
    try:
        socket.socket.shutdown(sock, socket.SHUT_RDWR)
    except OSError:
        pass  # already closed, or detached into the TLS socket


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
