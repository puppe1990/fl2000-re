"""Bulk pump cadence and capture worker. No USB: pure timing/worker helpers only."""

import multiprocessing as mp
import threading
import time
import types

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


class RevivedCaptureProcess(FakeProcess):
    """Dead on the first spawn, alive after the automatic restart."""

    alive_after_restart = False

    def is_alive(self) -> bool:
        return RevivedCaptureProcess.alive_after_restart


class FakeCapture:
    """Stands in for CaptureProcess in _ensure_capture unit tests."""

    def __init__(self, alive: bool, counter_value: int = 0, restarts: int = 1) -> None:
        self._alive = alive
        self.counter = types.SimpleNamespace(value=counter_value)
        self._exitcode = -11
        self._restarts = restarts
        self.restart_calls = 0

    def alive(self) -> bool:
        return self._alive

    def exitcode(self) -> int | None:
        return self._exitcode

    def restart(self) -> bool:
        self.restart_calls += 1
        if self._restarts <= 0:
            return False
        self._restarts -= 1
        return True


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


def test_capture_process_restart_respects_budget(monkeypatch, capsys):
    monkeypatch.setattr(stream.mp, "Process", FakeProcess)
    capture = stream.CaptureProcess(
        bytes(512), MODE_640x480, None, 1.0, 1.0, False, restarts_left=2
    )
    capture.start()
    assert capture.alive() is True
    assert len(capture.buffer()) == 512
    assert capture.restart() is True
    assert capture.restart() is True
    assert capture.restart() is False
    assert "reiniciando a captura" in capsys.readouterr().out
    capture.stop_and_join()


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


def test_pace_clone_gives_up_after_restart_budget(monkeypatch, capsys):
    fl = FL2000(dev=FakeBulkDevice())
    monkeypatch.setattr(stream.mp, "Process", SegfaultedCaptureProcess)
    monkeypatch.setattr(stream, "release_bulk", lambda _fl: None)
    assert stream._pace_clone(fl, bytes(512), MODE_640x480, None, 0.05, restart_budget=1) == 1
    out = capsys.readouterr().out
    assert "sinal 11" in out
    assert "CGImageGetWidth" in out
    assert "sem captura" in out
    assert fl.dev.packets


def test_pace_clone_respawns_dead_capture_and_keeps_streaming(monkeypatch, capsys):
    """Regression 2026-09-20: a SIGKILLed capture child must be respawned."""
    fl = FL2000(dev=FakeBulkDevice())
    RevivedCaptureProcess.alive_after_restart = False
    spawns = {"n": 0}

    def factory(**kwargs):
        spawns["n"] += 1
        if spawns["n"] >= 2:
            RevivedCaptureProcess.alive_after_restart = True
        return RevivedCaptureProcess(**kwargs)

    monkeypatch.setattr(stream.mp, "Process", factory)
    monkeypatch.setattr(stream, "release_bulk", lambda _fl: None)
    rc = stream._pace_clone(fl, bytes(512), MODE_640x480, None, 0.1, restart_budget=2)
    assert rc == 0
    assert spawns["n"] == 2
    assert "reiniciando a captura" in capsys.readouterr().out


def test_ensure_capture_restarts_dead_child(capsys):
    capture = FakeCapture(alive=False, counter_value=7, restarts=1)
    alive, last_cap, progress = stream._ensure_capture(capture, 0, 0.0, 10.0)
    assert alive is True
    assert last_cap == 7
    assert progress == 10.0
    assert capture.restart_calls == 1
    assert "sinal 11" in capsys.readouterr().out


def test_ensure_capture_gives_up_without_budget(capsys):
    capture = FakeCapture(alive=False, restarts=0)
    alive, _, _ = stream._ensure_capture(capture, 0, 0.0, 10.0)
    assert alive is False
    assert "sem captura" in capsys.readouterr().out


def test_ensure_capture_restarts_wedged_child(capsys):
    capture = FakeCapture(alive=True, counter_value=3, restarts=1)
    alive, _, _ = stream._ensure_capture(
        capture, last_cap=3, last_progress=0.0, now=stream.CAPTURE_STALL_S + 1
    )
    assert alive is True
    assert capture.restart_calls == 1
    assert "travada" in capsys.readouterr().out


def test_ensure_capture_keeps_live_child(capsys):
    capture = FakeCapture(alive=True, counter_value=42)
    alive, last_cap, progress = stream._ensure_capture(capture, 10, 0.0, 1.0)
    assert alive is True
    assert last_cap == 42
    assert progress == 1.0
    assert capture.restart_calls == 0
    assert capsys.readouterr().out == ""


def test_ensure_capture_allows_slow_first_frame(capsys):
    """Regression 2026-09-20: a cold spawn child needs >5s to import and grab once."""
    capture = FakeCapture(alive=True, counter_value=1, restarts=1)
    alive, _, _ = stream._ensure_capture(
        capture,
        last_cap=1,
        last_progress=0.0,
        now=stream.CAPTURE_STALL_S + 1,
        stall_s=stream.CAPTURE_START_S,
    )
    assert alive is True
    assert capture.restart_calls == 0
    assert capsys.readouterr().out == ""


def test_capture_process_has_frame_tracks_counter(monkeypatch):
    monkeypatch.setattr(stream.mp, "Process", FakeProcess)
    capture = stream.CaptureProcess(bytes(512), MODE_640x480, None, 1.0, 1.0, False)
    assert capture.has_frame() is False
    capture.start()
    assert capture.has_frame() is False
    capture.counter.value += 1
    assert capture.has_frame() is True
    capture.stop_and_join()


class TickingCapture:
    """Counter advances on every read; guards the stats-delta regression."""

    def __init__(self) -> None:
        self._n = 0
        self.counter = self
        self.grab_ms = types.SimpleNamespace(value=5.0)

    @property
    def value(self) -> int:
        self._n += 1
        return self._n

    def alive(self) -> bool:
        return True

    def has_frame(self) -> bool:
        return True

    def exitcode(self) -> None:
        return None

    def restart(self) -> bool:
        return True

    def buffer(self) -> bytes:
        return bytes(512)


def test_pump_reports_capture_fps_when_counter_advances(monkeypatch, capsys):
    """Regression: the stall watchdog must not consume the stats counter delta."""
    monkeypatch.setattr(stream, "STATS_INTERVAL_S", 0.0)
    fl = FL2000(dev=FakeBulkDevice())
    sent = stream._pump_paced_frames(
        fl, TickingCapture(), MODE_640x480, 0.05, time.monotonic(), "estendendo", False
    )
    assert sent > 0
    out = capsys.readouterr().out
    assert "captura 0.0 fps" not in out
    assert "captura " in out


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

    def fake_bring_up(*_a, **_k):
        seen["brought_up"] = True
        return 0

    monkeypatch.setattr("fl2000_re.virtual_display.spawn_virtual_display", lambda **_k: handle)
    monkeypatch.setattr(stream, "grab_cg_display_rgb", boom_cg)
    monkeypatch.setattr(stream, "grab_main_display_rgb", boom_main)
    monkeypatch.setattr(stream, "grab_via_screencapture", fake_preview)
    monkeypatch.setattr(stream, "bring_up_hdmi", fake_bring_up)
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


def test_grab_preview_retries_then_uses_black_frame(monkeypatch, capsys):
    from fl2000_re.capture import ScreencaptureError

    monkeypatch.setattr(stream, "PREVIEW_RETRY_S", 0.0)
    calls = {"n": 0}

    def hang(*_a, **_k):
        calls["n"] += 1
        raise ScreencaptureError("screencapture travou por 5s")

    monkeypatch.setattr(stream, "grab_letterboxed_rgb", hang)
    rgb, captured = stream._grab_preview_rgb(MODE_640x480, lambda: None, 1.0, 1.0)
    assert captured is False
    assert rgb == bytes(MODE_640x480.width * MODE_640x480.height * 3)
    assert calls["n"] == stream.PREVIEW_ATTEMPTS
    assert "fundo preto" in capsys.readouterr().out


def test_grab_preview_returns_frame_when_capture_works(monkeypatch):
    src = bytes([255, 0, 0]) * (64 * 48)
    monkeypatch.setattr(stream, "grab_letterboxed_rgb", lambda *_a, **_k: src)
    rgb, captured = stream._grab_preview_rgb(MODE_640x480, lambda: None, 1.0, 1.0)
    assert captured is True
    assert rgb is src


def test_cmd_extend_survives_hung_preview(monkeypatch, tmp_path):
    """Regression 2026-09-20: a screencapture timeout must not kill extend."""
    from fl2000_re.capture import ScreencaptureError

    seen = _stub_extend_preview(monkeypatch, tmp_path)
    monkeypatch.setattr(stream, "PREVIEW_RETRY_S", 0.0)

    def hang(_display_id=None):
        raise ScreencaptureError("screencapture travou por 5s")

    monkeypatch.setattr(stream, "grab_via_screencapture", hang)
    fl = FL2000(dev=FakeBulkDevice())
    rc = stream.cmd_extend(
        fl, 0.01, underscan=1.0, stretch_x=1.0, mode=MODE_640x480, place="left", cursor=False
    )
    assert rc == 0
    assert seen.get("brought_up") is True
    assert seen["pace_display"] == 18
    assert not (tmp_path / "preview.png").exists()


def test_cmd_mirror_survives_hung_preview(monkeypatch, tmp_path):
    from fl2000_re.capture import ScreencaptureError

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(stream, "PREVIEW_RETRY_S", 0.0)
    monkeypatch.setattr(
        stream,
        "grab_via_screencapture",
        lambda *_a, **_k: (_ for _ in ()).throw(ScreencaptureError("screencapture travou por 5s")),
    )
    monkeypatch.setattr(stream, "bring_up_hdmi", lambda *_a, **_k: 0)
    monkeypatch.setattr(stream, "_pace_clone", lambda *_a, **_k: 0)
    fl = FL2000(dev=FakeBulkDevice())
    assert stream.cmd_mirror(fl, 0.01, 1, underscan=1.0, mode=MODE_640x480) == 0
    assert not (tmp_path / "preview.png").exists()


def test_wall_minus_uptime_gap_is_sleep_duration():
    from fl2000_re.stream import wall_minus_uptime_gap, woke_from_sleep

    gap = wall_minus_uptime_gap(130.0, 50.2, 100.0, 50.0)
    assert gap == pytest.approx(29.8)
    assert woke_from_sleep(gap)
    assert not woke_from_sleep(0.05)


def test_pace_clone_exits_after_sleep_wake(monkeypatch, capsys):
    """macOS sleep leaves the process alive with a dead HDMI; exit so KeepAlive restarts."""
    fl = FL2000(dev=FakeBulkDevice())
    monkeypatch.setattr(stream.mp, "Process", FakeProcess)
    monkeypatch.setattr(stream, "release_bulk", lambda _fl: None)

    class JumpClocks:
        def __init__(self) -> None:
            self.n = 0

        def __call__(self) -> tuple[float, float]:
            self.n += 1
            if self.n < 4:
                return (100.0 + self.n * 0.02, 50.0 + self.n * 0.02)
            return (100.0 + 40.0, 50.0 + 0.1)

    monkeypatch.setattr(stream, "_read_clocks", JumpClocks())
    rc = stream._pace_clone(fl, bytes(512), MODE_640x480, None, 0.4)
    assert rc == 1
    assert "descanso" in capsys.readouterr().out


def test_pace_clone_streams_paced_without_relying_on_capture(monkeypatch):
    fl = FL2000(dev=FakeBulkDevice())
    monkeypatch.setattr(stream.mp, "Process", FakeProcess)
    monkeypatch.setattr(stream, "release_bulk", lambda _fl: None)
    assert stream._pace_clone(fl, bytes(512), MODE_640x480, None, 0.1) == 0
    assert fl.dev.packets
    assert set(fl.dev.packets) == {bytes(512), b""}
