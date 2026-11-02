import ctypes as ct
from types import SimpleNamespace

from fl2000_re import usb_runtime as runtime


def test_identity_uses_loaded_library_without_opening_usb(monkeypatch):
    class Backend:
        pass

    monkeypatch.setattr(runtime.native, "_LibUSB", Backend)
    version = runtime._LibusbVersion(1, 0, 30, 0, b"", b"release")

    def query():
        return ct.pointer(version)

    backend = Backend()
    backend.lib = SimpleNamespace(libusb_get_version=query)
    device = SimpleNamespace(_ctx=SimpleNamespace(backend=backend))
    assert runtime.usb_library_version(device) == "1.0.30"
    assert query.argtypes == []
    backend.lib.libusb_get_version = lambda: None
    assert runtime.usb_library_version(device) is None


def test_fake_device_never_loads_or_opens_a_usb_backend():
    assert runtime.usb_library_version(object()) is None
