"""CLI for the Hagibis FL2000 userspace probe."""

from __future__ import annotations

import argparse

from fl2000_re.fl2000_usb import FL2000, DongleNotFoundError
from fl2000_re.letterbox import UNDERSCAN
from fl2000_re.probe import cmd_detect, cmd_dump, cmd_edid
from fl2000_re.stream import cmd_bars, cmd_extend, cmd_mirror
from fl2000_re.video_modes import NAMED_MODES, resolve_mode


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Hagibis FL2000 reverse-engineering probe")
    ap.add_argument(
        "cmd",
        choices=[
            "dump",
            "detect",
            "edid",
            "bars",
            "mirror",
            "extend",
            "extend-agent",
            "install-agent",
            "uninstall-agent",
            "monitor-log",
            "all",
        ],
    )
    ap.add_argument("--seconds", type=float, default=8.0, help="0 = ate Ctrl+C (mirror/extend)")
    ap.add_argument("--monitor", type=int, default=1, help="indice mss do display (1=principal)")
    ap.add_argument(
        "--underscan",
        type=float,
        default=UNDERSCAN,
        help="moldura 0-1 (VGA analogico do P2016: 1.0)",
    )
    ap.add_argument(
        "--stretch-x",
        type=float,
        default=1.0,
        help="pre-compressao horizontal (P2016 no VGA: 1.2)",
    )
    ap.add_argument(
        "--mode",
        default="720x480",
        choices=sorted(NAMED_MODES),
        help="timing (P2016 no VGA: 640x480)",
    )
    ap.add_argument(
        "--v-shift",
        type=int,
        default=0,
        help="desloca a imagem verticalmente em linhas (negativo = cima)",
    )
    ap.add_argument(
        "--place",
        choices=["left", "right"],
        default="right",
        help="lado da tela virtual do extend",
    )
    ap.add_argument(
        "--cursor",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="ponteiro no HDMI (desligue com --no-cursor para mais fps)",
    )
    ap.add_argument("--out", default=None, help="arquivo .jsonl do monitor-log")
    ap.add_argument(
        "--now",
        action="store_true",
        help="install-agent: liga o LaunchAgent nesta sessão (briga com extend no Terminal)",
    )
    return ap


def main() -> int:
    args = build_parser().parse_args()
    if args.cmd in {"install-agent", "uninstall-agent", "extend-agent"}:
        return _agent_cmd(args.cmd, args.now)
    try:
        fl = FL2000()
    except DongleNotFoundError as exc:
        print(exc)
        return 1
    if args.cmd == "mirror":
        mode = resolve_mode(args.mode)
        return cmd_mirror(
            fl, args.seconds, args.monitor, args.underscan, args.stretch_x, mode, args.v_shift
        )
    if args.cmd == "extend":
        mode = resolve_mode(args.mode)
        return cmd_extend(
            fl,
            args.seconds,
            args.underscan,
            args.stretch_x,
            mode,
            args.v_shift,
            args.place,
            args.cursor,
        )
    if args.cmd == "monitor-log":
        from fl2000_re.status_log import cmd_monitor_log

        return cmd_monitor_log(fl, args.seconds, args.out)
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


def _agent_cmd(cmd: str, now: bool) -> int:
    from fl2000_re.launch_agent import cmd_extend_agent, cmd_install_agent, cmd_uninstall_agent

    if cmd == "install-agent":
        return cmd_install_agent(now=now)
    if cmd == "uninstall-agent":
        return cmd_uninstall_agent()
    return cmd_extend_agent()
