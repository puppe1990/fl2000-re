"""CLI for the Hagibis FL2000 userspace probe."""

from __future__ import annotations

import argparse

from fl2000_re.fl2000_usb import FL2000, DongleNotFoundError
from fl2000_re.probe import cmd_detect, cmd_dump, cmd_edid
from fl2000_re.stream import cmd_bars, cmd_mirror


def main() -> int:
    ap = argparse.ArgumentParser(description="Hagibis FL2000 reverse-engineering probe")
    ap.add_argument("cmd", choices=["dump", "detect", "edid", "bars", "mirror", "all"])
    ap.add_argument("--seconds", type=float, default=8.0, help="0 = ate Ctrl+C (so mirror)")
    ap.add_argument("--monitor", type=int, default=1, help="indice mss do display (1=principal)")
    args = ap.parse_args()
    try:
        fl = FL2000()
    except DongleNotFoundError as exc:
        print(exc)
        return 1
    if args.cmd == "mirror":
        return cmd_mirror(fl, args.seconds, args.monitor)
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
