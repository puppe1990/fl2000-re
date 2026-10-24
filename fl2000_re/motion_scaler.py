"""Optional macOS SIMD scaling, isolated from CoreGraphics and USB.

ABI and flags follow the installed Accelerate vImage Geometry.h/Convolution.h.
The four-channel operations also accept RGBA; the alpha channel is opaque.
"""

from __future__ import annotations

import ctypes as ct
import sys
from functools import lru_cache

from PIL import Image


class VImageBuffer(ct.Structure):
    _fields_ = [
        ("pixels", ct.c_void_p),
        ("height", ct.c_ulong),
        ("width", ct.c_ulong),
        ("row_bytes", ct.c_size_t),
    ]


@lru_cache(maxsize=1)
def _vimage_api():
    if sys.platform != "darwin":
        return None
    try:
        library = ct.CDLL("/System/Library/Frameworks/Accelerate.framework/Accelerate")
        buffer = ct.POINTER(VImageBuffer)
        library.vImageScale_ARGB8888.argtypes = [buffer, buffer, ct.c_void_p, ct.c_uint32]
        library.vImageScale_ARGB8888.restype = ct.c_long
        library.vImageConvolve_ARGB8888.argtypes = [
            buffer,
            buffer,
            ct.c_void_p,
            ct.c_ulong,
            ct.c_ulong,
            ct.POINTER(ct.c_int16),
            ct.c_uint32,
            ct.c_uint32,
            ct.c_int32,
            ct.c_void_p,
            ct.c_uint32,
        ]
        library.vImageConvolve_ARGB8888.restype = ct.c_long
        return library
    except (OSError, AttributeError):
        return None


class MotionScaler:
    """Scale full-raster motion with vImage; return None when Pillow should take over.

    Example: scaler.resize(rgb, 1280, 960, 1024, 768)
    """

    def __init__(self) -> None:
        self.backend = "pillow"
        self.disabled = False

    def resize(self, rgb: bytes, sw: int, sh: int, dw: int, dh: int) -> bytes | None:
        if min(sw, sh, dw, dh) < 1 or len(rgb) != sw * sh * 3:
            raise ValueError(
                f"RGB recebido: {len(rgb)} bytes, size={sw}x{sh}; expected {sw * sh * 3} bytes and positive {dw}x{dh}"
            )
        api = None if self.disabled else _vimage_api()
        if api is None:
            return None
        rgba = Image.frombytes("RGB", (sw, sh), rgb).convert("RGBA").tobytes()
        scaled, sharp = ct.create_string_buffer(dw * dh * 4), ct.create_string_buffer(dw * dh * 4)
        src = VImageBuffer(ct.cast(ct.c_char_p(rgba), ct.c_void_p), sh, sw, sw * 4)
        dst = VImageBuffer(ct.addressof(scaled), dh, dw, dw * 4)
        out = VImageBuffer(ct.addressof(sharp), dh, dw, dw * 4)
        if not self._process(api, src, dst, out):
            self.disabled = True
            self.backend = "pillow"
            return None
        self.backend = "vimage"
        return Image.frombytes("RGBA", (dw, dh), sharp.raw).convert("RGB").tobytes()

    def _process(self, api, src: VImageBuffer, dst: VImageBuffer, out: VImageBuffer) -> bool:
        if api.vImageScale_ARGB8888(ct.byref(src), ct.byref(dst), None, 0) != 0:
            return False
        # Pillow SHARPEN's kernel, without Python/Pillow's per-pixel convolution.
        kernel = (ct.c_int16 * 9)(-2, -2, -2, -2, 32, -2, -2, -2, -2)
        return (
            api.vImageConvolve_ARGB8888(
                ct.byref(dst), ct.byref(out), None, 0, 0, kernel, 3, 3, 16, None, 8
            )
            == 0
        )  # kvImageEdgeExtend
