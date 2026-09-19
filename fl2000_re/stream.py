"""USB bulk frame pump: color bars and paced desktop clone."""

from __future__ import annotations

import contextlib
import multiprocessing as mp
import time

import usb.core
import usb.util

from fl2000_re.capture import (
    Grabber,
    grab_cg_display_rgb,
    grab_letterboxed_rgb,
    grab_main_display_rgb,
    grab_via_screencapture,
)
from fl2000_re.fl2000_usb import FL2000
from fl2000_re.hdmi import bring_up_hdmi
from fl2000_re.letterbox import UNDERSCAN
from fl2000_re.pixels import dword_swap_frame, make_bars_rgb565, needs_zlp, pack_frame
from fl2000_re.registers import BULK_EP
from fl2000_re.video_modes import MODE_640x480, VideoMode, default_mirror_mode

STATS_INTERVAL_S = 1.0
TARGET_FPS = 60


def send_frame(fl: FL2000, frame: bytes) -> None:
    # Full-frame bulk rewrite outlasts one scanout, so a changed frame tears
    # once mid-screen: expected on this single-buffered chip, not a bug.
    fl.dev.write(BULK_EP, frame, timeout=2000)
    if not needs_zlp(len(frame)):
        return
    with contextlib.suppress(usb.core.USBError):
        fl.dev.write(BULK_EP, b"", timeout=50)


def frame_period_s(freq: int) -> float:
    """Bulk cadence for one FL2000 scanout (unpaced rewrites tear mid-scanout).

    Example: frame_period_s(60) == pytest.approx(1 / 60)
    """
    if freq <= 0:
        raise ValueError(f"frame rate must be positive, got freq={freq!r}")
    return 1.0 / freq


def release_bulk(fl: FL2000) -> None:
    with contextlib.suppress(usb.core.USBError):
        usb.util.release_interface(fl.dev, 0)


def cmd_bars(fl: FL2000, seconds: float) -> int:
    mode = MODE_640x480
    print(f"== test pattern {mode.width}x{mode.height} RGB565 ==")
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
) -> None:
    """Fill shm with a packed HDMI frame; never touches USB.

    display_id is a CGDirectDisplayID for extend, or None to clone the Air.
    """
    # Array(..., lock=False) has no get_obj() on some Python builds.
    buf = shm.get_obj() if hasattr(shm, "get_obj") else shm
    grab = (
        grab_main_display_rgb if display_id is None else _grab_display(display_id, include_cursor)
    )
    while not stop.is_set():
        t0 = time.perf_counter()
        rgb = grab_letterboxed_rgb(
            width, height, grab=grab, underscan=underscan, stretch_x=stretch_x
        )
        packed = pack_frame(rgb, bpp)
        if grab_ms is not None:
            grab_ms.value = (time.perf_counter() - t0) * 1000
        if len(packed) != n:
            continue
        buf[:n] = packed
        counter.value += 1


def _grab_display(display_id: int, include_cursor: bool = True) -> Grabber:
    return lambda: grab_cg_display_rgb(display_id, include_cursor=include_cursor)


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
) -> str:
    """One Portuguese status line for the live extend/mirror log.

    Example: format_stream_stats("estendendo", 7.4, 60.0, 134.0, True)
    """
    method = "mss+cursor" if cursor else "mss"
    bottleneck = _stream_bottleneck(capture_fps, usb_fps, target_fps, cursor)
    return (
        f"  {label}  captura {capture_fps:.1f} fps ({method} {grab_ms:.0f}ms)  "
        f"USB {usb_fps:.1f} fps  {bottleneck}"
    )


def _stream_bottleneck(capture_fps: float, usb_fps: float, target_fps: int, cursor: bool) -> str:
    if capture_fps < target_fps * 0.85:
        hint = " — tente --no-cursor" if cursor else ""
        return f"gargalo: captura{hint}"
    if usb_fps < target_fps * 0.85:
        return "gargalo: USB"
    return "gargalo: nenhum"


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
    from PIL import Image

    print(f"  capturando display (monitor={monitor})")
    # screencapture subprocess: mss.grab in this process SIGSEGVs (CGImageGetWidth).
    rgb = grab_letterboxed_rgb(
        mode.width,
        mode.height,
        grab=grab_via_screencapture,
        underscan=underscan,
        stretch_x=stretch_x,
    )
    Image.frombytes("RGB", (mode.width, mode.height), rgb).save("preview.png")
    print("  gravou preview.png (o que vai pro HDMI)")
    if max(rgb) < 12:
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
) -> int:
    """Create a WindowServer display and pump it to the Hagibis (not a clone of the Air)."""
    from PIL import Image

    from fl2000_re.virtual_display import spawn_virtual_display

    mode = default_mirror_mode() if mode is None else mode
    fmt = "RGB332" if mode.bpp == 1 else "RGB565"
    print(f"== tela extra (extend) → HDMI {mode.width}x{mode.height} {fmt} ==")
    handle = spawn_virtual_display(width=mode.width, height=mode.height, place=place)
    try:
        screen = handle.screen
        print(
            f"  display virtual id={screen.display_id} "
            f"{screen.width}x{screen.height} em ({screen.origin_x},{screen.origin_y}). "
            "Arraste janelas para o monitor 'Hagibis'."
        )
        print("  os dois HDMI do Hagibis continuam o mesmo stream.")
        rgb = grab_letterboxed_rgb(
            mode.width,
            mode.height,
            grab=lambda: grab_via_screencapture(screen.display_id),
            underscan=underscan,
            stretch_x=stretch_x,
        )
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
) -> int:
    n = len(frame)
    shm = mp.Array("B", frame, lock=False)
    counter = mp.Value("i", 1)
    grab_ms = mp.Value("d", 0.0)
    stop = mp.Event()
    proc = mp.Process(
        target=hdmi_capture_worker,
        args=(
            mode.width,
            mode.height,
            mode.bpp,
            display_id,
            shm,
            n,
            counter,
            stop,
            underscan,
            stretch_x,
            cursor,
            grab_ms,
        ),
        daemon=True,
    )
    proc.start()
    cursor_txt = "ligado (mss+blit)" if cursor else "desligado (--no-cursor)"
    print(f"  {label} — cursor {cursor_txt}. Ctrl+C para parar. Olhe o HDMI do HAGIBIS.")
    raw_out = shm.get_obj() if hasattr(shm, "get_obj") else shm
    t0 = time.monotonic()
    sent = 0
    try:
        sent = _pump_paced_frames(
            fl, raw_out, mode, seconds, t0, label, counter, grab_ms, cursor, proc
        )
    except KeyboardInterrupt:
        pass
    except usb.core.USBError as exc:
        print(f"  bulk erro: {exc}")
    finally:
        stop.set()
        proc.join(timeout=1)
        dt = max(0.001, time.monotonic() - t0)
        print(
            f"  USB {sent / dt:.1f} fps, captura {counter.value / dt:.1f} fps "
            f"({sent} frames / {dt:.1f}s)"
        )
        release_bulk(fl)
    return 0 if sent else 1


def _pump_paced_frames(
    fl: FL2000,
    raw_out,
    mode,
    seconds: float,
    t0: float,
    label: str,
    counter,
    grab_ms,
    cursor: bool,
    proc=None,
) -> int:
    # Continuous stream: bulk idle starves the line buffer and the sink
    # blanks/resyncs (screen off/on). Never skip ticks, even unchanged.
    sent = 0
    end_at = None if seconds <= 0 else t0 + seconds
    period = frame_period_s(mode.freq)
    next_tick = t0
    last_log, last_sent, last_cap = t0, 0, counter.value
    warned_dead = False
    while end_at is None or time.monotonic() < end_at:
        warned_dead = _warn_if_capture_dead(proc, warned_dead)
        send_frame(fl, bytes(raw_out))
        sent += 1
        now = time.monotonic()
        last_log, last_sent, last_cap = _maybe_print_stats(
            now, last_log, last_sent, last_cap, sent, counter, grab_ms, label, cursor, mode.freq
        )
        next_tick = _sleep_until_tick(next_tick, period, now)
    return sent


def _warn_if_capture_dead(proc, already: bool) -> bool:
    if already or proc is None or proc.is_alive():
        return already
    print(capture_worker_exit_message(proc.exitcode), flush=True)
    return True


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
        ),
        flush=True,
    )
    return now, sent, counter.value


def _sleep_until_tick(next_tick: float, period: float, now: float) -> float:
    next_tick += period
    delay = next_tick - now
    if delay > 0:
        time.sleep(delay)
        return next_tick
    if delay < -period:
        return time.monotonic()
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
