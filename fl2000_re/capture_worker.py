"""Isolated screen capture and publication; this process never touches USB."""

from __future__ import annotations

import contextlib
import os
import time

from fl2000_re.capture import Grabber, grab_cg_display_rgb, grab_main_display_rgb
from fl2000_re.capture_pacing import (
    FLUID_CAPTURE_FPS,
    IDLE_CAPTURE_FPS,
    _sleep_until_tick,
    capture_backoff_s,
    capture_period_s,
    pointer_capture_period,
    retune_pointer_period,
)
from fl2000_re.frame_buffer import write_shared_frame
from fl2000_re.motion_capture import MotionCapture
from fl2000_re.pixels import pack_frame
from fl2000_re.stream_metrics import CaptureTimings, SharedCaptureMetrics


def hdmi_capture_worker(
    width: int,
    height: int,
    bpp: int,
    display_id: int | None,
    shm,
    n: int,
    counter,
    stop,
    underscan: float,
    stretch_x: float,
    include_cursor: bool = True,
    grab_ms=None,
    target_fps: int | None = None,
    idle_flag=None,
    frame_lock=None,
    metrics: SharedCaptureMetrics | None = None,
) -> None:
    """Fill shm with a packed HDMI frame; never touches USB.

    display_id is a CGDirectDisplayID for extend, or None to clone the Air.
    target_fps paces the loop to the sink's refresh; None runs flat out, which
    only makes sense when nobody is waiting on a fixed cadence.
    """
    # Array(..., lock=False) has no get_obj() on some Python builds.
    buf = shm.get_obj() if hasattr(shm, "get_obj") else shm
    grab = (
        grab_main_display_rgb if display_id is None else _grab_display(display_id, include_cursor)
    )
    motion_capture = MotionCapture(grab, width, height, underscan, stretch_x)
    period = capture_period_s(target_fps) if target_fps else 0.0
    next_tick = time.monotonic()
    mouse_at: tuple[int, int] | None = None
    moved_at = next_tick
    bounds = _virtual_display_bounds(display_id)
    previous_rgb = previous_packed = None
    while not stop.is_set():
        if target_fps:
            if display_id is not None and bounds is None:
                bounds = _virtual_display_bounds(display_id)
            mouse_at, moved_at, period, next_tick = _retune_from_live_pointer(
                target_fps, mouse_at, moved_at, period, next_tick, display_bounds=bounds
            )
            if idle_flag is not None:
                idle_flag.value = int(period >= pointer_capture_period(IDLE_CAPTURE_FPS) - 1e-9)
        captured_at = time.monotonic()
        t0 = time.perf_counter()
        rgb = motion_capture()
        if target_fps and motion_capture.moving:
            # Video can move while the pointer is parked on another monitor.
            period = min(period, pointer_capture_period(FLUID_CAPTURE_FPS))
            if idle_flag is not None:
                idle_flag.value = 0
        pack_start = time.perf_counter()
        packed = previous_packed if rgb is previous_rgb else pack_frame(rgb, bpp)
        pack_ms = (time.perf_counter() - pack_start) * 1000
        previous_rgb, previous_packed = rgb, packed
        grab_s = time.perf_counter() - t0
        if grab_ms is not None:
            grab_ms.value = grab_s * 1000
        if len(packed) != n:
            continue
        requested_period = period
        period = max(period, grab_s * 1.25) if period else 0.0
        extra = capture_backoff_s(grab_s)
        with frame_lock if frame_lock is not None else contextlib.nullcontext():
            if metrics is not None:
                metrics.record(
                    CaptureTimings(
                        capture_ms=motion_capture.capture_ms,
                        scale_ms=motion_capture.scale_ms,
                        pack_ms=pack_ms,
                        requested_fps=1 / requested_period if requested_period else 0,
                        effective_fps=1 / max(period, grab_s + extra),
                        mode="movimento"
                        if motion_capture.moving
                        else "repouso"
                        if idle_flag is not None and idle_flag.value
                        else "texto",
                        backend=motion_capture.scaler.backend,
                        cached=motion_capture.cached,
                        backoff_ms=extra * 1000,
                    ),
                    captured_at,
                )
            write_shared_frame(buf, packed)
            counter.value += 1
        if extra:
            time.sleep(extra)
            next_tick = time.monotonic()
        elif period:
            next_tick = _sleep_until_tick(next_tick, period, time.monotonic())


def _retune_from_live_pointer(
    active_fps: int,
    mouse_at: tuple[int, int] | None,
    moved_at: float,
    period: float,
    next_tick: float,
    display_bounds: tuple[int, int, int, int] | None = None,
) -> tuple[tuple[int, int] | None, float, float, float]:
    from fl2000_re import cursor_overlay

    try:
        xy = cursor_overlay.mouse_global_xy()
    except Exception:
        xy = None
    return retune_pointer_period(
        active_fps,
        mouse_at,
        moved_at,
        period,
        next_tick,
        time.monotonic(),
        xy,
        display_bounds,
    )


def _virtual_display_bounds(display_id: int | None) -> tuple[int, int, int, int] | None:
    if display_id is None:
        return None
    from fl2000_re.capture import cg_display_bounds

    try:
        return cg_display_bounds(display_id)
    except Exception:
        return None


def _grab_display(display_id: int, include_cursor: bool = True) -> Grabber:
    return lambda: grab_cg_display_rgb(display_id, include_cursor=include_cursor)


def lower_capture_priority(nice: int = 5) -> None:
    """Give the 60 Hz USB pump priority over the capture child.

    The child spends ~50 ms per frame at 1280x960; nice(5) keeps the pump from
    missing ticks when a grab spikes. Example: lower_capture_priority()
    """
    with contextlib.suppress(OSError):
        os.nice(nice)


def _capture_child(*args) -> None:
    lower_capture_priority()
    hdmi_capture_worker(*args)
