"""Compiler flags must match the host and the bridge language."""

from fl2000_re import native_bridge as bridge


def test_c_bridge_uses_platform_linker_without_objc_runtime(monkeypatch, tmp_path):
    source, output = tmp_path / "sync.c", tmp_path / "sync.so"
    monkeypatch.setattr(bridge.sys, "platform", "linux")
    assert bridge._bridge_command(source, output) == [
        "cc",
        "-shared",
        "-fPIC",
        "-O2",
        str(source),
        "-o",
        str(output),
    ]
    monkeypatch.setattr(bridge.sys, "platform", "darwin")
    command = bridge._bridge_command(source, output)
    assert "-dynamiclib" in command and "-framework" not in command


def test_objc_bridge_links_foundation_on_macos(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge.sys, "platform", "darwin")
    command = bridge._bridge_command(tmp_path / "video.m", tmp_path / "video.dylib")
    assert "-fobjc-arc" in command and "Foundation" in command
