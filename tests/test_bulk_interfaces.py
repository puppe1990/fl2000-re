"""Release every claimed video interface even if the first has disconnected."""

import types

import pytest
from fl2000_re import stream


@pytest.mark.parametrize("first_disconnected", [False, True])
def test_bulk_teardown_releases_streaming_then_control(monkeypatch, first_disconnected):
    released = []

    def release(device, interface):
        released.append(interface)
        if interface == 1 and first_disconnected:
            raise stream.usb.core.USBError("already disconnected")

    monkeypatch.setattr(stream.usb.util, "release_interface", release)
    stream.release_bulk(types.SimpleNamespace(dev=object()))
    assert released == [1, 0]
