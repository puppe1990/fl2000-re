"""Cursor is composited onto an mss grab from an in-process sprite (no AppKit)."""

from fl2000_re.cursor_overlay import (
    CursorSprite,
    blit_cursor_rgba,
    builtin_arrow_sprite,
    cursor_local_xy,
    overlay_cursor_on_rgb,
    system_cursor_sprite,
)


def test_cursor_local_xy_inside_display():
    assert cursor_local_xy(10.0, 20.0, 0, 0, 640, 480) == (10.0, 20.0)


def test_cursor_local_xy_none_when_outside():
    assert cursor_local_xy(-10.0, 0.0, 0, 0, 640, 480) is None
    assert cursor_local_xy(640.0, 10.0, 0, 0, 640, 480) is None


def test_cursor_local_xy_left_placed_virtual_display():
    assert cursor_local_xy(-320.0, 240.0, -640, 0, 640, 480) == (320.0, 240.0)


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


def test_system_cursor_sprite_is_the_builtin_arrow():
    """Regression 2026-09-20: NSCursor (AppKit objc_msgSend) hangs a launchd agent."""
    assert system_cursor_sprite() == builtin_arrow_sprite()


def test_builtin_arrow_sprite_is_a_pointed_pointer():
    sprite = builtin_arrow_sprite()
    assert (sprite.width, sprite.height) == (17, 23)
    assert (sprite.hotspot_x, sprite.hotspot_y) == (0.0, 0.0)
    pixels = [sprite.rgba[i : i + 4] for i in range(0, len(sprite.rgba), 4)]
    assert pixels[0][3] > 0
    assert pixels[-1][3] == 0
    assert any(p[0] < 80 and p[3] > 200 for p in pixels)
    assert any(p[0] > 200 and p[3] > 200 for p in pixels)
