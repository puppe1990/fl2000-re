"""Screen grab is injected; unit tests never call mss or screencapture."""

import mss.darwin as darwin
from fl2000_re.capture import (
    grab_letterboxed_rgb,
    mss_hidpi_image_options,
    screencapture_display_args,
    screencapture_display_index,
    screencapture_rect_args,
)


def test_screencapture_display_index_is_one_based_main_first():
    """screencapture -D1 is the main display; CGGetOnlineDisplayList matches."""
    assert screencapture_display_index(18, [1, 2, 18]) == 3


def test_screencapture_display_index_rejects_unknown_id():
    try:
        screencapture_display_index(99, [1, 2, 18])
    except ValueError as exc:
        assert "99" in str(exc)
        assert "[1, 2, 18]" in str(exc)
        return
    raise AssertionError("expected ValueError for display 99 not in [1, 2, 18]")


def test_screencapture_display_args_include_cursor_uses_D_not_R():
    """-C is a no-op with -R on CGVirtualDisplay; -D composites the pointer."""
    args = screencapture_display_args(18, include_cursor=True, online_ids=[1, 2, 18])
    assert args == ["-D3", "-C"]
    assert "-R" not in args


def test_online_display_ids_returns_cg_list(monkeypatch):
    from fl2000_re import capture

    monkeypatch.setattr(capture, "_cg_online_display_ids", lambda: [1, 2, 18])
    assert capture.online_display_ids() == [1, 2, 18]


def test_online_display_ids_rejects_empty(monkeypatch):
    from fl2000_re import capture

    monkeypatch.setattr(capture, "_cg_online_display_ids", lambda: [])
    try:
        capture.online_display_ids()
    except ValueError as exc:
        assert "no displays" in str(exc)
        return
    raise AssertionError("expected ValueError for empty online list")


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


def test_grab_cg_display_rgb_uses_display_capture_for_cursor():
    from fl2000_re.capture import grab_cg_display_rgb

    seen: list[int] = []

    def fake_display(display_id: int) -> tuple[bytes, int, int]:
        seen.append(display_id)
        return b"\x01\x02\x03" * 8, 4, 2

    rgb, width, height = grab_cg_display_rgb(18, include_cursor=True, grab_display=fake_display)
    assert seen == [18]
    assert (rgb, width, height) == (b"\x01\x02\x03" * 8, 4, 2)


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
