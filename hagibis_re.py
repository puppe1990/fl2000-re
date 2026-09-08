#!/usr/bin/env python3
"""Reverse-engineering probe for the Hagibis USB Display Adapter.

Chip: Fresco Logic FL2000DX (USB 1d5c:2000)
Protocol: vendor EP0 register R/W (bRequest 64/65) documented by the
official GPL Linux driver (FrescoLogic/FL2000) and klogg/fl2000_drm.

This talks to the dongle from userspace. It is NOT a macOS display driver:
it cannot create an extended desktop. Goal is to prove we can read chip
state, detect the HDMI transmitter, read EDID, and optionally push a
640x480 test pattern over USB bulk.

Usage:
  .venv/bin/python hagibis_re.py dump
  .venv/bin/python hagibis_re.py detect
  .venv/bin/python hagibis_re.py edid
  .venv/bin/python hagibis_re.py bars [--seconds 8]
  .venv/bin/python hagibis_re.py mirror [--seconds 0]
"""

from __future__ import annotations

import argparse
import contextlib
import multiprocessing as mp
import sys
import time
from dataclasses import dataclass

import usb.core
import usb.util

VID, PID = 0x1D5C, 0x2000
REQ_READ, REQ_WRITE = 64, 65
RT_IN, RT_OUT = 0xC0, 0x40

REG_STATUS = 0x8000
REG_PXCLK = 0x8004
REG_HSYNC1, REG_HSYNC2 = 0x8008, 0x800C
REG_VSYNC1, REG_VSYNC2 = 0x8010, 0x8014
REG_ISOCH = 0x801C
REG_I2C_CTRL, REG_I2C_RDATA, REG_I2C_WDATA = 0x8020, 0x8024, 0x8028
REG_PLL = 0x802C
REG_ACLK = 0x803C
REG_RST = 0x8048
REG_CTRL3 = 0x8088
REG_USB_LPM = 0x0070
REG_USB_CTRL = 0x0078

I2C_ITE, I2C_DDC = 0x4C, 0x50
ITE_VENDOR, ITE_DEVICE = 0x4954, 0x612
BULK_EP = 0x01

DUMP_REGS = [0x0070, 0x0078] + list(range(0x8000, 0x8090, 4))


class FL2000:
    def __init__(self) -> None:
        self.dev = usb.core.find(idVendor=VID, idProduct=PID)
        if self.dev is None:
            sys.exit("FL2000 1d5c:2000 nao encontrado. Plugue o HAGIBIS.")
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

    def i2c_op(self, addr: int, offset: int, read: bool = True, data: int | None = None):
        if not read:
            assert data is not None
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

    def i2c_read32(self, addr: int, offset: int):
        return self.i2c_op(addr, offset, read=True)


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


def cmd_dump(fl: FL2000) -> None:
    print("== USB layout ==")
    fl.describe()
    print("== registers ==")
    for off in DUMP_REGS:
        try:
            val = fl.reg_read(off)
        except usb.core.USBError as exc:
            print(f"  [0x{off:04X}] ERROR {exc}")
            continue
        extra = ""
        if off == REG_STATUS:
            extra = "  " + decode_status(val)
        print(f"  [0x{off:04X}] = 0x{val:08X}{extra}")


def cmd_detect(fl: FL2000) -> int:
    print("== I2C scan: IT66121 @ 0x4C ==")
    try:
        dword, st = fl.i2c_read32(I2C_ITE, 0)
    except (TimeoutError, usb.core.USBError) as exc:
        print(f"  timeout/erro: {exc}")
        return 1
    vendor = dword & 0xFFFF
    device = (dword >> 16) & 0xFFF
    rev = (dword >> 28) & 0xF
    print(f"  raw=0x{dword:08X} byte_status=0x{st:X}")
    print(f"  vendor=0x{vendor:04X} device=0x{device:03X} rev={rev}")
    if vendor == ITE_VENDOR and device == ITE_DEVICE:
        print("  IT66121 encontrado (HDMI transmitter)")
        return 0
    print("  nao e IT66121 — tentando DDC direto @ 0x50 (VGA)")
    try:
        d0, st0 = fl.i2c_read32(I2C_DDC, 0)
        print(f"  DDC dword0=0x{d0:08X} status=0x{st0:X}")
    except Exception as exc:
        print(f"  DDC falhou: {exc}")
    return 1


def parse_edid(edid: bytes) -> None:
    if edid[:8] != bytes([0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00]):
        print("  header EDID invalido")
        return
    mfg = int.from_bytes(edid[8:10], "big")
    name = "".join(chr(((mfg >> s) & 0x1F) + 64) for s in (10, 5, 0))
    product = int.from_bytes(edid[10:12], "little")
    week, year = edid[16], 1990 + edid[17]
    print(f"  fabricante={name} product=0x{product:04X} semana {week}/{year}")
    for d in range(54, 126, 18):
        desc = edid[d : d + 18]
        if desc[0] == desc[1] == 0 and desc[3] == 0xFC:
            print(f"  nome: {desc[5:18].decode(errors='replace').strip()}")
        elif desc[0] or desc[1]:
            pixclk = int.from_bytes(desc[0:2], "little") * 10_000
            hact = desc[2] | ((desc[4] & 0xF0) << 4)
            vact = desc[5] | ((desc[7] & 0xF0) << 4)
            hblank = desc[3] | ((desc[4] & 0x0F) << 8)
            vblank = desc[6] | ((desc[7] & 0x0F) << 8)
            refresh = pixclk / ((hact + hblank) * (vact + vblank)) if hact else 0
            print(f"  timing: {hact}x{vact} @ {refresh:.1f} Hz ({pixclk / 1e6:.2f} MHz)")


def cmd_edid(fl: FL2000) -> int:
    print("== EDID ==")
    if cmd_detect(fl) != 0:
        return 1
    ite = IT66121(fl)
    print("  reset + power-up IT66121")
    ite.reset()
    ite.power_up()
    hpd, rx = ite.hpd()
    print(f"  HPD={'sim' if hpd else 'NAO'} RxSense={'sim' if rx else 'nao'}")
    if not hpd:
        print("  nenhum monitor no HDMI do HAGIBIS")
        return 1
    # Direct DDC 0x50 as a simpler first EDID path (4-byte chunks).
    print("  tentando EDID via I2C 0x50 em dwords")
    blob = bytearray()
    try:
        for off in range(0, 128, 4):
            dw, st = fl.i2c_read32(I2C_DDC, off)
            blob += dw.to_bytes(4, "little")
            if st:
                print(f"  warn offset {off}: status=0x{st:X}")
    except TimeoutError as exc:
        print(f"  DDC direto falhou ({exc}); HPD existe mas DDC nao passa pelo FL2000")
        print("  (em dongles HDMI o DDC vai pro IT66121, nao ao 0x50)")
        return 1
    path = "edid.bin"
    with open(path, "wb") as fh:
        fh.write(blob)
    checksum = "ok" if sum(blob) % 256 == 0 else "BAD"
    print(f"  gravou {path} ({len(blob)} bytes) checksum={checksum}")
    parse_edid(bytes(blob))
    return 0


def rgb888_to_rgb565(rgb: bytes) -> bytes:
    if len(rgb) % 3:
        raise ValueError("rgb888 length must be a multiple of 3")
    try:
        import numpy as np

        a = np.frombuffer(rgb, dtype=np.uint8).reshape(-1, 3).astype(np.uint16)
        px = ((a[:, 0] & 0xF8) << 8) | ((a[:, 1] & 0xFC) << 3) | (a[:, 2] >> 3)
        return px.astype("<u2").tobytes()
    except ImportError:
        n = len(rgb) // 3
        out = bytearray(n * 2)
        src = memoryview(rgb)
        dst = memoryview(out)
        oi = 0
        for i in range(0, n * 3, 3):
            px = ((src[i] & 0xF8) << 8) | ((src[i + 1] & 0xFC) << 3) | (src[i + 2] >> 3)
            dst[oi] = px & 0xFF
            dst[oi + 1] = px >> 8
            oi += 2
        return bytes(out)


def rgb888_to_rgb332(rgb: bytes) -> bytes:
    if len(rgb) % 3:
        raise ValueError("rgb888 length must be a multiple of 3")
    try:
        import numpy as np

        a = np.frombuffer(rgb, dtype=np.uint8).reshape(-1, 3)
        out = (a[:, 0] & 0xE0) | ((a[:, 1] >> 3) & 0x1C) | (a[:, 2] >> 6)
        return out.astype(np.uint8).tobytes()
    except ImportError:
        n = len(rgb) // 3
        out = bytearray(n)
        src = memoryview(rgb)
        for i in range(n):
            j = i * 3
            out[i] = (src[j] & 0xE0) | ((src[j + 1] >> 3) & 0x1C) | (src[j + 2] >> 6)
        return bytes(out)


def pack_frame(rgb: bytes, bpp: int) -> bytes:
    packed = rgb888_to_rgb332(rgb) if bpp == 1 else rgb888_to_rgb565(rgb)
    return dword_swap_frame(packed)


def make_bars(width: int, height: int) -> bytes:
    colors = [0xFFFF, 0xFFE0, 0x07FF, 0x07E0, 0xF81F, 0xF800, 0x001F, 0x0000]
    bar_w = max(1, width // len(colors))
    row = bytearray()
    for x in range(width):
        c = colors[min(x // bar_w, len(colors) - 1)]
        row += c.to_bytes(2, "little")
    return bytes(row) * height


def dword_swap_frame(buf: bytes) -> bytes:
    """FL2000 bulk stream is 64-bit words with 32-bit halves reversed."""
    try:
        import numpy as np

        n = (len(buf) // 8) * 8
        if n:
            words = np.frombuffer(buf[:n], dtype=np.uint32).reshape(-1, 2)[:, ::-1]
            swapped = words.reshape(-1).tobytes()
        else:
            swapped = b""
        return swapped + buf[n:]
    except ImportError:
        out = bytearray(len(buf))
        for i in range(0, len(buf) - 7, 8):
            out[i : i + 4] = buf[i + 4 : i + 8]
            out[i + 4 : i + 8] = buf[i : i + 4]
        rem = len(buf) % 8
        if rem:
            out[-rem:] = buf[-rem:]
        return bytes(out)


def needs_zlp(nbytes: int, max_packet: int = 512) -> bool:
    """USB bulk EOF: a max-packet-aligned payload needs an explicit zero-length packet."""
    return nbytes > 0 and nbytes % max_packet == 0


@dataclass(frozen=True)
class VideoMode:
    width: int
    height: int
    freq: int
    h_sync_1: int
    h_sync_2: int
    v_sync_1: int
    v_sync_2: int
    pll: int
    vic: int
    pixclk: int
    bpp: int = 2


# 640x480 RGB565 @ 60 Hz was measured at a full 60 fps on this dongle (~37 MB/s).
USB2_BUDGET_BPS = 40_000_000


def fits_usb2(
    width: int,
    height: int,
    fps: float = 60,
    bpp: int = 2,
    budget: int = USB2_BUDGET_BPS,
) -> bool:
    """True if RGB frames at fps fit the measured USB 2.0 bulk budget (~32 MB/s)."""
    return width * height * bpp * fps <= budget


def pll_pixel_clock(pll: int) -> int:
    """FL2000 VGA PLL: 10 MHz XTAL * multiplier / (prescaler * divisor)."""
    divisor = pll & 0xFF
    prescaler = (pll >> 8) & 0x3
    multiplier = (pll >> 16) & 0xFF
    return 10_000_000 * multiplier // (prescaler * divisor)


def pxclk_color_bit(bpp: int) -> int:
    """REG_PXCLK: bit 25 = RGB332, bit 6 = RGB565."""
    return 25 if bpp == 1 else 6


def encode_hsync1(hactive: int, htotal: int) -> int:
    return (hactive << 16) | htotal


def encode_vsync1(vactive: int, vtotal: int) -> int:
    return (vactive << 16) | vtotal


MODE_640x480 = VideoMode(
    width=640,
    height=480,
    freq=60,
    h_sync_1=0x2800320,
    h_sync_2=0x600091,
    v_sync_1=0x1E0020D,
    v_sync_2=0x2420024,
    pll=0x003F6119,
    vic=1,
    pixclk=800 * 525 * 60,
)

# CEA-861 1280x720@60 + official HDMI v_sync_reg_2 tweak (0x1A5001A).
# Too wide for USB 2.0 at 60 Hz (needs ~110 MB/s); kept for experiments.
MODE_1280x720 = VideoMode(
    width=1280,
    height=720,
    freq=60,
    h_sync_1=encode_hsync1(1280, 1650),
    h_sync_2=0x00280105,
    v_sync_1=encode_vsync1(720, 750),
    v_sync_2=0x01A5001A,
    pll=0x0059610C,
    vic=4,
    pixclk=1650 * 750 * 60,
)

MODE_800x600 = VideoMode(
    width=800,
    height=600,
    freq=60,
    h_sync_1=0x3200420,
    h_sync_2=0x8000D9,
    v_sync_1=0x2580274,
    v_sync_2=0x1C4001C,
    pll=0x00080102,
    vic=0,
    pixclk=1056 * 628 * 60,
    bpp=1,
)

# 16:9 square pixels at the same 25.2 MHz / 60 Hz VGA clock the chip already
# sustains. 1920x1080 is exactly 3x this, so the Dell scales evenly.
MODE_640x360 = VideoMode(
    width=640,
    height=360,
    freq=60,
    h_sync_1=0x2800320,
    h_sync_2=0x600091,
    v_sync_1=encode_vsync1(360, 525),
    v_sync_2=0x09C2009C,
    pll=0x003F6119,
    vic=0,
    pixclk=800 * 525 * 60,
)


def default_mirror_mode() -> VideoMode:
    # 800x600 RGB332 clocks at 60 Hz but the Dell never shows a picture.
    return MODE_640x480


def resize_rgb888(src: bytes, src_w: int, src_h: int, dst_w: int, dst_h: int) -> bytes:
    from PIL import Image

    img = Image.frombytes("RGB", (src_w, src_h), src)
    return img.resize((dst_w, dst_h), Image.Resampling.BOX).tobytes()


def fit_rgb888(src: bytes, src_w: int, src_h: int, dst_w: int, dst_h: int) -> bytes:
    """Scale src into dst with black bars, keeping aspect ratio."""
    from PIL import Image

    src_img = Image.frombytes("RGB", (src_w, src_h), src)
    scale = min(dst_w / src_w, dst_h / src_h)
    new_w = max(1, int(src_w * scale))
    new_h = max(1, int(src_h * scale))
    fitted = src_img.resize((new_w, new_h), Image.Resampling.BOX)
    canvas = Image.new("RGB", (dst_w, dst_h), (0, 0, 0))
    canvas.paste(fitted, ((dst_w - new_w) // 2, (dst_h - new_h) // 2))
    return canvas.tobytes()


def bring_up_hdmi(fl: FL2000, mode: VideoMode) -> int:
    """Program FL2000 + IT66121 for HDMI. Returns 0 on success."""
    if cmd_detect(fl) != 0:
        print("  sem IT66121; abortando")
        return 1

    ite = IT66121(fl)
    fl.bit_set(REG_RST, 15)
    time.sleep(0.05)
    ite.reset()
    ite.power_up()
    hpd, rx = ite.hpd()
    print(f"  HPD={'sim' if hpd else 'NAO'} RxSense={'sim' if rx else 'nao'}")

    fl.bit_clear(REG_USB_CTRL, 17)
    fl.bit_set(REG_USB_LPM, 19)
    fl.bit_set(REG_USB_LPM, 20)

    fl.reg_write(REG_PLL, mode.pll)
    fl.bit_set(REG_RST, 15)
    time.sleep(0.02)
    if fl.reg_read(REG_PLL) != mode.pll:
        print(f"  PLL readback mismatch: 0x{fl.reg_read(REG_PLL):08X}")

    for bit in (22, 24, 19, 21, 13, 27, 28, 29):
        fl.bit_clear(REG_ACLK, bit)
    fl.bit_set(REG_ACLK, 28)  # EOF = ZLP; required or bulk NAKs forever

    for bit in (28, 6, 31, 24, 25, 26, 27):
        fl.bit_clear(REG_PXCLK, bit)
    fl.bit_set(REG_PXCLK, 0)
    fl.bit_set(REG_PXCLK, pxclk_color_bit(mode.bpp))
    fl.bit_set(REG_PXCLK, 7)

    for off, val in (
        (REG_HSYNC1, mode.h_sync_1),
        (REG_HSYNC2, mode.h_sync_2),
        (REG_VSYNC1, mode.v_sync_1),
        (REG_VSYNC2, mode.v_sync_2),
    ):
        fl.reg_write(off, val)

    fl.reg_write(REG_ISOCH, fl.reg_read(REG_ISOCH) & 0xC000FFFF)
    fl.bit_set(REG_USB_LPM, 13)
    fl.bit_clear(REG_CTRL3, 10)
    fmt = "RGB332" if mode.bpp == 1 else "RGB565"
    print(f"  FL2000 mode {mode.width}x{mode.height} {fmt} programado")

    ite_av_mute(ite, 1)
    ite_send_avi_infoframe(ite, mode)
    ite_enable_video(ite, mode)
    ite_av_mute(ite, 0)
    print("  IT66121 video output ligado")

    try:
        with contextlib.suppress(usb.core.USBError):
            usb.util.claim_interface(fl.dev, 0)
        fl.dev.set_interface_altsetting(0, 1)
    except usb.core.USBError as exc:
        print(
            f"  USB claim falhou ({exc}). Desconecta e reconecta o USB-A do HAGIBIS e rode de novo."
        )
        return 1
    with contextlib.suppress(usb.core.USBError):
        fl.dev.clear_halt(BULK_EP)
    return 0


def bring_up_hdmi_640(fl: FL2000) -> int:
    return bring_up_hdmi(fl, MODE_640x480)


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


def send_frame(fl: FL2000, frame: bytes) -> None:
    fl.dev.write(BULK_EP, frame, timeout=2000)
    if not needs_zlp(len(frame)):
        return
    with contextlib.suppress(usb.core.USBError):
        fl.dev.write(BULK_EP, b"", timeout=50)


def release_bulk(fl: FL2000) -> None:
    with contextlib.suppress(usb.core.USBError):
        usb.util.release_interface(fl.dev, 0)


def cmd_bars(fl: FL2000, seconds: float) -> int:
    mode = MODE_640x480
    print(f"== test pattern {mode.width}x{mode.height} RGB565 ==")
    frame = dword_swap_frame(make_bars(mode.width, mode.height))
    if bring_up_hdmi(fl, mode) != 0:
        return 1
    print(f"  streaming {len(frame)} bytes/frame por {seconds:.0f}s (olhe o HDMI)")
    sent = 0
    t0 = time.monotonic()
    deadline = t0 + seconds
    try:
        while time.monotonic() < deadline:
            send_frame(fl, frame)
            sent += 1
    except KeyboardInterrupt:
        pass
    except usb.core.USBError as exc:
        print(f"  bulk erro: {exc}")
    finally:
        dt = max(0.001, time.monotonic() - t0)
        print(f"  {sent} frames em {dt:.1f}s → {sent / dt:.1f} fps")
        release_bulk(fl)
    return 0 if sent else 1


def _grab_resized_rgb(sct, mon, width: int, height: int) -> bytes:
    shot = sct.grab(mon)
    return fit_rgb888(shot.rgb, shot.width, shot.height, width, height)


def _capture_worker(
    width: int, height: int, bpp: int, monitor: int, shm, n: int, counter, stop
) -> None:
    """Fill shm with a packed HDMI frame; never touches USB."""
    import mss

    sct = mss.MSS()
    mon = sct.monitors[monitor]
    buf = shm.get_obj() if hasattr(shm, "get_obj") else shm
    while not stop.is_set():
        packed = pack_frame(_grab_resized_rgb(sct, mon, width, height), bpp)
        if len(packed) != n:
            continue
        buf[:n] = packed
        counter.value += 1


def cmd_mirror(fl: FL2000, seconds: float, monitor: int) -> int:
    mode = default_mirror_mode()
    fmt = "RGB332" if mode.bpp == 1 else "RGB565"
    print(f"== espelho da tela → HDMI {mode.width}x{mode.height} {fmt} ==")
    import mss
    from PIL import Image

    sct = mss.MSS()
    if monitor < 1 or monitor >= len(sct.monitors):
        print(f"  monitor {monitor} invalido. disponiveis: {sct.monitors[1:]}")
        return 1
    mon = sct.monitors[monitor]
    print(f"  capturando monitor {monitor}: {mon}")

    rgb = _grab_resized_rgb(sct, mon, mode.width, mode.height)
    preview = Image.frombytes("RGB", (mode.width, mode.height), rgb)
    preview.save("preview.png")
    print("  gravou preview.png (o que vai pro HDMI)")
    if max(rgb[::97]) < 12:
        print(
            "  captura preta. Em Ajustes → Privacidade → Gravacao da Tela, "
            "libere o Terminal (ou o Python) e rode de novo."
        )
        return 1

    frame = pack_frame(rgb, mode.bpp)
    if bring_up_hdmi(fl, mode) != 0:
        return 1

    n = len(frame)
    shm = mp.Array("B", frame, lock=False)
    counter = mp.Value("i", 1)
    stop = mp.Event()
    proc = mp.Process(
        target=_capture_worker,
        args=(mode.width, mode.height, mode.bpp, monitor, shm, n, counter, stop),
        daemon=True,
    )
    proc.start()
    print("  espelhando — Ctrl+C para parar. Olhe o HDMI do HAGIBIS.")
    sent = 0
    t0 = time.monotonic()
    end_at = None if seconds <= 0 else t0 + seconds
    period = 1.0 / mode.freq
    next_tick = t0
    raw_out = shm.get_obj() if hasattr(shm, "get_obj") else shm
    try:
        while end_at is None or time.monotonic() < end_at:
            send_frame(fl, bytes(raw_out))
            sent += 1
            next_tick += period
            delay = next_tick - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            elif delay < -period:
                next_tick = time.monotonic()
    except KeyboardInterrupt:
        pass
    except usb.core.USBError as exc:
        print(f"  bulk erro: {exc}")
    finally:
        stop.set()
        proc.join(timeout=1)
        dt = max(0.001, time.monotonic() - t0)
        print(
            f"  USB {sent / dt:.1f} fps, captura {counter.value / dt:.1f} fps "
            f"({sent} frames / {dt:.1f}s)"
        )
        release_bulk(fl)
    return 0 if sent else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Hagibis FL2000 reverse-engineering probe")
    ap.add_argument("cmd", choices=["dump", "detect", "edid", "bars", "mirror", "all"])
    ap.add_argument("--seconds", type=float, default=8.0, help="0 = ate Ctrl+C (so mirror)")
    ap.add_argument("--monitor", type=int, default=1, help="indice mss do display (1=principal)")
    args = ap.parse_args()
    if args.cmd == "mirror":
        fl = FL2000()
        return cmd_mirror(fl, args.seconds, args.monitor)
    fl = FL2000()
    rc = 0
    if args.cmd in ("dump", "all"):
        cmd_dump(fl)
    if args.cmd in ("detect", "all"):
        rc = cmd_detect(fl) or rc
    if args.cmd in ("edid", "all"):
        rc = cmd_edid(fl) or rc
    if args.cmd == "bars":
        rc = cmd_bars(fl, args.seconds)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
