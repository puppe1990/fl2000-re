"""Virtual CG display helper protocol. Never creates a real WindowServer display."""

from pathlib import Path

import pytest
from fl2000_re.video_modes import default_mirror_mode
from fl2000_re.virtual_display import (
    VirtualScreen,
    clang_virtual_display_command,
    default_virtual_desktop_size,
    parse_virtual_ready_line,
    spawn_virtual_display,
)


def test_virtual_desktop_matches_hdmi_mode_for_1to1_glyphs():
    """Clone downscales the Air retina ~5x. Extend must compose UI at HDMI size."""
    mode = default_mirror_mode()
    assert default_virtual_desktop_size() == (mode.width, mode.height)
    assert default_virtual_desktop_size() == (720, 480)


def test_parse_virtual_ready_line():
    screen = parse_virtual_ready_line(
        "HAGIBIS_VIRTUAL display_id=42 width=720 height=480 origin_x=1710 origin_y=0\n"
    )
    assert screen == VirtualScreen(display_id=42, width=720, height=480, origin_x=1710, origin_y=0)


def test_parse_virtual_ready_line_rejects_garbage():
    with pytest.raises(ValueError, match="HAGIBIS_VIRTUAL"):
        parse_virtual_ready_line("ready 42")


def test_parse_virtual_ready_line_requires_display_id():
    with pytest.raises(ValueError, match="display_id"):
        parse_virtual_ready_line("HAGIBIS_VIRTUAL width=720 height=480 origin_x=0 origin_y=0")


def test_clang_command_links_foundation_and_coregraphics(tmp_path: Path):
    src = tmp_path / "hagibis_virtual_display.m"
    dst = tmp_path / "hagibis_virtual_display"
    cmd = clang_virtual_display_command(src, dst)
    assert cmd[0] == "clang"
    assert "-fobjc-arc" in cmd
    assert "Foundation" in cmd
    assert "CoreGraphics" in cmd
    assert str(src) in cmd
    assert str(dst) in cmd


class _FakeProc:
    def __init__(self, line: str, returncode: int | None = None, stderr: str = "") -> None:
        self._line = line
        self.returncode = returncode
        self.stderr_text = stderr
        self.terminated = False
        self.killed = False
        self.pid = 4242

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = 0

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode or 0


def test_spawn_reads_ready_line_then_close_terminates(tmp_path: Path):
    binary = tmp_path / "hagibis_virtual_display"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    proc = _FakeProc("HAGIBIS_VIRTUAL display_id=9 width=720 height=480 origin_x=100 origin_y=0\n")

    def popen(*_args, **_kwargs):
        return proc

    def read_ready(got, timeout: float) -> str:
        assert got is proc
        assert timeout > 0
        return proc._line

    handle = spawn_virtual_display(binary=binary, popen=popen, read_ready=read_ready)
    assert handle.screen.display_id == 9
    assert handle.screen.origin_x == 100
    handle.close()
    assert proc.terminated


def test_spawn_includes_helper_stderr_when_process_dies(tmp_path: Path):
    binary = tmp_path / "hagibis_virtual_display"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    proc = _FakeProc("", returncode=1, stderr="CGVirtualDisplay API unavailable\n")

    def popen(*_args, **_kwargs):
        return proc

    def read_ready(got, timeout: float) -> str:
        raise RuntimeError(f"virtual display helper exited {got.poll()}: {got.stderr_text}")

    with pytest.raises(RuntimeError, match="CGVirtualDisplay API unavailable"):
        spawn_virtual_display(binary=binary, popen=popen, read_ready=read_ready)
