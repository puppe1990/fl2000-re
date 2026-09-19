"""Bulk pump cadence and capture worker. No USB: pure timing/worker helpers only."""

import multiprocessing as mp
import threading
import time

import fl2000_re.stream as stream
import pytest
from fl2000_re.stream import frame_period_s


def test_frame_period_is_one_over_freq():
    assert frame_period_s(60) == pytest.approx(1 / 60)


def test_frame_period_rejects_zero_freq():
    with pytest.raises(ValueError, match="freq=0"):
        frame_period_s(0)


def _run_worker_once(monkeypatch, width=64, height=48, bpp=2, underscan=1.0, stretch_x=1.0):
    src = bytes([255, 0, 0]) * (width * height)
    monkeypatch.setattr(stream, "grab_main_display_rgb", lambda: (src, width, height))
    n = width * height * bpp
    shm = mp.Array("B", n, lock=False)
    counter = mp.Value("i", 0)
    stop = mp.Event()
    thread = threading.Thread(
        target=stream.hdmi_capture_worker,
        args=(width, height, bpp, None, shm, n, counter, stop, underscan, stretch_x),
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 3
    while counter.value == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    stop.set()
    thread.join(timeout=3)
    buf = shm.get_obj() if hasattr(shm, "get_obj") else shm
    return bytes(buf[:n]), counter.value


def test_hdmi_capture_worker_packs_one_frame(monkeypatch):
    packed, count = _run_worker_once(monkeypatch)
    assert count >= 1
    assert any(packed)


def test_hdmi_capture_worker_forwards_fill_params(monkeypatch):
    seen: dict[str, float] = {}

    def fake_grab_letterboxed(width, height, grab=None, underscan=1.0, stretch_x=1.0):
        seen["underscan"] = underscan
        seen["stretch_x"] = stretch_x
        return bytes(width * height * 3)

    monkeypatch.setattr(stream, "grab_letterboxed_rgb", fake_grab_letterboxed)
    _run_worker_once(monkeypatch, underscan=1.0, stretch_x=1.1585)
    assert seen == {"underscan": 1.0, "stretch_x": 1.1585}
