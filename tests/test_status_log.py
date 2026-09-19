"""Monitor-link telemetry: REG_STATUS flags, frame counter and JSON log."""

import json

import pytest
from fl2000_re.fl2000_usb import FL2000, status_flags, status_frame_count
from fl2000_re.status_log import cmd_monitor_log, frame_rate, poll_status, status_snapshot


class FakeUSBDevice:
    """Returns a fixed REG_STATUS dword for every EP0 read."""

    def __init__(self, status: int = 0) -> None:
        self.status = status

    def set_configuration(self) -> None:
        return None

    def ctrl_transfer(self, request_type, request, value, index, data, timeout=None):
        return int(self.status).to_bytes(4, "little")


def test_status_flags_names_monitor_and_underflow():
    assert status_flags((1 << 9) | (1 << 29)) == ["lbuf_udf", "monitor"]


def test_status_frame_count_reads_bits_10_to_25():
    assert status_frame_count((1234 << 10) | 0b1) == 1234


def test_status_snapshot_is_json_ready():
    fl = FL2000(dev=FakeUSBDevice((7 << 10) | (1 << 27)))
    snap = status_snapshot(fl)
    assert snap == {"raw": "0x08001C00", "frame": 7, "flags": ["hdmi"]}
    json.dumps(snap)


def test_frame_rate_handles_counter_wrap():
    assert frame_rate(65530, 4, 1.0) == pytest.approx(10.0)


def test_frame_rate_zero_elapsed_is_zero():
    assert frame_rate(1, 2, 0) == 0.0


def test_poll_status_yields_samples(monkeypatch):
    fl = FL2000(dev=FakeUSBDevice(0))
    monkeypatch.setattr("fl2000_re.status_log.time.sleep", lambda _seconds: None)
    assert list(poll_status(fl, 0.02, 0.001))


def test_cmd_monitor_log_writes_jsonl(tmp_path, monkeypatch):
    fl = FL2000(dev=FakeUSBDevice(1 << 28))
    monkeypatch.setattr("fl2000_re.status_log.time.sleep", lambda _seconds: None)
    out = tmp_path / "monitor.jsonl"
    assert cmd_monitor_log(fl, 0.02, str(out)) == 0
    records = [json.loads(line) for line in out.read_text().splitlines()]
    assert records
    assert records[0]["flags"] == ["edid"]
