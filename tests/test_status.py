from hagibis_re import decode_status


def test_decode_status_hdmi_flag():
    # bit 27 = hdmi
    assert "hdmi" in decode_status(0x08000000)


def test_decode_status_frame_counter():
    val = 12 << 10
    text = decode_status(val)
    assert "frame_cnt=12" in text
