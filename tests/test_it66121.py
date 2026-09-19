"""IT66121 register math with an injected fake I2C bus; no dongle needed."""

from fl2000_re.it66121 import (
    IT66121,
    it66121_is_present,
    ite_av_mute,
    ite_enable_video,
    ite_send_avi_infoframe,
)
from fl2000_re.video_modes import MODE_640x480


class FakeI2C:
    """Byte-addressed dword register file standing in for the FL2000 I2C bridge."""

    def __init__(self, mem: dict[int, int] | None = None) -> None:
        self.mem = dict(mem or {})

    def i2c_read32(self, addr: int, off: int) -> tuple[int, int]:
        return self.mem.get(off, 0), 0

    def i2c_op(self, addr: int, off: int, read: bool = True, data: int | None = None):
        if not read:
            self.mem[off] = data
            return None, 0
        return self.mem.get(off, 0), 0


def _ite(mem: dict[int, int] | None = None) -> IT66121:
    return IT66121(FakeI2C(mem))


def test_present_reads_vendor_and_device():
    assert it66121_is_present(FakeI2C({0x00: 0x16124954}))


def test_present_false_for_other_chip():
    assert not it66121_is_present(FakeI2C({0x00: 0xDEADBEEF}))


def test_present_false_when_bus_returns_nothing():
    class NoData(FakeI2C):
        def i2c_read32(self, addr: int, off: int) -> tuple[int, int]:
            return None, 0

    assert not it66121_is_present(NoData())


def test_read_byte_extracts_each_byte_of_dword():
    ite = _ite({0x00: 0x16124954})
    assert [ite.read_byte(o) for o in range(4)] == [0x54, 0x49, 0x12, 0x16]


def test_write_byte_is_read_modify_write():
    ite = _ite({0x00: 0x16124954})
    ite.write_byte(1, 0xAB)
    assert ite.fl.mem[0x00] == 0x1612AB54


def test_write_masked_keeps_other_bits():
    ite = _ite({0x00: 0xFFFFFFFF})
    ite.write_masked(0, 0x0F, 0x0A)
    assert ite.fl.mem[0x00] == 0xFFFFFFFA


def test_switch_bank_writes_register_0f():
    ite = _ite()
    ite.switch_bank(1)
    assert ite.fl.mem[0x0C] == 1 << 24


def test_hpd_and_rxsense_decode_bits():
    assert _ite({0x0C: 0x00400000}).hpd() == (True, False)
    assert _ite({0x0C: 0x00200000}).hpd() == (False, True)


def test_av_mute_sets_mute_bit():
    ite = _ite()
    ite_av_mute(ite, 1)
    assert (ite.fl.mem[0xC0] >> 8) & 0x01 == 1
    assert (ite.fl.mem[0xC4] >> 16) & 0xFF == 0x03


def test_avi_infoframe_sets_4by3_vic_packet():
    ite = _ite()
    ite_send_avi_infoframe(ite, MODE_640x480)
    assert ite.fl.mem[0x58] == 0x01005810


def test_enable_video_ends_with_output_enabled():
    ite = _ite()
    ite_enable_video(ite, MODE_640x480)
    assert ite.fl.mem[0x04] & 0xFF == 0x01


def test_reset_sets_software_reset_bit(monkeypatch):
    import fl2000_re.it66121 as itmod

    monkeypatch.setattr(itmod.time, "sleep", lambda _seconds: None)
    ite = _ite()
    ite.reset()
    assert ite.fl.mem[0x04] & (1 << 5)


def test_power_up_runs_without_error():
    ite = _ite()
    ite.power_up()
    assert ite.fl.mem
