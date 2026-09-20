"""Stamp live CLI/log lines with a local wall-clock time.

The LaunchAgent redirects stdout+stderr to one log file. Without a timestamp a
long-running crash cannot be placed in time (the capture child died at an unknown
hour on 2026-09-20). Wrapping the text streams once gives every existing print a
stamp, tracebacks included, without touching each call site.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from typing import Any

STAMP_FORMAT = "[%Y-%m-%d %H:%M:%S] "
Clock = Callable[[], float]


def stamp(clock: Clock = time.time) -> str:
    """Local-time prefix for one log line.

    Example: stamp(lambda: 0.0).startswith("[19")
    """
    return time.strftime(STAMP_FORMAT, time.localtime(clock()))


class TimestampedStream:
    """Text stream proxy that prefixes each emitted line with one stamp.

    Partial writes are buffered until a newline so a line assembled from several
    writes still gets a single timestamp.
    """

    def __init__(self, raw: Any, clock: Clock = time.time) -> None:
        self._raw = raw
        self._clock = clock
        self._pending = ""

    def write(self, text: str) -> int:
        self._pending += text
        while "\n" in self._pending:
            line, self._pending = self._pending.split("\n", 1)
            self._raw.write(stamp(self._clock) + line + "\n")
        return len(text)

    def flush(self) -> None:
        if self._pending:
            self._raw.write(stamp(self._clock) + self._pending)
            self._pending = ""
        self._raw.flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._raw, name)


def install_timestamped_streams(clock: Clock = time.time) -> None:
    """Wrap sys.stdout/sys.stderr once; safe to call from every entry point."""
    if sys.stdout is not None and not isinstance(sys.stdout, TimestampedStream):
        sys.stdout = TimestampedStream(sys.stdout, clock)
    if sys.stderr is not None and not isinstance(sys.stderr, TimestampedStream):
        sys.stderr = TimestampedStream(sys.stderr, clock)
