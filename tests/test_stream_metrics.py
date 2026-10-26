"""Metrics distinguish capture delivery from USB repeats and scheduler stalls."""

import pytest
from fl2000_re.stream_metrics import CaptureTimings, PumpMetrics, SharedCaptureMetrics


def test_shared_capture_stage_timings_round_trip():
    metrics = SharedCaptureMetrics()
    timings = CaptureTimings(12, 9, 3, 60, 32, "movimento", "vimage", True, 80)
    metrics.record(timings, captured_at=10.0)
    assert metrics.read() == timings
    assert metrics.values[9] == 10.0


def test_fresh_and_repeated_deliveries_are_separate_and_intervals_reset():
    metrics = PumpMetrics(60)
    metrics.record(1.0, 1.003, True, 0.99)
    metrics.record(1.02, 1.025, False, 0.99)
    line = metrics.format_interval(1.0, CaptureTimings())
    assert "novas 1.0 fps repetidas 1.0 fps" in line
    assert "USB-max 5ms" in line
    assert "idade-max 35ms" in line
    assert metrics.fresh == metrics.repeated == 0
    metrics.record(1.04, 1.043, True, 1.03)
    assert metrics.max_gap_ms == pytest.approx(20.0)


def test_stall_is_visible_even_when_average_usb_fps_recovers():
    metrics = PumpMetrics(60)
    metrics.record(1.0, 1.001, True, 0.99)
    metrics.record(1.1, 1.101, True, 1.09)
    line = metrics.format_interval(1.0, CaptureTimings(mode="repouso"))
    assert "pausa-max 100ms" in line
    assert "atrasos 1" in line
    assert "modo repouso/pillow" in line


def test_preview_has_no_capture_age_until_first_real_frame():
    metrics = PumpMetrics(60)
    metrics.record(100.0, 100.002, False, 0.0)
    assert metrics.max_age_ms == 0.0


def test_queue_latency_is_visible_and_reset_each_interval():
    metrics = PumpMetrics(60)
    metrics.record(1, 1.01, True, 0.9, queue_ms=65)
    assert "fila-max 65ms" in metrics.format_interval(1, CaptureTimings())
    assert metrics.max_queue_ms == 0
