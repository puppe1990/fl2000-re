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

    When the source covers at least 2x the dest, center-crop to 2x and average
    2x2 blocks so TUI pixels sit on the HDMI grid instead of a 2.67x smear.
    """
    hi_w, hi_h = dst_w * OVERSAMPLE, dst_h * OVERSAMPLE
    if _source_is_about_2x_dest(src_w, src_h, dst_w, dst_h):
        hi = _center_crop(src, src_w, src_h, hi_w, hi_h)
    else:
        hi = _letterbox_into(src, src_w, src_h, hi_w, hi_h)
    out = hi.resize((dst_w, dst_h), Image.Resampling.BOX)
    return out.filter(_UNSHARP).tobytes()


def _source_is_about_2x_dest(src_w: int, src_h: int, dst_w: int, dst_h: int) -> bool:
    """Crop only for ~2x sources. Retina 5x (3420x2214) must letterbox the whole screen."""
    hi_w, hi_h = dst_w * OVERSAMPLE, dst_h * OVERSAMPLE
    if src_w < hi_w or src_h < hi_h:
        return False
    return src_w < 3 * dst_w and src_h < 3 * dst_h


def _center_crop(src: bytes, src_w: int, src_h: int, crop_w: int, crop_h: int) -> Image.Image:
    img = Image.frombytes("RGB", (src_w, src_h), src)
    left = (src_w - crop_w) // 2
    top = (src_h - crop_h) // 2
    return img.crop((left, top, left + crop_w, top + crop_h))


def _letterbox_into(src: bytes, src_w: int, src_h: int, dst_w: int, dst_h: int) -> Image.Image:
    src_img = Image.frombytes("RGB", (src_w, src_h), src)
    scale = min(dst_w / src_w, dst_h / src_h)
    new_w = max(1, int(src_w * scale))
    new_h = max(1, int(src_h * scale))
    fitted = src_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (dst_w, dst_h), (0, 0, 0))
    canvas.paste(fitted, ((dst_w - new_w) // 2, (dst_h - new_h) // 2))
    return canvas
