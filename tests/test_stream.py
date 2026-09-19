"""Bulk pump cadence and capture worker. No USB: pure timing/worker helpers only."""

import multiprocessing as mp
import threading
import time

import fl2000_re.stream as stream
import pytest
from fl2000_re.fl2000_usb import FL2000
from fl2000_re.registers import BULK_EP
from fl2000_re.stream import frame_period_s, send_frame
from fl2000_re.video_modes import MODE_640x480


class FakeBulkDevice:
    """Records every bulk packet so ZLP behaviour is observable."""

    def __init__(self) -> None:
        self.packets: list[bytes] = []

    def set_configuration(self) -> None:
        return None

    def write(self, endpoint, data, timeout=None):
        assert endpoint == BULK_EP
        self.packets.append(bytes(data))
        return len(bytes(data))


class FakeProcess:
    """Stands in for the capture worker; _pace_clone only starts/joins it."""

    def __init__(self, **kwargs) -> None:
        self.exitcode = None

    def start(self) -> None:
        return None

    def join(self, timeout=None) -> None:
        return None

    def terminate(self) -> None:
        return None


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


def test_send_frame_adds_zlp_when_max_packet_aligned():
    fl = FL2000(dev=FakeBulkDevice())
    send_frame(fl, bytes(512))
    assert fl.dev.packets == [bytes(512), b""]


def test_send_frame_skips_zlp_when_short():
    fl = FL2000(dev=FakeBulkDevice())
    send_frame(fl, bytes(100))
    assert fl.dev.packets == [bytes(100)]


def test_pace_clone_streams_paced_without_relying_on_capture(monkeypatch):
    fl = FL2000(dev=FakeBulkDevice())
    monkeypatch.setattr(stream.mp, "Process", FakeProcess)
    monkeypatch.setattr(stream, "release_bulk", lambda _fl: None)
    assert stream._pace_clone(fl, bytes(512), MODE_640x480, None, 0.1) == 0
    assert fl.dev.packets
    assert set(fl.dev.packets) == {bytes(512), b""}
