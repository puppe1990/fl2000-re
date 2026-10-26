"""Optional synchronous pixels/EOF transaction without Python or copies between phases."""

from __future__ import annotations

import ctypes as ct
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import usb.backend.libusb1 as native

from fl2000_re.fl2000_usb import FL2000
from fl2000_re.native_bridge import build_native_bridge
from fl2000_re.registers import BULK_EP


@dataclass(frozen=True)
class NativeFrameResult:
    status: int
    phase: str
    written: int
    pixel_ms: float
    eof_ms: float


class NativeFrameSender:
    """Borrow the existing libusb handle for one synchronous frame at a time.

    Example: sender.send(pixels, 2000, 50) returns validated phase/status metrics.
    """

    def __init__(
        self, transfer: ct._CFuncPtr, handle: int | ct.c_void_p | None, helper: ct._CFuncPtr
    ) -> None:
        self.transfer, self.handle, self.helper = transfer, handle, helper

    def send(self, frame: bytes, pixel_timeout: int, eof_timeout: int) -> NativeFrameResult:
        if not 0 < len(frame) <= 0x7FFFFFFF:
            raise ValueError(f"frame bytes={len(frame)}; expected 1..2147483647")
        phase, written = ct.c_int(), ct.c_int()
        pixel_ms, eof_ms = ct.c_double(), ct.c_double()
        status = self.helper(
            ct.cast(self.transfer, ct.c_void_p),
            self.handle,
            frame,
            len(frame),
            pixel_timeout,
            eof_timeout,
            ct.byref(phase),
            ct.byref(written),
            ct.byref(pixel_ms),
            ct.byref(eof_ms),
        )
        return NativeFrameResult(
            status,
            "pixels" if phase.value == 0 else "eof",
            written.value,
            pixel_ms.value,
            eof_ms.value,
        )


def load_native_frame_helper() -> ct._CFuncPtr:
    """Load the same tested C entry point for real or fake libusb callbacks.

    Example: helper = load_native_frame_helper()
    """
    source = Path(__file__).resolve().parent.parent / "native" / "sync_frame.c"
    cache = Path.home() / "Library" / "Caches" / "fl2000-re"
    library = ct.CDLL(str(build_native_bridge(source, cache)))
    function = library.fl2000_send_sync_frame
    function.argtypes = [
        ct.c_void_p,
        ct.c_void_p,
        ct.c_char_p,
        ct.c_int,
        ct.c_uint,
        ct.c_uint,
        ct.POINTER(ct.c_int),
        ct.POINTER(ct.c_int),
        ct.POINTER(ct.c_double),
        ct.POINTER(ct.c_double),
    ]
    function.restype = ct.c_int
    return function


def native_sync_enabled(profile: Path | None = None) -> bool:
    """Resolve an explicit local 0/1 opt-in without changing ordinary USB writes.

    Example: native_sync_enabled(Path("usb-native-sync"))
    """
    path = profile or Path.home() / "Library/Application Support/fl2000-re/usb-native-sync"
    if not path.exists():
        return False
    value = path.read_text().strip()
    if value not in ("0", "1"):
        raise ValueError(f"native USB sync={value!r}; expected 0 or 1")
    return value == "1"


def prepare_native_frame_sender(fl: FL2000) -> None:
    """Prepare the opt-in path outside the USB loop; fake devices never touch hardware.

    Example: prepare_native_frame_sender(fl) after selecting the bulk alternate.
    """
    fl.native_frame_sender = None
    context = getattr(fl.dev, "_ctx", None)
    if (
        sys.platform != "darwin"
        or context is None
        or not isinstance(context.backend, native._LibUSB)
    ):
        return
    if not native_sync_enabled():
        return
    helper = _try_native_frame_helper()
    if helper is None:
        return
    context.setup_request(fl.dev, BULK_EP)
    fl.native_frame_sender = NativeFrameSender(
        context.backend.lib.libusb_bulk_transfer, context.handle.handle, helper
    )
    print(json.dumps({"event": "usb_transport_selected", "kind": "native_sync"}), flush=True)


def _try_native_frame_helper() -> ct._CFuncPtr | None:
    try:
        return load_native_frame_helper()
    except (OSError, AttributeError, subprocess.SubprocessError) as exc:
        print(json.dumps({"event": "native_sync_unavailable", "error": str(exc)}), flush=True)
        return None
