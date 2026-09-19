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

    def is_alive(self) -> bool:
        return True


class SegfaultedCaptureProcess(FakeProcess):
    """mss.grab → CGImageGetWidth SIGSEGV in the capture child."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.exitcode = -11

    def is_alive(self) -> bool:
        return False


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


def test_capture_worker_exit_message_explains_sigsegv():
    from fl2000_re.stream import capture_worker_exit_message

    line = capture_worker_exit_message(-11)
    assert "sinal 11" in line
    assert "CGImageGetWidth" in line
    assert "último frame" in line


def test_capture_worker_exit_message_other_signal():
    from fl2000_re.stream import capture_worker_exit_message

    line = capture_worker_exit_message(-9)
    assert "sinal 9" in line
    assert "último frame" in line


def test_capture_worker_exit_message_none_and_nonzero():
    from fl2000_re.stream import capture_worker_exit_message

    none_line = capture_worker_exit_message(None)
    assert "sem exitcode" in none_line
    assert "último frame" in none_line
    code_line = capture_worker_exit_message(1)
    assert "código 1" in code_line
    assert "CGImageGetWidth" not in code_line


def test_pace_clone_reports_dead_capture_worker(monkeypatch, capsys):
    fl = FL2000(dev=FakeBulkDevice())
    monkeypatch.setattr(stream.mp, "Process", SegfaultedCaptureProcess)
    monkeypatch.setattr(stream, "release_bulk", lambda _fl: None)
    assert stream._pace_clone(fl, bytes(512), MODE_640x480, None, 0.05) == 0
    out = capsys.readouterr().out
    assert "sinal 11" in out
    assert "CGImageGetWidth" in out
    assert fl.dev.packets
    assert out.count("sinal 11") == 1


def test_warn_if_capture_dead_only_once(capsys):
    class Dead:
        exitcode = -11

        def is_alive(self) -> bool:
            return False

    dead = Dead()
    assert stream._warn_if_capture_dead(dead, False) is True
    assert stream._warn_if_capture_dead(dead, True) is True
    assert capsys.readouterr().out.count("sinal 11") == 1


def test_format_stream_stats_names_usb_bottleneck():
    from fl2000_re.stream import format_stream_stats

    line = format_stream_stats(
        label="estendendo",
        capture_fps=60.0,
        usb_fps=20.0,
        grab_ms=4.0,
        cursor=False,
        target_fps=60,
    )
    assert "gargalo: USB" in line
    assert "--no-cursor" not in line


def test_format_stream_stats_names_capture_bottleneck():
    from fl2000_re.stream import format_stream_stats

    line = format_stream_stats(
        label="estendendo",
        capture_fps=7.4,
        usb_fps=60.0,
        grab_ms=134.0,
        cursor=True,
        target_fps=60,
    )
    assert "captura 7.4 fps" in line
    assert "USB 60.0 fps" in line
    assert "gargalo: captura" in line
    assert "--no-cursor" in line
    assert "mss+cursor" in line


def test_format_stream_stats_quiet_when_on_target():
    from fl2000_re.stream import format_stream_stats

    line = format_stream_stats(
        label="estendendo",
        capture_fps=58.0,
        usb_fps=60.0,
        grab_ms=5.0,
        cursor=False,
        target_fps=60,
    )
    assert "gargalo: nenhum" in line
    assert "--no-cursor" not in line
    assert "mss+cursor" not in line
    assert "(mss " in line


def test_hdmi_capture_worker_honors_no_cursor(monkeypatch):
    seen: dict[str, object] = {}

    def fake_grab_cg(display_id: int, include_cursor: bool = False):
        seen["include_cursor"] = include_cursor
        return bytes([1, 2, 3]) * (64 * 48), 64, 48

    monkeypatch.setattr(stream, "grab_cg_display_rgb", fake_grab_cg)
    n = 64 * 48 * 2
    shm = mp.Array("B", n, lock=False)
    counter = mp.Value("i", 0)
    grab_ms = mp.Value("d", 0.0)
    stop = mp.Event()
    thread = threading.Thread(
        target=stream.hdmi_capture_worker,
        args=(64, 48, 2, 18, shm, n, counter, stop, 1.0, 1.0, False, grab_ms),
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 3
    while counter.value == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    stop.set()
    thread.join(timeout=3)
    assert seen == {"include_cursor": False}


def test_hdmi_capture_worker_requests_cursor_on_virtual_display(monkeypatch):
    seen: dict[str, object] = {}

    def fake_grab_cg(display_id: int, include_cursor: bool = False):
        seen["display_id"] = display_id
        seen["include_cursor"] = include_cursor
        return bytes([1, 2, 3]) * (64 * 48), 64, 48

    monkeypatch.setattr(stream, "grab_cg_display_rgb", fake_grab_cg)
    n = 64 * 48 * 2
    shm = mp.Array("B", n, lock=False)
    counter = mp.Value("i", 0)
    grab_ms = mp.Value("d", 0.0)
    stop = mp.Event()
    thread = threading.Thread(
        target=stream.hdmi_capture_worker,
        args=(64, 48, 2, 18, shm, n, counter, stop, 1.0, 1.0, True, grab_ms),
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 3
    while counter.value == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    stop.set()
    thread.join(timeout=3)
    assert seen == {"display_id": 18, "include_cursor": True}
    assert grab_ms.value > 0


def _stub_extend_preview(monkeypatch, tmp_path):
    """cmd_extend without USB, helper, or mss. Parent preview must use screencapture."""
    from fl2000_re.virtual_display import VirtualScreen, VirtualScreenHandle

    monkeypatch.chdir(tmp_path)
    seen: dict[str, object] = {}

    class FakeProc:
        def poll(self) -> int | None:
            return None

        def terminate(self) -> None:
            return None

        def wait(self, timeout: float | None = None) -> int:
            return 0

        def kill(self) -> None:
            return None

    screen = VirtualScreen(18, 64, 48, -64, 0)
    handle = VirtualScreenHandle(screen, FakeProc())

    def boom_cg(*_a, **_k):
        raise AssertionError("grab_cg_display_rgb (mss) must not run in the USB process")

    def boom_main():
        raise AssertionError("grab_main_display_rgb (mss) must not run in the USB process")

    def fake_preview(display_id=None):
        seen["preview_id"] = display_id
        return bytes([255, 0, 0]) * (64 * 48), 64, 48

    def fake_pace(_fl, _frame, _mode, display_id, _seconds, **kwargs):
        seen["pace_display"] = display_id
        seen["pace_cursor"] = kwargs.get("cursor")
        return 0

    monkeypatch.setattr("fl2000_re.virtual_display.spawn_virtual_display", lambda **_k: handle)
    monkeypatch.setattr(stream, "grab_cg_display_rgb", boom_cg)
    monkeypatch.setattr(stream, "grab_main_display_rgb", boom_main)
    monkeypatch.setattr(stream, "grab_via_screencapture", fake_preview)
    monkeypatch.setattr(stream, "bring_up_hdmi", lambda *_a, **_k: 0)
    monkeypatch.setattr(stream, "_pace_clone", fake_pace)
    return seen


def test_cmd_extend_preview_never_calls_mss(monkeypatch, tmp_path):
    seen = _stub_extend_preview(monkeypatch, tmp_path)
    fl = FL2000(dev=FakeBulkDevice())
    rc = stream.cmd_extend(
        fl, 0.01, underscan=1.0, stretch_x=1.0, mode=MODE_640x480, place="left", cursor=False
    )
    assert rc == 0
    assert seen["preview_id"] == 18
    assert seen["pace_display"] == 18
    assert seen["pace_cursor"] is False
    assert (tmp_path / "preview.png").is_file()


def test_cmd_extend_defaults_cursor_on_into_pace(monkeypatch, tmp_path):
    seen = _stub_extend_preview(monkeypatch, tmp_path)
    fl = FL2000(dev=FakeBulkDevice())
    assert stream.cmd_extend(fl, 0.01, underscan=1.0, mode=MODE_640x480) == 0
    assert seen["pace_cursor"] is True


def test_cmd_mirror_preview_never_calls_mss(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        stream,
        "grab_main_display_rgb",
        lambda: (_ for _ in ()).throw(AssertionError("mss primary in USB process")),
    )
    monkeypatch.setattr(
        stream,
        "grab_cg_display_rgb",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("mss cg in USB process")),
    )
    monkeypatch.setattr(
        stream,
        "grab_via_screencapture",
        lambda display_id=None: (bytes([255, 0, 0]) * (64 * 48), 64, 48),
    )
    monkeypatch.setattr(stream, "bring_up_hdmi", lambda *_a, **_k: 0)
    monkeypatch.setattr(stream, "_pace_clone", lambda *_a, **_k: 0)
    fl = FL2000(dev=FakeBulkDevice())
    assert stream.cmd_mirror(fl, 0.01, 1, underscan=1.0, mode=MODE_640x480) == 0
    assert (tmp_path / "preview.png").is_file()


def test_pace_clone_streams_paced_without_relying_on_capture(monkeypatch):
    fl = FL2000(dev=FakeBulkDevice())
    monkeypatch.setattr(stream.mp, "Process", FakeProcess)
    monkeypatch.setattr(stream, "release_bulk", lambda _fl: None)
    assert stream._pace_clone(fl, bytes(512), MODE_640x480, None, 0.1) == 0
    assert fl.dev.packets
    assert set(fl.dev.packets) == {bytes(512), b""}
