"""Observe chip scanout and IT66121 input stability outside the USB feeder.

This cannot see the VGA image after an HDMI->VGA adapter. No SET_CONFIGURATION,
bank switching, interrupt clearing or bulk transfer is performed by the observer.
"""

from __future__ import annotations

import contextlib
import json
import multiprocessing as mp
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from multiprocessing.synchronize import Event

import usb.core

from fl2000_re.fl2000_usb import FL2000, status_flags, status_frame_count
from fl2000_re.it66121 import IT66121
from fl2000_re.log_time import install_timestamped_streams
from fl2000_re.registers import FL2000_USB_PID, FL2000_USB_VID, REG_PLL, REG_STATUS

POLL_INTERVAL_S = 1.0
HEARTBEAT_S = 10.0


def read_link_state(fl: FL2000, ite: IT66121) -> dict[str, object]:
    """Read chip status and bridge input status without changing the video mode.

    Example: read_link_state(fl, ite)["video_stable"] is True.
    """
    raw = fl.reg_read(REG_STATUS)
    sampled_at = time.monotonic()
    pll = fl.reg_read(REG_PLL)
    bridge = ite.read_byte(0x0E)
    return {
        "chip_status": f"0x{raw:08X}",
        "frame": status_frame_count(raw),
        "flags": status_flags(raw),
        "pll": f"0x{pll:08X}",
        "ite_status": f"0x{bridge:02X}",
        "video_stable": bool(bridge & 0x10),
        "hpd": bool(bridge & 0x40),
        "rx_sense": bool(bridge & 0x20),
        "sampled_at": sampled_at,
    }


class LinkTrace:
    """Log status changes immediately and a scanout heartbeat every ten seconds.

    Example: trace.record(read_link_state(fl, ite))
    """

    def __init__(self, run_id: str, target_fps: int = 60) -> None:
        self.run_id = run_id
        self.target_fps = target_fps
        self.previous: dict[str, object] | None = None
        self.signature: tuple[object, ...] | None = None
        self.last_log = 0.0

    def record(self, reading: dict[str, object]) -> None:
        now = float(reading["sampled_at"])
        fps = self._scanout_fps(reading)
        running = fps is None or fps >= self.target_fps * 0.5
        signature = (*[reading[k] for k in ("flags", "pll", "ite_status")], running)
        changed = signature != self.signature
        self.previous, self.signature = reading, signature
        if not changed and now - self.last_log < HEARTBEAT_S:
            return
        self.last_log = now
        values = {k: v for k, v in reading.items() if k != "sampled_at"}
        print(
            json.dumps(
                {
                    "event": "hdmi_link_state",
                    "run_id": self.run_id,
                    "changed": changed,
                    "scanout_fps": fps,
                    "scanout_running": running,
                    **values,
                }
            ),
            flush=True,
        )

    def _scanout_fps(self, reading: dict[str, object]) -> float | None:
        if self.previous is None:
            return None
        elapsed = float(reading["sampled_at"]) - float(self.previous["sampled_at"])
        delta = (int(reading["frame"]) - int(self.previous["frame"])) & 0xFFFF
        return round(delta / elapsed, 2) if elapsed > 0 else None


def _watch_link(run_id: str, bus: int, address: int, stop: Event, target_fps: int) -> None:
    install_timestamped_streams()
    with contextlib.suppress(OSError):
        os.nice(10)
    try:
        device = usb.core.find(
            idVendor=FL2000_USB_VID,
            idProduct=FL2000_USB_PID,
            custom_match=lambda candidate: candidate.bus == bus and candidate.address == address,
        )
        if device is None:
            raise OSError(f"USB bus={bus} address={address} ausente; esperado Hagibis da sessao.")
        fl = FL2000(dev=device, configure=False)
        trace, ite = LinkTrace(run_id, target_fps), IT66121(fl)
        while not stop.is_set():
            trace.record(read_link_state(fl, ite))
            stop.wait(POLL_INTERVAL_S)
    except Exception as exc:
        print(
            json.dumps({"event": "hdmi_link_watch_error", "run_id": run_id, "error": str(exc)}),
            flush=True,
        )


@dataclass
class LinkWatch:
    process: mp.Process
    stop: Event

    def close(self) -> None:
        """Stop observer I2C traffic before the feeder releases/reconfigures USB.

        Example: watch.close()
        """
        self.stop.set()
        self.process.join(timeout=0.1)
        if not self.process.is_alive():
            return
        self.process.terminate()
        self.process.join(timeout=0.1)
        if self.process.is_alive():
            self.process.kill()
            self.process.join(timeout=0.1)


def start_link_watch(
    device: object,
    run_id: str,
    target_fps: int,
    process_factory: Callable[..., mp.Process] = mp.Process,
) -> LinkWatch | None:
    """Start only for real USB devices; fake USB unit tests never probe hardware.

    Example: watch = start_link_watch(fl.dev, run_id, 50)
    """
    if not isinstance(device, usb.core.Device):
        return None
    stop = mp.Event()
    process = process_factory(
        target=_watch_link, args=(run_id, device.bus, device.address, stop, target_fps), daemon=True
    )
    watch = LinkWatch(process, stop)
    try:
        process.start()
        return watch
    except (OSError, RuntimeError) as exc:
        print(
            json.dumps({"event": "hdmi_link_watch_error", "run_id": run_id, "error": str(exc)}),
            flush=True,
        )
        return None


def stop_link_watch(watch: LinkWatch | None) -> None:
    """Close an optional observer; fake devices have no observer to stop.

    Example: stop_link_watch(None)
    """
    if watch is not None:
        watch.close()
