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


def test_first_frame_and_live_capture_delivery_are_logged_once(capsys):
    capture = types.SimpleNamespace(
        run_id="session", snapshot_fresh=False, first_capture_logged=False
    )
    sessions.record_frame_ready(capture, 1, 0.02)
    capture.snapshot_fresh = True
    sessions.record_frame_ready(capture, 2, 0.5)
    sessions.record_frame_ready(capture, 3, 0.6)
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [e["event"] for e in events] == ["stream_first_frame_sent", "stream_first_capture_sent"]
    assert [e["elapsed_ms"] for e in events] == [20, 500]
    assert capture.first_capture_logged


def test_frame_logging_skips_unidentified_test_or_legacy_captures(capsys):
    sessions.record_frame_ready(types.SimpleNamespace(), 1, 0.02)
    assert capsys.readouterr().out == ""


def test_delivery_readiness_follows_completed_frame_not_future_capture(capsys):
    capture = types.SimpleNamespace(run_id="run", snapshot_fresh=True, first_capture_logged=False)
    sessions.record_frame_ready(capture, 1, 0.02, fresh=False)
    assert not capture.first_capture_logged
    sessions.record_frame_ready(capture, 2, 0.05, fresh=True)
    assert capture.first_capture_logged
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events[-1]["event"] == "stream_first_capture_sent"
    assert events[-1]["elapsed_ms"] == 50
