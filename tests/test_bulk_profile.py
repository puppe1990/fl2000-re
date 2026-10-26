"""Burst selection never guesses unsupported endpoint capabilities."""

import types

import pytest
from fl2000_re.bulk_profile import has_bulk_alternate, stream_bulk_alternate


class Alternate(list):
    bInterfaceNumber, bAlternateSetting = 1, 0


def device(companion=b"\x06\x30\x0f\x00\x00\x00", speed=4):
    endpoint = types.SimpleNamespace(
        bEndpointAddress=1, bmAttributes=2, extra_descriptors=companion
    )
    return types.SimpleNamespace(
        speed=speed, get_active_configuration=lambda: [Alternate([endpoint])]
    )


def test_burst_profile_defaults_single_and_requires_explicit_valid_descriptor(tmp_path):
    path = tmp_path / "profile"
    assert stream_bulk_alternate(device(), path) == 1
    path.write_text("burst16")
    assert stream_bulk_alternate(device(), path) == 0
    for companion in (b"", b"\x06\x30\x0f", b"\x06\x30\x00\x00\x00\x00"):
        with pytest.raises(ValueError, match="bMaxBurst=15"):
            stream_bulk_alternate(device(companion), path)
    assert not has_bulk_alternate(device(speed=3), 0, 15)
    path.write_text("invalid")
    with pytest.raises(ValueError, match="single or burst16"):
        stream_bulk_alternate(device(), path)
