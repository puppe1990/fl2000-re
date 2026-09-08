"""Userspace probe for Fresco Logic FL2000DX / Hagibis USB display adapters."""

from fl2000_re.fl2000_usb import FL2000, DongleNotFoundError, decode_status
from fl2000_re.letterbox import fit_rgb888, resize_rgb888
from fl2000_re.pixels import (
    dword_swap_frame,
    needs_zlp,
    pack_frame,
    rgb888_to_rgb332,
    rgb888_to_rgb565,
)
from fl2000_re.video_modes import (
    MODE_640x360,
    MODE_640x480,
    MODE_720x480,
    MODE_800x600,
    MODE_1280x720,
    VideoMode,
    default_mirror_mode,
    encode_hsync1,
    encode_vsync1,
    fits_usb2,
    pll_pixel_clock,
    pxclk_color_bit,
)

__all__ = [
    "DongleNotFoundError",
    "FL2000",
    "MODE_1280x720",
    "MODE_640x360",
    "MODE_640x480",
    "MODE_720x480",
    "MODE_800x600",
    "VideoMode",
    "decode_status",
    "default_mirror_mode",
    "dword_swap_frame",
    "encode_hsync1",
    "encode_vsync1",
    "fit_rgb888",
    "fits_usb2",
    "needs_zlp",
    "pack_frame",
    "pll_pixel_clock",
    "pxclk_color_bit",
    "resize_rgb888",
    "rgb888_to_rgb332",
    "rgb888_to_rgb565",
]
