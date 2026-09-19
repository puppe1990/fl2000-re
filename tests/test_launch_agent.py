"""LaunchAgent install is local filesystem + launchctl stubs — no USB."""

from pathlib import Path

from fl2000_re.launch_agent import (
    LABEL,
    render_plist,
    unmount_fake_cd,
    wait_for_dongle,
    write_launch_agent_plist,
)


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
