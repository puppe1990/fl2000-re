"""Install a user LaunchAgent that starts `extend` at Aqua login.

Example: cmd_install_agent(now=False)
"""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import usb.core

from fl2000_re.registers import FL2000_USB_PID, FL2000_USB_VID
from fl2000_re.video_modes import MODE_640x480

LABEL = "com.fl2000-re.extend"
POLL_S = 2.0
RunFn = Callable[..., Any]
FindDevice = Callable[..., Any]


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def launch_agents_dir(home: Path | None = None) -> Path:
    return (home or Path.home()) / "Library" / "LaunchAgents"


def _xml(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_plist(*, repo: Path, python: Path, log: Path) -> str:
    """Plist for Aqua login. KeepAlive retries if USB was unplugged at boot."""
    repo_s, py_s, log_s, script = (
        _xml(str(repo)),
        _xml(str(python)),
        _xml(str(log)),
        _xml(str(repo / "hagibis_re.py")),
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{LABEL}</string>
  <key>LimitLoadToSessionType</key>
  <string>Aqua</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>15</integer>
  <key>WorkingDirectory</key>
  <string>{repo_s}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PYTHONUNBUFFERED</key>
    <string>1</string>
  </dict>
  <key>ProgramArguments</key>
  <array>
    <string>{py_s}</string>
    <string>-u</string>
    <string>{script}</string>
    <string>extend-agent</string>
  </array>
  <key>StandardOutPath</key>
  <string>{log_s}</string>
  <key>StandardErrorPath</key>
  <string>{log_s}</string>
</dict>
</plist>
"""


def write_launch_agent_plist(
    *,
    home: Path | None = None,
    repo: Path | None = None,
    python: Path | None = None,
    log: Path | None = None,
) -> Path:
    root = repo or repo_root()
    dest_dir = launch_agents_dir(home)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{LABEL}.plist"
    dest.write_text(
        render_plist(
            repo=root,
            python=python or (root / ".venv" / "bin" / "python"),
            log=log or ((home or Path.home()) / "Library" / "Logs" / "fl2000-re-extend.log"),
        )
    )
    return dest


def wait_for_dongle(
    find_device: FindDevice | None = None,
    *,
    timeout_s: float | None = None,
    poll_s: float = POLL_S,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """True when 1d5c:2000 is on the bus. timeout_s=None waits forever."""
    find = find_device or usb.core.find
    deadline = None if timeout_s is None else time.monotonic() + timeout_s
    while True:
        if find(idVendor=FL2000_USB_VID, idProduct=FL2000_USB_PID) is not None:
            return True
        if deadline is not None and time.monotonic() >= deadline:
            return False
        sleep(poll_s)


def unmount_fake_cd(
    volume: Path | None = None,
    run: RunFn = subprocess.run,
) -> None:
    path = volume or Path("/Volumes/FL2000DX")
    if not path.exists():
        return
    run(["diskutil", "unmountDisk", str(path)], check=False, timeout=10)


def cmd_install_agent(*, now: bool = False, run: RunFn = subprocess.run) -> int:
    """Write ~/Library/LaunchAgents plist. --now bootstraps this Aqua session."""
    dest = write_launch_agent_plist()
    log = Path.home() / "Library" / "Logs" / "fl2000-re-extend.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    print(f"instalado: {dest}")
    print(f"log: {log}")
    print("sobe no login. Mate o extend do Terminal (PID exato) senão Access denied.")
    print("Gravação de Tela: Ajustes → Privacidade → Python (.venv).")
    if now:
        _bootstrap(dest, run=run)
        print("agent ligado nesta sessão (--now).")
    return 0


def cmd_uninstall_agent(run: RunFn = subprocess.run) -> int:
    dest = launch_agents_dir() / f"{LABEL}.plist"
    _bootout(run=run)
    dest.unlink(missing_ok=True)
    print(f"removido: {dest}")
    return 0


def cmd_extend_agent() -> int:
    """Wait for the dongle, drop the fake CD, then extend (LaunchAgent entry)."""
    print("aguardando o Hagibis USB (1d5c:2000)...")
    wait_for_dongle()
    unmount_fake_cd()
    from fl2000_re.fl2000_usb import FL2000
    from fl2000_re.stream import cmd_extend

    return cmd_extend(FL2000(), 0, 1.0, 1.0, MODE_640x480, 0, "left", True)


def _gui_target() -> str:
    return f"gui/{os.getuid()}/{LABEL}"


def _bootstrap(plist: Path, run: RunFn) -> None:
    domain = f"gui/{os.getuid()}"
    run(["launchctl", "bootout", _gui_target()], check=False)
    run(["launchctl", "bootstrap", domain, str(plist)], check=True)


def _bootout(run: RunFn) -> None:
    run(["launchctl", "bootout", _gui_target()], check=False)
