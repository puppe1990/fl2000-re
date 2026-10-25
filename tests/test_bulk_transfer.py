"""The backend can report partial success on timeout; EOF must never be hidden."""

import json
import types

import fl2000_re.bulk_transfer as transfer
import pytest
import usb.core


class FakeDevice:
    def __init__(self, results):
        self.results = iter(results)
        self.calls = []

    def write(self, endpoint, payload, timeout):
        self.calls.append((endpoint, payload, timeout))
        result = next(self.results)
        if isinstance(result, Exception):
            raise result
        return result


def test_partial_timeout_return_is_rejected_without_eof_or_retry(capsys):
    device = FakeDevice([40])
    with pytest.raises(usb.core.USBError, match="40 bytes; esperados 100"):
        transfer.send_frame(types.SimpleNamespace(dev=device), bytes(100))
    assert len(device.calls) == 1
    event = json.loads(capsys.readouterr().out)
    assert event["event"] == "usb_frame_incomplete"
    assert event["sent_bytes"] == 40
    assert event["expected_bytes"] == 100


def test_eof_timeout_is_propagated_instead_of_silently_discarded(capsys):
    error = usb.core.USBTimeoutError("EOF timed out")
    device = FakeDevice([100, error])
    with pytest.raises(usb.core.USBTimeoutError):
        transfer.send_frame(types.SimpleNamespace(dev=device), bytes(100))
    assert len(device.calls) == 2
    event = json.loads(capsys.readouterr().out)
    assert event["phase"] == "eof"
    assert event["event"] == "usb_transfer_error"


def test_bulk_error_is_reported_without_retries(capsys):
    device = FakeDevice([usb.core.USBError("bulk stopped")])
    with pytest.raises(usb.core.USBError):
        transfer.send_frame(types.SimpleNamespace(dev=device), bytes(100))
    assert len(device.calls) == 1
    assert json.loads(capsys.readouterr().out)["phase"] == "pixels"


def test_two_second_success_reports_bulk_and_eof_times_separately(monkeypatch, capsys):
    clock = iter([1.0, 3.25, 3.25, 3.251])
    monkeypatch.setattr(transfer.time, "perf_counter", lambda: next(clock))
    device = FakeDevice([100, 0])
    transfer.send_frame(types.SimpleNamespace(dev=device), bytes(100))
    event = json.loads(capsys.readouterr().out)
    assert event["event"] == "usb_frame_slow"
    assert event["bulk_ms"] == pytest.approx(2250)
    assert event["eof_ms"] == pytest.approx(1)


def test_healthy_frame_is_quiet_and_always_sends_zlp(monkeypatch, capsys):
    clock = iter([0.0, 0.001, 0.001, 0.002])
    monkeypatch.setattr(transfer.time, "perf_counter", lambda: next(clock))
    device = FakeDevice([100, 0])
    transfer.send_frame(types.SimpleNamespace(dev=device), bytes(100))
    assert device.calls == [(1, bytes(100), 2000), (1, b"", 50)]
    assert not capsys.readouterr().out


def test_unexpected_eof_byte_count_is_rejected():
    device = FakeDevice([100, 1])
    with pytest.raises(usb.core.USBError, match="esperado 0"):
        transfer.send_frame(types.SimpleNamespace(dev=device), bytes(100))


def test_large_frame_uses_packet_aligned_blocks_and_one_final_eof():
    from fl2000_re.registers import BULK_BLOCK_BYTES

    frame = bytes(range(256)) * (1024 * 768 * 2 // 256)
    sizes = [BULK_BLOCK_BYTES] * 6
    device = FakeDevice([*sizes, 0])
    transfer.send_frame(types.SimpleNamespace(dev=device), frame)
    payloads = [call[1] for call in device.calls]
    assert b"".join(payloads[:-1]) == frame
    assert [len(p) for p in payloads[:-1]] == sizes
    assert all(size % 1024 == 0 for size in sizes)
    assert payloads[-1] == b""
    assert payloads.count(b"") == 1


def test_non_aligned_tail_is_sent_before_mandatory_eof():
    from fl2000_re.registers import BULK_BLOCK_BYTES

    frame = bytes(BULK_BLOCK_BYTES + 100)
    device = FakeDevice([BULK_BLOCK_BYTES, 100, 0])
    transfer.send_frame(types.SimpleNamespace(dev=device), frame)
    assert [len(c[1]) for c in device.calls] == [BULK_BLOCK_BYTES, 100, 0]


def test_partial_middle_block_stops_without_more_pixels_or_eof(capsys):
    from fl2000_re.registers import BULK_BLOCK_BYTES

    device = FakeDevice([BULK_BLOCK_BYTES, 400])
    frame = bytes(BULK_BLOCK_BYTES * 3)
    with pytest.raises(usb.core.USBError, match="Sem retry"):
        transfer.send_frame(types.SimpleNamespace(dev=device), frame)
    assert len(device.calls) == 2
    event = json.loads(capsys.readouterr().out)
    assert event["sent_bytes"] == BULK_BLOCK_BYTES + 400
    assert event["block_offset"] == BULK_BLOCK_BYTES
    assert event["block_sent"] == 400
    assert event["expected_bytes"] == len(frame)
