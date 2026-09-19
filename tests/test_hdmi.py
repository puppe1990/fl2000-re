"""Register programming is verified with a fake USB device; no dongle needed."""

import fl2000_re.hdmi as hdmi
from fl2000_re.fl2000_usb import FL2000
from fl2000_re.registers import (
    REG_ACLK,
    REG_PLL,
    REG_VSYNC2,
    REQ_READ,
)
from fl2000_re.video_modes import MODE_640x480, shift_vsync2


class FakeUSBDevice:
    """Minimal EP0 register file so hdmi.py can be exercised without hardware."""

    def __init__(self) -> None:
        self.regs: dict[int, int] = {}

    def set_configuration(self) -> None:
        return None

    def ctrl_transfer(self, request_type, request, value, index, data, timeout=None):
        if request == REQ_READ:
            return int(self.regs.get(index, 0)).to_bytes(4, "little")
        self.regs[index] = int.from_bytes(bytes(data), "little")
        return len(bytes(data))


def _fake_fl() -> FL2000:
    return FL2000(dev=FakeUSBDevice())


def test_program_mode_without_shift_writes_base_vsync2():
    fl = _fake_fl()
    hdmi._program_fl2000_mode_registers(fl, MODE_640x480, 0)
    assert fl.dev.regs[REG_VSYNC2] == MODE_640x480.v_sync_2


def test_program_mode_applies_v_shift_to_vsync2():
    fl = _fake_fl()
    hdmi._program_fl2000_mode_registers(fl, MODE_640x480, 8)
    assert fl.dev.regs[REG_VSYNC2] == shift_vsync2(MODE_640x480.v_sync_2, 8)


def test_program_mode_keeps_eof_zlp_bit_and_pll():
    """REG_ACLK bit 28 (EOF=ZLP) must stay set or bulk NAKs forever."""
    fl = _fake_fl()
    hdmi._program_fl2000_mode_registers(fl, MODE_640x480, 0)
    assert fl.dev.regs[REG_ACLK] & (1 << 28)
    assert fl.dev.regs[REG_PLL] == MODE_640x480.pll


def test_bring_up_hdmi_forwards_v_shift(monkeypatch):
    fl = _fake_fl()
    monkeypatch.setattr(hdmi, "cmd_detect", lambda _fl: 0)
    monkeypatch.setattr(hdmi, "_reset_and_power_ite", lambda *a, **k: None)
    monkeypatch.setattr(hdmi, "_enable_ite_video", lambda *a, **k: None)
    monkeypatch.setattr(hdmi, "_claim_bulk_alt1", lambda _fl: 0)
    assert hdmi.bring_up_hdmi(fl, MODE_640x480, v_shift=8) == 0
    assert fl.dev.regs[REG_VSYNC2] == shift_vsync2(MODE_640x480.v_sync_2, 8)


def test_bring_up_hdmi_aborts_without_it66121(monkeypatch):
    fl = _fake_fl()
    monkeypatch.setattr(hdmi, "cmd_detect", lambda _fl: 1)
    assert hdmi.bring_up_hdmi(fl, MODE_640x480) == 1
    assert REG_PLL not in fl.dev.regs
