"""Spawn the Objective-C CGVirtualDisplay helper so WindowServer has an extra screen.

The helper must stay alive: releasing CGVirtualDisplay removes the display and
dumps every window back onto the Air.
"""

from __future__ import annotations

import os
import select
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from fl2000_re.video_modes import default_mirror_mode

READY_PREFIX = "HAGIBIS_VIRTUAL"
HELPER_NAME = "hagibis_virtual_display"
VIRTUAL_VENDOR_ID = 0xF200
VIRTUAL_PRODUCT_ID = 0x0E00
VIRTUAL_DISPLAY_NAME = "Hagibis"
READY_TIMEOUT_S = 8.0


class HelperProc(Protocol):
    stdout: object
    stderr: object
    returncode: int | None

    def poll(self) -> int | None: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...
    def wait(self, timeout: float | None = None) -> int: ...


PopenFn = Callable[..., HelperProc]
ReadReadyFn = Callable[[HelperProc, float], str]


@dataclass(frozen=True)
class VirtualScreen:
    display_id: int
    width: int
    height: int
    origin_x: int
    origin_y: int


class VirtualScreenHandle:
    """Owns the helper process. close() tears the CG display down."""

    def __init__(self, screen: VirtualScreen, proc: HelperProc) -> None:
        self.screen = screen
        self._proc = proc

    def close(self) -> None:
        if self._proc.poll() is not None:
            return
        self._proc.terminate()
        try:
            self._proc.wait(timeout=2)
        except Exception:
            self._proc.kill()
            self._proc.wait(timeout=1)


def default_virtual_desktop_size() -> tuple[int, int]:
    """Match HDMI 720x480 so UI chrome is not downscaled on the Dell."""
    mode = default_mirror_mode()
    return mode.width, mode.height


def parse_virtual_ready_line(line: str) -> VirtualScreen:
    stripped = line.strip()
    if not stripped.startswith(READY_PREFIX):
        raise ValueError(
            f"virtual display helper ready line must start with {READY_PREFIX!r}, got {stripped!r}"
        )
    fields: dict[str, str] = {}
    for token in stripped.split()[1:]:
        if "=" not in token:
            raise ValueError(
                f"virtual display helper ready token must be key=value, got {token!r} in {stripped!r}"
            )
        key, value = token.split("=", 1)
        fields[key] = value
    required = ("display_id", "width", "height", "origin_x", "origin_y")
    missing = [key for key in required if key not in fields]
    if missing:
        raise ValueError(f"virtual display helper ready line missing {missing} in {stripped!r}")
    try:
        return VirtualScreen(
            display_id=int(fields["display_id"]),
            width=int(fields["width"]),
            height=int(fields["height"]),
            origin_x=int(fields["origin_x"]),
            origin_y=int(fields["origin_y"]),
        )
    except ValueError as exc:
        raise ValueError(
            f"virtual display helper ready line has non-integer fields in {stripped!r}"
        ) from exc


def clang_virtual_display_command(src: Path, dst: Path) -> list[str]:
    return [
        "clang",
        "-fobjc-arc",
        "-O2",
        "-framework",
        "Foundation",
        "-framework",
        "CoreGraphics",
        "-o",
        str(dst),
        str(src),
    ]


def native_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "native"


def helper_source_path() -> Path:
    return native_dir() / f"{HELPER_NAME}.m"


def helper_binary_path() -> Path:
    return native_dir() / HELPER_NAME


def ensure_virtual_display_helper() -> Path:
    src = helper_source_path()
    dst = helper_binary_path()
    if dst.exists() and src.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
        return dst
    if os.uname().sysname != "Darwin":
        raise RuntimeError(
            f"virtual display helper runs only on macOS, sysname={os.uname().sysname!r}"
        )
    if not src.exists():
        raise FileNotFoundError(f"virtual display helper source missing: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        clang_virtual_display_command(src, dst), capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        raise RuntimeError(f"clang failed building {dst} from {src}: {completed.stderr.strip()!r}")
    return dst


def spawn_virtual_display(
    *,
    binary: Path | None = None,
    popen: PopenFn = subprocess.Popen,
    read_ready: ReadReadyFn | None = None,
    width: int | None = None,
    height: int | None = None,
) -> VirtualScreenHandle:
    desk_w, desk_h = default_virtual_desktop_size()
    width = width or desk_w
    height = height or desk_h
    path = binary or ensure_virtual_display_helper()
    if not path.exists():
        raise FileNotFoundError(
            f"virtual display helper missing at {path}; run ./bin/setup on macOS"
        )
    argv = [
        str(path),
        "--width",
        str(width),
        "--height",
        str(height),
        "--name",
        VIRTUAL_DISPLAY_NAME,
        "--vendor",
        hex(VIRTUAL_VENDOR_ID),
        "--product",
        hex(VIRTUAL_PRODUCT_ID),
    ]
    proc = popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    reader = read_ready or read_virtual_ready_line
    try:
        line = reader(proc, READY_TIMEOUT_S)
        screen = parse_virtual_ready_line(line)
    except Exception:
        if proc.poll() is None:
            proc.terminate()
        raise
    return VirtualScreenHandle(screen, proc)


def read_virtual_ready_line(proc: HelperProc, timeout: float) -> str:
    stdout = proc.stdout
    if stdout is None:
        raise RuntimeError("virtual display helper stdout is not piped")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            stderr = proc.stderr
            err = stderr.read() if stderr is not None else ""
            raise RuntimeError(f"virtual display helper exited {proc.returncode}: {err!r}")
        ready, _, _ = select.select([stdout], [], [], 0.1)
        if not ready:
            continue
        line = stdout.readline()
        if line.strip().startswith(READY_PREFIX):
            return line
    raise TimeoutError(f"virtual display helper did not print {READY_PREFIX} within {timeout}s")
