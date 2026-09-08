"""Scale RGB888 into a canvas without stretching the Air's 3:2 into 16:9."""


def resize_rgb888(src: bytes, src_w: int, src_h: int, dst_w: int, dst_h: int) -> bytes:
    from PIL import Image

    img = Image.frombytes("RGB", (src_w, src_h), src)
    return img.resize((dst_w, dst_h), Image.Resampling.BOX).tobytes()


def fit_rgb888(src: bytes, src_w: int, src_h: int, dst_w: int, dst_h: int) -> bytes:
    """Scale src into dst with black bars, keeping aspect ratio."""
    from PIL import Image

    src_img = Image.frombytes("RGB", (src_w, src_h), src)
    scale = min(dst_w / src_w, dst_h / src_h)
    new_w = max(1, int(src_w * scale))
    new_h = max(1, int(src_h * scale))
    fitted = src_img.resize((new_w, new_h), Image.Resampling.BOX)
    canvas = Image.new("RGB", (dst_w, dst_h), (0, 0, 0))
    canvas.paste(fitted, ((dst_w - new_w) // 2, (dst_h - new_h) // 2))
    return canvas.tobytes()
