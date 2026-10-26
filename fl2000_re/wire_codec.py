"""Exact RGB565 run grouping in the FL2000 RGB888/RLE wire format.

Protocol facts: FrescoLogic/FL2000 src/fl2000_compression.c. This independent
vectorized encoder does not use the vendor's approximate color masks: every
original RGB565 bit survives RGB888 expansion and the format's one-bit tag.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import usb.core

from fl2000_re.native_rle import optional_native_encoder
from fl2000_re.pixels import dword_swap_frame
from fl2000_re.video_modes import VideoMode, pll_pixel_clock

MAX_RUN_PIXELS = 0x800000


def wire_codec_profile(path: Path | None = None) -> str:
    """Resolve the explicit local codec; absent configuration retains raw pixels.

    Example: wire_codec_profile(Path("usb-wire-codec")) returns raw or rle24.
    """
    path = path or Path.home() / "Library/Application Support/fl2000-re/usb-wire-codec"
    value = path.read_text().strip() if path.exists() else "raw"
    if value not in ("raw", "rle24"):
        raise ValueError(f"USB wire codec={value!r}; expected raw or rle24")
    return value


class EncodedWireFrame(bytes):
    """Complete RLE payload prepared by the isolated capture child.

    Example: EncodedWireFrame(payload) bypasses RGB565 re-encoding in USB.
    """


class Rle24Encoder:
    """Cache and encode immutable complete RGB565 frames, never partial damage.

    Example: encoder.encode(packed_rgb565_frame) returns a complete wire frame.
    """

    def __init__(self, pixel_count: int) -> None:
        if not 1 <= pixel_count <= MAX_RUN_PIXELS:
            raise ValueError(f"pixel_count={pixel_count}; expected 1..{MAX_RUN_PIXELS}")
        self.pixel_count = pixel_count
        self._source: bytes | None = None
        self._wire: bytes = b""
        self._frames = 0
        self._native = optional_native_encoder(pixel_count)

    def encode(self, frame: bytes) -> bytes:
        if isinstance(frame, EncodedWireFrame):
            self._source = None
            self._wire = frame
            self._report_wire_size()
            return frame
        if len(frame) != self.pixel_count * 2:
            raise ValueError(f"RGB565 frame bytes={len(frame)}; expected {self.pixel_count * 2}")
        if frame is not self._source and frame != self._source:
            if self._native is not None:
                self._wire = self._native.encode(frame)
            else:
                pixels = np.frombuffer(dword_swap_frame(frame), dtype="<u2")
                self._wire = _encode_runs(pixels)
        self._source = frame
        self._report_wire_size()
        return self._wire

    def _report_wire_size(self) -> None:
        self._frames += 1
        if self._frames % 600 != 1:
            return
        print(
            json.dumps(
                {
                    "event": "usb_wire_compression",
                    "frame": self._frames,
                    "source_bytes": self.pixel_count * 2,
                    "encoder_backend": "native" if self._native is not None else "numpy",
                    "wire_bytes": len(self._wire),
                    "wire_to_source_ratio": round(len(self._wire) / (self.pixel_count * 2), 4),
                }
            ),
            flush=True,
        )


def _rgb565_literal_words(colors: np.ndarray) -> np.ndarray:
    colors = colors.astype(np.uint32)
    red, green, blue = colors >> 11, (colors >> 5) & 63, colors & 31
    red8, green8, blue8 = (
        (red << 3) | (red >> 2),
        (green << 2) | (green >> 4),
        (blue << 3) | (blue >> 2),
    )
    # RGB888 is BGR little-endian. Its tag consumes only an expanded red LSB;
    # all five original red bits (and all green/blue bits) are still present.
    return 0x800000 | ((red8 >> 1) << 16) | (green8 << 8) | blue8


def _encode_runs(pixels: np.ndarray) -> bytes:
    starts = np.r_[0, np.flatnonzero(pixels[1:] != pixels[:-1]) + 1]
    counts = np.diff(np.r_[starts, pixels.size])
    repeated = counts > 1
    positions = np.arange(starts.size) + np.r_[0, np.cumsum(repeated[:-1])]
    final_literal = counts[-1] > 2
    tokens = np.empty(starts.size + np.count_nonzero(repeated) + final_literal, dtype=np.uint32)
    literals = _rgb565_literal_words(pixels[starts])
    tokens[positions] = literals
    # A two-pixel run costs the same as two literals. Avoid a trailing count=1,
    # matching the vendor's explicit final-run handling without a lossy mask.
    tokens[positions[repeated] + 1] = np.where(
        counts[repeated] == 2, literals[repeated], counts[repeated] - 1
    )
    # Fresco documents a hardware bug: the compressed raster MUST end with a
    # literal pixel, never a repeat count. Keep the last pixel outside its run.
    if final_literal:
        tokens[-2], tokens[-1] = counts[-1] - 2, literals[-1]
    octets = ((tokens[:, None] >> np.array([0, 8, 16])) & 255).astype(np.uint8).tobytes()
    # The vendor pads to dword alignment with 0x80; the decoder stops at the
    # configured raster size. Preserve the ordinary FL2000 64-bit lane order.
    return dword_swap_frame(octets + b"\x80" * (-len(octets) % 4))


def make_wire_encoder(device: object, pixel_count: int, bpp: int) -> Rle24Encoder | None:
    """Select only for real USB devices and an explicit local opt-in.

    Example: make_wire_encoder(fl.dev, 1024 * 768, 2)
    """
    if not isinstance(device, usb.core.Device) or wire_codec_profile() == "raw":
        return None
    if bpp != 2:
        raise ValueError(f"source bpp={bpp}; expected 2 (RGB565) for rle24")
    return Rle24Encoder(pixel_count)


def rle_frame_period_s(mode: VideoMode) -> float:
    """Pace complete compressed frames to the programmed raster, not 5% ahead.

    Tiny compressed frames can fit entirely in the device queue; the raw feed
    lead would accumulate old images. Example: rle_frame_period_s(MODE_1024x768).
    """
    htotal, vtotal = mode.h_sync_1 & 0xFFF, mode.v_sync_1 & 0xFFF
    return htotal * vtotal / pll_pixel_clock(mode.pll)
