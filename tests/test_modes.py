"""Mirror output must be 16:9 HD, not 640x480 stretched on a 1080p panel."""

from hagibis_re import (
    MODE_1280x720,
    default_mirror_mode,
    encode_hsync1,
    encode_vsync1,
    resize_rgb888,
)


def test_default_mirror_mode_is_720p():
    mode = default_mirror_mode()
    assert mode.width == 1280
    assert mode.height == 720


def test_720p_is_sixteen_by_nine():
    mode = default_mirror_mode()
    assert mode.width / mode.height == 16 / 9


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
