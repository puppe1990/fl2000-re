"""Screen grab is injected; unit tests never call mss or screencapture."""

import mss.darwin as darwin
from fl2000_re.capture import grab_letterboxed_rgb, mss_hidpi_image_options


def test_grab_letterboxed_rgb_uses_injected_grabber():
    src = bytes([255, 0, 0]) * (20 * 10)

    def grab() -> tuple[bytes, int, int]:
        return src, 20, 10

    out = grab_letterboxed_rgb(20, 20, grab=grab)
    assert len(out) == 20 * 20 * 3
    assert out[0:3] == bytes([0, 0, 0])
    mid = (10 * 20 + 10) * 3
    assert out[mid : mid + 3] == bytes([255, 0, 0])


def test_mss_hidpi_options_drop_nominal_resolution():
    """NominalResolution forces 1710x1107 and smears TUI glyphs on the Dell."""
    opts = mss_hidpi_image_options()
    assert opts & darwin.kCGWindowImageNominalResolution == 0
    assert opts & darwin.kCGWindowImageShouldBeOpaque


def test_grab_cg_display_rgb_uses_injected_bounds_and_region():
    from fl2000_re.capture import grab_cg_display_rgb

    src = bytes([1, 2, 3]) * (4 * 2)

    def bounds_of(display_id: int) -> tuple[int, int, int, int]:
        assert display_id == 7
        return (100, 20, 4, 2)

    def grab_bounds(left: int, top: int, width: int, height: int) -> tuple[bytes, int, int]:
        assert (left, top, width, height) == (100, 20, 4, 2)
        return src, 4, 2

    rgb, width, height = grab_cg_display_rgb(7, bounds_of=bounds_of, grab_bounds=grab_bounds)
    assert (width, height) == (4, 2)
    assert rgb == src
