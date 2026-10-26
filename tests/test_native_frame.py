"""Run the real native transaction against fake libusb callbacks, never USB."""

import ctypes as ct
import shutil
import types

import pytest
from fl2000_re import bulk_transfer
from fl2000_re import native_frame as frames
from fl2000_re.fl2000_usb import FL2000

CALLBACK = ct.CFUNCTYPE(
    ct.c_int, ct.c_void_p, ct.c_ubyte, ct.c_void_p, ct.c_int, ct.POINTER(ct.c_int), ct.c_uint
)


@pytest.fixture
def helper():
    if not (shutil.which("clang") or shutil.which("cc")):
        pytest.skip("No native C compiler")
    return frames.load_native_frame_helper()


def sender_with_callback(helper, results=None):
    calls = []
    outcomes = iter(results or [(0, None), (0, None)])

    def transfer(handle, endpoint, pointer, length, written, timeout):
        calls.append((endpoint, ct.string_at(pointer, length) if length else b"", timeout))
        status, count = next(outcomes)
        written[0] = length if count is None else count
        return status

    callback = CALLBACK(transfer)
    return frames.NativeFrameSender(callback, None, helper), calls


def test_native_frame_keeps_nul_pixels_and_sends_explicit_eof_in_order(helper):
    sender, calls = sender_with_callback(helper)
    payload = b"\x00\xff\x01\x00" * 128
    result = sender.send(payload, 2000, 50)
    assert calls == [(1, payload, 2000), (1, b"", 50)]
    assert (result.status, result.phase, result.written) == (0, "eof", 0)
    assert result.pixel_ms >= 0 and result.eof_ms >= 0
    assert payload == b"\x00\xff\x01\x00" * 128


@pytest.mark.parametrize("status,count", [(-7, 4), (0, 4), (-7, 8)])
def test_failed_or_partial_pixels_are_never_followed_by_eof_or_retry(helper, status, count):
    sender, calls = sender_with_callback(helper, [(status, count)])
    result = sender.send(b"abcdefgh", 2000, 50)
    assert result.status < 0 and result.phase == "pixels"
    assert result.written == count
    assert len(calls) == 1
    assert result.eof_ms == 0


def test_eof_timeout_with_zero_bytes_is_still_an_error(helper):
    sender, calls = sender_with_callback(helper, [(0, None), (-7, 0)])
    result = sender.send(b"pixels", 2000, 50)
    assert (result.status, result.phase, result.written) == (-7, "eof", 0)
    assert len(calls) == 2


def test_native_invalid_frame_is_rejected_before_callback(helper):
    sender, calls = sender_with_callback(helper)
    with pytest.raises(ValueError, match="expected 1"):
        sender.send(b"", 2000, 50)
    assert calls == []


def test_native_status_reaches_bulk_failure_without_repeating_frame(helper, capsys):
    sender, calls = sender_with_callback(helper, [(0, None), (-7, 0)])
    fl = FL2000(dev=object(), configure=False)
    fl.native_frame_sender = sender
    with pytest.raises(bulk_transfer.usb.core.USBError, match="No retry"):
        bulk_transfer.send_frame(fl, b"pixels")
    assert len(calls) == 2
    assert '"transport": "native_sync"' in capsys.readouterr().out


def test_native_success_does_not_fall_through_to_pyusb(helper):
    sender, calls = sender_with_callback(helper)
    fl = FL2000(dev=object(), configure=False)
    fl.native_frame_sender = sender
    bulk_transfer.send_frame(fl, b"pixels")
    assert len(calls) == 2


def test_native_profile_is_explicit_and_bounded(tmp_path):
    profile = tmp_path / "usb-native-sync"
    assert not frames.native_sync_enabled(profile)
    for value, expected in (("0", False), ("1", True)):
        profile.write_text(value)
        assert frames.native_sync_enabled(profile) is expected
    profile.write_text("4")
    with pytest.raises(ValueError, match="expected 0 or 1"):
        frames.native_sync_enabled(profile)


def test_fake_usb_never_loads_native_helper(monkeypatch):
    monkeypatch.setattr(frames, "load_native_frame_helper", lambda: pytest.fail("loaded native"))
    fl = FL2000(dev=object(), configure=False)
    frames.prepare_native_frame_sender(fl)
    assert fl.native_frame_sender is None


def test_unavailable_helper_is_nonfatal_and_reported(monkeypatch, capsys):
    def unavailable():
        raise OSError("compiler missing")

    monkeypatch.setattr(frames, "load_native_frame_helper", unavailable)
    assert frames._try_native_frame_helper() is None
    assert "native_sync_unavailable" in capsys.readouterr().out


def test_factory_borrows_existing_backend_handle_only_when_opted_in(monkeypatch):
    class Backend:
        lib = types.SimpleNamespace(libusb_bulk_transfer="existing transfer")

    calls = []
    context = types.SimpleNamespace(
        backend=Backend(),
        handle=types.SimpleNamespace(handle=123),
        setup_request=lambda *args: calls.append(args),
    )
    fl = FL2000(dev=types.SimpleNamespace(_ctx=context), configure=False)
    monkeypatch.setattr(frames.sys, "platform", "darwin")
    monkeypatch.setattr(frames.native, "_LibUSB", Backend)
    monkeypatch.setattr(frames, "native_sync_enabled", lambda: False)
    monkeypatch.setattr(frames, "_try_native_frame_helper", lambda: "helper")
    frames.prepare_native_frame_sender(fl)
    assert fl.native_frame_sender is None and calls == []
    monkeypatch.setattr(frames, "native_sync_enabled", lambda: True)
    frames.prepare_native_frame_sender(fl)
    assert fl.native_frame_sender.handle == 123
    assert fl.native_frame_sender.transfer == "existing transfer"
    assert calls == [(fl.dev, 1)]


def test_slow_native_transfer_keeps_phase_timings_in_logs(capsys):
    result = frames.NativeFrameResult(0, "eof", 0, 110.0, 3.0)
    fl = FL2000(dev=object(), configure=False)
    fl.native_frame_sender = types.SimpleNamespace(send=lambda *args: result)
    bulk_transfer.send_frame(fl, b"pixels")
    output = capsys.readouterr().out
    assert "usb_frame_slow" in output
    assert '"bulk_ms": 110.0' in output and '"eof_ms": 3.0' in output


def test_native_sender_consumes_prepared_rle_with_final_literal_and_eof(helper, monkeypatch):
    from fl2000_re import wire_codec

    wire = wire_codec.EncodedWireFrame(wire_codec.Rle24Encoder(32).encode(bytes(64)))
    sender, calls = sender_with_callback(helper)
    fl = FL2000(dev=object(), configure=False)
    fl.native_frame_sender = sender
    fl.wire_encoder = wire_codec.Rle24Encoder(32)
    fl.wire_encoder._native = None
    monkeypatch.setattr(wire_codec, "_encode_runs", lambda _: pytest.fail("encoding on USB"))
    bulk_transfer.send_frame(fl, wire)
    assert calls == [(1, wire, 2000), (1, b"", 50)]
    assert len(wire) == 12
