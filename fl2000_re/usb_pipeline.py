"""Bounded FIFO of pixel/EOF pairs, following Fresco's four render contexts.

Each transfer owns immutable pixels until completion or cancellation. No transfer
is retried. PyUSB's existing libusb1 ABI is reused rather than duplicating C structs.
"""

from __future__ import annotations

import ctypes as ct
import json
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import usb.backend.libusb1 as native
import usb.core

from fl2000_re.fl2000_usb import FL2000
from fl2000_re.registers import BULK_EP

TransferPointer = ct.POINTER(native._libusb_transfer)


class _Timeval(ct.Structure):
    _fields_ = [("seconds", ct.c_long), ("microseconds", ct.c_long)]


@dataclass(frozen=True)
class DeliveredFrame:
    frame_at: float
    fresh: bool
    queue_ms: float = 0.0


class FrameTransport(Protocol):
    def send(self, frame: bytes, frame_at: float, fresh: bool) -> DeliveredFrame: ...
    def close(self) -> bool: ...


@dataclass
class _FrameSlot:
    pixels: bytes
    frame_at: float
    fresh: bool
    queued_at: float = field(default_factory=time.monotonic)
    transfers: list[TransferPointer] = field(default_factory=list)


class SyncFrameTransport:
    def __init__(self, fl: FL2000, sender: Callable[[FL2000, bytes], None]) -> None:
        self.fl, self.sender = fl, sender

    def send(self, frame: bytes, frame_at: float, fresh: bool) -> DeliveredFrame:
        self.sender(self.fl, frame)
        return DeliveredFrame(frame_at, fresh)

    def close(self) -> bool:
        return True


class PipelineTransport:
    """Keep two or four complete frame pairs queued while Python prepares the next.

    Example: transport.send(frame, captured_at, fresh=True)
    """

    def __init__(self, library, context, handle, depth: int = 4, timeout_ms: int = 2000) -> None:
        if depth not in (2, 4):
            raise ValueError(f"USB queue depth={depth!r}; expected 2 or 4")
        self.library, self.context, self.handle = library, context, handle
        self.depth, self.timeout_ms = depth, timeout_ms
        self.slots: deque[_FrameSlot] = deque()
        self.pending: set[int] = set()
        self.results: dict[int, tuple[int, int]] = {}
        self.callback = native._libusb_transfer_cb_fn_p(self._completed)
        self.closed = False
        library.libusb_cancel_transfer.argtypes = [TransferPointer]
        library.libusb_cancel_transfer.restype = ct.c_int
        library.libusb_handle_events_timeout.argtypes = [ct.c_void_p, ct.POINTER(_Timeval)]
        library.libusb_handle_events_timeout.restype = ct.c_int

    def send(self, frame: bytes, frame_at: float, fresh: bool) -> DeliveredFrame:
        if self.closed:
            raise RuntimeError("USB queue is closed; expected an open transport")
        self._validate_completed()
        first = not self.slots
        self._enqueue(frame, frame_at, fresh)
        if first:
            for _ in range(self.depth - 1):
                self._enqueue(frame, frame_at, False)
        oldest = self.slots[0]
        self._wait_for(oldest)
        self._validate_completed()
        delivered = DeliveredFrame(
            oldest.frame_at, oldest.fresh, (time.monotonic() - oldest.queued_at) * 1000
        )
        self.slots.popleft()
        self._free_slot(oldest)
        return delivered

    def _enqueue(self, frame: bytes, frame_at: float, fresh: bool) -> None:
        slot = _FrameSlot(frame, frame_at, fresh)
        self.slots.append(slot)
        for payload in (frame, b""):
            pointer = self.library.libusb_alloc_transfer(0)
            if not pointer:
                raise usb.core.USBError("libusb allocation failed; expected pixel/EOF pair")
            slot.transfers.append(pointer)
            transfer = pointer.contents
            transfer.dev_handle, transfer.endpoint, transfer.type = self.handle, BULK_EP, 2
            transfer.timeout, transfer.length = self.timeout_ms, len(payload)
            transfer.buffer = ct.cast(ct.c_char_p(payload), ct.c_void_p) if payload else None
            transfer.callback = self.callback
            code = self.library.libusb_submit_transfer(pointer)
            if code < 0:
                raise usb.core.USBError(f"libusb submit={code}; expected 0, no retry")
            self.pending.add(ct.addressof(transfer))

    def _completed(self, pointer) -> None:
        address = ct.addressof(pointer.contents)
        self.pending.discard(address)
        self.results[address] = (int(pointer.contents.status), int(pointer.contents.actual_length))

    def _wait_for(self, slot: _FrameSlot) -> None:
        deadline = time.monotonic() + self.timeout_ms / 1000 + 1
        while any(ct.addressof(p.contents) in self.pending for p in slot.transfers):
            self._poll(deadline)
            self._validate_completed()

    def _poll(self, deadline: float) -> None:
        if time.monotonic() >= deadline:
            raise usb.core.USBTimeoutError("libusb callback missing past deadline; no retry")
        interval = _Timeval(0, 10_000)
        code = self.library.libusb_handle_events_timeout(self.context, ct.byref(interval))
        if code < 0 and code != -10:
            raise usb.core.USBError(f"libusb event result={code}; expected 0")

    def _validate_completed(self) -> None:
        for slot in self.slots:
            for index, pointer in enumerate(slot.transfers):
                result = self.results.get(ct.addressof(pointer.contents))
                expected = len(slot.pixels) if index == 0 else 0
                if result is not None and result != (0, expected):
                    self._report_fault(slot, index, result, expected)

    def _report_fault(
        self, slot: _FrameSlot, index: int, result: tuple[int, int], expected: int
    ) -> None:
        phase = "pixels" if index == 0 else "eof"
        print(
            json.dumps(
                {
                    "event": "usb_transfer_error",
                    "transport": "pipeline",
                    "phase": phase,
                    "status": result[0],
                    "sent_bytes": result[1],
                    "expected_bytes": expected,
                    "duration_ms": round((time.monotonic() - slot.queued_at) * 1000, 1),
                    "queue_depth": self.depth,
                }
            ),
            flush=True,
        )
        raise usb.core.USBError(
            f"USB {phase}: status={result[0]}, bytes={result[1]}; "
            f"expected status=0, bytes={expected}. No retry."
        )

    def _free_slot(self, slot: _FrameSlot) -> None:
        for pointer in slot.transfers:
            self.results.pop(ct.addressof(pointer.contents), None)
            self.library.libusb_free_transfer(pointer)
        slot.transfers.clear()

    def close(self) -> bool:
        if self.closed:
            return not self.pending
        self.closed = True
        try:
            self._cancel_pending()
        except (usb.core.USBError, KeyboardInterrupt) as exc:
            # Native callbacks still own these pointers; retain buffers rather than free live DMA.
            _undrained_transports.append(self)
            print(json.dumps({"event": "usb_pipeline_close_error", "error": str(exc)}), flush=True)
            return False
        while self.slots:
            self._free_slot(self.slots.popleft())
        return True

    def _cancel_pending(self) -> None:
        for slot in self.slots:
            for pointer in slot.transfers:
                if ct.addressof(pointer.contents) in self.pending:
                    self.library.libusb_cancel_transfer(pointer)
        deadline = time.monotonic() + self.timeout_ms / 1000 + 1
        while self.pending:
            self._poll(deadline)


_undrained_transports: list[PipelineTransport] = []


def pipeline_depth(profile_path: Path | None = None) -> int:
    """Default to synchronous I/O; enable a local bounded pipeline explicitly.

    Example: pipeline_depth(Path("usb-queue-depth")) resolves 0, 2 or 4.
    """
    path = (
        profile_path
        or Path.home() / "Library" / "Application Support" / "fl2000-re" / "usb-queue-depth"
    )
    if not path.exists():
        return 0
    value = path.read_text().strip()
    if value not in ("0", "2", "4"):
        raise ValueError(f"USB queue depth={value!r}; expected 0, 2 or 4")
    return int(value)


def make_frame_transport(fl: FL2000, sender: Callable[[FL2000, bytes], None]) -> FrameTransport:
    """Choose a queued libusb1 path only for real supported devices and explicit local config.

    Example: transport = make_frame_transport(fl, send_frame)
    """
    context = getattr(fl.dev, "_ctx", None)
    if context is None or not isinstance(context.backend, native._LibUSB):
        return SyncFrameTransport(fl, sender)
    depth = pipeline_depth()
    if depth == 0:
        return SyncFrameTransport(fl, sender)
    context.setup_request(fl.dev, BULK_EP)
    print(
        json.dumps({"event": "usb_transport_selected", "kind": "pipeline", "queue_depth": depth}),
        flush=True,
    )
    return PipelineTransport(context.backend.lib, context.backend.ctx, context.handle.handle, depth)
