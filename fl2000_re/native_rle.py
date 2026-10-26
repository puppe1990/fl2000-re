"""Optional exact native codec; compilation and allocation stay outside USB ticks."""

from __future__ import annotations

import ctypes
import functools
import hashlib
import os
import platform
import subprocess
import sys
from pathlib import Path


@functools.lru_cache(maxsize=1)
def _native_function():
    source = Path(__file__).resolve().parent.parent / "native" / "rle24.c"
    digest = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
    cache = Path.home() / ".cache" / "fl2000-re"
    suffix = ".dylib" if sys.platform == "darwin" else ".so"
    library = cache / f"rle24-{platform.machine()}-{digest}{suffix}"
    if not library.exists():
        _compile_codec(source, library)
    function = ctypes.CDLL(str(library)).fl2000_encode_rle24
    function.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t]
    function.restype = ctypes.c_size_t
    return function


def _compile_codec(source: Path, library: Path) -> None:
    library.parent.mkdir(parents=True, exist_ok=True)
    temporary = library.with_suffix(f".{os.getpid()}{library.suffix}")
    flags = ["-dynamiclib"] if sys.platform == "darwin" else ["-shared", "-fPIC"]
    try:
        subprocess.run(
            ["cc", *flags, "-O3", "-std=c11", str(source), "-o", str(temporary)],
            capture_output=True,
            check=True,
            timeout=20,
        )
        temporary.replace(library)
    finally:
        temporary.unlink(missing_ok=True)


class NativeRle24:
    """Reuse one worst-case output allocation for exact complete frames.

    Example: NativeRle24(1024 * 768).encode(packed_rgb565)
    """

    def __init__(self, pixel_count: int) -> None:
        if not 1 <= pixel_count <= 0x800000:
            raise ValueError(f"pixel_count={pixel_count}; expected 1..8388608")
        self.pixel_count = pixel_count
        self.function = _native_function()
        self.output = ctypes.create_string_buffer(pixel_count * 3 + 4)

    def encode(self, frame: bytes) -> bytes:
        if len(frame) != self.pixel_count * 2:
            raise ValueError(f"frame bytes={len(frame)}; expected {self.pixel_count * 2}")
        length = self.function(frame, self.pixel_count, self.output, len(self.output))
        if not 0 < length <= len(self.output):
            raise RuntimeError(f"native RLE output length={length}; expected 1..{len(self.output)}")
        return ctypes.string_at(self.output, length)


def optional_native_encoder(pixel_count: int) -> NativeRle24 | None:
    """Keep the portable reference codec if the local compiler is unavailable.

    Example: optional_native_encoder(1024 * 768) is None without a compiler.
    """
    try:
        return NativeRle24(pixel_count)
    except (OSError, subprocess.SubprocessError):
        return None
