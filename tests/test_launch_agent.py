"""LaunchAgent install is local filesystem + launchctl stubs — no USB."""

import subprocess
from pathlib import Path

from fl2000_re.launch_agent import (
    LABEL,
    _bootstrap,
    cmd_extend_agent,
    render_plist,
    unmount_fake_cd,
    wait_for_dongle,
    write_launch_agent_plist,
)


def test_render_plist_silences_leaked_semaphore_warning():
    xml = render_plist(
        repo=Path("/repo"),
        python=Path("/repo/.venv/bin/python"),
        log=Path("/tmp/extend.log"),
    )
    assert "<key>PYTHONWARNINGS</key>" in xml
    assert "ignore:resource_tracker:UserWarning" in xml


def test_bootstrap_retries_after_io_error():
    calls: list[list[str]] = []
    state = {"bootstraps": 0}

    def run(cmd: list[str], **_kwargs) -> None:
        calls.append(cmd)
        if cmd[1] == "bootstrap":
            state["bootstraps"] += 1
            if state["bootstraps"] == 1:
                raise subprocess.CalledProcessError(5, cmd)

    sleeps: list[float] = []
    _bootstrap(Path("/tmp/x.plist"), run=run, sleep=sleeps.append)
    assert calls[0][1] == "bootout"
    assert state["bootstraps"] == 2
    assert sleeps == [1.0]


def test_bootstrap_raises_after_all_attempts_fail():
    def run(cmd: list[str], **_kwargs) -> None:
        if cmd[1] == "bootstrap":
            raise subprocess.CalledProcessError(5, cmd)

    try:
        _bootstrap(Path("/tmp/x.plist"), run=run, sleep=lambda _s: None)
    except subprocess.CalledProcessError as exc:
        assert exc.returncode == 5
    else:
        raise AssertionError("expected CalledProcessError")


def test_render_plist_is_aqua_keepalive_and_unbuffered():
    xml = render_plist(
        repo=Path("/repo"),
        python=Path("/repo/.venv/bin/python"),
        log=Path("/tmp/extend.log"),
    )
    assert f"<string>{LABEL}</string>" in xml
    assert "<string>Aqua</string>" in xml
    assert "<key>RunAtLoad</key>" in xml
    assert "<true/>" in xml
    assert "<key>KeepAlive</key>" in xml
    assert "<key>PYTHONUNBUFFERED</key>" in xml
    assert "<string>install-agent</string>" not in xml
    assert "<string>extend-agent</string>" in xml
    assert "<string>/repo/.venv/bin/python</string>" in xml
    assert "<string>/repo</string>" in xml


def test_render_plist_escapes_xml_in_paths():
    xml = render_plist(
        repo=Path("/r&p<o>"),
        python=Path("/r&p<o>/python"),
        log=Path("/tmp/a&b.log"),
    )
    assert "/r&amp;p&lt;o&gt;" in xml
    assert "/r&p<o>" not in xml


def test_render_plist_runs_interactive_so_the_60hz_pump_is_scheduled():
    """Standard QoS dropped the 2.6 MB 1440x900 frame pump to ~44 fps."""
    xml = render_plist(
        repo=Path("/repo"),
        python=Path("/repo/.venv/bin/python"),
        log=Path("/tmp/extend.log"),
    )
    assert "<key>ProcessType</key>" in xml
    assert "<string>Interactive</string>" in xml


def test_write_launch_agent_plist_creates_file(tmp_path: Path):
    dest = tmp_path / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    path = write_launch_agent_plist(
        home=tmp_path,
        repo=Path("/repo"),
        python=Path("/repo/.venv/bin/python"),
        log=tmp_path / "extend.log",
    )
    assert path == dest
    text = dest.read_text()
    assert LABEL in text
    assert "extend-agent" in text


def test_wait_for_dongle_returns_true_when_present():
    assert wait_for_dongle(find_device=lambda **_k: object(), timeout_s=0.0, poll_s=0.0)


def test_wait_for_dongle_times_out_when_missing():
    assert wait_for_dongle(find_device=lambda **_k: None, timeout_s=0.0, poll_s=0.0) is False


def test_unmount_fake_cd_skips_missing(tmp_path: Path):
    seen: list[list[str]] = []
    unmount_fake_cd(volume=tmp_path / "nope", run=lambda cmd, **_k: seen.append(cmd))
    assert seen == []


def test_unmount_fake_cd_runs_diskutil(tmp_path: Path):
    vol = tmp_path / "FL2000DX"
    vol.mkdir()
    seen: list[list[str]] = []
    unmount_fake_cd(volume=vol, run=lambda cmd, **_k: seen.append(cmd))
    assert seen == [["diskutil", "unmountDisk", str(vol)]]


def test_cmd_extend_agent_shows_the_pointer(monkeypatch):
    """2026-09-21: the sprite is drawn in-process, so launchd can show the cursor."""
    import fl2000_re.launch_agent as launch_agent
    import fl2000_re.stream as stream
    from fl2000_re import fl2000_usb
    from fl2000_re.video_modes import MODE_1024x768_50

    monkeypatch.setattr(launch_agent, "agent_video_mode", lambda: MODE_1024x768_50)
    monkeypatch.setattr(launch_agent, "wait_for_dongle", lambda *_a, **_k: True)
    monkeypatch.setattr(launch_agent, "unmount_fake_cd", lambda *_a, **_k: None)
    monkeypatch.setattr(launch_agent, "rotate_extend_log", lambda *_a, **_k: None)
    monkeypatch.setattr(fl2000_usb, "FL2000", lambda *_a, **_k: object())
    seen: dict[str, object] = {}

    def fake_extend(_fl, _seconds, underscan, _stretch_x, mode, _v_shift, _place, cursor, **kwargs):
        seen["underscan"] = underscan
        seen["mode"] = mode
        seen["cursor"] = cursor
        seen["capture_fps"] = kwargs.get("capture_fps")
        seen["virtual_size"] = kwargs.get("virtual_size")
        seen["startup_preview"] = kwargs.get("startup_preview")
        return 0

    monkeypatch.setattr(stream, "cmd_extend", fake_extend)
    assert cmd_extend_agent() == 0
    assert seen["cursor"] is True
    assert seen["startup_preview"] is False
    assert seen["mode"] is MODE_1024x768_50
    assert seen["virtual_size"] == launch_agent.VIRTUAL_DESKTOP
    assert launch_agent.VIRTUAL_DESKTOP == (1280, 960)
    assert seen["underscan"] == 1.0
    assert seen["capture_fps"] == stream.EXTEND_CAPTURE_FPS


def test_rotate_extend_log_leaves_a_small_file(tmp_path: Path):
    from fl2000_re.launch_agent import rotate_extend_log

    path = tmp_path / "fl2000-re-extend.log"
    path.write_text("ok\n")
    rotate_extend_log(path, max_bytes=100)
    assert path.read_text() == "ok\n"
    assert not path.with_name("fl2000-re-extend.log.old").exists()


def test_rotate_extend_log_keeps_live_path_and_archives_contents(tmp_path: Path):
    from fl2000_re.launch_agent import rotate_extend_log

    path = tmp_path / "fl2000-re-extend.log"
    path.write_bytes(b"x" * 200)
    rotate_extend_log(path, max_bytes=100)
    assert path.read_bytes() == b""
    assert path.with_name("fl2000-re-extend.log.old").read_bytes() == b"x" * 200


def test_rotation_keeps_launchd_open_stdout_writing_to_live_log(tmp_path: Path):
    from fl2000_re.launch_agent import rotate_extend_log

    path = tmp_path / "extend.log"
    with path.open("a") as writer:
        writer.write("before\n")
        writer.flush()
        original_inode = path.stat().st_ino
        rotate_extend_log(path, max_bytes=1)
        writer.write("after\n")
        writer.flush()
    assert path.stat().st_ino == original_inode
    assert path.read_text() == "after\n"
    assert path.with_name("extend.log.old").read_text() == "before\n"


def test_local_profile_is_optional_and_resolves_existing_modes(tmp_path):
    from fl2000_re.launch_agent import agent_video_mode
    from fl2000_re.video_modes import MODE_640x480, MODE_1024x768_50

    profile = tmp_path / "video-mode"
    assert agent_video_mode(profile) is MODE_1024x768_50
    profile.write_text("640x480\n")
    assert agent_video_mode(profile) is MODE_640x480


def test_invalid_local_profile_reports_received_value(tmp_path):
    import pytest
    from fl2000_re.launch_agent import agent_video_mode

    profile = tmp_path / "video-mode"
    profile.write_text("invalid-mode")
    with pytest.raises(ValueError, match="invalid-mode"):
        agent_video_mode(profile)
