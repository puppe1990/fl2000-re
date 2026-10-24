"""Keep the synchronous USB feeder responsive under compositor load."""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable

USER_INTERACTIVE_QOS = 0x21  # sys/qos.h: QOS_CLASS_USER_INTERACTIVE


def prioritize_usb_thread(set_qos: Callable[[int, int], int] | None = None) -> bool:
    """Request interactive scheduling for this thread, without root privileges.

    Invoke after spawning capture so its nice(5) policy stays independent.
    Example: prioritize_usb_thread() is True on a supported macOS session.
    """
    if set_qos is None and sys.platform != "darwin":
        return False
    try:
        if set_qos is None:
            library = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
            set_qos = library.pthread_set_qos_class_self_np
            set_qos.argtypes = [ctypes.c_uint, ctypes.c_int]
            set_qos.restype = ctypes.c_int
        return set_qos(USER_INTERACTIVE_QOS, 0) == 0
    except (OSError, AttributeError):
        return False
