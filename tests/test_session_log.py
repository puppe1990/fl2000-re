"""A source fingerprint distinguishes code edits from plain process restarts."""

import json
import types
from pathlib import Path

import fl2000_re.session_log as sessions
from fl2000_re.video_modes import MODE_1024x768


def test_source_hash_includes_uncommitted_code_and_helper_binary(tmp_path):
    package = tmp_path / "fl2000_re"
    package.mkdir()
    source = package / "stream.py"
    source.write_text("first")
    first = sessions.source_fingerprint(tmp_path)
    assert sessions.source_fingerprint(tmp_path) == first
    source.write_text("second")
    second = sessions.source_fingerprint(tmp_path)
    assert second != first
    native = tmp_path / "native"
    native.mkdir()
    (native / "hagibis_virtual_display").write_bytes(b"binary")
    assert sessions.source_fingerprint(tmp_path) != second


def test_revision_handles_missing_git_and_timeouts(monkeypatch):
    def unavailable(*args, **kwargs):
        raise OSError("no git")

    monkeypatch.setattr(sessions.subprocess, "run", unavailable)
    assert sessions.git_revision(Path("/tmp")) == "unknown"
    monkeypatch.setattr(
        sessions.subprocess, "run", lambda *a, **kw: types.SimpleNamespace(returncode=1)
    )
    assert sessions.git_revision(Path("/tmp")) == "unknown"


def test_session_reports_effective_usb_profile_and_link(monkeypatch, capsys):
    monkeypatch.setattr(sessions, "git_revision", lambda root: "abc123")
    monkeypatch.setattr(sessions, "source_fingerprint", lambda root: "fingerprint")
    device = types.SimpleNamespace(speed=4, bus=1, address=7)
    run_id = sessions.start_stream_session(types.SimpleNamespace(dev=device), MODE_1024x768, 18, 20)
    sessions.end_stream_session(run_id, 1, 42, 1.2345)
    start, end = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert start["commit"] == "abc123"
    assert start["source_hash"] == "fingerprint"
    assert start["bulk_block_bytes"] == 1572864
    assert start["frame_bytes"] == 1572864
    assert start["usb_speed"] == 4
    assert start["hdmi"] == [1024, 768, 60]
    assert start["run_id"] == end["run_id"] == run_id
    assert end["sent_frames"] == 42


def test_session_logging_survives_unreadable_source(monkeypatch, capsys):
    def unreadable(root):
        raise OSError("unreadable")

    monkeypatch.setattr(sessions, "source_fingerprint", unreadable)
    monkeypatch.setattr(sessions, "git_revision", lambda root: "unknown")
    sessions.start_stream_session(types.SimpleNamespace(dev=object()), MODE_1024x768, None, 20)
    assert json.loads(capsys.readouterr().out)["source_hash"] == "unavailable"
