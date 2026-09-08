"""Mirror must be 16:9 at 60 Hz and fit USB 2.0, or the Dell smears."""

from hagibis_re import (
    MODE_1280x720,
    default_mirror_mode,
    encode_hsync1,
    encode_vsync1,
    fits_usb2,
    resize_rgb888,
)


def test_720p60_rgb565_does_not_fit_usb2():
    assert not fits_usb2(1280, 720, fps=60)


def test_640x480_60_rgb565_fits_usb2():
    assert fits_usb2(640, 480, fps=60)


def test_default_mirror_is_sixteen_by_nine():
    mode = default_mirror_mode()
    assert abs(mode.width / mode.height - 16 / 9) < 0.02


def test_default_mirror_is_60hz():
    assert default_mirror_mode().freq == 60


def test_default_mirror_fits_usb2():
    mode = default_mirror_mode()
    assert fits_usb2(mode.width, mode.height, fps=mode.freq)


def test_encode_hsync1_packs_active_and_total():
    assert encode_hsync1(1280, 1650) == 0x05000672


def test_encode_vsync1_packs_active_and_total():
    assert encode_vsync1(720, 750) == 0x02D002EE


def test_720p_hdmi_tweak_vsync2():
    assert MODE_1280x720.v_sync_2 == 0x01A5001A


def test_resize_rgb888_output_size():
    src = bytes([10, 20, 30]) * (64 * 48)
    out = resize_rgb888(src, 64, 48, 1280, 720)
    assert len(out) == 1280 * 720 * 3


def test_resize_rgb888_keeps_solid_color():
    src = bytes([255, 0, 0]) * (8 * 8)
    out = resize_rgb888(src, 8, 8, 16, 16)
    assert out[0:3] == bytes([255, 0, 0])
    assert out[-3:] == bytes([255, 0, 0])
