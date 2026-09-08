"""Mirror must fit USB 2.0 at 60 Hz or the Dell smears."""

from fl2000_re.video_modes import (
    MODE_640x480,
    MODE_720x480,
    MODE_800x600,
    MODE_1280x720,
    default_mirror_mode,
    encode_hsync1,
    encode_vsync1,
    fits_usb2,
    pll_pixel_clock,
    pxclk_color_bit,
)


def test_720p60_rgb565_does_not_fit_usb2():
    assert not fits_usb2(1280, 720, fps=60)


def test_640x480_60_rgb565_fits_usb2():
    assert fits_usb2(640, 480, fps=60)


def test_default_mirror_is_60hz():
    assert default_mirror_mode().freq == 60


def test_default_mirror_fits_usb2():
    mode = default_mirror_mode()
    assert fits_usb2(mode.width, mode.height, fps=mode.freq, bpp=mode.bpp)


def test_default_mirror_is_rgb565():
    assert default_mirror_mode().bpp == 2


def test_default_mirror_is_720x480_16by9():
    """Dell 1 first filled 16:9 correctly on CEA 480p (not VGA 4:3)."""
    mode = default_mirror_mode()
    assert mode is MODE_720x480
    assert (mode.width, mode.height) == (720, 480)
    assert mode.vic == 3


def test_default_mirror_is_not_vga_4_by_3():
    assert default_mirror_mode() is not MODE_640x480
    assert default_mirror_mode().width / default_mirror_mode().height == 1.5


def test_720x480_vsync1_is_cea():
    assert MODE_720x480.v_sync_1 == encode_vsync1(480, 525)


def test_720x480_hsync2_is_cea():
    assert MODE_720x480.h_sync_2 == 0x003E007B


def test_720x480_vsync2_is_cea():
    assert MODE_720x480.v_sync_2 == 0x02560025


def test_800x600_rgb332_fits_usb2():
    assert fits_usb2(800, 600, fps=60, bpp=1)


def test_800x600_rgb565_does_not_fit_usb2():
    assert not fits_usb2(800, 600, fps=60, bpp=2)


def test_1024x768_rgb332_does_not_fit_usb2():
    assert not fits_usb2(1024, 768, fps=60, bpp=1)


def test_800x600_hsync1_is_vesa():
    assert MODE_800x600.h_sync_1 == encode_hsync1(800, 1056)


def test_800x600_vsync1_is_vesa():
    assert MODE_800x600.v_sync_1 == encode_vsync1(600, 628)


def test_800x600_pll_is_40mhz():
    assert pll_pixel_clock(MODE_800x600.pll) == 40_000_000


def test_640x480_pll_is_25_2mhz():
    assert pll_pixel_clock(MODE_640x480.pll) == 25_200_000


def test_encode_hsync1_packs_active_and_total():
    assert encode_hsync1(1280, 1650) == 0x05000672


def test_encode_vsync1_packs_active_and_total():
    assert encode_vsync1(720, 750) == 0x02D002EE


def test_720p_hdmi_tweak_vsync2():
    assert MODE_1280x720.v_sync_2 == 0x01A5001A


def test_720x480_hsync1_is_cea():
    assert MODE_720x480.h_sync_1 == encode_hsync1(720, 858)


def test_720x480_pll_is_27mhz():
    assert pll_pixel_clock(MODE_720x480.pll) == 27_000_000


def test_720x480_rgb565_fits_usb2():
    assert fits_usb2(720, 480, fps=60, bpp=2)


def test_rgb332_uses_pxclk_bit_25():
    assert pxclk_color_bit(1) == 25


def test_rgb565_uses_pxclk_bit_6():
    assert pxclk_color_bit(2) == 6
