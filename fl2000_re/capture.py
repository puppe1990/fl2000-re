"""Grab the Mac screen. mss monitor enumeration is empty on some macOS 26 sessions;
screencapture still works and returns retina pixels (3420x2214 on the Air)."""

from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from fl2000_re.letterbox import UNDERSCAN, fit_rgb888

Grabber = Callable[[], tuple[bytes, int, int]]
BoundsOf = Callable[[int], tuple[int, int, int, int]]
RegionGrabber = Callable[[int, int, int, int], tuple[bytes, int, int]]
DisplayGrabber = Callable[[int], tuple[bytes, int, int]]


def mss_hidpi_image_options() -> int:
    """Drop NominalResolution so CG returns 3420x2214, not the smeared 1710x1107."""
    import mss.darwin as darwin

    return darwin.kCGWindowImageBoundsIgnoreFraming | darwin.kCGWindowImageShouldBeOpaque


def grab_letterboxed_rgb(
    width: int,
    height: int,
    grab: Grabber | None = None,
    underscan: float = UNDERSCAN,
    stretch_x: float = 1.0,
) -> bytes:
    """Fit the grabbed screen into width x height (see letterbox.fit_rgb888)."""
    grab = grab or grab_main_display_rgb
    rgb, src_w, src_h = grab()
    return fit_rgb888(rgb, src_w, src_h, width, height, underscan, stretch_x)


def grab_main_display_rgb() -> tuple[bytes, int, int]:
    grabbed = _try_mss_primary()
    if grabbed is not None:
        return grabbed
    return _screencapture_rgb()


def grab_cg_display_rgb(
    display_id: int,
    bounds_of: BoundsOf | None = None,
    grab_bounds: RegionGrabber | None = None,
    include_cursor: bool = False,
    grab_display: DisplayGrabber | None = None,
) -> tuple[bytes, int, int]:
    """Capture one CGDirectDisplayID (the virtual Hagibis desktop, not the Air).

    include_cursor uses screencapture -D -C. -C is a no-op with -R on a
    CGVirtualDisplay (byte-identical with/without -C, 2026-09-19).

    Example: grab_cg_display_rgb(18, include_cursor=True)
    """
    if include_cursor:
        grab_display = grab_display or _screencapture_display_rgb
        return grab_display(display_id)
    bounds_of = bounds_of or cg_display_bounds
    grab_bounds = grab_bounds or grab_region_rgb
    left, top, width, height = bounds_of(display_id)
    if width < 1 or height < 1:
        raise ValueError(
            f"CG display {display_id} bounds too small: {width}x{height} at ({left},{top})"
        )
    return grab_bounds(left, top, width, height)


def cg_display_bounds(display_id: int) -> tuple[int, int, int, int]:
    import ctypes

    import mss.darwin as darwin

    core = ctypes.cdll.LoadLibrary(
        "/System/Library/Frameworks/CoreGraphics.framework/Versions/Current/CoreGraphics"
    )
    core.CGDisplayBounds.restype = darwin.CGRect
    core.CGDisplayBounds.argtypes = [ctypes.c_uint32]
    rect = core.CGDisplayBounds(ctypes.c_uint32(display_id))
    return (
        int(rect.origin.x),
        int(rect.origin.y),
        int(rect.size.width),
        int(rect.size.height),
    )


def grab_region_rgb(
    left: int, top: int, width: int, height: int, include_cursor: bool = False
) -> tuple[bytes, int, int]:
    if not include_cursor:
        grabbed = _try_mss_region(left, top, width, height)
        if grabbed is not None:
            return grabbed
    return _screencapture_rect_rgb(left, top, width, height, include_cursor)


def screencapture_display_index(display_id: int, online_ids: list[int]) -> int:
    """1-based screencapture -D index. 1 is main (CGGetOnlineDisplayList order).

    Example: screencapture_display_index(18, [1, 2, 18]) == 3
    """
    try:
        return online_ids.index(display_id) + 1
    except ValueError as exc:
        raise ValueError(f"CG display {display_id} not in online list {online_ids}") from exc


def screencapture_display_args(
    display_id: int,
    include_cursor: bool = False,
    online_ids: list[int] | None = None,
) -> list[str]:
    """screencapture -D flags. -C only composites the pointer with -D, not -R.

    Example: screencapture_display_args(18, True, [1, 2, 18]) == ['-D3', '-C']
    """
    ids = online_ids if online_ids is not None else online_display_ids()
    args = [f"-D{screencapture_display_index(display_id, ids)}"]
    if include_cursor:
        args.append("-C")
    return args


def online_display_ids() -> list[int]:
    """Online CGDirectDisplayIDs, main first (screencapture -D1 is main).

    Example: online_display_ids()[:1] is the built-in Air.
    """
    ids = _cg_online_display_ids()
    if not ids:
        raise ValueError(
            "CGGetOnlineDisplayList returned no displays (expected >=1 CGDirectDisplayID)"
        )
    return ids


def screencapture_rect_args(
    left: int, top: int, width: int, height: int, include_cursor: bool = False
) -> list[str]:
    """screencapture -R flags. -C is ignored for a rect; use screencapture_display_args."""
    args = ["-R", f"{left},{top},{width},{height}"]
    if include_cursor:
        args.append("-C")
    return args


def _try_mss_primary() -> tuple[bytes, int, int] | None:
    try:
        import mss
        import mss.darwin as darwin

        darwin.IMAGE_OPTIONS = mss_hidpi_image_options()
        sct = mss.MSS()
        mons = sct.monitors
        if len(mons) < 2 or mons[1].get("width", 0) < 64:
            return None
        shot = sct.grab(mons[1])
        if max(shot.rgb[::97] or b"\x00") < 12:
            return None
        return shot.rgb, shot.width, shot.height
    except Exception:
        return None


def _try_mss_region(left: int, top: int, width: int, height: int) -> tuple[bytes, int, int] | None:
    try:
        import mss
        import mss.darwin as darwin

        darwin.IMAGE_OPTIONS = mss_hidpi_image_options()
        sct = mss.MSS()
        shot = sct.grab({"left": left, "top": top, "width": width, "height": height})
        return shot.rgb, shot.width, shot.height
    except Exception:
        return None


def _screencapture_rgb() -> tuple[bytes, int, int]:
    return _screencapture_to_rgb([])


def _screencapture_rect_rgb(
    left: int, top: int, width: int, height: int, include_cursor: bool = False
) -> tuple[bytes, int, int]:
    return _screencapture_to_rgb(screencapture_rect_args(left, top, width, height, include_cursor))


def _screencapture_display_rgb(display_id: int) -> tuple[bytes, int, int]:
    # png: jpeg smears the 16px pointer on 640x480.
    return _screencapture_to_rgb(
        screencapture_display_args(display_id, include_cursor=True),
        image_format="png",
    )


def _cg_online_display_ids() -> list[int]:
    import ctypes

    core = ctypes.cdll.LoadLibrary(
        "/System/Library/Frameworks/CoreGraphics.framework/Versions/Current/CoreGraphics"
    )
    max_displays = 16
    ids = (ctypes.c_uint32 * max_displays)()
    count = ctypes.c_uint32()
    core.CGGetOnlineDisplayList.restype = ctypes.c_int32
    err = core.CGGetOnlineDisplayList(max_displays, ids, ctypes.byref(count))
    if err != 0:
        raise ValueError(f"CGGetOnlineDisplayList failed: err={err} (expected 0)")
    return [int(ids[i]) for i in range(count.value)]


def _screencapture_to_rgb(extra: list[str], image_format: str = "jpg") -> tuple[bytes, int, int]:
    from PIL import Image

    if image_format not in {"jpg", "png"}:
        raise ValueError(f"screencapture format {image_format!r} not in {{'jpg', 'png'}}")
    with tempfile.NamedTemporaryFile(suffix=f".{image_format}", delete=False) as fh:
        path = Path(fh.name)
    try:
        subprocess.run(
            ["screencapture", "-x", "-t", image_format, *extra, str(path)],
            check=True,
            timeout=5,
        )
        im = Image.open(path).convert("RGB")
        return im.tobytes(), im.width, im.height
    finally:
        path.unlink(missing_ok=True)
