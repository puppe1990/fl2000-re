"""USB bulk frame pump: color bars and paced desktop clone."""

from __future__ import annotations

import contextlib
import multiprocessing as mp
import os
import time

import usb.core
import usb.util

from fl2000_re.capture import (
    Grabber,
    ScreencaptureError,
    grab_cg_display_rgb,
    grab_letterboxed_rgb,
    grab_main_display_rgb,
    grab_via_screencapture,
)
from fl2000_re.fl2000_usb import FL2000
from fl2000_re.frame_buffer import new_shared_frame, write_shared_frame
from fl2000_re.hdmi import bring_up_hdmi
from fl2000_re.letterbox import UNDERSCAN
from fl2000_re.pixels import dword_swap_frame, make_bars_rgb565, pack_frame
from fl2000_re.registers import BULK_EP
from fl2000_re.video_modes import MODE_640x480, VideoMode, default_mirror_mode, needs_usb3

STATS_INTERVAL_S = 1.0
TARGET_FPS = 60
USB3_LINK_SPEED = 4
# USB still pumps 60 Hz by resending the last frame. Grabbing ~69 fps via
# CGWindowListCreateImage is what pinned WindowServer on the 8 GB Air
# (2026-10-20: grab 636-2735 ms, then four btn_rst freezes).
EXTEND_CAPTURE_FPS = 20
FLUID_CAPTURE_FPS = 60
IDLE_CAPTURE_FPS = 5
MOUSE_IDLE_S = 10.0
HEAVY_GRAB_S = 0.080
MAX_BACKOFF_S = 2.0
# The capture child leads the pump: a tick that lands on a frame the pump already
# sent is a stutter, and a child paced exactly at the tick period aliases into it.
CAPTURE_LEAD = 1.15
WAKE_GAP_S = 3.0
MAX_CAPTURE_RESTARTS = 5
CAPTURE_STALL_S = 5.0
CAPTURE_START_S = 20.0
PREVIEW_ATTEMPTS = 2
PREVIEW_RETRY_S = 0.3


class SystemWakeError(RuntimeError):
    """macOS slept; FL2000/IT66121 HDMI is gone but this process is still alive."""


class CaptureDeadError(RuntimeError):
    """Capture child died/wedged and the restart budget is spent; exit to relaunch."""


def wall_minus_uptime_gap(
    wall_now: float, uptime_now: float, wall_prev: float, uptime_prev: float
) -> float:
    """Wall clock minus CLOCK_UPTIME_RAW. Sleep pauses uptime; wall keeps going.

    Example: wall_minus_uptime_gap(130, 50.2, 100, 50) == 29.8
    """
    return (wall_now - wall_prev) - (uptime_now - uptime_prev)


def woke_from_sleep(gap_s: float, threshold_s: float = WAKE_GAP_S) -> bool:
    return gap_s >= threshold_s


def _uptime_s() -> float:
    raw = getattr(time, "CLOCK_UPTIME_RAW", None)
    if raw is None:
        return time.monotonic()
    return time.clock_gettime(raw)


def _read_clocks() -> tuple[float, float]:
    return time.time(), _uptime_s()


def send_frame(fl: FL2000, frame: bytes) -> None:
    # Full-frame bulk rewrite outlasts one scanout, so a changed frame tears
    # once mid-screen: expected on this single-buffered chip, not a bug.
    fl.dev.write(BULK_EP, frame, timeout=2000)
    # The chip's bulk EOF is this zero-length packet (REG_ACLK bit 28), not the
    # transfer's short packet. The reference driver submits one after EVERY frame;
    # gating it on 512-byte alignment left non-aligned frames (1440x900) with no
    # end-of-frame at all (2026-10-23).
    with contextlib.suppress(usb.core.USBError):
        fl.dev.write(BULK_EP, b"", timeout=50)


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


def release_bulk(fl: FL2000) -> None:
    with contextlib.suppress(usb.core.USBError):
        usb.util.release_interface(fl.dev, 0)


def link_warning(usb_speed: int | None, mode: VideoMode) -> str | None:
    """Warning when the mode's raw frames need USB 3 but the dongle is on USB 2.

    Example: link_warning(3, MODE_1920x1080) is not None
    """
    if usb_speed is None or usb_speed >= USB3_LINK_SPEED or not needs_usb3(mode):
        return None
    needed_mbps = mode.width * mode.height * mode.bpp * mode.freq / 1_000_000
    return (
        f"aviso: {mode.width}x{mode.height}@{mode.freq} precisa de ~{needed_mbps:.0f} MB/s; "
        "o link atual e USB 2 (42 MB/s). Use uma porta USB 3."
    )


def _print_link_warning(fl: FL2000, mode: VideoMode) -> None:
    warning = link_warning(getattr(fl.dev, "speed", None), mode)
    if warning:
        print(f"  {warning}")


def cmd_bars(fl: FL2000, seconds: float, mode: VideoMode | None = None) -> int:
    mode = MODE_640x480 if mode is None else mode
    print(f"== test pattern {mode.width}x{mode.height} RGB565 ==")
    _print_link_warning(fl, mode)
    frame = dword_swap_frame(make_bars_rgb565(mode.width, mode.height))
    if bring_up_hdmi(fl, mode) != 0:
        return 1
    print(f"  streaming {len(frame)} bytes/frame por {seconds:.0f}s (olhe o HDMI)")
    return _pump_until(fl, frame, seconds)


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
    period = capture_period_s(target_fps) if target_fps else 0.0
    next_tick = time.monotonic()
    mouse_at: tuple[int, int] | None = None
    moved_at = next_tick
    bounds = _virtual_display_bounds(display_id)
    while not stop.is_set():
        if target_fps:
            if display_id is not None and bounds is None:
                bounds = _virtual_display_bounds(display_id)
            mouse_at, moved_at, period, next_tick = _retune_from_live_pointer(
                target_fps, mouse_at, moved_at, period, next_tick, display_bounds=bounds
            )
            if idle_flag is not None:
                idle_flag.value = int(period >= pointer_capture_period(IDLE_CAPTURE_FPS) - 1e-9)
        t0 = time.perf_counter()
        rgb = grab_letterboxed_rgb(
            width, height, grab=grab, underscan=underscan, stretch_x=stretch_x
        )
        packed = pack_frame(rgb, bpp)
        grab_s = time.perf_counter() - t0
        if grab_ms is not None:
            grab_ms.value = grab_s * 1000
        if len(packed) != n:
            continue
        with frame_lock if frame_lock is not None else contextlib.nullcontext():
            write_shared_frame(buf, packed)
            counter.value += 1
        extra = capture_backoff_s(grab_s)
        if extra:
            time.sleep(extra)
            next_tick = time.monotonic()
        elif period:
            next_tick = _sleep_until_tick(next_tick, max(period, grab_s * 1.25), time.monotonic())


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


class CaptureProcess:
    """Owns the capture child and its shared frame so a dead child can be respawned.

    Pumping the last frame during a restart keeps the HDMI lit; a child killed by
    the OS (SIGKILL) or wedged mid-grab must never freeze the desktop forever
    (2026-09-20: signal 9 left a frozen frame and 15k "0.0 fps" log lines).
    """

    def __init__(
        self,
        frame: bytes,
        mode: VideoMode,
        display_id: int | None,
        underscan: float,
        stretch_x: float,
        cursor: bool,
        restarts_left: int = MAX_CAPTURE_RESTARTS,
        capture_fps: int | None = None,
    ) -> None:
        self._mode = mode
        self._display_id = display_id
        self._underscan = underscan
        self._stretch_x = stretch_x
        self._cursor = cursor
        self._restarts_left = restarts_left
        self._capture_fps = mode.freq if capture_fps is None else capture_fps
        self.n = len(frame)
        self.shm = new_shared_frame(frame)
        self.frame_lock = mp.Lock()
        self._last_complete_frame = frame
        self.counter = mp.Value("i", 1)
        self.grab_ms = mp.Value("d", 0.0)
        self.idle = mp.Value("i", 0)
        self.stop = mp.Event()
        self.proc = None
        self._baseline = self.counter.value

    def start(self) -> None:
        self._baseline = self.counter.value
        self.proc = mp.Process(target=_capture_child, args=self._worker_args(), daemon=True)
        self.proc.start()

    def _worker_args(self) -> tuple:
        return (
            self._mode.width,
            self._mode.height,
            self._mode.bpp,
            self._display_id,
            self.shm,
            self.n,
            self.counter,
            self.stop,
            self._underscan,
            self._stretch_x,
            self._cursor,
            self.grab_ms,
            self._capture_fps,
            self.idle,
            self.frame_lock,
        )

    def alive(self) -> bool:
        return self.proc is not None and self.proc.is_alive()

    def has_frame(self) -> bool:
        """True once the current child has produced a frame (import+first grab done)."""
        return self.counter.value != self._baseline

    def exitcode(self) -> int | None:
        return None if self.proc is None else self.proc.exitcode

    def buffer(self):
        return self.shm.get_obj() if hasattr(self.shm, "get_obj") else self.shm

    def snapshot(self) -> bytes:
        """Copy a complete frame without delaying USB if the writer is busy.

        Example: send_frame(fl, capture.snapshot())
        """
        if not self.frame_lock.acquire(block=False):
            return self._last_complete_frame
        try:
            self._last_complete_frame = bytes(self.buffer())
        finally:
            self.frame_lock.release()
        return self._last_complete_frame

    def restart(self) -> bool:
        if self._restarts_left <= 0:
            return False
        self._restarts_left -= 1
        self._kill_current()
        self.start()
        print(f"  reiniciando a captura ({self._restarts_left} tentativas restantes).", flush=True)
        return True

    def _kill_current(self) -> None:
        if self.proc is None:
            return
        with contextlib.suppress(Exception):
            self.proc.terminate()
        with contextlib.suppress(Exception):
            self.proc.join(timeout=1)
        self.proc = None
        # A terminated writer may have owned the old lock; never reuse it.
        self.frame_lock = mp.Lock()

    def stop_and_join(self, timeout: float = 1.0) -> None:
        self.stop.set()
        if self.proc is None:
            return
        with contextlib.suppress(Exception):
            self.proc.join(timeout=timeout)


def capture_worker_exit_message(exitcode: int | None) -> str:
    """Portuguese diagnostic when the capture child dies (SIGSEGV cannot be excepted).

    Example: capture_worker_exit_message(-11)
    """
    if exitcode is None:
        return "  captura morreu (sem exitcode). HDMI ficou no último frame. Religue o extend."
    if exitcode < 0:
        sig = -exitcode
        detail = ""
        if sig == 11:
            detail = (
                " mss.grab chama CGImageGetWidth via ctypes; ponteiro inválido mata o Python "
                "(não dá try/except). "
            )
        return (
            f"  captura morreu (sinal {sig}).{detail}"
            " HDMI ficou no último frame para o monitor não apagar. Religue o extend."
        )
    return f"  captura saiu com código {exitcode}. HDMI ficou no último frame. Religue o extend."


def format_stream_stats(
    label: str,
    capture_fps: float,
    usb_fps: float,
    grab_ms: float,
    cursor: bool,
    target_fps: int = TARGET_FPS,
    capture_target_fps: int | None = None,
    parked: bool = False,
) -> str:
    """One Portuguese status line for the live extend/mirror log.

    Example: format_stream_stats("estendendo", 7.4, 60.0, 134.0, True)
    """
    method = "mss+cursor" if cursor else "mss"
    cap_target = target_fps if capture_target_fps is None else capture_target_fps
    bottleneck = _stream_bottleneck(
        capture_fps, usb_fps, target_fps, cursor, cap_target, parked=parked
    )
    return (
        f"  {label}  captura {capture_fps:.1f} fps ({method} {grab_ms:.0f}ms)  "
        f"USB {usb_fps:.1f} fps  {bottleneck}"
    )


def _stream_bottleneck(
    capture_fps: float,
    usb_fps: float,
    target_fps: int,
    cursor: bool,
    capture_target_fps: int,
    parked: bool = False,
) -> str:
    if parked:
        return "ponteiro parado"
    if capture_fps < capture_target_fps * 0.85:
        hint = " — tente --no-cursor" if cursor else ""
        return f"gargalo: captura{hint}"
    if usb_fps < target_fps * 0.85:
        return "gargalo: USB"
    return "gargalo: nenhum"


def _grab_preview_rgb(
    mode: VideoMode,
    grab: Grabber,
    underscan: float,
    stretch_x: float,
) -> tuple[bytes, bool]:
    """Best-effort preview: (rgb, captured). captured=False means use a black frame.

    A WindowServer-busy screencapture (-D/-R) can outlast its 5s timeout; that
    TimeoutExpired used to propagate and the LaunchAgent restart-looped, spawning
    a fresh virtual display every ~10s (2026-09-20). Retry once, then let extend
    come up on black so the capture worker can fill the real desktop.
    """
    for attempt in range(1, PREVIEW_ATTEMPTS + 1):
        try:
            rgb = grab_letterboxed_rgb(
                mode.width, mode.height, grab=grab, underscan=underscan, stretch_x=stretch_x
            )
            return rgb, True
        except ScreencaptureError as exc:
            if attempt >= PREVIEW_ATTEMPTS:
                print(f"  preview indisponivel ({exc}); subindo o HDMI com fundo preto.")
                break
            print(f"  preview tentativa {attempt}/{PREVIEW_ATTEMPTS} falhou ({exc}); repetindo.")
            time.sleep(PREVIEW_RETRY_S)
    return bytes(mode.width * mode.height * 3), False


def cmd_mirror(
    fl: FL2000,
    seconds: float,
    monitor: int,
    underscan: float = UNDERSCAN,
    stretch_x: float = 1.0,
    mode: VideoMode | None = None,
    v_shift: int = 0,
) -> int:
    mode = default_mirror_mode() if mode is None else mode
    fmt = "RGB332" if mode.bpp == 1 else "RGB565"
    print(f"== espelho da tela → HDMI {mode.width}x{mode.height} {fmt} ==")
    _print_link_warning(fl, mode)
    from PIL import Image

    print(f"  capturando display (monitor={monitor})")
    # screencapture subprocess: mss.grab in this process SIGSEGVs (CGImageGetWidth).
    rgb, captured = _grab_preview_rgb(mode, grab_via_screencapture, underscan, stretch_x)
    if captured:
        Image.frombytes("RGB", (mode.width, mode.height), rgb).save("preview.png")
        print("  gravou preview.png (o que vai pro HDMI)")
    if captured and max(rgb) < 12:
        print(
            "  captura preta. Em Ajustes → Privacidade → Gravacao da Tela, "
            "libere o Terminal (ou o Python) e rode de novo."
        )
        return 1
    frame = pack_frame(rgb, mode.bpp)
    if bring_up_hdmi(fl, mode, v_shift) != 0:
        return 1
    return _pace_clone(
        fl, frame, mode, None, seconds, underscan=underscan, stretch_x=stretch_x, cursor=False
    )


def cmd_extend(
    fl: FL2000,
    seconds: float,
    underscan: float = UNDERSCAN,
    stretch_x: float = 1.0,
    mode: VideoMode | None = None,
    v_shift: int = 0,
    place: str = "right",
    cursor: bool = True,
    capture_fps: int | None = None,
    virtual_size: tuple[int, int] | None = None,
) -> int:
    """Create a WindowServer display and pump it to the Hagibis (not a clone of the Air).

    virtual_size lets the desktop be drawn larger than the HDMI mode (e.g. 1280x800
    downscaled to the stable 1024x768) so the UI comes out smaller.
    """
    from PIL import Image

    from fl2000_re.virtual_display import spawn_virtual_display

    mode = default_mirror_mode() if mode is None else mode
    virtual_w, virtual_h = virtual_size or (mode.width, mode.height)
    fmt = "RGB332" if mode.bpp == 1 else "RGB565"
    print(f"== tela extra (extend) → HDMI {mode.width}x{mode.height} {fmt} ==")
    _print_link_warning(fl, mode)
    if (virtual_w, virtual_h) != (mode.width, mode.height):
        print(f"  desktop virtual {virtual_w}x{virtual_h} reduzido para {mode.width}x{mode.height}")
    handle = spawn_virtual_display(width=virtual_w, height=virtual_h, place=place)
    try:
        screen = handle.screen
        print(
            f"  display virtual id={screen.display_id} "
            f"{screen.width}x{screen.height} em ({screen.origin_x},{screen.origin_y}). "
            "Arraste janelas para o monitor 'Hagibis'."
        )
        print("  os dois HDMI do Hagibis continuam o mesmo stream.")
        rgb, captured = _grab_preview_rgb(
            mode, lambda: grab_via_screencapture(screen.display_id), underscan, stretch_x
        )
        if captured:
            Image.frombytes("RGB", (mode.width, mode.height), rgb).save("preview.png")
            print("  gravou preview.png (o que vai pro HDMI)")
        frame = pack_frame(rgb, mode.bpp)
        if bring_up_hdmi(fl, mode, v_shift) != 0:
            return 1
        return _pace_clone(
            fl,
            frame,
            mode,
            screen.display_id,
            seconds,
            label="estendendo",
            underscan=underscan,
            stretch_x=stretch_x,
            cursor=cursor,
            capture_fps=EXTEND_CAPTURE_FPS if capture_fps is None else capture_fps,
        )
    finally:
        handle.close()


def _pace_clone(
    fl: FL2000,
    frame: bytes,
    mode,
    display_id: int | None,
    seconds: float,
    label: str = "espelhando",
    underscan: float = UNDERSCAN,
    stretch_x: float = 1.0,
    cursor: bool = True,
    restart_budget: int = MAX_CAPTURE_RESTARTS,
    capture_fps: int | None = None,
) -> int:
    capture = CaptureProcess(
        frame,
        mode,
        display_id,
        underscan,
        stretch_x,
        cursor,
        restarts_left=restart_budget,
        capture_fps=capture_fps,
    )
    capture.start()
    cursor_txt = "ligado (mss+blit)" if cursor else "desligado (--no-cursor)"
    print(f"  {label} — cursor {cursor_txt}. Ctrl+C para parar. Olhe o HDMI do HAGIBIS.")
    t0 = time.monotonic()
    sent = 0
    rc = 1
    try:
        sent = _pump_paced_frames(fl, capture, mode, seconds, t0, label, cursor)
        rc = 0 if sent else 1
    except KeyboardInterrupt:
        rc = 0 if sent else 1
    except usb.core.USBError as exc:
        print(f"  bulk erro: {exc}")
        rc = 1
    except SystemWakeError as exc:
        print(f"  {exc}", flush=True)
        rc = 1
    except CaptureDeadError as exc:
        print(f"  {exc}", flush=True)
        rc = 1
    finally:
        capture.stop_and_join()
        dt = max(0.001, time.monotonic() - t0)
        print(
            f"  USB {sent / dt:.1f} fps, captura {capture.counter.value / dt:.1f} fps "
            f"({sent} frames / {dt:.1f}s)"
        )
        release_bulk(fl)
    return rc


def _pump_paced_frames(
    fl: FL2000,
    capture: CaptureProcess,
    mode,
    seconds: float,
    t0: float,
    label: str,
    cursor: bool,
) -> int:
    # Continuous stream: bulk idle starves the line buffer and the sink
    # blanks/resyncs (screen off/on). Never skip ticks, even unchanged.
    sent = 0
    end_at = None if seconds <= 0 else t0 + seconds
    period = frame_period_s(mode.freq)
    next_tick = t0
    last_log, last_sent = t0, 0
    stat_cap = capture.counter.value
    progress_cap = stat_cap
    last_progress = t0
    wall0, up0 = _read_clocks()
    while end_at is None or time.monotonic() < end_at:
        wall0, up0 = _raise_if_woke(wall0, up0)
        now = time.monotonic()
        # A fresh spawn child must import mss/PIL and do its first grab before it
        # can advance the counter; under launchd that is ~6s, past CAPTURE_STALL_S,
        # so a 5s watchdog killed every child before its first frame (2026-09-20).
        stall_s = CAPTURE_STALL_S if capture.has_frame() else CAPTURE_START_S
        alive, progress_cap, last_progress = _ensure_capture(
            capture, progress_cap, last_progress, now, stall_s
        )
        if not alive:
            raise CaptureDeadError(
                "captura nao se recuperou; saindo para o LaunchAgent religar o extend."
            )
        send_frame(fl, capture.snapshot())
        sent += 1
        now = time.monotonic()
        last_log, last_sent, stat_cap = _maybe_print_stats(
            now,
            last_log,
            last_sent,
            stat_cap,
            sent,
            capture.counter,
            capture.grab_ms,
            label,
            cursor,
            mode.freq,
            getattr(capture, "_capture_fps", mode.freq),
            parked=_capture_parked(capture),
        )
        next_tick = _sleep_until_tick(next_tick, period, now)
    return sent


def _ensure_capture(
    capture: CaptureProcess,
    last_cap: int,
    last_progress: float,
    now: float,
    stall_s: float = CAPTURE_STALL_S,
) -> tuple[bool, int, float]:
    """Restart a dead or stalled capture child; False once the budget is spent."""
    if capture.alive() and capture.counter.value != last_cap:
        return True, capture.counter.value, now
    if capture.alive() and now - last_progress < stall_s:
        return True, last_cap, last_progress
    if capture.alive():
        print(f"  captura travada (sem frames há {now - last_progress:.0f}s).", flush=True)
    else:
        print(capture_worker_exit_message(capture.exitcode()), flush=True)
    if not capture.restart():
        print("  sem captura e sem tentativas de reinicio. Saindo para religar.", flush=True)
        return False, last_cap, last_progress
    return True, capture.counter.value, now


def _raise_if_woke(wall_prev: float, up_prev: float) -> tuple[float, float]:
    wall, up = _read_clocks()
    if woke_from_sleep(wall_minus_uptime_gap(wall, up, wall_prev, up_prev)):
        raise SystemWakeError(
            "macOS saiu do descanso. O FL2000 perde o HDMI no sleep; "
            "saindo para o LaunchAgent religar (ou rode ./bin/extend)."
        )
    return wall, up


def _maybe_print_stats(
    now: float,
    last_log: float,
    last_sent: int,
    last_cap: int,
    sent: int,
    counter,
    grab_ms,
    label: str,
    cursor: bool,
    target_fps: int,
    capture_target_fps: int | None = None,
    parked: bool = False,
) -> tuple[float, int, int]:
    if now - last_log < STATS_INTERVAL_S:
        return last_log, last_sent, last_cap
    dt = now - last_log
    print(
        format_stream_stats(
            label,
            (counter.value - last_cap) / dt,
            (sent - last_sent) / dt,
            grab_ms.value,
            cursor,
            target_fps,
            capture_target_fps,
            parked,
        ),
        flush=True,
    )
    return now, sent, counter.value


def _capture_parked(capture) -> bool:
    idle = getattr(capture, "idle", None)
    return idle is not None and bool(idle.value)


def _sleep_until_tick(next_tick: float, period: float, now: float) -> float:
    next_tick += period
    delay = next_tick - now
    if delay > 0:
        time.sleep(delay)
        return next_tick
    if delay < -period:
        return now
    return next_tick


def _pump_until(fl: FL2000, frame: bytes, seconds: float) -> int:
    sent = 0
    t0 = time.monotonic()
    deadline = t0 + seconds
    try:
        while time.monotonic() < deadline:
            send_frame(fl, frame)
            sent += 1
    except KeyboardInterrupt:
        pass
    except usb.core.USBError as exc:
        print(f"  bulk erro: {exc}")
    finally:
        dt = max(0.001, time.monotonic() - t0)
        print(f"  {sent} frames em {dt:.1f}s → {sent / dt:.1f} fps")
        release_bulk(fl)
    return 0 if sent else 1
