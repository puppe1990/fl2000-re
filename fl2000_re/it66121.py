"""IT66121 HDMI transmitter on FL2000 I2C 0x4C."""

from __future__ import annotations

import time

from fl2000_re.fl2000_usb import FL2000
from fl2000_re.registers import I2C_ITE, ITE_DEVICE, ITE_VENDOR
from fl2000_re.video_modes import VideoMode


class IT66121:
    def __init__(self, fl: FL2000) -> None:
        self.fl = fl

    def read_byte(self, off: int) -> int:
        rem, al = off % 4, off & ~3
        dw, st = self.fl.i2c_read32(I2C_ITE, al)
        if st:
            raise OSError(f"ITE read 0x{off:02X} status=0x{st:X}")
        return (dw >> (rem * 8)) & 0xFF

    def write_byte(self, off: int, val: int) -> None:
        rem, al = off % 4, off & ~3
        dw, st = self.fl.i2c_read32(I2C_ITE, al)
        if st:
            raise OSError(f"ITE rmw 0x{off:02X} status=0x{st:X}")
        dw = (dw & ~(0xFF << (rem * 8))) | ((val & 0xFF) << (rem * 8))
        _, st = self.fl.i2c_op(I2C_ITE, al, read=False, data=dw)
        if st:
            raise OSError(f"ITE write 0x{off:02X} status=0x{st:X}")

    def write_dword(self, off: int, val: int) -> None:
        _, st = self.fl.i2c_op(I2C_ITE, off, read=False, data=val)
        if st:
            raise OSError(f"ITE write32 0x{off:02X} status=0x{st:X}")

    def write_masked(self, off: int, mask: int, val: int) -> None:
        self.write_byte(off, (self.read_byte(off) & ~mask) | (val & mask))

    def reset(self) -> None:
        self.write_byte(0x04, self.read_byte(0x04) | (1 << 5))
        time.sleep(0.3)

    def power_up(self) -> None:
        table = [
            (0x0F, 0x78, 0x38),
            (0x05, 0x01, 0x00),
            (0x61, 0x20, 0x00),
            (0x62, 0x44, 0x00),
            (0x64, 0x40, 0x00),
            (0x61, 0x10, 0x00),
            (0x62, 0x08, 0x08),
            (0x64, 0x04, 0x04),
            (0x6A, 0xFF, 0x70),
            (0x66, 0xFF, 0x1F),
            (0x63, 0xFF, 0x38),
            (0x0F, 0x78, 0x08),
        ]
        for reg, mask, val in table:
            if mask == 0xFF:
                self.write_byte(reg, val)
            else:
                self.write_masked(reg, mask, val)

    def hpd(self) -> tuple[bool, bool]:
        st = self.read_byte(0x0E)
        return bool(st & 0x40), bool(st & 0x20)

    def switch_bank(self, bank: int) -> None:
        self.write_byte(0x0F, bank & 1)


def it66121_is_present(fl: FL2000) -> bool:
    dword, _st = fl.i2c_read32(I2C_ITE, 0)
    if dword is None:
        return False
    vendor = dword & 0xFFFF
    device = (dword >> 16) & 0xFFF
    return vendor == ITE_VENDOR and device == ITE_DEVICE


def ite_av_mute(ite: IT66121, mute: int) -> None:
    ite.switch_bank(0)
    ite.write_masked(0xC1, 0x01, mute)
    ite.write_byte(0xC6, 0x03)


def ite_send_avi_infoframe(ite: IT66121, mode: VideoMode) -> None:
    db = [0] * 13
    db[0] = 1 << 4
    wide = mode.vic in (4, 16) or mode.width / mode.height > 1.6
    if wide:
        db[1] = 8 | (2 << 4) | (2 << 6)
    else:
        db[1] = 8 | (1 << 4) | (1 << 6)
    db[3] = mode.vic
    checksum = (0x100 - sum(db) - (0x82 + 0x02 + 13)) & 0xFF
    ite.switch_bank(1)
    ite.write_dword(0x58, db[0] | (db[1] << 8) | (db[2] << 16) | (db[3] << 24))
    ite.write_dword(0x5C, db[4] | (checksum << 8) | (db[5] << 16) | (db[6] << 24))
    ite.write_dword(0x60, db[7] | (db[8] << 8) | (db[9] << 16) | (db[10] << 24))
    ite.write_dword(0x64, db[11] | (db[12] << 8))
    ite.switch_bank(0)
    ite.write_byte(0xCD, 0x03)


def ite_enable_video(ite: IT66121, mode: VideoMode) -> None:
    high = mode.pixclk > 80_000_000
    ite.write_byte(0x04, 0x09)
    cur = ite.read_byte(0x70)
    cur &= ~((3 << 6) | (1 << 4) | (1 << 3) | (1 << 2) | (1 << 5))
    ite.write_byte(0x70, cur | 0x01)
    ite.write_masked(0x0F, 0x10, 0x10)
    cur = ite.read_byte(0x72)
    cur &= ~(3 | (1 << 5) | (1 << 7) | (1 << 6))
    ite.write_byte(0x72, cur)
    ite.write_byte(0xC0, 1)
    ite.write_byte(0x61, 0x10)
    if high:
        ite.write_masked(0x62, 0x90, 0x80)
        ite.write_masked(0x64, 0x89, 0x80)
        ite.write_masked(0x68, 0x10, 0x80)
    else:
        ite.write_masked(0x62, 0x90, 0x10)
        ite.write_masked(0x64, 0x89, 0x09)
        ite.write_masked(0x68, 0x10, 0x10)
    ite.write_masked(0x04, 0x28, 0x00)
    ite.write_byte(0x61, 0x00)
    ite.write_byte(0x04, 0x01)
    ite.write_byte(0x61, 0x00)
