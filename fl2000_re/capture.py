"""Grab the Mac screen. mss monitor enumeration is empty on some macOS 26 sessions;
screencapture still works and returns retina pixels (3420x2214 on the Air)."""

from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from fl2000_re.letterbox import fit_rgb888

Grabber = Callable[[], tuple[bytes, int, int]]


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
        if max(shot.rgb[::97]) < 12:
            return None
        return shot.rgb, shot.width, shot.height
    except Exception:
        return None


def _screencapture_rgb() -> tuple[bytes, int, int]:
    from PIL import Image

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as fh:
        path = Path(fh.name)
    try:
        subprocess.run(
            ["screencapture", "-x", "-t", "jpg", str(path)],
            check=True,
            timeout=5,
        )
        im = Image.open(path).convert("RGB")
        return im.tobytes(), im.width, im.height
    finally:
        path.unlink(missing_ok=True)
