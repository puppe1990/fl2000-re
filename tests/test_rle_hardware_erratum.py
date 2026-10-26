"""Fresco chip erratum: the last decoded token must be a literal pixel.

Source: FrescoLogic/FL2000 src/fl2000_compression.c final-run termination.
A generic lossless roundtrip alone cannot detect this hardware restriction.
"""

import shutil

import numpy as np
import pytest
from fl2000_re.pixels import dword_swap_frame
from fl2000_re.wire_codec import Rle24Encoder


def assert_final_literal(wire: bytes, pixel_count: int) -> None:
    logical = dword_swap_frame(wire)
    decoded, offset, last = 0, 0, 0
    while decoded < pixel_count:
        last = int.from_bytes(logical[offset : offset + 3], "little")
        decoded += 1 if last & 0x800000 else last
        offset += 3
        assert decoded <= pixel_count
    assert last & 0x800000, "chip erratum: final RLE token must be literal"


@pytest.mark.parametrize("backend", ["native", "numpy"])
@pytest.mark.parametrize("tail", [1, 2, 3, 4, 7, 32, 65537, 786432])
def test_every_terminal_run_reserves_a_final_literal(backend, tail):
    if backend == "native" and shutil.which("cc") is None:
        pytest.skip("native compiler unavailable")
    pixels = np.r_[np.array([0xF800, 0x07E0], dtype="<u2"), np.zeros(tail, dtype="<u2")]
    encoder = Rle24Encoder(len(pixels))
    if backend == "numpy":
        encoder._native = None
    else:
        assert encoder._native is not None
    wire = encoder.encode(dword_swap_frame(pixels.tobytes()))
    assert_final_literal(wire, len(pixels))


@pytest.mark.parametrize("native", [False, True])
def test_final_run_matches_vendor_erratum_golden_tokens(native):
    if native and shutil.which("cc") is None:
        pytest.skip("native compiler unavailable")
    encoder = Rle24Encoder(4)
    if not native:
        encoder._native = None
    # black literal, two extra copies, final black literal, dword padding.
    expected = bytes.fromhex("000080 020000 000080 808080")
    assert dword_swap_frame(encoder.encode(bytes(8))) == expected
