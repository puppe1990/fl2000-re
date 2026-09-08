"""Pixel packing for the FL2000 bulk stream."""

import pytest
from fl2000_re.pixels import (
    dword_swap_frame,
    needs_zlp,
    pack_frame,
    rgb888_to_rgb332,
    rgb888_to_rgb565,
)


def test_white_is_ffff():
    assert rgb888_to_rgb565(bytes([255, 255, 255])) == (0xFFFF).to_bytes(2, "little")


def test_red_is_f800():
    assert rgb888_to_rgb565(bytes([255, 0, 0])) == (0xF800).to_bytes(2, "little")


def test_green_is_07e0():
    assert rgb888_to_rgb565(bytes([0, 255, 0])) == (0x07E0).to_bytes(2, "little")


def test_blue_is_001f():
    assert rgb888_to_rgb565(bytes([0, 0, 255])) == (0x001F).to_bytes(2, "little")


def test_two_pixels_keep_order():
    packed = rgb888_to_rgb565(bytes([255, 0, 0, 0, 0, 255]))
    assert packed == (0xF800).to_bytes(2, "little") + (0x001F).to_bytes(2, "little")


def test_dword_swap_reverses_32bit_halves():
    src = bytes.fromhex("1111111122222222")
    out = dword_swap_frame(src)
    assert out == bytes.fromhex("2222222211111111")


def test_rgb888_rejects_odd_length():
    with pytest.raises(ValueError, match="multiple of 3"):
        rgb888_to_rgb565(bytes([1, 2]))


def test_640x480_rgb565_needs_zlp():
    assert needs_zlp(640 * 480 * 2)


def test_short_packet_does_not_need_zlp():
    assert not needs_zlp(513)


def test_red_rgb332():
    assert rgb888_to_rgb332(bytes([255, 0, 0])) == bytes([0xE0])


def test_white_rgb332():
    assert rgb888_to_rgb332(bytes([255, 255, 255])) == bytes([0xFF])


def test_green_rgb332():
    assert rgb888_to_rgb332(bytes([0, 255, 0])) == bytes([0x1C])


def test_blue_rgb332():
    assert rgb888_to_rgb332(bytes([0, 0, 255])) == bytes([0x03])


def test_rgb332_rejects_odd_length():
    with pytest.raises(ValueError, match="multiple of 3"):
        rgb888_to_rgb332(bytes([1, 2]))


def test_pack_frame_rgb332_dword_swaps():
    rgb = bytes([255, 0, 0]) * 8
    assert pack_frame(rgb, 1) == dword_swap_frame(bytes([0xE0] * 8))


def test_pack_frame_rgb565_dword_swaps():
    rgb = bytes([255, 0, 0]) * 4
    assert pack_frame(rgb, 2) == dword_swap_frame(rgb888_to_rgb565(rgb))
