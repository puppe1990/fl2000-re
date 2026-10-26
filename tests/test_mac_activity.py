"""Foundation activity ownership, cached bridge build and graceful fallback."""

import subprocess
import sys
import types

import pytest
from fl2000_re import mac_activity as activity
from fl2000_re import native_bridge as bridge


def test_video_activity_ends_retained_token_exactly_once():
    ended = []
    token = activity.VideoActivity(123, ended.append)
    token.close()
    token.close()
    assert ended == [123]


def test_other_platforms_do_not_load_foundation(monkeypatch):
    monkeypatch.setattr(activity.sys, "platform", "linux")
    monkeypatch.setattr(
        activity, "_load_activity_library", lambda: pytest.fail("loaded Foundation")
    )
    assert activity.begin_video_activity() is None


def test_foundation_activity_is_retained_until_stream_closes(monkeypatch, capsys):
    ended = []
    library = types.SimpleNamespace(
        fl2000_begin_video_activity=lambda: 42, fl2000_end_video_activity=ended.append
    )
    monkeypatch.setattr(activity.sys, "platform", "darwin")
    monkeypatch.setattr(activity, "_load_activity_library", lambda: library)
    token = activity.begin_video_activity()
    assert token.token == 42
    assert ended == []
    token.close()
    assert ended == [42]
    assert '"idle_sleep_allowed": true' in capsys.readouterr().out


@pytest.mark.parametrize("failure", [OSError("compiler missing"), AttributeError("API missing")])
def test_unavailable_native_support_does_not_stop_usb(monkeypatch, capsys, failure):
    monkeypatch.setattr(activity.sys, "platform", "darwin")

    def unavailable():
        raise failure

    monkeypatch.setattr(activity, "_load_activity_library", unavailable)
    assert activity.begin_video_activity() is None
    assert "macos_video_activity_unavailable" in capsys.readouterr().out


def test_null_foundation_activity_is_reported_without_invalid_cleanup(monkeypatch, capsys):
    library = types.SimpleNamespace(fl2000_begin_video_activity=lambda: None)
    monkeypatch.setattr(activity.sys, "platform", "darwin")
    monkeypatch.setattr(activity, "_load_activity_library", lambda: library)
    assert activity.begin_video_activity() is None
    assert "token=NULL" in capsys.readouterr().out


def test_native_bridge_is_built_once_per_source_and_architecture(monkeypatch, tmp_path):
    source, cache = tmp_path / "activity.m", tmp_path / "cache"
    source.write_text("first source")
    builds = []

    def compile_bridge(command, **kwargs):
        builds.append(command)
        activity.Path(command[-1]).write_bytes(b"compiled")

    monkeypatch.setattr(activity.subprocess, "run", compile_bridge)
    monkeypatch.setattr(bridge.platform, "machine", lambda: "arm64")
    first = bridge.build_native_bridge(source, cache)
    assert bridge.build_native_bridge(source, cache) == first
    assert len(builds) == 1
    source.write_text("changed source")
    assert bridge.build_native_bridge(source, cache) != first
    assert len(builds) == 2
    assert not list(cache.glob("*.partial"))


def test_failed_build_never_leaves_loadable_partial_library(monkeypatch, tmp_path):
    source, cache = tmp_path / "activity.m", tmp_path / "cache"
    source.write_text("invalid")

    def failed_build(command, **kwargs):
        activity.Path(command[-1]).write_bytes(b"partial")
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(activity.subprocess, "run", failed_build)
    with pytest.raises(subprocess.CalledProcessError):
        bridge.build_native_bridge(source, cache)
    assert list(cache.iterdir()) == []


@pytest.mark.skipif(sys.platform != "darwin", reason="Foundation is macOS only")
def test_native_foundation_bridge_can_begin_and_end_without_usb():
    library = activity._load_activity_library()
    token = library.fl2000_begin_video_activity()
    assert token
    library.fl2000_end_video_activity(token)
