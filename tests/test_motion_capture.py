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
    capture.observe(changed, 48, 48, 2.6)
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
