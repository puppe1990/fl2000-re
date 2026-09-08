"""EP0 register access for the Fresco Logic FL2000DX (1d5c:2000)."""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from typing import Any

import usb.core

from fl2000_re.registers import (
    FL2000_USB_PID,
    FL2000_USB_VID,
    REG_I2C_CTRL,
    REG_I2C_RDATA,
    REG_I2C_WDATA,
    REQ_READ,
    REQ_WRITE,
    RT_IN,
    RT_OUT,
)


class DongleNotFoundError(FileNotFoundError):
    """USB 1d5c:2000 is not on the bus."""

    def __init__(self) -> None:
        super().__init__(
            f"FL2000 {FL2000_USB_VID:04x}:{FL2000_USB_PID:04x} nao encontrado. Plugue o HAGIBIS."
        )


class FL2000:
    def __init__(
        self,
        dev: Any | None = None,
        *,
        find_device: Callable[..., Any] = usb.core.find,
    ) -> None:
        self.dev = (
            dev
            if dev is not None
            else find_device(idVendor=FL2000_USB_VID, idProduct=FL2000_USB_PID)
        )
        if self.dev is None:
            raise DongleNotFoundError
        with contextlib.suppress(usb.core.USBError):
            self.dev.set_configuration()

    def describe(self) -> None:
        cfg = self.dev.get_active_configuration()
        speed = {1: "Low", 2: "Full", 3: "High(USB2)", 4: "Super(USB3)", 5: "Super+"}.get(
            self.dev.speed, str(self.dev.speed)
        )
        print(f"bus={self.dev.bus} addr={self.dev.address} speed={speed}")
        print(f"idVendor=0x{self.dev.idVendor:04x} idProduct=0x{self.dev.idProduct:04x}")
        for intf in cfg:
            eps = [
                f"0x{ep.bEndpointAddress:02x}/{usb.util.endpoint_type(ep.bmAttributes)}"
                f" max={ep.wMaxPacketSize}"
                for ep in intf
            ]
            print(
                f"  IF{intf.bInterfaceNumber} alt={intf.bAlternateSetting} "
                f"class=0x{intf.bInterfaceClass:02x} eps={eps or '-'}"
            )

    def reg_read(self, offset: int) -> int:
        data = self.dev.ctrl_transfer(RT_IN, REQ_READ, 0, offset, 4, timeout=2000)
        return int.from_bytes(bytes(data), "little")

    def reg_write(self, offset: int, value: int) -> None:
        self.dev.ctrl_transfer(
            RT_OUT, REQ_WRITE, 0, offset, value.to_bytes(4, "little"), timeout=2000
        )

    def bit_set(self, offset: int, bit: int) -> None:
        self.reg_write(offset, self.reg_read(offset) | (1 << bit))

    def bit_clear(self, offset: int, bit: int) -> None:
        self.reg_write(offset, self.reg_read(offset) & ~(1 << bit))

    def i2c_op(
        self, addr: int, offset: int, read: bool = True, data: int | None = None
    ) -> tuple[int | None, int]:
        if not read:
            if data is None:
                raise ValueError(f"I2C write needs data: addr=0x{addr:02X} off=0x{offset:02X}")
            self.reg_write(REG_I2C_WDATA, data)
        ctrl = self.reg_read(REG_I2C_CTRL)
        ctrl |= 0x10000000
        ctrl &= ~0x8003FFFF
        ctrl |= (addr & 0x7F) | ((1 if read else 0) << 7) | ((offset & 0xFF) << 8)
        self.reg_write(REG_I2C_CTRL, ctrl)
        time.sleep(0.003)
        for _ in range(20):
            status = self.reg_read(REG_I2C_CTRL)
            if status & 0x80000000:
                st = (status >> 24) & 0xF
                val = self.reg_read(REG_I2C_RDATA) if read else None
                return val, st
            time.sleep(0.01)
        raise TimeoutError(f"I2C timeout addr=0x{addr:02X} off=0x{offset:02X}")

    def i2c_read32(self, addr: int, offset: int) -> tuple[int | None, int]:
        return self.i2c_op(addr, offset, read=True)


def decode_status(val: int) -> str:
    bits = {
        0: "vga",
        1: "vga_err",
        2: "lbuf_halt",
        6: "pll",
        7: "dac",
        8: "lbuf_ovf",
        9: "lbuf_udf",
        26: "hdmi_evt",
        27: "hdmi",
        28: "edid",
        29: "monitor",
        30: "mon_evt",
        31: "edid_evt",
    }
    flags = [name for bit, name in bits.items() if val & (1 << bit)]
    frame = (val >> 10) & 0xFFFF
    return f"frame_cnt={frame} flags={flags or '-'}"
