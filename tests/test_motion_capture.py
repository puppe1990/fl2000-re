"""Motion policy uses image changes, independently of the mouse position."""

import numpy as np
from fl2000_re.motion_capture import MotionCapture


def make_capture():
    return MotionCapture(lambda: (bytes(48 * 48 * 3), 48, 48), 24, 24, 1.0, 1.0)


def test_first_frame_and_static_content_do_not_trigger_motion():
    capture = make_capture()
    capture.observe(bytes(48 * 48 * 3), 48, 48, 1.0)
    capture.observe(bytes(48 * 48 * 3), 48, 48, 2.0)
    assert not capture.moving


def test_video_moves_without_pointer_and_settles_back_to_text():
    capture = make_capture()
    capture.observe(bytes(48 * 48 * 3), 48, 48, 1.0)
    changed = bytes([255]) * (48 * 48 * 3)
    capture.observe(changed, 48, 48, 2.0)
    assert capture.moving
    capture.observe(changed, 48, 48, 2.3)
    assert capture.moving
    capture.observe(changed, 48, 48, 3.1)
    assert not capture.moving


def test_cursor_sized_change_does_not_trigger_motion():
    capture = make_capture()
    capture.observe(bytes(48 * 48 * 3), 48, 48, 1.0)
    image = np.zeros((48, 48, 3), dtype=np.uint8)
    image[:4, :4] = 255
    capture.observe(image.tobytes(), 48, 48, 2.0)
    assert not capture.moving


def test_low_amplitude_noise_does_not_trigger_motion():
    capture = make_capture()
    capture.observe(bytes(48 * 48 * 3), 48, 48, 1.0)
    capture.observe(bytes([3]) * (48 * 48 * 3), 48, 48, 2.0)
    assert not capture.moving


def test_geometry_change_resets_baseline():
    capture = make_capture()
    capture.observe(bytes(48 * 48 * 3), 48, 48, 1.0)
    capture.observe(bytes([255]) * (96 * 48 * 3), 48, 96, 2.0)
    assert not capture.moving


def test_call_uses_fast_filter_only_while_content_moves(monkeypatch):
    values = iter([0, 255, 255])
    capture = MotionCapture(
        lambda: (bytes([next(values)]) * (48 * 48 * 3), 48, 48), 24, 24, 1.0, 1.0
    )
    monkeypatch.setattr(capture.scaler, "resize", lambda *args: None)
    times = iter([1.0, 2.0, 3.0])
    monkeypatch.setattr("fl2000_re.motion_capture.time.monotonic", lambda: next(times))
    seen = []

    def fit(*args, fast_motion=False):
        seen.append(fast_motion)
        return bytes(24 * 24 * 3)

    monkeypatch.setattr("fl2000_re.motion_capture.fit_rgb888", fit)
    assert len(capture()) == 24 * 24 * 3
    capture()
    capture()
    assert seen == [False, True, False]


def test_unchanged_image_reuses_fitted_bytes_without_scaling(monkeypatch):
    capture = make_capture()
    calls = []
    monkeypatch.setattr(capture, "_fit", lambda *args: calls.append(args) or bytes(24 * 24 * 3))
    first = capture()
    assert not capture.cached
    assert capture() is first
    assert capture.cached
    assert len(calls) == 1


def test_motion_settling_rebuilds_cached_frame_with_text_filter(monkeypatch):
    capture = make_capture()
    times = iter([1.0, 2.0])
    monkeypatch.setattr("fl2000_re.motion_capture.time.monotonic", lambda: next(times))
    capture.motion_until = 1.5
    calls = []
    monkeypatch.setattr(capture, "_fit", lambda *args: calls.append(capture.moving) or bytes(3))
    capture()
    capture()
    assert calls == [True, False]
    assert not capture.cached


def test_accelerated_filter_only_runs_for_matching_full_raster(monkeypatch):
    capture = make_capture()
    capture.moving = True
    monkeypatch.setattr(capture.scaler, "resize", lambda *args: b"accelerated")
    assert capture._fit(bytes(48 * 48 * 3), 48, 48) == b"accelerated"
    capture.underscan = 0.9
    assert len(capture._fit(bytes(48 * 48 * 3), 48, 48)) == 24 * 24 * 3
    capture.underscan = 1.0
    assert len(capture._fit(bytes(48 * 24 * 3), 48, 24)) == 24 * 24 * 3


def test_cached_image_rebuilds_after_equal_byte_length_geometry_change(monkeypatch):
    frames = iter([(bytes(48 * 24 * 3), 48, 24), (bytes(48 * 24 * 3), 24, 48)])
    capture = MotionCapture(lambda: next(frames), 24, 24, 1.0, 1.0)
    calls = []
    monkeypatch.setattr(capture, "_fit", lambda *args: calls.append(args[1:]) or b"rgb")
    capture()
    capture()
    assert calls == [(48, 24), (24, 48)]
    assert not capture.cached


def test_short_video_capture_stall_does_not_flip_back_to_text_scaling():
    capture = make_capture()
    black, white = bytes(48 * 48 * 3), b"\xff" * (48 * 48 * 3)
    capture.observe(black, 48, 48, 1.0)
    capture.observe(white, 48, 48, 2.0)
    capture.observe(white, 48, 48, 2.8)
    assert capture.moving
    capture.observe(white, 48, 48, 3.1)
    assert not capture.moving


def test_smooth_low_contrast_video_is_detected_without_pointer_motion():
    capture = make_capture()
    capture.observe(bytes([80]) * (48 * 48 * 3), 48, 48, 1.0)
    capture.observe(bytes([96]) * (48 * 48 * 3), 48, 48, 1.1)
    assert capture.moving
    capture.observe(bytes([96]) * (48 * 48 * 3), 48, 48, 2.2)
    assert not capture.moving
