"""Reproduce rotation, partial writes and missing live kernel events without USB."""

import json
import plistlib
import types
from pathlib import Path

from fl2000_re import fault_listener as listener


def line(event):
    return "[2026-10-26 15:42:08] " + json.dumps(event) + "\n"


def test_follower_reassembles_partial_utf8_lines(tmp_path):
    path = tmp_path / "source"
    path.write_bytes(b"")
    follower = listener.LogFollower(path)
    path.write_bytes(b"[x] captura caiu: resolu")
    assert follower.read_line() is None
    with path.open("ab") as output:
        output.write("ção\n".encode())
    assert follower.read_line() == ("[x] captura caiu: resolução", False)
    follower.close()
    follower.close()


def test_copytruncate_resets_offset_and_startup_history(tmp_path):
    path = tmp_path / "source"
    path.write_text("old historical record\n")
    follower = listener.LogFollower(path)
    assert follower.read_line() == ("old historical record", True)
    path.write_text("new\n")
    assert follower.read_line() == ("new", False)
    follower.close()


def test_rename_rotation_and_temporarily_missing_path(tmp_path):
    path = tmp_path / "source"
    path.write_text("old\n")
    follower = listener.LogFollower(path)
    assert follower.read_line() == ("old", True)
    path.rename(tmp_path / "old")
    assert follower.read_line() is None
    path.write_text("new session\n")
    assert follower.read_line() == ("new session", False)
    follower.close()


def test_context_is_bounded_and_reset_at_new_session():
    context = listener.FaultContext()
    context.consume(line({"event": "stream_session_start", "run_id": "old"}))
    for index in range(30):
        context.consume(line({"event": "hdmi_link_state", "frame": index}))
        context.consume(f"[timestamp] estendendo USB {index} fps")
    fault = context.consume(line({"event": "usb_frame_incomplete", "sent_bytes": 4}))
    assert len(fault["link_states"]) == 20 and len(fault["recent_stats"]) == 8
    context.consume(line({"event": "stream_session_start", "run_id": "new"}))
    fault = context.consume(line({"event": "usb_transfer_error"}))
    assert fault["session"]["run_id"] == "new"
    assert fault["link_states"] == [] and fault["recent_stats"] == []
    assert context.consume("malformed {json") is None
    assert context.consume("{[1,2]") is None
    assert context.consume(line({"event": []})) is None


def test_listener_skips_historical_faults_and_correlates_future_kernel_snapshot(tmp_path):
    source, output = tmp_path / "source", tmp_path / "faults"
    source.write_text(
        line({"event": "stream_session_start", "run_id": "run", "usb_address": 2})
        + line({"event": "usb_frame_incomplete", "sent_bytes": 1})
    )
    state = {"added": False, "done": False}
    seen = []

    def sleep(seconds):
        if seconds == 0.1 and not state["added"]:
            with source.open("a") as stream:
                stream.write(line({"event": "usb_transfer_error", "errno": 5}))
            state["added"] = True

    def collect(at, address):
        seen.append((at, address))
        state["done"] = True
        return [{"eventMessage": "endpoint 0x01: timeout"}]

    listener.run_listener(source, output, stop=lambda: state["done"], sleep=sleep, collect=collect)
    bundle, kernel = [json.loads(row) for row in output.read_text().splitlines()]
    assert bundle["fault"]["event"] == "usb_transfer_error"
    assert bundle["session"]["run_id"] == "run"
    assert bundle["bundle_id"] == kernel["bundle_id"]
    assert seen == [("2026-10-26 15:42:08", 2)]


def test_kernel_query_failure_preserves_fault_bundle(tmp_path):
    output = tmp_path / "faults"
    bundle = {"event": "usb_fault_bundle", "bundle_id": "id", "fault_at": "bad", "session": {}}
    listener._capture_fault(
        bundle, output, lambda _: None, lambda *args: (_ for _ in ()).throw(ValueError("bad time"))
    )
    rows = [json.loads(row) for row in output.read_text().splitlines()]
    assert rows[0] == bundle
    assert rows[1]["error"] == "bad time"


def test_kernel_lookup_filters_target_and_discards_unrelated_fields(monkeypatch):
    seen = []
    payload = {
        "timestamp": "now",
        "eventMessage": "timeout",
        "messageType": "Error",
        "backtrace": "large",
    }
    monkeypatch.setattr(
        listener.subprocess,
        "run",
        lambda args, **kwargs: (
            seen.append((args, kwargs))
            or types.SimpleNamespace(stdout="header\n" + json.dumps(payload))
        ),
    )
    rows = listener.kernel_fault_events("2026-10-26 15:42:08", 2)
    assert rows == [{key: payload[key] for key in ("timestamp", "messageType", "eventMessage")}]
    assert "device 2 (" in seen[0][0][-1]
    assert seen[0][1]["timeout"] == 10


def test_installed_listener_is_persistent_and_has_no_usb_capture_command(tmp_path):
    config = plistlib.loads(
        listener.listener_plist(tmp_path, Path("/python"), tmp_path / "logs").encode()
    )
    assert config["KeepAlive"] and config["RunAtLoad"]
    assert config["ProgramArguments"] == ["/python", "-u", "-m", "fl2000_re.fault_listener"]
    assert config["WorkingDirectory"] == str(tmp_path)


def test_fault_bundle_keeps_compression_size_and_resets_it_for_new_run():
    context = listener.FaultContext()
    context.consume(line({"event": "stream_session_start", "run_id": "first"}))
    context.consume(
        line({"event": "usb_wire_compression", "wire_bytes": 849396, "encoder_backend": "native"})
    )
    fault = context.consume(line({"event": "usb_transfer_error"}))
    assert fault["wire_compression"]["wire_bytes"] == 849396
    context.consume(line({"event": "stream_session_start", "run_id": "second"}))
    assert context.consume(line({"event": "usb_transfer_error"}))["wire_compression"] == {}
