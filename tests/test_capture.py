"""Screen grab is injected; unit tests never call mss or screencapture."""

import mss.darwin as darwin
from fl2000_re.capture import (
    grab_letterboxed_rgb,
    mss_hidpi_image_options,
    screencapture_rect_args,
)


def test_screencapture_rect_args_include_cursor_flag():
    """The virtual desktop must show the pointer or the user cannot aim."""
    args = screencapture_rect_args(10, 20, 30, 40, include_cursor=True)
    assert "-C" in args
    assert "-R" in args
    assert "10,20,30,40" in args


def test_screencapture_rect_args_omit_cursor_by_default():
    assert "-C" not in screencapture_rect_args(10, 20, 30, 40)


def test_grab_region_rgb_uses_screencapture_when_cursor_requested(monkeypatch):
    from fl2000_re import capture

    calls: list[tuple[int, int, int, int, bool]] = []

    def fake_rect(left: int, top: int, width: int, height: int, include_cursor: bool = False):
        calls.append((left, top, width, height, include_cursor))
        return b"\x01\x02\x03", 1, 1

    monkeypatch.setattr(
        capture, "_try_mss_region", lambda *a: (_ for _ in ()).throw(AssertionError)
    )
    monkeypatch.setattr(capture, "_screencapture_rect_rgb", fake_rect)
    rgb, width, height = capture.grab_region_rgb(1, 2, 3, 4, include_cursor=True)
    assert calls == [(1, 2, 3, 4, True)]
    assert (rgb, width, height) == (b"\x01\x02\x03", 1, 1)


def test_grab_cg_display_rgb_forwards_include_cursor(monkeypatch):
    from fl2000_re import capture

    seen: list[bool] = []

    def fake_region(left, top, width, height, include_cursor=False):  # noqa: ANN001
        seen.append(include_cursor)
        return b"\x01\x02\x03" * (width * height), width, height

    monkeypatch.setattr(capture, "grab_region_rgb", fake_region)
    monkeypatch.setattr(capture, "cg_display_bounds", lambda _d: (0, 0, 4, 2))
    capture.grab_cg_display_rgb(7, include_cursor=True)
    assert seen == [True]


def test_grab_letterboxed_rgb_uses_injected_grabber():
    src = bytes([255, 0, 0]) * (20 * 10)

    def grab() -> tuple[bytes, int, int]:
        return src, 20, 10

    out = grab_letterboxed_rgb(20, 20, grab=grab)
    assert len(out) == 20 * 20 * 3
    assert out[0:3] == bytes([0, 0, 0])
    mid = (10 * 20 + 10) * 3
    assert out[mid : mid + 3] == bytes([255, 0, 0])


def test_grab_letterboxed_rgb_forwards_underscan_and_stretch():
    """P2016 VGA fill (underscan=1.0, stretch) must reach the fit step."""
    src = bytes([255, 0, 0]) * (20 * 10)

    def grab() -> tuple[bytes, int, int]:
        return src, 20, 10

    out = grab_letterboxed_rgb(20, 20, grab=grab, underscan=1.0, stretch_x=2.0)
    assert len(out) == 20 * 20 * 3
    assert out[0:3] == bytes([255, 0, 0])


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
