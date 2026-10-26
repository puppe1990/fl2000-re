"""Independent RLE decode checks every original RGB565 color and EOF boundaries."""

import types

import numpy as np
import pytest
from fl2000_re import bulk_transfer
from fl2000_re import wire_codec as codec
from fl2000_re.fl2000_usb import FL2000
from fl2000_re.pixels import dword_swap_frame
from fl2000_re.video_modes import MODE_1024x768, pll_pixel_clock


def decode_rgb565(wire, pixel_count):
    logical = dword_swap_frame(wire)
    result, previous, offset = [], None, 0
    while len(result) < pixel_count:
        token = int.from_bytes(logical[offset : offset + 3], "little")
        offset += 3
        if token & 0x800000:
            red, green, blue = ((token >> 16) & 127) << 1, (token >> 8) & 255, token & 255
            previous = ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3)
            result.append(previous)
        else:
            assert previous is not None and token > 0
            result.extend([previous] * token)
        assert len(result) <= pixel_count
    return np.asarray(result, dtype="<u2").tobytes()


def encode_values(values):
    raw = np.asarray(values, dtype="<u2").tobytes()
    encoder = codec.Rle24Encoder(len(values))
    return encoder.encode(dword_swap_frame(raw)), raw


def test_every_rgb565_color_roundtrips_without_losing_source_bits():
    wire, raw = encode_values(np.arange(65536, dtype=np.uint16))
    assert decode_rgb565(wire, 65536) == raw


@pytest.mark.parametrize("count", [1, 2, 3, 7, 8, 15, 1024])
def test_exact_runs_and_dword_padding_roundtrip(count):
    values = np.random.default_rng(7).choice([0, 0xFFFF, 0xF800, 0x7E0, 0x1F, 0x1234], count)
    wire, raw = encode_values(values)
    assert len(wire) % 4 == 0
    assert decode_rgb565(wire, count) == raw


def test_static_xga_frame_compresses_to_one_pixel_and_count():
    encoder = codec.Rle24Encoder(1024 * 768)
    wire = encoder.encode(bytes(1024 * 768 * 2))
    logical = dword_swap_frame(wire)
    assert len(wire) == 8
    assert logical[:3] == b"\x00\x00\x80"
    assert int.from_bytes(logical[3:6], "little") == 1024 * 768 - 1
    assert decode_rgb565(wire, 1024 * 768) == bytes(1024 * 768 * 2)


def test_repeat_count_is_not_truncated_to_16_bits():
    wire, raw = encode_values(np.full(65537, 0x1234, dtype=np.uint16))
    assert int.from_bytes(dword_swap_frame(wire)[3:6], "little") == 65536
    assert decode_rgb565(wire, 65537) == raw


def test_two_pixel_final_run_uses_two_literals():
    wire, raw = encode_values([0xF800, 0xF800])
    logical = dword_swap_frame(wire)
    assert logical[:3] == logical[3:6]
    assert decode_rgb565(wire, 2) == raw


def test_identical_frame_reuses_immutable_encoded_payload(monkeypatch):
    encoder = codec.Rle24Encoder(4)
    first = encoder.encode(bytes(8))
    monkeypatch.setattr(codec, "_encode_runs", lambda *a: pytest.fail("reencoded identical pixels"))
    assert encoder.encode(bytes(bytearray(8))) is first


def test_invalid_pixel_count_and_incomplete_frame_are_rejected():
    for count in (0, -1, codec.MAX_RUN_PIXELS + 1):
        with pytest.raises(ValueError, match="expected 1"):
            codec.Rle24Encoder(count)
    with pytest.raises(ValueError, match="expected 8"):
        codec.Rle24Encoder(4).encode(bytes(6))


def test_bulk_validation_uses_encoded_bytes_and_still_sends_explicit_eof():
    packets = []
    device = types.SimpleNamespace(
        write=lambda ep, frame, timeout: packets.append(frame) or len(frame)
    )
    fl = FL2000(dev=device, configure=False)
    fl.wire_encoder = codec.Rle24Encoder(1024 * 768)
    bulk_transfer.send_frame(fl, bytes(1024 * 768 * 2))
    assert len(packets[0]) == 8
    assert packets[1] == b""


def test_profile_is_explicit_and_fake_devices_keep_raw(tmp_path, monkeypatch):
    profile = tmp_path / "codec"
    assert codec.wire_codec_profile(profile) == "raw"
    profile.write_text("rle24")
    assert codec.wire_codec_profile(profile) == "rle24"
    profile.write_text("unknown")
    with pytest.raises(ValueError, match="expected raw or rle24"):
        codec.wire_codec_profile(profile)
    monkeypatch.setattr(codec, "wire_codec_profile", lambda: "rle24")
    assert codec.make_wire_encoder(object(), 4, 2) is None


def test_real_backend_selection_requires_rgb565_source(monkeypatch):
    class Device:
        pass

    monkeypatch.setattr(codec.usb.core, "Device", Device)
    monkeypatch.setattr(codec, "wire_codec_profile", lambda: "rle24")
    assert isinstance(codec.make_wire_encoder(Device(), 4, 2), codec.Rle24Encoder)
    with pytest.raises(ValueError, match="expected 2"):
        codec.make_wire_encoder(Device(), 4, 1)


def test_compressed_feed_tracks_real_pll_instead_of_raw_headroom():
    mode = MODE_1024x768
    expected = (mode.h_sync_1 & 0xFFF) * (mode.v_sync_1 & 0xFFF) / pll_pixel_clock(mode.pll)
    assert codec.rle_frame_period_s(mode) == pytest.approx(expected)
    assert 1 / codec.rle_frame_period_s(mode) == pytest.approx(60.0038402)


def test_compression_telemetry_is_bounded_and_reports_actual_wire_size(capsys):
    import json

    encoder = codec.Rle24Encoder(1024 * 768)
    for _ in range(601):
        encoder.encode(bytes(1024 * 768 * 2))
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [event["frame"] for event in events] == [1, 601]
    assert all(event["wire_bytes"] == 8 for event in events)
    assert all(event["source_bytes"] == 1572864 for event in events)
