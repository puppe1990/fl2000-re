"""HDMI timings the FL2000 + IT66121 can scan out.

Default clone is CEA-861 VIC 3 720x480p60 16:9 RGB565 (Dell 1 filled the
panel correctly). 640x480 4:3 is stretched on that 16:9. 800x600 RGB332
never locked. 1280x720 RGB565 overruns USB 2.0.
"""

from __future__ import annotations

from dataclasses import dataclass

# 720x480 RGB565@60 = 41.5 MB/s. 640x480 held 36.9; 1280x720 at 110 does not.
USB2_BUDGET_BPS = 42_000_000

# Dell P2016 (1440x900 16:10) over HDMI→VGA: the analog raster has no overscan
# and the scaler stretches 4:3 to 16:10. 1.2 would keep true proportions but
# leaves ~3% side bars (measured); 1.1585 fills the width, ~3.6% wide.
P2016_VGA_UNDERSCAN = 1.0
P2016_VGA_STRETCH_X = 1.1585


@dataclass(frozen=True)
class VideoMode:
    width: int
    height: int
    freq: int
    h_sync_1: int
    h_sync_2: int
    v_sync_1: int
    v_sync_2: int
    pll: int
    vic: int
    pixclk: int
    bpp: int = 2


def fits_usb2(
    width: int,
    height: int,
    fps: float = 60,
    bpp: int = 2,
    budget: int = USB2_BUDGET_BPS,
) -> bool:
    """True if RGB frames at fps fit the measured USB 2.0 bulk budget (~40 MB/s)."""
    return width * height * bpp * fps <= budget


def pll_pixel_clock(pll: int) -> int:
    """FL2000 VGA PLL: 10 MHz XTAL * multiplier / (prescaler * divisor)."""
    divisor = pll & 0xFF
    prescaler = (pll >> 8) & 0x3
    multiplier = (pll >> 16) & 0xFF
    return 10_000_000 * multiplier // (prescaler * divisor)


def pxclk_color_bit(bpp: int) -> int:
    """REG_PXCLK: bit 25 = RGB332, bit 6 = RGB565."""
    return 25 if bpp == 1 else 6


def encode_hsync1(hactive: int, htotal: int) -> int:
    return (hactive << 16) | htotal


def encode_vsync1(vactive: int, vtotal: int) -> int:
    return (vactive << 16) | vtotal


# CEA-861 VIC 3: 720x480p60 16:9. Dell P2219H lists this; 4:3 640x480 is
# stretched on the 16:9 panel and smears TUI glyphs (IMG_2119).
MODE_720x480 = VideoMode(
    width=720,
    height=480,
    freq=60,
    h_sync_1=encode_hsync1(720, 858),
    h_sync_2=0x003E007B,
    v_sync_1=encode_vsync1(480, 525),
    v_sync_2=0x02560025,
    pll=0x001B610A,
    vic=3,
    pixclk=858 * 525 * 60,
)


MODE_640x480 = VideoMode(
    width=640,
    height=480,
    freq=60,
    h_sync_1=0x2800320,
    h_sync_2=0x600091,
    v_sync_1=0x1E0020D,
    v_sync_2=0x2420024,
    pll=0x003F6119,
    vic=1,
    pixclk=800 * 525 * 60,
)

# CEA-861 1280x720@60 + official HDMI v_sync_reg_2 tweak (0x1A5001A).
# Too wide for USB 2.0 at 60 Hz (needs ~110 MB/s); kept for experiments.
MODE_1280x720 = VideoMode(
    width=1280,
    height=720,
    freq=60,
    h_sync_1=encode_hsync1(1280, 1650),
    h_sync_2=0x00280105,
    v_sync_1=encode_vsync1(720, 750),
    v_sync_2=0x01A5001A,
    pll=0x0059610C,
    vic=4,
    pixclk=1650 * 750 * 60,
)

MODE_800x600 = VideoMode(
    width=800,
    height=600,
    freq=60,
    h_sync_1=0x3200420,
    h_sync_2=0x8000D9,
    v_sync_1=0x2580274,
    v_sync_2=0x1C4001C,
    pll=0x00080102,
    vic=0,
    pixclk=1056 * 628 * 60,
    bpp=1,
)

# 16:9 square pixels at the same 25.2 MHz / 60 Hz VGA clock the chip already
# sustains. Custom 640x360 lost sync on the Dell P2219H.
MODE_640x360 = VideoMode(
    width=640,
    height=360,
    freq=60,
    h_sync_1=0x2800320,
    h_sync_2=0x600091,
    v_sync_1=encode_vsync1(360, 525),
    v_sync_2=0x09C2009C,
    pll=0x003F6119,
    vic=0,
    pixclk=800 * 525 * 60,
)


def default_mirror_mode() -> VideoMode:
    # 640x480 4:3 is stretched on the Dell 16:9 (IMG_2119). CEA 480p 16:9
    # is in the EDID; 800x600 RGB332 never locked.
    return MODE_720x480


NAMED_MODES = {
    "720x480": MODE_720x480,
    "640x480": MODE_640x480,
    "640x360": MODE_640x360,
    "800x600": MODE_800x600,
    "1280x720": MODE_1280x720,
}


def resolve_mode(name: str) -> VideoMode:
    """Map a --mode name to timings (P2016 over VGA wants 640x480).

    Example: resolve_mode("640x480") is MODE_640x480
    """
    try:
        return NAMED_MODES[name]
    except KeyError:
        valid = ", ".join(sorted(NAMED_MODES))
        raise ValueError(f"unknown mode {name!r}, expected one of: {valid}") from None


def shift_vsync2(v_sync_2: int, lines: int) -> int:
    """Nudge the scanout vertically via the VSYNC2 porch field.

    Positive lines push the picture DOWN on an analog sink whose own Vertical
    Position control is dead (Dell P2016 up button). The porch field is the low
    16 bits and must stay within 1..65535. Example:
    shift_vsync2(MODE_640x480.v_sync_2, 8) == 0x0242002C
    """
    porch = (v_sync_2 & 0xFFFF) + lines
    if not 1 <= porch <= 0xFFFF:
        raise ValueError(f"v_shift={lines} yields VSYNC2 porch={porch}, expected 1..65535")
    return (v_sync_2 & 0xFFFF0000) | porch
