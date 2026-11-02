"""Detect actual sleep without mistaking a wall-clock correction for a wake.

Apple Libc gen/clock_gettime.c maps MONOTONIC_RAW to mach_continuous_time
(includes sleep), and UPTIME_RAW to mach_absolute_time (excludes sleep).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable

MAX_CLOCK_READ_SPAN_S = 0.05
WAKE_GAP_S = 3.0


class SystemWakeError(RuntimeError):
    """macOS slept; FL2000/IT66121 HDMI is gone but this process is still alive."""


def wall_minus_uptime_gap(
    wall_now: float, uptime_now: float, wall_prev: float, uptime_prev: float
) -> float:
    """Elapsed continuous clock minus awake uptime; retained name for compatibility.

    Inputs must be monotonic clock samples, never time.time() (NTP can step it).

    Example: wall_minus_uptime_gap(130, 50.2, 100, 50) == 29.8
    """
    return (wall_now - wall_prev) - (uptime_now - uptime_prev)


def woke_from_sleep(gap_s: float, threshold_s: float = WAKE_GAP_S) -> bool:
    return gap_s >= threshold_s


def raise_if_woke(
    wall_prev: float | None,
    up_prev: float | None,
    clocks: Callable[[], tuple[float | None, float | None]] | None = None,
) -> tuple[float | None, float | None]:
    """Raise SystemWakeError when the machine slept during a stream.

    Example: raise_if_woke(100, 70) returns the latest clocks while awake.
    """
    continuous, awake = (clocks or read_sleep_clocks)()
    if continuous is None or awake is None:
        return wall_prev, up_prev
    if wall_prev is None or up_prev is None:
        return continuous, awake
    gap = wall_minus_uptime_gap(continuous, awake, wall_prev, up_prev)
    if woke_from_sleep(gap):
        print(
            json.dumps(
                {
                    "event": "system_wake_detected",
                    "sleep_gap_s": round(gap, 3),
                    "clock_source": "monotonic_raw_minus_uptime_raw",
                }
            ),
            flush=True,
        )
        raise SystemWakeError(
            "macOS saiu do descanso. O FL2000 perde o HDMI no sleep; "
            "saindo para o LaunchAgent religar (ou rode ./bin/extend)."
        )
    return continuous, awake


def sleep_clock_pair() -> tuple[float, float] | None:
    """Sample sleep-inclusive and awake-only clocks; reject interrupted sampling.

    Example: sleep_clock_pair() returns raw seconds on macOS, None elsewhere.
    """
    continuous = getattr(time, "CLOCK_MONOTONIC_RAW", None)
    awake = getattr(time, "CLOCK_UPTIME_RAW", None)
    if continuous is None or awake is None:
        return None
    before = time.clock_gettime(continuous)
    uptime = time.clock_gettime(awake)
    after = time.clock_gettime(continuous)
    if after - before > MAX_CLOCK_READ_SPAN_S:
        return None
    return (before + after) / 2, uptime


def read_sleep_clocks() -> tuple[float | None, float | None]:
    """Represent unavailable or interrupted clock sampling explicitly.

    Example: read_sleep_clocks() returns (None, None) on unsupported platforms.
    """
    sample = sleep_clock_pair()
    return (None, None) if sample is None else sample
