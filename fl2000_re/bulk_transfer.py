"""Validate every bulk frame and expose stalls without retrying a stalled endpoint."""

from __future__ import annotations

import json
import time

import usb.core

from fl2000_re.fl2000_usb import FL2000
from fl2000_re.registers import BULK_EP

BULK_TIMEOUT_MS = 2000
EOF_TIMEOUT_MS = 50
SLOW_TRANSFER_MS = 100


def send_frame(fl: FL2000, frame: bytes) -> None:
    """Send all pixels followed by the mandatory EOF packet; never retry partial I/O.

    PyUSB/libusb1 can return a short byte count after a timeout without raising.
    Example: send_frame(fl, packed_frame)
    """
    if getattr(fl, "wire_encoder", None) is not None:
        frame = fl.wire_encoder.encode(frame)
    sent, bulk_ms = _write_phase(fl, frame, BULK_TIMEOUT_MS, "pixels")
    if sent != len(frame):
        _transfer_event("usb_frame_incomplete", "pixels", bulk_ms, sent, len(frame))
        raise usb.core.USBError(
            f"quadro USB incompleto: enviados {sent} bytes; esperados {len(frame)}. "
            "Envio interrompido sem repetir o quadro. Reconecte o USB-A do Hagibis."
        )
    # REG_ACLK bit 28 uses ZLP as EOF, including non-512-aligned modes.
    eof_count, eof_ms = _write_phase(fl, b"", EOF_TIMEOUT_MS, "eof")
    if eof_count != 0:
        raise usb.core.USBError(f"EOF USB retornou {eof_count} bytes; esperado 0 (ZLP).")
    if bulk_ms + eof_ms >= SLOW_TRANSFER_MS:
        _transfer_event(
            "usb_frame_slow",
            "frame",
            bulk_ms + eof_ms,
            sent,
            len(frame),
            bulk_ms=bulk_ms,
            eof_ms=eof_ms,
        )


def _write_phase(fl: FL2000, payload: bytes, timeout: int, phase: str) -> tuple[int, float]:
    start = time.perf_counter()
    try:
        count = fl.dev.write(BULK_EP, payload, timeout=timeout)
    except usb.core.USBError as exc:
        elapsed_ms = (time.perf_counter() - start) * 1000
        _transfer_event(
            "usb_transfer_error",
            phase,
            elapsed_ms,
            None,
            len(payload),
            errno=exc.errno,
            backend_error_code=exc.backend_error_code,
            error=str(exc),
        )
        raise
    return count, (time.perf_counter() - start) * 1000


def _transfer_event(
    event: str, phase: str, elapsed_ms: float, sent: int | None, expected: int, **details: object
) -> None:
    print(
        json.dumps(
            {
                "event": event,
                "phase": phase,
                "duration_ms": round(elapsed_ms, 1),
                "sent_bytes": sent,
                "expected_bytes": expected,
                **details,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
