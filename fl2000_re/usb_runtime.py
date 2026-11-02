"""Identify the actual libusb ABI loaded by PyUSB without opening/configuring USB."""

from __future__ import annotations

import ctypes as ct

import usb.backend.libusb1 as native


class _LibusbVersion(ct.Structure):
    # ABI: libusb.h struct libusb_version; pointers refer to static library storage.
    _fields_ = [
        ("major", ct.c_uint16),
        ("minor", ct.c_uint16),
        ("micro", ct.c_uint16),
        ("nano", ct.c_uint16),
        ("rc", ct.c_char_p),
        ("describe", ct.c_char_p),
    ]


def usb_library_version(device: object) -> str | None:
    """Record the loaded library, including an isolated DYLD override.

    Example: usb_library_version(fl.dev) returns "1.0.30" with that library loaded.
    """
    backend = getattr(getattr(device, "_ctx", None), "backend", None)
    if not isinstance(backend, native._LibUSB):
        return None
    query = backend.lib.libusb_get_version
    query.argtypes, query.restype = [], ct.POINTER(_LibusbVersion)
    pointer = query()
    if not pointer:
        return None
    version = pointer.contents
    return f"{version.major}.{version.minor}.{version.micro}"
