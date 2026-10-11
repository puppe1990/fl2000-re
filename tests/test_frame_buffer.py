"""Shared frame buffer. No USB, no WindowServer: the buffer contract only."""

import time

import pytest
from fl2000_re.frame_buffer import new_shared_frame, write_shared_frame

FRAME_BYTES = 640 * 480 * 2
# A c_ubyte slice assign walks every element in Python (~22 ms for one RGB565
# frame, measured 2026-10-11); memmove is ~0.015 ms. The budget is 4x under the
# bug and ~350x over the fix, so a loaded CI runner cannot flake it.
FRAME_WRITE_BUDGET_MS = 5.0


def test_new_shared_frame_holds_the_seed_frame():
    buf = new_shared_frame(bytes([7, 8, 9]))
    assert len(buf) == 3
    assert bytes(buf) == bytes([7, 8, 9])


def test_write_shared_frame_copies_every_byte_in_order():
    buf = new_shared_frame(8)
    write_shared_frame(buf, b"\x01\x02\x03\x04")
    assert bytes(buf) == bytes([1, 2, 3, 4, 0, 0, 0, 0])


def test_write_shared_frame_rejects_a_frame_larger_than_the_buffer():
    buf = new_shared_frame(4)
    with pytest.raises(ValueError, match="8 bytes"):
        write_shared_frame(buf, bytes(8))


def test_write_shared_frame_is_a_memcpy_not_a_python_loop():
    """Regression 2026-10-11: buf[:n] = packed cost 22 ms/frame and capped extend at 24 fps."""
    buf = new_shared_frame(FRAME_BYTES)
    payload = bytes(range(256)) * (FRAME_BYTES // 256)
    write_shared_frame(buf, payload)
    started = time.perf_counter()
    write_shared_frame(buf, payload)
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert elapsed_ms < FRAME_WRITE_BUDGET_MS, (
        f"escrever {FRAME_BYTES} bytes levou {elapsed_ms:.1f} ms (teto {FRAME_WRITE_BUDGET_MS} ms): "
        f"isso limita a captura a {1000 / elapsed_ms:.0f} fps"
    )
