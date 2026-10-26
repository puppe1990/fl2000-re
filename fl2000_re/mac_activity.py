"""Bound a macOS latency-critical activity to the lifetime of USB video."""

from __future__ import annotations

import ctypes
import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from fl2000_re.native_bridge import build_native_bridge


@dataclass
class VideoActivity:
    """Own one retained Foundation token; close it exactly once.

    Example: activity.close() ends the video scheduling hint.
    """

    token: int
    end: Callable[[int], None]

    def close(self) -> None:
        if not self.token:
            return
        token, self.token = self.token, 0
        self.end(token)


def _load_activity_library():
    source = Path(__file__).resolve().parent.parent / "native" / "video_activity.m"
    cache = Path.home() / "Library" / "Caches" / "fl2000-re"
    library = ctypes.CDLL(str(build_native_bridge(source, cache)))
    library.fl2000_begin_video_activity.argtypes = []
    library.fl2000_begin_video_activity.restype = ctypes.c_void_p
    library.fl2000_end_video_activity.argtypes = [ctypes.c_void_p]
    library.fl2000_end_video_activity.restype = None
    return library


def begin_video_activity() -> VideoActivity | None:
    """Request precise timer/I/O scheduling only while the USB feeder is active.

    Allows user/system idle sleep and degrades gracefully off macOS.
    Example: activity = begin_video_activity()
    """
    if sys.platform != "darwin":
        return None
    try:
        library = _load_activity_library()
        token = library.fl2000_begin_video_activity()
        if not token:
            raise OSError("Foundation activity token=NULL; expected a retained video activity")
        activity = VideoActivity(token, library.fl2000_end_video_activity)
    except (OSError, AttributeError, subprocess.SubprocessError) as exc:
        print(
            json.dumps({"event": "macos_video_activity_unavailable", "error": str(exc)}), flush=True
        )
        return None
    print(
        json.dumps({"event": "macos_video_activity_started", "idle_sleep_allowed": True}),
        flush=True,
    )
    return activity
