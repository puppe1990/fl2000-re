"""Scale RGB888 into a canvas without stretching the Air's 3:2 into 16:9.

BOX-only collapse of 1710x1107 into 640x480 smears TUI glyphs. Letterbox into a
2x canvas with LANCZOS, then an exact 2x2 average, then unsharp so edges survive
the Dell's 1080p upscale.
"""

from PIL import Image, ImageFilter

OVERSAMPLE = 2
_UNSHARP = ImageFilter.UnsharpMask(radius=1.2, percent=180, threshold=1)


def resize_rgb888(src: bytes, src_w: int, src_h: int, dst_w: int, dst_h: int) -> bytes:
    img = Image.frombytes("RGB", (src_w, src_h), src)
    return img.resize((dst_w, dst_h), Image.Resampling.LANCZOS).tobytes()


def sharpen_downscaled_rgb(rgb: bytes, width: int, height: int) -> bytes:
    img = Image.frombytes("RGB", (width, height), rgb)
    return img.filter(_UNSHARP).tobytes()


def fit_rgb888(src: bytes, src_w: int, src_h: int, dst_w: int, dst_h: int) -> bytes:
    """Scale src into dst with black bars, keeping aspect ratio.

    Never center-crop: a 1280x960 window of the Air 1710x1107 threw the
    Grok TUI off the Dell (IMG_2117).
    """
    hi_w, hi_h = dst_w * OVERSAMPLE, dst_h * OVERSAMPLE
    hi = _letterbox_into(src, src_w, src_h, hi_w, hi_h)
    out = hi.resize((dst_w, dst_h), Image.Resampling.BOX)
    return out.filter(_UNSHARP).tobytes()


def _letterbox_into(src: bytes, src_w: int, src_h: int, dst_w: int, dst_h: int) -> Image.Image:
    src_img = Image.frombytes("RGB", (src_w, src_h), src)
    scale = min(dst_w / src_w, dst_h / src_h)
    new_w = max(1, int(src_w * scale))
    new_h = max(1, int(src_h * scale))
    fitted = src_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (dst_w, dst_h), (0, 0, 0))
    canvas.paste(fitted, ((dst_w - new_w) // 2, (dst_h - new_h) // 2))
    return canvas
