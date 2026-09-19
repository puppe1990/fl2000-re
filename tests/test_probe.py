"""EDID parsing. Pure bytes in, text out — no I2C."""

from fl2000_re.probe import _print_edid_timing, parse_edid

EDID_HEADER = bytes([0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00])


def _manufacturer_bytes(letters: str) -> bytes:
    packed = 0
    for char in letters:
        packed = (packed << 5) | (ord(char) - 64)
    return packed.to_bytes(2, "big")


def test_parse_edid_rejects_bad_header(capsys):
    parse_edid(bytes(128))
    assert "header EDID invalido" in capsys.readouterr().out


def test_parse_edid_reads_manufacturer_and_product(capsys):
    edid = bytearray(128)
    edid[0:8] = EDID_HEADER
    edid[8:10] = _manufacturer_bytes("DEL")
    edid[10:12] = (0x1234).to_bytes(2, "little")
    edid[16] = 10
    edid[17] = 30
    parse_edid(bytes(edid))
    out = capsys.readouterr().out
    assert "fabricante=DEL" in out
    assert "product=0x1234" in out
    assert "semana 10/2020" in out


def test_parse_edid_reads_monitor_name_descriptor(capsys):
    edid = bytearray(128)
    edid[0:8] = EDID_HEADER
    name = b"P2016\n"
    edid[54:72] = bytes([0, 0, 0, 0xFC, 0]) + name + b" " * (13 - len(name))
    parse_edid(bytes(edid))
    assert "P2016" in capsys.readouterr().out


def test_parse_edid_prints_timing_descriptor(capsys):
    edid = bytearray(128)
    edid[0:8] = EDID_HEADER
    edid[54:72] = _timing_descriptor(1024, 768)
    parse_edid(bytes(edid))
    assert "1024x768" in capsys.readouterr().out


def test_print_edid_timing_computes_refresh(capsys):
    _print_edid_timing(_timing_descriptor(640, 480))
    assert "640x480" in capsys.readouterr().out


def _timing_descriptor(hact: int, vact: int) -> bytes:
    """Minimal EDID detailed timing: 0x1B6D pixel clock, small blanking."""
    desc = bytearray(18)
    desc[0:2] = (0x1B6D).to_bytes(2, "little")
    desc[2] = hact & 0xFF
    desc[3] = 0x20
    desc[4] = ((hact >> 4) & 0xF0) | 0x00
    desc[5] = vact & 0xFF
    desc[6] = 0x05
    desc[7] = ((vact >> 4) & 0xF0) | 0x00
    return bytes(desc)
