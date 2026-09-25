"""Virtual CG display helper protocol. Never creates a real WindowServer display."""

import signal
from pathlib import Path

import pytest
from fl2000_re import virtual_display
from fl2000_re.video_modes import default_mirror_mode
from fl2000_re.virtual_display import (
    HELPER_REAP_WAIT_S,
    VirtualScreen,
    clang_virtual_display_command,
    default_virtual_desktop_size,
    find_helper_pids,
    parse_virtual_ready_line,
    reap_stale_helpers,
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

    handle = spawn_virtual_display(
        binary=binary, popen=popen, read_ready=read_ready, reap=lambda _p: []
    )
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
        spawn_virtual_display(binary=binary, popen=popen, read_ready=read_ready, reap=lambda _p: [])


def test_spawn_forwards_place_to_helper(tmp_path: Path):
    binary = tmp_path / "hagibis_virtual_display"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    proc = _FakeProc("HAGIBIS_VIRTUAL display_id=9 width=720 height=480 origin_x=-720 origin_y=0\n")
    captured: list[str] = []

    def popen(argv, **_kwargs):
        captured.extend(argv)
        return proc

    def read_ready(_got, _timeout: float) -> str:
        return proc._line

    spawn_virtual_display(
        binary=binary, popen=popen, read_ready=read_ready, reap=lambda _p: [], place="left"
    )
    assert captured[-2:] == ["--place", "left"]


def test_reap_stale_helpers_sigterms_each_pid_and_waits():
    """A helper orphaned by a killed parent keeps the display and blocks the next extend."""
    killed: list[tuple[int, int]] = []
    slept: list[float] = []
    reaped = reap_stale_helpers(
        Path("/x/hagibis_virtual_display"),
        list_pids=lambda _target: [11, 22],
        kill=lambda pid, sig: killed.append((pid, sig)),
        sleep=slept.append,
    )
    assert reaped == [11, 22]
    assert killed == [(11, signal.SIGTERM), (22, signal.SIGTERM)]
    assert slept == [HELPER_REAP_WAIT_S]


def test_reap_stale_helpers_skips_already_dead_pid():
    def kill(_pid: int, _sig: int) -> None:
        raise ProcessLookupError

    reaped = reap_stale_helpers(
        Path("/x/h"), list_pids=lambda _target: [7], kill=kill, sleep=lambda _s: None
    )
    assert reaped == []


def test_find_helper_pids_parses_pgrep_tokens(monkeypatch: pytest.MonkeyPatch):
    class _Done:
        stdout = "123\n456\n"

    monkeypatch.setattr(virtual_display.subprocess, "run", lambda *_a, **_k: _Done())
    assert find_helper_pids(Path("/x/hagibis_virtual_display")) == [123, 456]


def test_spawn_reaps_leftover_helper_for_its_binary(tmp_path: Path):
    binary = tmp_path / "hagibis_virtual_display"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    proc = _FakeProc("HAGIBIS_VIRTUAL display_id=9 width=720 height=480 origin_x=0 origin_y=0\n")
    seen: list[Path] = []

    def reap(target: Path) -> list[int]:
        seen.append(target)
        return []

    def read_ready(_got, _timeout: float) -> str:
        return proc._line

    spawn_virtual_display(
        binary=binary, popen=lambda *_a, **_k: proc, read_ready=read_ready, reap=reap
    )
    assert seen == [binary]


def test_spawn_rejects_unknown_place(tmp_path: Path):
    binary = tmp_path / "hagibis_virtual_display"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    with pytest.raises(ValueError, match="place='up'"):
        spawn_virtual_display(binary=binary, place="up")
