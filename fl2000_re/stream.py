"""USB bulk frame pump: color bars and paced desktop clone."""

from __future__ import annotations

import contextlib
import json
import time

import usb.core
import usb.util

from fl2000_re.bulk_transfer import send_frame as send_frame
from fl2000_re.capture import (
    Grabber,
    ScreencaptureError,
    grab_letterboxed_rgb,
    grab_via_screencapture,
)
from fl2000_re.capture_pacing import CAPTURE_LEAD as CAPTURE_LEAD
from fl2000_re.capture_pacing import EXTEND_CAPTURE_FPS as EXTEND_CAPTURE_FPS
from fl2000_re.capture_pacing import FLUID_CAPTURE_FPS as FLUID_CAPTURE_FPS
from fl2000_re.capture_pacing import IDLE_CAPTURE_FPS as IDLE_CAPTURE_FPS
from fl2000_re.capture_pacing import USB_FEED_LEAD, usb_feed_period_s
from fl2000_re.capture_pacing import (
    _sleep_until_tick as _sleep_until_tick,
)
from fl2000_re.capture_pacing import capture_backoff_s as capture_backoff_s
from fl2000_re.capture_pacing import capture_fps_for_idle as capture_fps_for_idle
from fl2000_re.capture_pacing import (
    capture_period_s as capture_period_s,
)
from fl2000_re.capture_pacing import (
    frame_period_s as frame_period_s,
)
from fl2000_re.capture_pacing import (
    pointer_capture_period as pointer_capture_period,
)
from fl2000_re.capture_pacing import (
    pointer_on_display as pointer_on_display,
)
from fl2000_re.capture_pacing import (
    retune_pointer_period as retune_pointer_period,
)
from fl2000_re.capture_pacing import (
    snapped_pointer as snapped_pointer,
)
from fl2000_re.capture_process import MAX_CAPTURE_RESTARTS, CaptureProcess
from fl2000_re.capture_worker import _capture_child as _capture_child
from fl2000_re.capture_worker import _retune_from_live_pointer as _retune_from_live_pointer
from fl2000_re.capture_worker import hdmi_capture_worker as hdmi_capture_worker
from fl2000_re.capture_worker import lower_capture_priority as lower_capture_priority
from fl2000_re.fl2000_usb import FL2000
from fl2000_re.hdmi import bring_up_hdmi
from fl2000_re.letterbox import UNDERSCAN
from fl2000_re.link_watch import start_link_watch, stop_link_watch
from fl2000_re.mac_activity import begin_video_activity
from fl2000_re.pixels import dword_swap_frame, make_bars_rgb565, pack_frame
from fl2000_re.session_log import end_stream_session, record_frame_ready, start_stream_session
from fl2000_re.sleep_clock import read_sleep_clocks as _read_clocks
from fl2000_re.stream_metrics import PumpMetrics
from fl2000_re.stream_metrics import _maybe_print_stats as _maybe_print_stats
from fl2000_re.stream_metrics import format_stream_stats as format_stream_stats
from fl2000_re.thread_priority import prioritize_usb_thread
from fl2000_re.video_modes import MODE_640x480, VideoMode, default_mirror_mode, needs_usb3
from fl2000_re.wire_codec import rle_frame_period_s

TARGET_FPS = 60
USB3_LINK_SPEED = 4
# USB still pumps 60 Hz by resending the last frame. Grabbing ~69 fps via
# CGWindowListCreateImage is what pinned WindowServer on the 8 GB Air
# (2026-10-20: grab 636-2735 ms, then four btn_rst freezes).
WAKE_GAP_S = 3.0
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
    """Elapsed continuous clock minus awake uptime; retained name for compatibility.

    Inputs must be monotonic clock samples, never time.time() (NTP can step it).

    Example: wall_minus_uptime_gap(130, 50.2, 100, 50) == 29.8
    """
    return (wall_now - wall_prev) - (uptime_now - uptime_prev)


def woke_from_sleep(gap_s: float, threshold_s: float = WAKE_GAP_S) -> bool:
    return gap_s >= threshold_s


def release_bulk(fl: FL2000) -> None:
    for interface in (1, 0):
        with contextlib.suppress(usb.core.USBError):
            usb.util.release_interface(fl.dev, interface)


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
    startup_preview: bool = True,
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
        rgb, captured = bytes(mode.width * mode.height * 3), False
        if startup_preview:
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
    run_id = start_stream_session(fl, mode, display_id, capture._capture_fps)
    capture.run_id = run_id
    capture.start()
    link_watch = start_link_watch(fl.dev, run_id, mode.freq)
    qos = "ativa" if prioritize_usb_thread() else "indisponivel"
    print(
        f"  prioridade interativa da thread USB: {qos}; margem de envio {0 if getattr(fl, 'wire_encoder', None) is not None else (USB_FEED_LEAD - 1) * 100:.0f}%."
    )
    cursor_txt = "ligado (mss+blit)" if cursor else "desligado (--no-cursor)"
    print(f"  {label} — cursor {cursor_txt}. Ctrl+C para parar. Olhe o HDMI do HAGIBIS.")
    t0 = time.monotonic()
    sent = 0
    rc = 1
    video_activity = begin_video_activity()
    try:
        sent = _pump_paced_frames(fl, capture, mode, seconds, t0, label, cursor)
        rc = 0 if sent else 1
    except KeyboardInterrupt:
        rc = 0 if capture.sent_frames else 1
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
        if video_activity is not None:
            video_activity.close()
        stop_link_watch(link_watch)
        sent = capture.sent_frames
        capture.stop_and_join()
        dt = max(0.001, time.monotonic() - t0)
        print(
            f"  USB {sent / dt:.1f} fps, captura {capture.counter.value / dt:.1f} fps "
            f"({sent} frames / {dt:.1f}s)"
        )
        end_stream_session(run_id, rc, sent, dt)
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
    period = (
        rle_frame_period_s(mode)
        if getattr(fl, "wire_encoder", None) is not None
        else usb_feed_period_s(mode.freq)
    )
    next_tick = t0
    last_log, last_sent = t0, 0
    stat_cap = capture.counter.value
    progress_cap = stat_cap
    last_progress = t0
    wall0, up0 = _read_clocks()
    pump_metrics = PumpMetrics(mode.freq)
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
        frame = capture.snapshot()
        usb_start = time.monotonic()
        send_frame(fl, frame)
        sent += 1
        capture.sent_frames = sent
        now = time.monotonic()
        record_frame_ready(capture, sent, now - t0)
        pump_metrics.record(
            usb_start,
            now,
            getattr(capture, "snapshot_fresh", True),
            getattr(capture, "frame_at", 0.0),
        )
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
            pump_metrics=pump_metrics,
            capture_metrics=getattr(capture, "metrics", None),
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


def _raise_if_woke(
    wall_prev: float | None, up_prev: float | None
) -> tuple[float | None, float | None]:
    continuous, awake = _read_clocks()
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


def _capture_parked(capture) -> bool:
    idle = getattr(capture, "idle", None)
    return idle is not None and bool(idle.value)


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
