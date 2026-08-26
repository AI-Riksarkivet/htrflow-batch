"""Ship the run's own stdout/stderr to S3 while it runs (docs: wrapper).

The browser only ever reads S3, so a live log means the pod has to put its
log there itself. ``LogCapture`` tees ``sys.stdout``/``sys.stderr`` into one
in-memory buffer (the originals still get everything, so ``kubectl logs`` is
unchanged) and a daemon thread uploads that buffer whenever it changed. The
final ``finish()`` upload makes the object the complete log, not a tail.

Shipping must never fail the run: upload errors are logged once and retried
on the next interval.
"""

from __future__ import annotations

import io
import logging
import sys
import threading
from typing import Callable, Optional, TextIO

log = logging.getLogger("htrflow_batch.logship")

# Sizes are in characters (the buffer holds str); ASCII logs make it bytes.
# Truncation trims to HEAD + TAIL (3 MiB) and re-triggers only at CAP, so the
# expensive join is amortised over ~1 MiB of new output, not paid per write.
CAP_BYTES = 4 * 1024 * 1024
HEAD_BYTES = 1 * 1024 * 1024
TAIL_BYTES = 2 * 1024 * 1024
TRUNCATION_MARKER = (
    "\n... [log truncated: middle dropped by htrflow_batch.logship] ...\n"
)


class _Tee(io.TextIOBase):
    """Write-through wrapper: the original stream gets every write first."""

    def __init__(self, original: TextIO, capture: "LogCapture"):
        self._original = original
        self._capture = capture

    def write(self, s: str) -> int:
        self._original.write(s)
        self._capture._append(s)
        return len(s)

    def flush(self) -> None:
        self._original.flush()

    def fileno(self) -> int:
        return self._original.fileno()

    def isatty(self) -> bool:
        return self._original.isatty()

    def writable(self) -> bool:
        return True

    @property
    def encoding(self) -> str:  # type: ignore[override]
        return getattr(self._original, "encoding", "utf-8")


class LogCapture:
    def __init__(
        self,
        cap_bytes: int = CAP_BYTES,
        head_bytes: int = HEAD_BYTES,
        tail_bytes: int = TAIL_BYTES,
    ):
        self._lock = threading.Lock()
        # Serialises uploads: finish() must not race a slow periodic PUT and
        # let the older body land last on the same key.
        self._upload_lock = threading.Lock()
        self._chunks: list[str] = []
        self._size = 0
        self._version = 0  # bumped on every append
        self._shipped_version = 0
        self._cap = cap_bytes
        self._head = head_bytes
        self._tail = tail_bytes
        self._upload: Optional[Callable[[str], None]] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._originals: Optional[tuple[TextIO, TextIO]] = None
        self._rebound: list[tuple[logging.StreamHandler, TextIO]] = []
        self._added_handler: Optional[logging.Handler] = None
        self._warned = False

    @classmethod
    def install(cls, **kwargs) -> "LogCapture":
        """Tee both process streams into a new capture. Install BEFORE
        ``logging.basicConfig`` so the root StreamHandler binds the tee."""
        capture = cls(**kwargs)
        capture._originals = (sys.stdout, sys.stderr)
        sys.stdout = _Tee(sys.stdout, capture)  # type: ignore[assignment]
        sys.stderr = _Tee(sys.stderr, capture)  # type: ignore[assignment]
        # A root StreamHandler that bound the raw stream before we got here
        # (an earlier basicConfig in the same process) would bypass the tee;
        # point it at the tee for the capture's lifetime. Handlers on other
        # streams (pytest's caplog, files) are left alone.
        for handler in logging.getLogger().handlers:
            if not isinstance(handler, logging.StreamHandler):
                continue
            for original, tee in zip(capture._originals, (sys.stdout, sys.stderr)):
                if handler.stream is original:
                    capture._rebound.append((handler, original))
                    handler.setStream(tee)  # ty: ignore[invalid-argument-type]
        return capture

    def attach_logging(
        self,
        level: int = logging.INFO,
        fmt: str = "%(asctime)s %(levelname)s %(message)s",
    ) -> None:
        """Guarantee root logging reaches the tee (and so the shipped log).

        ``logging.basicConfig`` is a no-op whenever the root logger already
        has handlers — pytest's capture, or a library that configured logging
        at import — and then nothing would ever write to stderr. Reuse a root
        StreamHandler already on the tee, else add one; ``finish`` removes
        what was added here.
        """
        root = logging.getLogger()
        root.setLevel(level)
        tee = sys.stderr
        for handler in root.handlers:
            if isinstance(handler, logging.StreamHandler) and handler.stream is tee:
                return
        handler = logging.StreamHandler(tee)
        handler.setFormatter(logging.Formatter(fmt))
        root.addHandler(handler)
        self._added_handler = handler

    # -- buffer -----------------------------------------------------------

    def _append(self, s: str) -> None:
        if not s:
            return
        with self._lock:
            self._chunks.append(s)
            self._size += len(s)
            self._version += 1
            if self._size > self._cap:
                self._truncate_locked()

    def _truncate_locked(self) -> None:
        text = "".join(self._chunks)
        head = text[: self._head]
        cut = head.rfind("\n")
        if cut > 0:
            head = head[: cut + 1]
        tail = text[-self._tail :] if self._tail > 0 else ""
        nl = tail.find("\n")
        if nl >= 0:
            tail = tail[nl + 1 :]
        self._chunks = [head, TRUNCATION_MARKER, tail]
        self._size = sum(len(c) for c in self._chunks)

    def text(self) -> str:
        with self._lock:
            return "".join(self._chunks)

    # -- shipping ---------------------------------------------------------

    def start_shipping(self, upload: Callable[[str], None], interval: float) -> None:
        """Upload the buffer every ``interval`` seconds when it changed.
        ``interval <= 0`` disables periodic shipping (``finish`` still ships)."""
        self._upload = upload
        if interval <= 0:
            return
        # Claim the key right away: a retried volume must replace the previous
        # attempt's log before the reader's first poll, not 15 s later.
        self.ship()
        self._thread = threading.Thread(
            target=self._run, args=(interval,), daemon=True, name="logship"
        )
        self._thread.start()

    def _run(self, interval: float) -> None:
        while not self._stop.wait(interval):
            self.ship()

    def ship(self) -> bool:
        """Upload if anything was appended since the last successful upload.
        Returns True on upload. Never raises."""
        if self._upload is None:
            return False
        with self._upload_lock:
            with self._lock:
                version = self._version
                if version == self._shipped_version:
                    return False
                text = "".join(self._chunks)
            try:
                self._upload(text)
            except Exception:
                if not self._warned:
                    self._warned = True
                    log.warning("could not ship run log; will retry", exc_info=True)
                return False
            self._shipped_version = version
            self._warned = False  # a later outage warns again
            return True

    def finish(self) -> None:
        """Stop the thread, do the final upload, restore the streams."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=30)
        self.ship()
        if self._added_handler is not None:
            logging.getLogger().removeHandler(self._added_handler)
            self._added_handler = None
        for handler, original in self._rebound:
            handler.setStream(original)
        self._rebound = []
        if self._originals is not None:
            sys.stdout, sys.stderr = self._originals  # type: ignore[assignment]
            self._originals = None
