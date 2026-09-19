"""Composite the system pointer onto an mss grab (screencapture -C is a no-op with -R)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from io import BytesIO


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


def pick_cursor_frame_size(sizes: list[tuple[int, int]], target_w: int) -> tuple[int, int]:
    """Closest NSCursor representation to the canvas scale (1x display → 17px arrow).

    Example: pick_cursor_frame_size([(34, 46), (17, 23)], 17) == (17, 23)
    """
    if not sizes:
        raise ValueError(f"cursor representation list is empty (target_w={target_w})")
    return min(sizes, key=lambda size: (abs(size[0] - target_w), size[0]))


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
    if sprite is None:
        return rgb
    mouse = mouse if mouse is not None else mouse_global_xy()
    local = cursor_local_xy(mouse[0], mouse[1], origin_x, origin_y, bounds_w, bounds_h)
    if local is None:
        return rgb
    return blit_cursor_rgba(
        rgb, width, height, sprite, local[0] * width / bounds_w, local[1] * height / bounds_h
    )


def mouse_global_xy() -> tuple[float, float]:
    """Global AppKit mouse location in points."""
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


_SPRITE_CACHE: tuple[float, CursorSprite | None] | None = None
_SPRITE_TTL_S = 0.05


def system_cursor_sprite() -> CursorSprite | None:
    """Current NSCursor bitmap at 1x. None if AppKit is unavailable."""
    global _SPRITE_CACHE
    now = time.monotonic()
    if _SPRITE_CACHE is not None and now - _SPRITE_CACHE[0] < _SPRITE_TTL_S:
        return _SPRITE_CACHE[1]
    try:
        sprite = _nscursor_sprite()
    except Exception:
        sprite = None
    _SPRITE_CACHE = (now, sprite)
    return sprite


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


def _nscursor_sprite() -> CursorSprite:
    cur, img = _nscursor_image()
    tiff = _nsdata_bytes(_objc_msg(img, "TIFFRepresentation"))
    hotspot = _nscursor_hotspot(cur)
    frames = _tiff_rgba_frames(tiff)
    target_w = max(1, round(_nsimage_point_size(img)[0]))
    chosen = pick_cursor_frame_size([frame.size for frame in frames], target_w)
    frame = next(frame for frame in frames if frame.size == chosen)
    rgba = frame.convert("RGBA")
    return CursorSprite(rgba.tobytes(), rgba.width, rgba.height, hotspot[0], hotspot[1])


def _tiff_rgba_frames(tiff: bytes) -> list:
    from PIL import Image

    im = Image.open(BytesIO(tiff))
    frames = []
    for index in range(getattr(im, "n_frames", 1)):
        im.seek(index)
        frames.append(im.copy())
    if not frames:
        raise ValueError(f"NSCursor TIFF had no frames (len={len(tiff)})")
    return frames


def _nscursor_image() -> tuple[int, int]:
    import ctypes

    ctypes.cdll.LoadLibrary("/System/Library/Frameworks/AppKit.framework/AppKit")
    cur = _objc_msg(_objc_cls("NSCursor"), "currentSystemCursor")
    if not cur:
        raise ValueError("NSCursor.currentSystemCursor returned nil")
    img = _objc_msg(cur, "image")
    if not img:
        raise ValueError("NSCursor.image returned nil")
    return cur, img


def _nscursor_hotspot(cursor: int) -> tuple[float, float]:
    import ctypes

    class CGPoint(ctypes.Structure):
        _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]

    fn = ctypes.CFUNCTYPE(CGPoint, ctypes.c_void_p, ctypes.c_void_p)(("objc_msgSend", _objc()))
    hot = fn(cursor, _objc().sel_registerName(b"hotSpot"))
    return (float(hot.x), float(hot.y))


def _nsimage_point_size(image: int) -> tuple[float, float]:
    import ctypes

    class CGSize(ctypes.Structure):
        _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]

    fn = ctypes.CFUNCTYPE(CGSize, ctypes.c_void_p, ctypes.c_void_p)(("objc_msgSend", _objc()))
    size = fn(image, _objc().sel_registerName(b"size"))
    return (float(size.width), float(size.height))


def _nsdata_bytes(data: int) -> bytes:
    import ctypes

    if not data:
        raise ValueError("NSData is nil (expected TIFFRepresentation bytes)")
    length_fn = ctypes.CFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p)(
        ("objc_msgSend", _objc())
    )
    length = int(length_fn(data, _objc().sel_registerName(b"length")))
    ptr = _objc_msg(data, "bytes")
    if not ptr or length < 1:
        raise ValueError(f"NSData bytes empty: ptr={ptr!r} length={length}")
    return ctypes.string_at(ptr, length)


def _objc_cls(name: str) -> int:
    return int(_objc().objc_getClass(name.encode()))


def _objc_msg(obj: int, selector: str) -> int:
    import ctypes

    fn = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(
        ("objc_msgSend", _objc())
    )
    return int(fn(obj, _objc().sel_registerName(selector.encode())) or 0)


def _objc():
    import ctypes

    lib = ctypes.cdll.LoadLibrary("/usr/lib/libobjc.A.dylib")
    lib.objc_getClass.restype = ctypes.c_void_p
    lib.objc_getClass.argtypes = [ctypes.c_char_p]
    lib.sel_registerName.restype = ctypes.c_void_p
    lib.sel_registerName.argtypes = [ctypes.c_char_p]
    return lib
