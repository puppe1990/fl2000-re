"""Cross-process frame buffer: the capture child publishes, the USB pump reads.

The buffer is a lock-free ctypes array in shared memory, so a frame copy must be
a memcpy: `buf[:n] = packed` walks a 614400-byte RGB565 frame element by element
in Python (22 ms, 2026-10-11) and capped the capture child at 24 fps.
"""

from __future__ import annotations

import ctypes
import multiprocessing as mp


def new_shared_frame(seed: bytes):
    """Lock-free shared buffer holding seed until the child publishes a frame.

    The pump sends the buffer from the first tick, so seeding it with the preview
    frame keeps the HDMI from flashing black before the capture child comes up.

    Example: new_shared_frame(bytes(640 * 480 * 2))
    """
    return mp.Array("B", seed, lock=False)


def write_shared_frame(buf, packed: bytes) -> None:
    """Publish a packed frame into the shared buffer as one memcpy.

    Example: write_shared_frame(buf, pack_frame(rgb, 2))
    """
    if len(packed) > len(buf):
        raise ValueError(f"packed frame is {len(packed)} bytes, buffer holds {len(buf)}")
    ctypes.memmove(buf, packed, len(packed))
