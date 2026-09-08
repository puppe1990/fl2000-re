"""Grab the Mac screen. mss monitor enumeration is empty on some macOS 26 sessions;
screencapture still works and returns retina pixels (3420x2214 on the Air)."""

from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from fl2000_re.letterbox import fit_rgb888

Grabber = Callable[[], tuple[bytes, int, int]]
BoundsOf = Callable[[int], tuple[int, int, int, int]]
RegionGrabber = Callable[[int, int, int, int], tuple[bytes, int, int]]


def mss_hidpi_image_options() -> int:
    """Drop NominalResolution so CG returns 3420x2214, not the smeared 1710x1107."""
    import mss.darwin as darwin

    return darwin.kCGWindowImageBoundsIgnoreFraming | darwin.kCGWindowImageShouldBeOpaque


def grab_letterboxed_rgb(width: int, height: int, grab: Grabber | None = None) -> bytes:
    grab = grab or grab_main_display_rgb
    rgb, src_w, src_h = grab()
    return fit_rgb888(rgb, src_w, src_h, width, height)


def grab_main_display_rgb() -> tuple[bytes, int, int]:
    grabbed = _try_mss_primary()
    if grabbed is not None:
        return grabbed
    return _screencapture_rgb()


def grab_cg_display_rgb(
    display_id: int,
    bounds_of: BoundsOf | None = None,
    grab_bounds: RegionGrabber | None = None,
) -> tuple[bytes, int, int]:
    """Capture one CGDirectDisplayID (the virtual Hagibis desktop, not the Air)."""
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


def grab_region_rgb(left: int, top: int, width: int, height: int) -> tuple[bytes, int, int]:
    grabbed = _try_mss_region(left, top, width, height)
    if grabbed is not None:
        return grabbed
    return _screencapture_rect_rgb(left, top, width, height)


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


def _screencapture_rect_rgb(left: int, top: int, width: int, height: int) -> tuple[bytes, int, int]:
    return _screencapture_to_rgb(["-R", f"{left},{top},{width},{height}"])


def _screencapture_to_rgb(extra: list[str]) -> tuple[bytes, int, int]:
    from PIL import Image

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as fh:
        path = Path(fh.name)
    try:
        subprocess.run(
            ["screencapture", "-x", "-t", "jpg", *extra, str(path)],
            check=True,
            timeout=5,
        )
        im = Image.open(path).convert("RGB")
        return im.tobytes(), im.width, im.height
    finally:
        path.unlink(missing_ok=True)
