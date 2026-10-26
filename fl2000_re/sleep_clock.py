"""Detect actual sleep without mistaking a wall-clock correction for a wake.

Apple Libc gen/clock_gettime.c maps MONOTONIC_RAW to mach_continuous_time
(includes sleep), and UPTIME_RAW to mach_absolute_time (excludes sleep).
"""

from __future__ import annotations

import time

MAX_CLOCK_READ_SPAN_S = 0.05


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
