"""vImage ABI, fallback and channel order; no display capture or USB."""

import ctypes as ct
import sys
import types

import fl2000_re.motion_scaler as scaler_module
import pytest
from fl2000_re.motion_scaler import MotionScaler, VImageBuffer


def copy_buffer(src, dst, *args):
    source = ct.cast(src, ct.POINTER(VImageBuffer)).contents
    target = ct.cast(dst, ct.POINTER(VImageBuffer)).contents
    ct.memmove(target.pixels, source.pixels, target.height * target.row_bytes)
    return 0


def test_mocked_native_pipeline_preserves_rgb_channels(monkeypatch):
    api = types.SimpleNamespace(
        vImageScale_ARGB8888=copy_buffer, vImageConvolve_ARGB8888=copy_buffer
    )
    monkeypatch.setattr(scaler_module, "_vimage_api", lambda: api)
    scaler = MotionScaler()
    rgb = bytes([17, 81, 205]) * 16
    assert scaler.resize(rgb, 4, 4, 4, 4) == rgb
    assert scaler.backend == "vimage"


@pytest.mark.parametrize("failing_stage", ["vImageScale_ARGB8888", "vImageConvolve_ARGB8888"])
def test_native_error_disables_acceleration_and_falls_back(monkeypatch, failing_stage):
    api = types.SimpleNamespace(
        vImageScale_ARGB8888=copy_buffer, vImageConvolve_ARGB8888=copy_buffer
    )
    setattr(api, failing_stage, lambda *args: -21772)
    monkeypatch.setattr(scaler_module, "_vimage_api", lambda: api)
    scaler = MotionScaler()
    assert scaler.resize(bytes(48), 4, 4, 4, 4) is None
    assert scaler.disabled
    assert scaler.backend == "pillow"
    monkeypatch.setattr(scaler_module, "_vimage_api", lambda: pytest.fail("native retry"))
    assert scaler.resize(bytes(48), 4, 4, 4, 4) is None


def test_missing_library_returns_pillow_fallback(monkeypatch):
    monkeypatch.setattr(scaler_module, "_vimage_api", lambda: None)
    assert MotionScaler().resize(bytes(48), 4, 4, 4, 4) is None


@pytest.mark.parametrize("shape", [(4, 4, 4, 4), (0, 4, 4, 4), (4, 4, -1, 4)])
def test_invalid_rgb_shape_never_reaches_native_code(monkeypatch, shape):
    monkeypatch.setattr(scaler_module, "_vimage_api", lambda: pytest.fail("invalid native access"))
    with pytest.raises(ValueError, match="expected"):
        MotionScaler().resize(b"invalid", *shape)


def test_loader_skips_unsupported_platform(monkeypatch):
    scaler_module._vimage_api.cache_clear()
    with monkeypatch.context() as patch:
        patch.setattr(scaler_module.sys, "platform", "linux")
        assert scaler_module._vimage_api() is None
    scaler_module._vimage_api.cache_clear()


def test_loader_handles_missing_framework(monkeypatch):
    scaler_module._vimage_api.cache_clear()
    with monkeypatch.context() as patch:
        patch.setattr(scaler_module.sys, "platform", "darwin")

        def missing(*args):
            raise OSError("missing framework")

        patch.setattr(scaler_module.ct, "CDLL", missing)
        assert scaler_module._vimage_api() is None
    scaler_module._vimage_api.cache_clear()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS Accelerate integration")
def test_real_vimage_scale_preserves_solid_rgb_channel_order():
    result = MotionScaler().resize(bytes([17, 81, 205]) * (64 * 48), 64, 48, 32, 24)
    assert result is not None
    assert result == bytes([17, 81, 205]) * (32 * 24)
