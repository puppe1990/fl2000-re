"""Source aspect must be preserved; stretching the Air 3:2 into 16:9 misframes it."""

from fl2000_re.letterbox import fit_rgb888, resize_rgb888
from fl2000_re.video_modes import default_mirror_mode, fits_usb2


def test_default_mirror_is_stable_640x480_60():
    mode = default_mirror_mode()
    assert (mode.width, mode.height, mode.freq, mode.bpp) == (640, 480, 60, 2)
    assert fits_usb2(mode.width, mode.height, mode.freq, bpp=mode.bpp)


def test_fit_letterboxes_wide_source():
    src = bytes([255, 0, 0]) * (20 * 10)
    out = fit_rgb888(src, 20, 10, 20, 20)
    assert len(out) == 20 * 20 * 3
    assert out[0:3] == bytes([0, 0, 0])
    mid = (10 * 20 + 10) * 3
    assert out[mid : mid + 3] == bytes([255, 0, 0])


def test_fit_pillarboxes_tall_source():
    src = bytes([0, 255, 0]) * (10 * 20)
    out = fit_rgb888(src, 10, 20, 20, 20)
    assert out[0:3] == bytes([0, 0, 0])
    mid = (10 * 20 + 10) * 3
    assert out[mid : mid + 3] == bytes([0, 255, 0])


def test_resize_rgb888_output_size():
    src = bytes([10, 20, 30]) * (64 * 48)
    out = resize_rgb888(src, 64, 48, 1280, 720)
    assert len(out) == 1280 * 720 * 3


def test_resize_rgb888_keeps_solid_color():
    src = bytes([255, 0, 0]) * (8 * 8)
    out = resize_rgb888(src, 8, 8, 16, 16)
    assert out[0:3] == bytes([255, 0, 0])
    assert out[-3:] == bytes([255, 0, 0])
