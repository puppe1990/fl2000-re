import json
import plistlib
import subprocess
from datetime import datetime
from types import SimpleNamespace

import pytest
from fl2000_re import health_monitor as monitor


def record(event):
    return "[2026-10-29 21:00:00] " + json.dumps(event)


def test_health_distinguishes_stale_bridge_delivery_and_new_session():
    health = monitor.DisplayHealth()
    at = datetime(2026, 10, 29, 21).timestamp()
    health.consume(record({"event": "stream_session_start", "run_id": "a"}))
    health.consume(record({"event": "hdmi_link_state", "run_id": "a", "video_stable": True}))
    health.consume("[2026-10-29 21:00:00] estendendo USB 60 fps")
    assert health.snapshot(at + 5, {})["delivery"] == "recebendo"
    assert health.snapshot(at + 5, {})["bridge"] is True
    health.consume(record({"event": "hdmi_link_state", "run_id": "other", "video_stable": False}))
    assert health.video_stable is True
    health.consume(record({"event": "hdmi_link_state", "run_id": "a", "video_stable": False}))
    assert health.counts["hdmi_losses"] == 1
    health.consume(record({"event": "usb_transfer_error", "status": 2}))
    assert health.last_fault["status"] == 2
    assert health.snapshot(at + 31, {})["bridge"] is None
    assert health.snapshot(at + 31, {})["delivery"] == "sem_sinal_recente"
    health.consume(record({"event": "stream_session_end"}))
    assert health.snapshot(at, {})["delivery"] == "sem_sinal_recente"
    health.consume(record({"event": "stream_session_start", "run_id": "b"}))
    assert health.delivery_at == 0 and health.video_stable is None
    assert health.counts == {"sessions": 2, "usb_faults": 1, "hdmi_losses": 1}
    for malformed in ("no timestamp", "[2026-10-29 21:00:00] {bad", record([])):
        health.consume(malformed)


def test_status_is_atomic_and_history_only_tracks_changes(tmp_path):
    status = monitor.DisplayHealth().snapshot(0, {"extend": False})
    signature = monitor.publish_status(tmp_path, status, "")
    monitor.publish_status(tmp_path, status, signature)
    assert json.loads((tmp_path / "fl2000-re-health.json").read_text()) == status
    assert len((tmp_path / "fl2000-re-health-history.jsonl").read_text().splitlines()) == 1
    assert not (tmp_path / "fl2000-re-health.tmp").exists()


def test_launchd_probe_is_bounded_and_missing_pid_is_unhealthy(monkeypatch):
    def run(args, **kwargs):
        assert args == ["launchctl", "list", "example"] and kwargs["timeout"] == 3
        return SimpleNamespace(returncode=0, stdout='"PID" = 123;')

    monkeypatch.setattr(monitor.subprocess, "run", run)
    assert monitor.service_running("example")
    monkeypatch.setattr(
        monitor.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="")
    )
    assert not monitor.service_running("example")

    def fail(*args, **kwargs):
        raise subprocess.TimeoutExpired("launchctl", 3)

    monkeypatch.setattr(monitor.subprocess, "run", fail)
    assert not monitor.service_running("example")


def test_monitor_runs_without_usb_and_closes_follower(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(monitor, "service_running", lambda _: True)

    def stop(_):
        raise KeyboardInterrupt

    monkeypatch.setattr(monitor.time, "sleep", stop)
    with pytest.raises(KeyboardInterrupt):
        monitor.main()
    assert (tmp_path / "Library/Logs/fl2000-re-health.json").exists()
    config = plistlib.loads(monitor.monitor_plist(tmp_path, tmp_path))
    assert config["KeepAlive"] and config["RunAtLoad"]
    assert config["ProgramArguments"][-1] == "fl2000_re.health_monitor"


def test_session_metrics_keep_fault_provenance_and_observer_state():
    health = monitor.DisplayHealth()
    at = datetime(2026, 10, 29, 21).timestamp()
    health.consume(record({"event": "stream_session_start", "run_id": "a"}))
    health.consume(record({"event": "usb_transfer_error"}))
    assert health.snapshot(at + 40, {})["session_counts"]["usb_faults"] == 1
    assert health.last_fault["run_id"] == "a"
    health.consume(record({"event": "stream_session_start", "run_id": "b"}))
    health.consume(record({"event": "hdmi_link_watch_disabled", "run_id": "a"}))
    assert health.observer_enabled is None
    health.consume(record({"event": "hdmi_link_watch_disabled", "run_id": "b"}))
    status = health.snapshot(at + 40, {})
    assert status["usb_observer_enabled"] is False
    assert status["session_counts"] == {"usb_faults": 0, "hdmi_losses": 0}
    assert status["session_age_s"] == 40
    assert status["last_usb_fault"]["run_id"] == "a"


def test_restart_does_not_erase_recent_failure_verdict():
    health = monitor.DisplayHealth()
    at = datetime(2026, 10, 29, 21).timestamp()
    health.consume(record({"event": "stream_session_start", "run_id": "a"}))
    health.consume(record({"event": "usb_transfer_error"}))
    health.consume(record({"event": "stream_session_start", "run_id": "b"}))
    assert health.assessment(at + 30) == "queda_recente"
    assert health.assessment(at + 601) == "observacao_parcial_sem_transmissor"
    health.consume(record({"event": "hdmi_link_state", "run_id": "b", "video_stable": True}))
    assert health.assessment(at + 601) == "em_observacao_sem_confirmacao_visual_automatica"
