"""Mirror must fit USB 2.0 at 60 Hz or the Dell smears."""

import pytest
from fl2000_re.video_modes import (
    P2016_VGA_STRETCH_X,
    P2016_VGA_UNDERSCAN,
    MODE_640x360,
    MODE_640x480,
    MODE_720x480,
    MODE_800x600,
    MODE_1024x768,
    MODE_1280x720,
    MODE_1440x900,
    MODE_1920x1080,
    MODE_1920x1080_30,
    default_mirror_mode,
    encode_hsync1,
    encode_vsync1,
    fits_usb2,
    needs_usb3,
    parse_size,
    pll_pixel_clock,
    pxclk_color_bit,
    resolve_mode,
    shift_vsync2,
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


def test_p2016_vga_sink_profile():
    """P2016 over HDMI→VGA: no overscan, pre-squeeze to fill the 4:3 raster."""
    assert P2016_VGA_UNDERSCAN == 1.0
    assert pytest.approx(1.1585) == P2016_VGA_STRETCH_X


def test_resolve_mode_maps_names_to_timings():
    assert resolve_mode("640x480") is MODE_640x480
    assert resolve_mode("640x360") is MODE_640x360
    assert resolve_mode("720x480") is MODE_720x480


def test_resolve_mode_rejects_unknown_name():
    with pytest.raises(ValueError, match="640x481"):
        resolve_mode("640x481")


def test_shift_vsync2_moves_vstart_and_start_latency():
    """Reference driver keeps start_latency == vstart, so both must move together."""
    base = MODE_640x480.v_sync_2
    out = shift_vsync2(base, 8)
    assert out & 0xFFF == (base & 0xFFF) + 8
    assert (out >> 20) & 0x3FF == ((base >> 20) & 0x3FF) + 8
    assert (out >> 16) & 0x7 == (base >> 16) & 0x7


def test_shift_vsync2_zero_is_identity():
    assert shift_vsync2(0x02420024, 0) == 0x02420024


def test_shift_vsync2_rejects_underflow():
    with pytest.raises(ValueError, match="v_shift=-40"):
        shift_vsync2(0x02420024, -40)


def test_1080p60_matches_the_official_16bit_table():
    """big_table_16bit_r0 (1920x1080@60) is the table the bulk path uses."""
    assert MODE_1920x1080.h_sync_1 == 0x7800898
    assert MODE_1920x1080.h_sync_2 == 0x002C00C1
    assert MODE_1920x1080.v_sync_1 == 0x04380465
    assert MODE_1920x1080.v_sync_2 == 0x02A5002A
    assert MODE_1920x1080.pll == 0x00596106


def test_1080p60_timings_are_cea_861():
    assert MODE_1920x1080.h_sync_1 == encode_hsync1(1920, 2200)
    assert MODE_1920x1080.v_sync_1 == encode_vsync1(1080, 1125)
    assert MODE_1920x1080.pixclk == 2200 * 1125 * 60
    assert MODE_1920x1080.vic == 16


def test_1080p60_pll_is_148mhz():
    assert pll_pixel_clock(MODE_1920x1080.pll) == 148_333_333


def test_1080p30_matches_the_official_16bit_table():
    assert MODE_1920x1080_30.freq == 30
    assert MODE_1920x1080_30.pll == 0x00346107
    assert MODE_1920x1080_30.vic == 34
    assert MODE_1920x1080_30.pixclk == 2200 * 1125 * 30


def test_1080p_does_not_fit_usb2():
    assert not fits_usb2(1920, 1080, fps=60)
    assert not fits_usb2(1920, 1080, fps=30)


def test_1080p_needs_usb3():
    assert needs_usb3(MODE_1920x1080)
    assert needs_usb3(MODE_1920x1080_30)


def test_720x480_does_not_need_usb3():
    assert not needs_usb3(MODE_720x480)
    assert not needs_usb3(MODE_640x480)


def test_resolve_mode_maps_1080p_names():
    assert resolve_mode("1920x1080") is MODE_1920x1080
    assert resolve_mode("1920x1080@30") is MODE_1920x1080_30


def test_1440x900_matches_the_official_16bit_table():
    """Dell P2016 native timing; the monitor rejects 1080p with 'timing not supported'."""
    assert MODE_1440x900.h_sync_1 == 0x5A00770
    assert MODE_1440x900.h_sync_2 == 0x00980181
    assert MODE_1440x900.v_sync_1 == 0x038403A6
    assert MODE_1440x900.v_sync_2 == 0x02060020
    assert MODE_1440x900.pll == 0x00406106


def test_1440x900_pll_is_106mhz():
    assert pll_pixel_clock(MODE_1440x900.pll) == 106_666_666
    assert MODE_1440x900.pixclk == 1904 * 934 * 60


def test_1440x900_is_the_p2016_native_timing():
    assert MODE_1440x900.width / MODE_1440x900.height == 1.6
    assert MODE_1440x900.freq == 60
    assert MODE_1440x900.vic == 0


def test_1440x900_needs_usb3():
    assert needs_usb3(MODE_1440x900)


def test_resolve_mode_maps_p2016_native():
    assert resolve_mode("1440x900") is MODE_1440x900


def test_1024x768_matches_the_official_16bit_table():
    """VESA 1024x768@60 at 65 MHz is the highest mode that stays stable on the VGA sink."""
    assert MODE_1024x768.h_sync_1 == 0x04000540
    assert MODE_1024x768.h_sync_2 == 0x00880129
    assert MODE_1024x768.v_sync_1 == 0x03000326
    assert MODE_1024x768.v_sync_2 == 0x02460024
    assert MODE_1024x768.pll == 0x000D2102


def test_1024x768_pll_is_65mhz():
    assert pll_pixel_clock(MODE_1024x768.pll) == 65_000_000
    assert MODE_1024x768.pixclk == 1344 * 806 * 60


def test_1024x768_stays_below_the_high_clock_afe():
    """Above ~80 MHz the HDMI->VGA link flickers; 1024x768 is 65 MHz."""
    assert MODE_1024x768.pixclk < 80_000_000
    assert needs_usb3(MODE_1024x768)


def test_resolve_mode_maps_1024x768():
    assert resolve_mode("1024x768") is MODE_1024x768


def test_parse_size_accepts_wxh():
    assert parse_size("1280x800") == (1280, 800)


def test_parse_size_rejects_missing_x():
    with pytest.raises(ValueError, match="'1280'"):
        parse_size("1280")


def test_parse_size_rejects_zero():
    with pytest.raises(ValueError, match="'0x800'"):
        parse_size("0x800")


def test_lower_clock_xga_preserves_geometry_and_uses_valid_pll():
    from fl2000_re.video_modes import MODE_1024x768_50

    mode = resolve_mode("1024x768@50")
    assert mode is MODE_1024x768_50
    assert (mode.width, mode.height, mode.bpp) == (1024, 768, 2)
    assert (mode.h_sync_1, mode.h_sync_2) == (MODE_1024x768.h_sync_1, MODE_1024x768.h_sync_2)
    assert (mode.v_sync_1, mode.v_sync_2) == (MODE_1024x768.v_sync_1, MODE_1024x768.v_sync_2)
    assert pll_pixel_clock(mode.pll) == mode.pixclk == 54_000_000
    assert mode.freq == 50
    assert mode.pixclk / (1344 * 806) == pytest.approx(50, rel=0.004)
