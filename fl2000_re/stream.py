"""USB bulk frame pump: color bars and paced desktop clone."""

from __future__ import annotations

import contextlib
import multiprocessing as mp
import time

import usb.core
import usb.util

from fl2000_re.capture import grab_letterboxed_rgb
from fl2000_re.fl2000_usb import FL2000
from fl2000_re.hdmi import bring_up_hdmi
from fl2000_re.pixels import dword_swap_frame, make_bars_rgb565, needs_zlp, pack_frame
from fl2000_re.registers import BULK_EP
from fl2000_re.video_modes import MODE_640x480, default_mirror_mode


def send_frame(fl: FL2000, frame: bytes) -> None:
    fl.dev.write(BULK_EP, frame, timeout=2000)
    if not needs_zlp(len(frame)):
        return
    with contextlib.suppress(usb.core.USBError):
        fl.dev.write(BULK_EP, b"", timeout=50)


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
    width: int, height: int, bpp: int, _monitor: int, shm, n: int, counter, stop
) -> None:
    """Fill shm with a packed HDMI frame; never touches USB."""
    # Array(..., lock=False) has no get_obj() on some Python builds.
    buf = shm.get_obj() if hasattr(shm, "get_obj") else shm
    while not stop.is_set():
        packed = pack_frame(grab_letterboxed_rgb(width, height), bpp)
        if len(packed) != n:
            continue
        buf[:n] = packed
        counter.value += 1


def cmd_mirror(fl: FL2000, seconds: float, monitor: int) -> int:
    mode = default_mirror_mode()
    fmt = "RGB332" if mode.bpp == 1 else "RGB565"
    print(f"== espelho da tela → HDMI {mode.width}x{mode.height} {fmt} ==")
    from PIL import Image

    print(f"  capturando display (monitor={monitor})")
    rgb = grab_letterboxed_rgb(mode.width, mode.height)
    Image.frombytes("RGB", (mode.width, mode.height), rgb).save("preview.png")
    print("  gravou preview.png (o que vai pro HDMI)")
    if max(rgb) < 12:
        print(
            "  captura preta. Em Ajustes → Privacidade → Gravacao da Tela, "
            "libere o Terminal (ou o Python) e rode de novo."
        )
        return 1
    frame = pack_frame(rgb, mode.bpp)
    if bring_up_hdmi(fl, mode) != 0:
        return 1
    return _pace_clone(fl, frame, mode, monitor, seconds)


def _pace_clone(fl: FL2000, frame: bytes, mode, monitor: int, seconds: float) -> int:
    n = len(frame)
    shm = mp.Array("B", frame, lock=False)
    counter = mp.Value("i", 1)
    stop = mp.Event()
    proc = mp.Process(
        target=hdmi_capture_worker,
        args=(mode.width, mode.height, mode.bpp, monitor, shm, n, counter, stop),
        daemon=True,
    )
    proc.start()
    print("  espelhando — Ctrl+C para parar. Olhe o HDMI do HAGIBIS.")
    sent = 0
    t0 = time.monotonic()
    end_at = None if seconds <= 0 else t0 + seconds
    period = 1.0 / mode.freq
    next_tick = t0
    raw_out = shm.get_obj() if hasattr(shm, "get_obj") else shm
    try:
        while end_at is None or time.monotonic() < end_at:
            send_frame(fl, bytes(raw_out))
            sent += 1
            next_tick += period
            delay = next_tick - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            elif delay < -period:
                next_tick = time.monotonic()
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
