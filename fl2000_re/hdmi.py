"""Program FL2000 scanout + IT66121 so HDMI actually emits a picture."""

from __future__ import annotations

import contextlib
import time

import usb.core
import usb.util

from fl2000_re.fl2000_usb import FL2000
from fl2000_re.it66121 import (
    IT66121,
    ite_av_mute,
    ite_enable_video,
    ite_send_avi_infoframe,
)
from fl2000_re.probe import cmd_detect
from fl2000_re.registers import (
    BULK_EP,
    REG_ACLK,
    REG_CTRL3,
    REG_HSYNC1,
    REG_HSYNC2,
    REG_ISOCH,
    REG_PLL,
    REG_PXCLK,
    REG_RST,
    REG_USB_CTRL,
    REG_USB_LPM,
    REG_VSYNC1,
    REG_VSYNC2,
)
from fl2000_re.video_modes import MODE_640x480, VideoMode, pxclk_color_bit


def bring_up_hdmi(fl: FL2000, mode: VideoMode) -> int:
    """Program FL2000 + IT66121 for HDMI. Returns 0 on success."""
    if cmd_detect(fl) != 0:
        print("  sem IT66121; abortando")
        return 1
    ite = IT66121(fl)
    _reset_and_power_ite(fl, ite)
    _program_fl2000_mode_registers(fl, mode)
    _enable_ite_video(ite, mode)
    return _claim_bulk_alt1(fl)


def bring_up_hdmi_640(fl: FL2000) -> int:
    return bring_up_hdmi(fl, MODE_640x480)


def _reset_and_power_ite(fl: FL2000, ite: IT66121) -> None:
    fl.bit_set(REG_RST, 15)
    time.sleep(0.05)
    ite.reset()
    ite.power_up()
    hpd, rx = ite.hpd()
    print(f"  HPD={'sim' if hpd else 'NAO'} RxSense={'sim' if rx else 'nao'}")


def _program_fl2000_mode_registers(fl: FL2000, mode: VideoMode) -> None:
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


def _enable_ite_video(ite: IT66121, mode: VideoMode) -> None:
    ite_av_mute(ite, 1)
    ite_send_avi_infoframe(ite, mode)
    ite_enable_video(ite, mode)
    ite_av_mute(ite, 0)
    print("  IT66121 video output ligado")


def _claim_bulk_alt1(fl: FL2000) -> int:
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
