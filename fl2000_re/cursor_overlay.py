"""Composite a pointer sprite onto an mss grab (screencapture -C is a no-op with -R)."""

from __future__ import annotations

from dataclasses import dataclass

ARROW_WIDTH = 17
ARROW_HEIGHT = 23
ARROW_OUTLINE_PX = 1.5
ARROW_SUPERSAMPLE = 6
# 17x23 silhouette: tip on the hotspot, left notch, tail, right wing.
ARROW_SILHOUETTE = (
    (0.0, 0.0),
    (0.0, 18.0),
    (4.5, 13.5),
    (7.0, 21.0),
    (10.0, 19.5),
    (7.5, 12.0),
    (12.0, 12.0),
)


@dataclass(frozen=True)
class CursorSprite:
    rgba: bytes
    width: int
    height: int
    hotspot_x: float
    hotspot_y: float


def cursor_local_xy(
    mouse_x: float,
    mouse_y: float,
    origin_x: int,
    origin_y: int,
    width: int,
    height: int,
) -> tuple[float, float] | None:
    """Mouse in display-local points, or None if it is not on this screen.

    Example: cursor_local_xy(-320, 240, -640, 0, 640, 480) == (320.0, 240.0)
    """
    lx = mouse_x - origin_x
    ly = mouse_y - origin_y
    if lx < 0 or ly < 0 or lx >= width or ly >= height:
        return None
    return (lx, ly)


def builtin_arrow_sprite() -> CursorSprite:
    """macOS-style pointer drawn in-process; no AppKit/NSCursor involved.

    NSCursor's objc_msgSend blocks forever in a launchd Aqua agent with no run
    loop, freezing the capture child on its first frame (2026-09-20), so the
    LaunchAgent cannot borrow the system cursor image.

    Example: builtin_arrow_sprite().width == 17
    """
    from PIL import Image, ImageDraw

    scale = ARROW_SUPERSAMPLE
    big = Image.new("RGBA", (ARROW_WIDTH * scale, ARROW_HEIGHT * scale), (0, 0, 0, 0))
    ImageDraw.Draw(big).polygon(
        [(x * scale, y * scale) for x, y in ARROW_SILHOUETTE],
        fill=(255, 255, 255, 255),
        outline=(0, 0, 0, 255),
        width=int(ARROW_OUTLINE_PX * scale),
    )
    sprite = big.resize((ARROW_WIDTH, ARROW_HEIGHT), Image.BOX)
    return CursorSprite(sprite.tobytes(), ARROW_WIDTH, ARROW_HEIGHT, 0.0, 0.0)


def system_cursor_sprite() -> CursorSprite:
    """Sprite the overlay paints on every frame."""
    return builtin_arrow_sprite()


def blit_cursor_rgba(
    rgb: bytes,
    canvas_w: int,
    canvas_h: int,
    sprite: CursorSprite,
    local_x: float,
    local_y: float,
) -> bytes:
    """Paste sprite so its hotspot sits at local_x/local_y in canvas pixels."""
    from PIL import Image

    canvas = Image.frombytes("RGB", (canvas_w, canvas_h), rgb).convert("RGBA")
    cursor = Image.frombytes("RGBA", (sprite.width, sprite.height), sprite.rgba)
    px = int(round(local_x - sprite.hotspot_x))
    py = int(round(local_y - sprite.hotspot_y))
    _paste_rgba(canvas, cursor, px, py)
    return canvas.convert("RGB").tobytes()


def overlay_cursor_on_rgb(
    rgb: bytes,
    width: int,
    height: int,
    origin_x: int,
    origin_y: int,
    bounds_w: int,
    bounds_h: int,
    mouse: tuple[float, float] | None = None,
    sprite: CursorSprite | None = None,
) -> bytes:
    """Draw the pointer if the mouse is on this display; otherwise return rgb."""
    if bounds_w < 1 or bounds_h < 1:
        raise ValueError(
            f"display bounds too small for cursor overlay: {bounds_w}x{bounds_h} at ({origin_x},{origin_y})"
        )
    sprite = sprite if sprite is not None else system_cursor_sprite()
    mouse = mouse if mouse is not None else mouse_global_xy()
    local = cursor_local_xy(mouse[0], mouse[1], origin_x, origin_y, bounds_w, bounds_h)
    if local is None:
        return rgb
    return blit_cursor_rgba(
        rgb, width, height, sprite, local[0] * width / bounds_w, local[1] * height / bounds_h
    )


def mouse_global_xy() -> tuple[float, float]:
    """Global pointer location in points (CoreGraphics, no AppKit)."""
    import ctypes

    class CGPoint(ctypes.Structure):
        _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]

    core = ctypes.cdll.LoadLibrary(
        "/System/Library/Frameworks/CoreGraphics.framework/Versions/Current/CoreGraphics"
    )
    core.CGEventCreate.restype = ctypes.c_void_p
    core.CGEventCreate.argtypes = [ctypes.c_void_p]
    core.CGEventGetLocation.restype = CGPoint
    core.CGEventGetLocation.argtypes = [ctypes.c_void_p]
    event = core.CGEventCreate(None)
    if not event:
        raise ValueError("CGEventCreate returned NULL (expected a mouse-location event)")
    loc = core.CGEventGetLocation(event)
    core.CFRelease.argtypes = [ctypes.c_void_p]
    core.CFRelease(event)
    return (float(loc.x), float(loc.y))


def _paste_rgba(canvas, cursor, px: int, py: int) -> None:
    cw, ch = canvas.size
    sw, sh = cursor.size
    src_x, src_y = max(0, -px), max(0, -py)
    dst_x, dst_y = max(0, px), max(0, py)
    copy_w = min(sw - src_x, cw - dst_x)
    copy_h = min(sh - src_y, ch - dst_y)
    if copy_w <= 0 or copy_h <= 0:
        return
    piece = cursor.crop((src_x, src_y, src_x + copy_w, src_y + copy_h))
    canvas.alpha_composite(piece, (dst_x, dst_y))
