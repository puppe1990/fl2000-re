"""Screen grab is injected; unit tests never call mss or screencapture."""

import subprocess

import mss.darwin as darwin
import pytest
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


def test_grab_via_screencapture_none_uses_full_screen_cli(monkeypatch):
    from fl2000_re import capture

    seen: list[str] = []
    monkeypatch.setattr(
        capture, "_screencapture_rgb", lambda: seen.append("full") or (b"\x01\x02\x03", 1, 1)
    )
    monkeypatch.setattr(
        capture,
        "_screencapture_display_rgb",
        lambda _did: (_ for _ in ()).throw(AssertionError("-D path")),
    )
    monkeypatch.setattr(
        capture,
        "_try_mss_primary",
        lambda: (_ for _ in ()).throw(AssertionError("mss primary")),
    )
    assert capture.grab_via_screencapture() == (b"\x01\x02\x03", 1, 1)
    assert seen == ["full"]


def test_grab_via_screencapture_never_calls_mss(monkeypatch):
    """Parent-process preview must not call mss.grab: CGImageGetWidth SIGSEGVs Python."""
    from fl2000_re import capture

    monkeypatch.setattr(
        capture,
        "_try_mss_region",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("mss in parent")),
    )
    monkeypatch.setattr(
        capture, "_screencapture_display_rgb", lambda display_id: (b"\x01\x02\x03", 1, 1)
    )
    assert capture.grab_via_screencapture(18) == (b"\x01\x02\x03", 1, 1)


def test_mss_client_returns_cached_instance():
    from fl2000_re import capture

    sentinel = object()
    capture._MSS_CLIENT = sentinel
    try:
        assert capture._mss_client() is sentinel
    finally:
        capture._MSS_CLIENT = None


def test_grab_cg_display_rgb_blits_cursor_onto_mss_region():
    """screencapture -C -R is a no-op on CGVirtualDisplay; blit onto mss instead."""
    from fl2000_re.capture import grab_cg_display_rgb

    seen: dict[str, object] = {}

    def fake_region(left: int, top: int, width: int, height: int) -> tuple[bytes, int, int]:
        seen["region"] = (left, top, width, height)
        return b"\x01\x02\x03" * 8, 4, 2

    def fake_overlay(
        rgb: bytes,
        width: int,
        height: int,
        origin_x: int,
        origin_y: int,
        bounds_w: int,
        bounds_h: int,
    ) -> bytes:
        seen["overlay"] = (width, height, origin_x, origin_y, bounds_w, bounds_h)
        return b"\x09\x09\x09" * 8

    rgb, width, height = grab_cg_display_rgb(
        18,
        include_cursor=True,
        bounds_of=lambda _d: (-640, 0, 4, 2),
        grab_bounds=fake_region,
        overlay_cursor=fake_overlay,
    )
    assert seen["region"] == (-640, 0, 4, 2)
    assert seen["overlay"] == (4, 2, -640, 0, 4, 2)
    assert (rgb, width, height) == (b"\x09\x09\x09" * 8, 4, 2)


def test_grab_cg_display_rgb_skips_overlay_without_cursor():
    from fl2000_re.capture import grab_cg_display_rgb

    def boom(*_a, **_k):  # noqa: ANN002
        raise AssertionError("overlay must not run when include_cursor=False")

    rgb, width, height = grab_cg_display_rgb(
        7,
        include_cursor=False,
        bounds_of=lambda _d: (0, 0, 4, 2),
        grab_bounds=lambda *_a: (b"\x01\x02\x03" * 8, 4, 2),
        overlay_cursor=boom,
    )
    assert (width, height) == (4, 2)
    assert rgb[:3] == b"\x01\x02\x03"


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


def test_screencapture_timeout_raises_screencapture_error(monkeypatch):
    """A WindowServer hang must be catchable, not a raw subprocess.TimeoutExpired."""
    from fl2000_re import capture

    def hang(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="screencapture", timeout=5)

    monkeypatch.setattr(capture.subprocess, "run", hang)
    with pytest.raises(capture.ScreencaptureError, match="travou"):
        capture._screencapture_to_rgb(["-D3", "-C"], image_format="png")


def test_screencapture_failure_raises_screencapture_error(monkeypatch):
    from fl2000_re import capture

    def fail(*_a, **_k):
        raise subprocess.CalledProcessError(returncode=1, cmd="screencapture")

    monkeypatch.setattr(capture.subprocess, "run", fail)
    with pytest.raises(capture.ScreencaptureError, match="código 1"):
        capture._screencapture_to_rgb(["-R", "0,0,4,2"])
