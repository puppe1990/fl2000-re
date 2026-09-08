"""FL2000 EP0 helpers that do not need the dongle."""

import pytest
from fl2000_re.fl2000_usb import FL2000, DongleNotFoundError, decode_status


def test_decode_status_hdmi_flag():
    assert "hdmi" in decode_status(0x08000000)


def test_decode_status_frame_counter():
    val = 12 << 10
    text = decode_status(val)
    assert "frame_cnt=12" in text


def test_missing_dongle_names_usb_ids():
    with pytest.raises(DongleNotFoundError, match="1d5c:2000"):
        FL2000(find_device=lambda **_: None)
