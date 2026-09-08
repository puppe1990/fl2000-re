"""Pack RGB888 into the FL2000 bulk wire formats (RGB565 / RGB332 + dword swap)."""


def rgb888_to_rgb565(rgb: bytes) -> bytes:
    if len(rgb) % 3:
        raise ValueError(f"rgb888 length must be a multiple of 3, got {len(rgb)}")
    try:
        import numpy as np

        a = np.frombuffer(rgb, dtype=np.uint8).reshape(-1, 3).astype(np.uint16)
        px = ((a[:, 0] & 0xF8) << 8) | ((a[:, 1] & 0xFC) << 3) | (a[:, 2] >> 3)
        return px.astype("<u2").tobytes()
    except ImportError:
        n = len(rgb) // 3
        out = bytearray(n * 2)
        src = memoryview(rgb)
        dst = memoryview(out)
        oi = 0
        for i in range(0, n * 3, 3):
            px = ((src[i] & 0xF8) << 8) | ((src[i + 1] & 0xFC) << 3) | (src[i + 2] >> 3)
            dst[oi] = px & 0xFF
            dst[oi + 1] = px >> 8
            oi += 2
        return bytes(out)


def rgb888_to_rgb332(rgb: bytes) -> bytes:
    if len(rgb) % 3:
        raise ValueError(f"rgb888 length must be a multiple of 3, got {len(rgb)}")
    try:
        import numpy as np

        a = np.frombuffer(rgb, dtype=np.uint8).reshape(-1, 3)
        out = (a[:, 0] & 0xE0) | ((a[:, 1] >> 3) & 0x1C) | (a[:, 2] >> 6)
        return out.astype(np.uint8).tobytes()
    except ImportError:
        n = len(rgb) // 3
        out = bytearray(n)
        src = memoryview(rgb)
        for i in range(n):
            j = i * 3
            out[i] = (src[j] & 0xE0) | ((src[j + 1] >> 3) & 0x1C) | (src[j + 2] >> 6)
        return bytes(out)


def dword_swap_frame(buf: bytes) -> bytes:
    """FL2000 bulk stream is 64-bit words with 32-bit halves reversed."""
    try:
        import numpy as np

        n = (len(buf) // 8) * 8
        if n:
            words = np.frombuffer(buf[:n], dtype=np.uint32).reshape(-1, 2)[:, ::-1]
            swapped = words.reshape(-1).tobytes()
        else:
            swapped = b""
        return swapped + buf[n:]
    except ImportError:
        out = bytearray(len(buf))
        for i in range(0, len(buf) - 7, 8):
            out[i : i + 4] = buf[i + 4 : i + 8]
            out[i + 4 : i + 8] = buf[i : i + 4]
        rem = len(buf) % 8
        if rem:
            out[-rem:] = buf[-rem:]
        return bytes(out)


def pack_frame(rgb: bytes, bpp: int) -> bytes:
    packed = rgb888_to_rgb332(rgb) if bpp == 1 else rgb888_to_rgb565(rgb)
    return dword_swap_frame(packed)


def make_bars_rgb565(width: int, height: int) -> bytes:
    colors = [0xFFFF, 0xFFE0, 0x07FF, 0x07E0, 0xF81F, 0xF800, 0x001F, 0x0000]
    bar_w = max(1, width // len(colors))
    row = bytearray()
    for x in range(width):
        c = colors[min(x // bar_w, len(colors) - 1)]
        row += c.to_bytes(2, "little")
    return bytes(row) * height


def needs_zlp(nbytes: int, max_packet: int = 512) -> bool:
    """USB bulk EOF: a max-packet-aligned payload needs an explicit zero-length packet."""
    return nbytes > 0 and nbytes % max_packet == 0
