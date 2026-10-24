"""Scheduling requests do not touch hardware and must degrade gracefully."""

import fl2000_re.thread_priority as priority


def test_interactive_priority_requests_current_thread_only():
    calls = []
    assert priority.prioritize_usb_thread(lambda qos, relative: calls.append((qos, relative)) or 0)
    assert calls == [(0x21, 0)]


def test_rejected_priority_is_nonfatal():
    assert not priority.prioritize_usb_thread(lambda *args: 1)


def test_other_platforms_skip_native_library(monkeypatch):
    monkeypatch.setattr(priority.sys, "platform", "linux")
    monkeypatch.setattr(priority.ctypes, "CDLL", lambda *a: (_ for _ in ()).throw(AssertionError()))
    assert not priority.prioritize_usb_thread()


def test_missing_native_library_is_nonfatal(monkeypatch):
    monkeypatch.setattr(priority.sys, "platform", "darwin")

    def missing(*args):
        raise OSError("missing libSystem")

    monkeypatch.setattr(priority.ctypes, "CDLL", missing)
    assert not priority.prioritize_usb_thread()
