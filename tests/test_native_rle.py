"""Compiled codec matches the independent vectorized protocol implementation."""

import ctypes
import shutil
import subprocess

import numpy as np
import pytest
from fl2000_re import native_rle, wire_codec
from fl2000_re.native_rle import NativeRle24
from fl2000_re.pixels import dword_swap_frame


@pytest.mark.skipif(shutil.which("cc") is None, reason="native compiler unavailable")
@pytest.mark.parametrize("count", [1, 2, 3, 4, 5, 7, 8, 15, 1024, 65536, 65537])
def test_native_matches_reference_for_runs_colors_and_lane_tails(count):
    rng = np.random.default_rng(42)
    samples = [
        np.zeros(count, dtype="<u2"),
        np.full(count, 0xF800, dtype="<u2"),
        np.arange(count).astype("<u2"),
        rng.choice([0, 0xFFFF, 0xF800, 0x07E0, 0x001F], count).astype("<u2"),
    ]
    encoder = NativeRle24(count)
    for pixels in samples:
        raw = dword_swap_frame(pixels.tobytes())
        assert encoder.encode(raw) == wire_codec._encode_runs(pixels)


def test_native_rejects_invalid_lengths_and_protects_output_capacity():
    if shutil.which("cc") is None:
        pytest.skip("native compiler unavailable")
    encoder = NativeRle24(4)
    with pytest.raises(ValueError, match="expected 8"):
        encoder.encode(bytes(6))
    output = ctypes.create_string_buffer(b"sentinel")
    before = output.raw
    assert encoder.function(bytes(8), 4, output, 1) == 0
    assert output.raw == before
    for invalid in (0, -1, 0x800001):
        with pytest.raises(ValueError, match="expected 1"):
            NativeRle24(invalid)


@pytest.mark.parametrize("failure", [OSError("no compiler"), subprocess.TimeoutExpired("cc", 20)])
def test_missing_native_toolchain_keeps_reference_codec(monkeypatch, failure):
    def unavailable(_count):
        raise failure

    monkeypatch.setattr(native_rle, "NativeRle24", unavailable)
    assert native_rle.optional_native_encoder(4) is None


def test_native_rejects_invalid_returned_length(monkeypatch):
    monkeypatch.setattr(native_rle, "_native_function", lambda: lambda *args: 0)
    encoder = NativeRle24(4)
    with pytest.raises(RuntimeError, match="output length=0"):
        encoder.encode(bytes(8))


def test_prepared_payload_never_calls_native_encoder(monkeypatch):
    encoder = wire_codec.Rle24Encoder(4)

    class FailNative:
        def encode(self, raw):
            pytest.fail("native compression on USB feeder")

    encoder._native = FailNative()
    payload = wire_codec.EncodedWireFrame(b"\x00\x00\x80\x03\x00\x00\x80\x80")
    assert encoder.encode(payload) is payload
