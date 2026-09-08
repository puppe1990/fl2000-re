"""Read-only probe commands: USB dump, IT66121 detect, EDID."""

from __future__ import annotations

import usb.core

from fl2000_re.fl2000_usb import FL2000, decode_status
from fl2000_re.it66121 import IT66121
from fl2000_re.registers import DUMP_REGS, I2C_DDC, I2C_ITE, ITE_DEVICE, ITE_VENDOR, REG_STATUS


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
        extra = f"  {decode_status(val)}" if off == REG_STATUS else ""
        print(f"  [0x{off:04X}] = 0x{val:08X}{extra}")


def cmd_detect(fl: FL2000) -> int:
    print("== I2C scan: IT66121 @ 0x4C ==")
    try:
        dword, st = fl.i2c_read32(I2C_ITE, 0)
    except (TimeoutError, usb.core.USBError) as exc:
        print(f"  timeout/erro: {exc}")
        return 1
    if dword is None:
        print("  I2C read returned no data")
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
    except (TimeoutError, usb.core.USBError) as exc:
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
            _print_edid_timing(desc)


def _print_edid_timing(desc: bytes) -> None:
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
            blob += (dw or 0).to_bytes(4, "little")
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
