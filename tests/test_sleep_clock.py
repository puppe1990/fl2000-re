"""Sleep detection must ignore wall-clock changes and interrupted clock reads."""

import fl2000_re.sleep_clock as clocks
import fl2000_re.stream as stream
import pytest


def setup_clocks(monkeypatch, values):
    monkeypatch.setattr(clocks.time, "CLOCK_MONOTONIC_RAW", 4, raising=False)
    monkeypatch.setattr(clocks.time, "CLOCK_UPTIME_RAW", 8, raising=False)
    samples = iter(values)
    calls = []

    def read(clock):
        calls.append(clock)
        return next(samples)

    monkeypatch.setattr(clocks.time, "clock_gettime", read)
    return calls


def test_pair_uses_two_monotonic_sources_and_brackets_the_sample(monkeypatch):
    calls = setup_clocks(monkeypatch, [100, 70, 100.002])
    assert clocks.sleep_clock_pair() == pytest.approx((100.001, 70))
    assert calls == [4, 8, 4]


def test_wall_clock_step_cannot_trigger_display_restart(monkeypatch):
    setup_clocks(monkeypatch, [101, 71, 101.002])
    monkeypatch.setattr(clocks.time, "time", lambda: 9_999_999_999.0)
    assert stream._raise_if_woke(100.001, 70) == pytest.approx((101.001, 71))


def test_preemption_during_clock_sampling_is_not_a_sleep_event(monkeypatch):
    setup_clocks(monkeypatch, [101, 71, 106])
    assert stream._raise_if_woke(100, 70) == (100, 70)


def test_real_sleep_gap_still_triggers_recovery_and_logs_measurement(monkeypatch, capsys):
    setup_clocks(monkeypatch, [130, 70.1, 130.002])
    with pytest.raises(stream.SystemWakeError):
        stream._raise_if_woke(100, 70)
    output = capsys.readouterr().out
    assert '"event": "system_wake_detected"' in output
    assert "monotonic_raw_minus_uptime_raw" in output
    assert "29.901" in output


def test_platform_without_awake_clock_does_not_fall_back_to_wall_time(monkeypatch):
    monkeypatch.delattr(clocks.time, "CLOCK_UPTIME_RAW", raising=False)
    assert clocks.sleep_clock_pair() is None
    assert stream._read_clocks() == (None, None)
    assert stream._raise_if_woke(None, None) == (None, None)


def test_first_valid_clock_sample_only_initializes_baseline(monkeypatch):
    setup_clocks(monkeypatch, [100, 70, 100])
    assert stream._raise_if_woke(None, None) == (100, 70)
