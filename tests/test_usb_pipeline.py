"""Deterministic async libusb simulation: ordering, ownership and no retry on failure."""

import ctypes as ct
import json
import types

import fl2000_re.usb_pipeline as pipeline
import pytest
import usb.backend.libusb1 as native
import usb.core


class FakeLibrary:
    def __init__(self, fail_completion=None, fail_submit=None, fail_allocate=None):
        self.allocated = []
        self.freed = []
        self.inflight = []
        self.submissions = []
        self.cancelled = set()
        self.wait_counts = []
        self.completion_count = 0
        self.submit_count = 0
        self.fail_completion, self.fail_submit, self.fail_allocate = (
            fail_completion,
            fail_submit,
            fail_allocate,
        )
        self.libusb_cancel_transfer = lambda p: self.cancelled.add(ct.addressof(p.contents)) or 0
        self.libusb_handle_events_timeout = lambda *args: self.complete_pair()

    def libusb_alloc_transfer(self, count):
        if self.fail_allocate == len(self.allocated) + 1:
            return None
        p = ct.pointer(native._libusb_transfer())
        self.allocated.append(p)
        return p

    def libusb_submit_transfer(self, pointer):
        self.submit_count += 1
        if self.submit_count == self.fail_submit:
            return -1
        t = pointer.contents
        self.submissions.append(ct.string_at(t.buffer, t.length) if t.length else b"")
        self.inflight.append(pointer)
        return 0

    def libusb_free_transfer(self, pointer):
        assert ct.addressof(pointer.contents) not in [
            ct.addressof(p.contents) for p in self.inflight
        ]
        self.freed.append(pointer)

    def complete_pair(self):
        self.wait_counts.append(len(self.submissions))
        for _ in range(min(2, len(self.inflight))):
            p = self.inflight.pop(0)
            t = p.contents
            t.status = 3 if ct.addressof(t) in self.cancelled else 0
            t.actual_length = t.length
            if self.completion_count == self.fail_completion:
                t.status, t.actual_length = 2, max(0, t.length - 1)
            self.completion_count += 1
            t.callback(p)
        return 0


def test_four_frame_pairs_are_queued_before_waiting_and_pixels_stay_owned():
    lib = FakeLibrary()
    sender = pipeline.PipelineTransport(lib, None, None, depth=4)
    delivered = sender.send(b"first", 10, True)
    assert lib.wait_counts[0] == 8
    assert lib.submissions == [b"first", b""] * 4
    assert delivered.frame_at == 10 and delivered.fresh
    assert len(sender.slots) == 3
    assert all(slot.pixels == b"first" for slot in sender.slots)
    assert len(lib.freed) == 2
    assert sender.close()
    assert len(lib.freed) == len(lib.allocated) == 8


def test_fifo_reports_delivered_generation_not_latest_submitted_frame():
    lib = FakeLibrary()
    sender = pipeline.PipelineTransport(lib, None, None)
    results = [sender.send(bytes([i]), float(i), True) for i in range(1, 6)]
    assert [r.frame_at for r in results] == [1, 1, 1, 1, 2]
    assert [r.fresh for r in results] == [True, False, False, False, True]
    assert len(sender.slots) == 3
    assert sender.close()


@pytest.mark.parametrize("bad_completion,phase", [(0, "pixels"), (1, "eof")])
def test_failed_transfer_is_not_retried_or_followed_by_new_submissions(
    bad_completion, phase, capsys
):
    lib = FakeLibrary(fail_completion=bad_completion)
    sender = pipeline.PipelineTransport(lib, None, None)
    with pytest.raises(usb.core.USBError):
        sender.send(b"frame", 1, True)
    before = len(lib.submissions)
    with pytest.raises(usb.core.USBError):
        sender.send(b"next", 2, True)
    assert len(lib.submissions) == before == 8
    event = json.loads(capsys.readouterr().out.splitlines()[0])
    assert event["phase"] == phase
    assert event["status"] == 2
    assert sender.close()
    assert len(lib.freed) == len(lib.allocated)


def test_submission_failure_cancels_inflight_before_freeing():
    lib = FakeLibrary(fail_submit=2)
    sender = pipeline.PipelineTransport(lib, None, None)
    with pytest.raises(usb.core.USBError):
        sender.send(b"frame", 1, True)
    assert sender.close()
    assert len(lib.cancelled) == 1
    assert len(lib.freed) == 2


def test_allocation_failure_retains_previous_pair_until_cancellation():
    lib = FakeLibrary(fail_allocate=3)
    sender = pipeline.PipelineTransport(lib, None, None)
    with pytest.raises(usb.core.USBError):
        sender.send(b"frame", 1, True)
    assert sender.close()
    assert len(lib.freed) == 2
    assert not lib.inflight


def test_interrupt_does_not_free_active_buffers_before_cancel():
    lib = FakeLibrary()
    sender = pipeline.PipelineTransport(lib, None, None)

    def interrupted(*args):
        raise KeyboardInterrupt

    lib.libusb_handle_events_timeout = interrupted
    with pytest.raises(KeyboardInterrupt):
        sender.send(b"frame", 1, True)
    assert not lib.freed
    lib.libusb_handle_events_timeout = lambda *a: lib.complete_pair()
    assert sender.close()
    assert len(lib.cancelled) == 8
    assert len(lib.freed) == 8
    assert sender.close()


def test_failed_event_context_keeps_native_buffers_alive(capsys):
    lib = FakeLibrary()
    sender = pipeline.PipelineTransport(lib, None, None)
    lib.libusb_handle_events_timeout = lambda *a: -1
    with pytest.raises(usb.core.USBError):
        sender.send(b"frame", 1, True)
    assert not sender.close()
    assert not lib.freed
    assert sender in pipeline._undrained_transports
    assert all(slot.pixels == b"frame" for slot in sender.slots)
    assert "usb_pipeline_close_error" in capsys.readouterr().out


def test_event_interrupt_resumes_wait_without_resubmitting():
    lib = FakeLibrary()
    sender = pipeline.PipelineTransport(lib, None, None, depth=2)
    calls = []

    def interrupted(*args):
        calls.append(1)
        return -10 if len(calls) == 1 else lib.complete_pair()

    lib.libusb_handle_events_timeout = interrupted
    sender.send(b"frame", 1, True)
    assert len(lib.submissions) == 4
    assert sender.close()


def test_local_pipeline_is_explicit_and_bounded(tmp_path):
    path = tmp_path / "depth"
    assert pipeline.pipeline_depth(path) == 0
    for depth in [0, 2, 4]:
        path.write_text(str(depth))
        assert pipeline.pipeline_depth(path) == depth
    path.write_text("100")
    with pytest.raises(ValueError, match="expected 0, 2 or 4"):
        pipeline.pipeline_depth(path)


def test_fake_devices_keep_validated_synchronous_transport():
    calls = []
    fl = types.SimpleNamespace(dev=object())
    transport = pipeline.make_frame_transport(fl, lambda *args: calls.append(args))
    delivered = transport.send(b"frame", 10, True)
    assert calls == [(fl, b"frame")]
    assert delivered.frame_at == 10 and delivered.fresh and delivered.queue_ms == 0
    assert transport.close()


def test_factory_uses_configured_queue_only_for_known_backend(monkeypatch):
    class Backend:
        lib = FakeLibrary()
        ctx = None

    monkeypatch.setattr(pipeline.native, "_LibUSB", Backend)
    monkeypatch.setattr(pipeline, "pipeline_depth", lambda: 4)
    calls = []
    ctx = types.SimpleNamespace(
        backend=Backend(),
        handle=types.SimpleNamespace(handle=None),
        setup_request=lambda *a: calls.append(a),
    )
    fl = types.SimpleNamespace(dev=types.SimpleNamespace(_ctx=ctx))
    transport = pipeline.make_frame_transport(fl, lambda *a: None)
    assert isinstance(transport, pipeline.PipelineTransport)
    assert len(calls) == 1
    assert transport.close()


def test_two_prepared_rle_pairs_are_submitted_before_waiting_without_encoding(monkeypatch):
    from fl2000_re import wire_codec

    encoded = wire_codec.EncodedWireFrame(wire_codec.Rle24Encoder(32).encode(bytes(64)))
    lib = FakeLibrary()
    sender = pipeline.PipelineTransport(lib, None, None, depth=2)
    sender.encoder = wire_codec.Rle24Encoder(32)
    sender.encoder._native = None
    monkeypatch.setattr(wire_codec, "_encode_runs", lambda _: pytest.fail("compression on USB"))
    sender.send(encoded, 1.0, True)
    assert lib.wait_counts[0] == 4
    assert lib.submissions == [encoded, b""] * 2
    assert len(sender.slots) == 1
    assert sender.close()


def test_interface_release_waits_until_queued_buffers_are_drained(monkeypatch):
    calls = []
    monkeypatch.setattr(pipeline.usb.util, "release_interface", lambda dev, i: calls.append(i))
    fl = types.SimpleNamespace(
        dev=object(), frame_transport=types.SimpleNamespace(close=lambda: False)
    )
    pipeline.release_bulk(fl)
    assert calls == []
    fl.frame_transport.close = lambda: True
    pipeline.release_bulk(fl)
    assert calls == [1, 0]
