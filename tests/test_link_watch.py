"""Bridge diagnostics stay separate from USB video and never reconfigure the device."""

import json
import types

import fl2000_re.link_watch as watch
from fl2000_re.registers import REG_PLL, REG_STATUS


def reading(frame=100, now=1.0, stable=True):
    return {
        "chip_status": "0x00000000",
        "frame": frame,
        "flags": ["pll", "hdmi"],
        "pll": "0x001B2105",
        "ite_status": "0x10" if stable else "0x00",
        "video_stable": stable,
        "hpd": False,
        "rx_sense": False,
        "sampled_at": now,
    }


def test_snapshot_reads_only_status_clock_and_bridge_status():
    calls = []
    values = {REG_STATUS: (25 << 10) | (1 << 9), REG_PLL: 0x001B2105}
    fl = types.SimpleNamespace(reg_read=lambda reg: calls.append(reg) or values[reg])
    ite = types.SimpleNamespace(read_byte=lambda reg: calls.append(reg) or 0x70)
    state = watch.read_link_state(fl, ite)
    assert calls == [REG_STATUS, REG_PLL, 0x0E]
    assert state["frame"] == 25
    assert state["flags"] == ["lbuf_ovf"]
    assert state["video_stable"] and state["hpd"] and state["rx_sense"]


def test_counter_progress_does_not_spam_logs_every_poll(capsys):
    trace = watch.LinkTrace("run", 50)
    trace.record(reading())
    trace.record(reading(frame=150, now=2))
    trace.record(reading(frame=650, now=12))
    events = [json.loads(s) for s in capsys.readouterr().out.splitlines()]
    assert len(events) == 2
    assert events[0]["scanout_fps"] is None
    assert events[1]["scanout_fps"] == 50
    assert not events[1]["changed"]


def test_video_sync_loss_is_logged_immediately(capsys):
    trace = watch.LinkTrace("run", 50)
    trace.record(reading())
    trace.record(reading(frame=150, now=2, stable=False))
    events = [json.loads(s) for s in capsys.readouterr().out.splitlines()]
    assert len(events) == 2
    assert events[1]["changed"]
    assert not events[1]["video_stable"]
    assert events[1]["scanout_fps"] == 50


def test_stopped_chip_scanout_is_detected_before_heartbeat(capsys):
    trace = watch.LinkTrace("run", 50)
    trace.record(reading())
    trace.record(reading(frame=100, now=2))
    event = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert not event["scanout_running"]
    assert event["scanout_fps"] == 0


def test_frame_counter_wrap_is_not_a_scanout_failure():
    trace = watch.LinkTrace("run", 50)
    trace.previous = reading(frame=65530, now=1)
    assert trace._scanout_fps(reading(frame=44, now=2)) == 50


def test_fake_usb_does_not_start_any_real_hardware_observer():
    assert watch.start_link_watch(object(), "run", 50) is None
    watch.stop_link_watch(None)


def test_observer_close_stops_i2c_before_forcing_a_wedged_process():
    calls = []

    class Process:
        alive = True

        def join(self, timeout):
            calls.append("join")

        def is_alive(self):
            return self.alive

        def terminate(self):
            calls.append("terminate")

        def kill(self):
            self.alive = False
            calls.append("kill")

    stop = types.SimpleNamespace(set=lambda: calls.append("stop"))
    handle = watch.LinkWatch(Process(), stop)
    watch.stop_link_watch(handle)
    assert calls == ["stop", "join", "terminate", "join", "kill", "join"]


def test_start_isolated_observer_uses_same_device_and_session(monkeypatch):
    class Device:
        bus, address = 1, 2

    monkeypatch.setattr(watch.usb.core, "Device", Device)
    seen = {}

    def factory(**kwargs):
        seen.update(kwargs)
        return types.SimpleNamespace(start=lambda: seen.update(started=True))

    handle = watch.start_link_watch(Device(), "run", 50, factory)
    assert handle is not None
    assert seen["args"][:3] == ("run", 1, 2)
    assert seen["args"][-1] == 50
    assert seen["started"]


def test_worker_observes_without_reconfiguring_or_retrying(monkeypatch, capsys):
    calls = []
    device = object()
    monkeypatch.setattr(watch, "install_timestamped_streams", lambda: None)
    monkeypatch.setattr(watch.os, "nice", lambda level: None)
    monkeypatch.setattr(watch.usb.core, "find", lambda **kwargs: device)
    monkeypatch.setattr(watch, "FL2000", lambda **kwargs: calls.append(kwargs) or "fl")
    monkeypatch.setattr(watch, "IT66121", lambda fl: "ite")
    monkeypatch.setattr(watch, "read_link_state", lambda *args: reading())
    state = {"stop": False}
    stop = types.SimpleNamespace(
        is_set=lambda: state["stop"], wait=lambda s: state.update(stop=True)
    )
    watch._watch_link("run", 1, 2, stop, 50)
    assert calls == [{"dev": device, "configure": False}]
    assert json.loads(capsys.readouterr().out)["event"] == "hdmi_link_state"


def test_missing_observer_device_is_reported_once_without_affecting_feeder(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(watch, "install_timestamped_streams", lambda: None)
    monkeypatch.setattr(watch.os, "nice", lambda n: None)
    monkeypatch.setattr(watch.usb.core, "find", lambda **kwargs: calls.append(kwargs) or None)
    watch._watch_link("run", 1, 2, object(), 50)
    assert len(calls) == 1
    assert json.loads(capsys.readouterr().out)["event"] == "hdmi_link_watch_error"


def test_unavailable_observer_process_does_not_stop_stream(monkeypatch, capsys):
    class Device:
        bus, address = 1, 2

    monkeypatch.setattr(watch.usb.core, "Device", Device)

    def failed_start():
        raise OSError("cannot spawn")

    def factory(**kw):
        return types.SimpleNamespace(start=failed_start)

    assert watch.start_link_watch(Device(), "run", 50, factory) is None
    assert json.loads(capsys.readouterr().out)["event"] == "hdmi_link_watch_error"


def test_disabled_observer_never_starts_control_traffic(monkeypatch, capsys):
    class Device:
        bus, address = 1, 2

    monkeypatch.setattr(watch.usb.core, "Device", Device)
    monkeypatch.setenv("FL2000_LINK_WATCH", "0")

    def forbidden_factory(**kwargs):
        raise AssertionError("Disabled observer must not create a process")

    assert watch.start_link_watch(Device(), "run", 60, forbidden_factory) is None
    event = json.loads(capsys.readouterr().out)
    assert event == {"event": "hdmi_link_watch_disabled", "run_id": "run"}


def test_saturated_counter_is_unknown_not_a_false_scanout_stop(capsys):
    trace = watch.LinkTrace("long-session", 60)
    trace.record(reading(frame=65535, now=1))
    trace.record(reading(frame=65535, now=2))
    event = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert event["scanout_counter_saturated"] is True
    assert event["scanout_fps"] is None
    assert event["scanout_running"] is None
    trace.record(reading(frame=60, now=3))
    event = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert event["scanout_counter_saturated"] is False
    assert event["scanout_running"] is True
