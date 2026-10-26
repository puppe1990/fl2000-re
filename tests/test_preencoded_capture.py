"""Complete compressed publication never waits for capture/encoding on USB."""

import ctypes
import threading
import types
from dataclasses import replace

import pytest
from fl2000_re import capture_worker, wire_codec
from fl2000_re.bulk_transfer import send_frame
from fl2000_re.capture_process import CaptureProcess
from fl2000_re.fl2000_usb import FL2000
from fl2000_re.video_modes import MODE_640x480
from fl2000_re.wire_codec import EncodedWireFrame, Rle24Encoder


def small_capture():
    mode = replace(MODE_640x480, width=8, height=4)
    capture = CaptureProcess(bytes(64), mode, None, 1.0, 1.0, False)
    capture.enable_wire_encoding(Rle24Encoder(32))
    return capture


def test_prepared_frame_skips_encoding_and_keeps_explicit_eof(monkeypatch):
    capture = small_capture()
    packets = []
    fl = FL2000(
        dev=types.SimpleNamespace(
            write=lambda ep, payload, timeout: packets.append(payload) or len(payload)
        ),
        configure=False,
    )
    fl.wire_encoder = Rle24Encoder(32)
    monkeypatch.setattr(wire_codec, "_encode_runs", lambda _: pytest.fail("encoding on USB"))
    for _ in range(4):
        send_frame(fl, capture.snapshot())
    assert [len(packet) for packet in packets] == [12, 0] * 4


def test_variable_length_publication_is_complete_and_busy_writer_keeps_last():
    capture = small_capture()
    seed = capture.snapshot()
    payload = EncodedWireFrame(Rle24Encoder(32).encode(bytes(range(64))))
    assert len(payload) > len(seed)
    capture.frame_lock.acquire()
    try:
        ctypes.memmove(capture.shm, payload, len(payload))
        capture.wire_length.value = len(payload)
        capture.counter.value += 1
        assert capture.snapshot() is seed
    finally:
        capture.frame_lock.release()
    assert capture.snapshot() == payload
    assert isinstance(capture.snapshot(), EncodedWireFrame)
    assert capture.snapshot_fresh is False
    assert capture._worker_args()[-1] is capture.wire_length


def test_blocked_capture_encoder_does_not_hold_publication_lock_or_stop_usb(monkeypatch):
    capture = small_capture()
    entered, release = threading.Event(), threading.Event()
    encoded = Rle24Encoder(32).encode(b"\xff" * 64)

    class BlockedEncoder:
        def __init__(self, pixels):
            assert pixels == 32

        def encode(self, raw):
            entered.set()
            assert release.wait(3)
            capture.stop.set()
            return encoded

    monkeypatch.setattr(capture_worker, "Rle24Encoder", BlockedEncoder)
    monkeypatch.setattr(capture_worker, "grab_main_display_rgb", lambda: (b"\xff" * 96, 8, 4))
    monkeypatch.setattr("fl2000_re.cursor_overlay.mouse_global_xy", lambda: (0.0, 0.0))
    worker = threading.Thread(
        target=capture_worker.hdmi_capture_worker, args=capture._worker_args()
    )
    worker.start()
    try:
        assert entered.wait(3)
        assert capture.frame_lock.acquire(block=False)
        capture.frame_lock.release()
        seed = capture.snapshot()
        packets = []
        fl = FL2000(
            dev=types.SimpleNamespace(
                write=lambda ep, payload, timeout: packets.append(payload) or len(payload)
            ),
            configure=False,
        )
        fl.wire_encoder = Rle24Encoder(32)
        for _ in range(60):
            assert capture.snapshot() is seed
            send_frame(fl, capture.snapshot())
        assert len(packets) == 120
        assert packets[1::2] == [b""] * 60
    finally:
        release.set()
        worker.join(timeout=3)
    assert not worker.is_alive()
    assert capture.snapshot() == encoded


def test_wire_configuration_rejects_started_capture_and_raw_mode_is_unchanged():
    capture = small_capture()
    capture.proc = object()
    with pytest.raises(RuntimeError, match="before start"):
        capture.enable_wire_encoding(Rle24Encoder(32))
    raw = CaptureProcess(bytes(64), replace(MODE_640x480, width=8, height=4), None, 1, 1, False)
    raw.enable_wire_encoding(None)
    assert raw.wire_length is None
    assert type(raw.snapshot()) is bytes


def test_prepared_frame_does_not_leave_stale_raw_cache():
    encoder = Rle24Encoder(32)
    black = encoder.encode(bytes(64))
    white = EncodedWireFrame(Rle24Encoder(32).encode(b"\xff" * 64))
    assert encoder.encode(white) is white
    assert encoder.encode(bytes(64)) == black
