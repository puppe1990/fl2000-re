"""Cursor is composited onto an mss grab; unit tests never call NSCursor."""

from fl2000_re.cursor_overlay import (
    CursorSprite,
    blit_cursor_rgba,
    cursor_local_xy,
    overlay_cursor_on_rgb,
    pick_cursor_frame_size,
)


def test_cursor_local_xy_inside_display():
    assert cursor_local_xy(10.0, 20.0, 0, 0, 640, 480) == (10.0, 20.0)


def test_cursor_local_xy_none_when_outside():
    assert cursor_local_xy(-10.0, 0.0, 0, 0, 640, 480) is None
    assert cursor_local_xy(640.0, 10.0, 0, 0, 640, 480) is None


def test_cursor_local_xy_left_placed_virtual_display():
    assert cursor_local_xy(-320.0, 240.0, -640, 0, 640, 480) == (320.0, 240.0)


def test_pick_cursor_frame_prefers_1x_for_1x_canvas():
    sizes = [(170, 230), (85, 115), (34, 46), (17, 23)]
    assert pick_cursor_frame_size(sizes, 17) == (17, 23)


def test_pick_cursor_frame_prefers_2x_for_retina_canvas():
    sizes = [(170, 230), (85, 115), (34, 46), (17, 23)]
    assert pick_cursor_frame_size(sizes, 34) == (34, 46)


def test_pick_cursor_frame_size_rejects_empty():
    try:
        pick_cursor_frame_size([], 17)
    except ValueError as exc:
        assert "empty" in str(exc)
        assert "17" in str(exc)
        return
    raise AssertionError("expected ValueError for empty representation list")


def test_blit_cursor_places_hotspot_pixel():
    rgb = bytes([255, 0, 0]) * (4 * 4)
    sprite = CursorSprite(
        rgba=bytes([255, 255, 255, 255]) * 4,
        width=2,
        height=2,
        hotspot_x=0.0,
        hotspot_y=0.0,
    )
    out = blit_cursor_rgba(rgb, 4, 4, sprite, 1.0, 1.0)
    assert out[0:3] == bytes([255, 0, 0])
    mid = (1 * 4 + 1) * 3
    assert out[mid : mid + 3] == bytes([255, 255, 255])


def test_overlay_cursor_on_rgb_uses_injected_mouse_and_sprite():
    rgb = bytes([0, 0, 255]) * (4 * 4)
    sprite = CursorSprite(
        rgba=bytes([255, 255, 255, 255]) * 4,
        width=2,
        height=2,
        hotspot_x=0.0,
        hotspot_y=0.0,
    )
    out = overlay_cursor_on_rgb(rgb, 4, 4, 0, 0, 4, 4, mouse=(1.0, 1.0), sprite=sprite)
    mid = (1 * 4 + 1) * 3
    assert out[mid : mid + 3] == bytes([255, 255, 255])


def test_overlay_cursor_scales_when_canvas_is_2x_bounds():
    rgb = bytes([0, 0, 255]) * (4 * 4)
    sprite = CursorSprite(
        rgba=bytes([255, 255, 255, 255]) * 4,
        width=2,
        height=2,
        hotspot_x=0.0,
        hotspot_y=0.0,
    )
    out = overlay_cursor_on_rgb(rgb, 4, 4, 0, 0, 2, 2, mouse=(1.0, 1.0), sprite=sprite)
    mid = (2 * 4 + 2) * 3
    assert out[mid : mid + 3] == bytes([255, 255, 255])


def test_overlay_cursor_on_rgb_skips_when_mouse_outside():
    rgb = bytes([0, 0, 255]) * 12
    sprite = CursorSprite(
        rgba=bytes([255, 0, 0, 255]) * 4, width=2, height=2, hotspot_x=0, hotspot_y=0
    )
    out = overlay_cursor_on_rgb(rgb, 2, 2, 0, 0, 2, 2, mouse=(50.0, 50.0), sprite=sprite)
    assert out == rgb


def test_blit_cursor_respects_alpha():
    rgb = bytes([0, 0, 255]) * (2 * 2)
    sprite = CursorSprite(
        rgba=bytes([255, 0, 0, 0]) * 4,
        width=2,
        height=2,
        hotspot_x=0.0,
        hotspot_y=0.0,
    )
    out = blit_cursor_rgba(rgb, 2, 2, sprite, 0.0, 0.0)
    assert out[0:3] == bytes([0, 0, 255])
