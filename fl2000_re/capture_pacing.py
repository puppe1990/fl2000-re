"""Capture and USB cadence, pointer policy and compositor headroom."""

from __future__ import annotations

import time

# USB keeps resending at 60 Hz; 69 CG grabs/s froze WindowServer on the 8 GB Air.
EXTEND_CAPTURE_FPS = 20
FLUID_CAPTURE_FPS = 60
IDLE_CAPTURE_FPS = 5
MOUSE_IDLE_S = 10.0
HEAVY_GRAB_S = 0.080
MAX_BACKOFF_S = 2.0
# Lead avoids aliasing a capture onto a frame the pump already sent.
CAPTURE_LEAD = 1.15


def frame_period_s(freq: int) -> float:
    """Bulk cadence for one FL2000 scanout (unpaced rewrites tear mid-scanout).

    Example: frame_period_s(60) == pytest.approx(1 / 60)
    """
    if freq <= 0:
        raise ValueError(f"frame rate must be positive, got freq={freq!r}")
    return 1.0 / freq


def capture_fps_for_idle(
    idle_s: float,
    active_fps: int = EXTEND_CAPTURE_FPS,
    idle_fps: int = IDLE_CAPTURE_FPS,
    idle_after_s: float = MOUSE_IDLE_S,
    on_display: bool = False,
    fluid_fps: int = FLUID_CAPTURE_FPS,
) -> int:
    """Fluid while the pointer is on the Hagibis; 5 fps after 10s still.

    USB still resends the last frame at 60 Hz. Moving on that display snaps
    to 60 fps with lead=1 so we do not grab 69/s.

    Example: capture_fps_for_idle(0.0, on_display=True) == 60
    """
    if idle_s >= idle_after_s:
        return min(active_fps, idle_fps)
    if on_display:
        return fluid_fps
    return active_fps


def pointer_on_display(xy: tuple[float, float], bounds: tuple[int, int, int, int] | None) -> bool:
    """True when the pointer is on the virtual Hagibis desktop.

    bounds=None means unknown or mirror: stay at rest fps so a 60 Hz grab of
    the Air does not freeze WindowServer.

    Example: pointer_on_display((-320.0, 240.0), (-640, 0, 640, 480)) is True
    """
    if bounds is None:
        return False
    from fl2000_re.cursor_overlay import cursor_local_xy

    return cursor_local_xy(xy[0], xy[1], bounds[0], bounds[1], bounds[2], bounds[3]) is not None


def pointer_capture_period(fps: int) -> float:
    """60 fps fluid uses lead=1; rest/idle keep the 1.15 lead."""
    if fps >= FLUID_CAPTURE_FPS:
        return capture_period_s(fps, lead=1.0)
    return capture_period_s(fps)


def snapped_pointer(xy: tuple[float, float]) -> tuple[int, int]:
    """Integer pointer cell; sub-pixel CG jitter must not reset the idle timer."""
    return (int(round(xy[0])), int(round(xy[1])))


def retune_pointer_period(
    active_fps: int,
    mouse_at: tuple[int, int] | None,
    moved_at: float,
    period: float,
    next_tick: float,
    now: float,
    mouse_xy: tuple[float, float] | None,
    display_bounds: tuple[int, int, int, int] | None = None,
) -> tuple[tuple[int, int] | None, float, float, float]:
    """60 fps while the pointer moves on the Hagibis; 5 fps after 10s still.

    Example: retune_pointer_period(20, (10, 20), 0.0, 0.2, 100.0, 12.0, (40.0, 20.0), (0, 0, 640, 480))
    """
    if mouse_xy is None:
        return mouse_at, moved_at, period, next_tick
    snapped = snapped_pointer(mouse_xy)
    on_display = pointer_on_display(mouse_xy, display_bounds)
    if mouse_at is None or snapped != mouse_at:
        fps = capture_fps_for_idle(0.0, active_fps=active_fps, on_display=on_display)
        return snapped, now, pointer_capture_period(fps), now
    fps = capture_fps_for_idle(now - moved_at, active_fps=active_fps, on_display=on_display)
    return mouse_at, moved_at, pointer_capture_period(fps), next_tick


def capture_backoff_s(
    grab_s: float, heavy_s: float = HEAVY_GRAB_S, max_s: float = MAX_BACKOFF_S
) -> float:
    """Extra sleep after a slow CG grab so a dying WindowServer is not polled at 60 Hz.

    A healthy 640x480 mss grab is ~8-15 ms. At 636-2735 ms the compositor is
    already wedged; grabbing again immediately preceded the 2026-10-20 freezes.

    Example: capture_backoff_s(2.735) == 2.0
    """
    if grab_s < heavy_s:
        return 0.0
    return min(max_s, grab_s)


def capture_period_s(freq: int, lead: float = CAPTURE_LEAD) -> float:
    """Capture cadence: the pump's period divided by lead, so no tick repeats a frame.

    Free-running the child at ~125 fps cost 44% of a core grabbing frames the 60 Hz
    pump discards; paced at 60x1.15 it costs 24% and still feeds every tick
    (measured 2026-10-11). A slow grab is never delayed further: the worker skips
    the sleep when it is already past the slot.

    Example: capture_period_s(60) == pytest.approx(1 / 69)
    """
    if lead < 1:
        raise ValueError(f"lead must be at least 1 (the pump's cadence), got lead={lead!r}")
    return frame_period_s(freq) / lead


def _sleep_until_tick(next_tick: float, period: float, now: float) -> float:
    next_tick += period
    delay = next_tick - now
    if delay > 0:
        time.sleep(delay)
        return next_tick
    if delay < -period:
        return now
    return next_tick
