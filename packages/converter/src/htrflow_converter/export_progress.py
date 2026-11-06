"""Console progress of ``htrflow-campaigns export``, on stderr.

An export of many volumes runs for a long time while the report is printed
only at the end. So each volume gets a line when it is done, and in a
terminal a counter for the slow steps (reading page sizes, building the
ALTO) is redrawn in place. Written to a file or a pipe, only the finished
lines appear, one per volume, so a log is not flooded.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TextIO

if TYPE_CHECKING:
    from htrflow_converter.export import Row

__all__ = ["Progress", "duration"]

_CLEAR = "\r\x1b[K"  # back to the start of the line, erase it


def duration(seconds: float) -> str:
    """``42s``, ``3m 05s``, ``1h 02m``."""
    s = round(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s // 3600}h {s % 3600 // 60:02d}m"


class Progress:
    def __init__(self, stream: TextIO, total: int) -> None:
        self._out = stream
        self._total = total
        self._live = stream.isatty()
        self._head = ""
        self._drawn = False  # a live counter is on the line

    def start(self, index: int, volume: str) -> None:
        width = len(str(self._total))
        self._head = f"[{index:>{width}}/{self._total}] {volume}"
        if self._live:  # shown before the first, possibly slow, read
            self._draw("reading results")

    def pages(self, step: str, done: int, total: int) -> None:
        if self._live:
            self._draw(f"{step} {done}/{total}")

    def _draw(self, status: str) -> None:
        self._out.write(f"{_CLEAR}{self._head}  {status}")
        self._out.flush()
        self._drawn = True

    def finish(self, row: Row, seconds: float) -> None:
        parts = [self._head, row.outcome]
        if row.pages:
            parts.append(f"{row.pages} pages")
        parts.append(f"({duration(seconds)})")
        if row.reason:
            parts.append(row.reason)
        self._line("  ".join(parts))

    def done(self, seconds: float) -> None:
        noun = "volume" if self._total == 1 else "volumes"
        self._line(f"{self._total} {noun} in {duration(seconds)}")

    def _line(self, text: str) -> None:
        self._out.write(f"{_CLEAR if self._drawn else ''}{text}\n")
        self._out.flush()
        self._drawn = False
