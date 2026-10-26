"""Explicit local burst selection, validated against SuperSpeed descriptors."""

from pathlib import Path

from fl2000_re.registers import BULK_EP


def stream_bulk_alternate(device: object, path: Path | None = None) -> int:
    """Select only an advertised streaming alternate, without changing USB state.

    Example: stream_bulk_alternate(device) returns 1 unless burst16 is opted in.
    """
    path = path or Path.home() / "Library/Application Support/fl2000-re/usb-burst-profile"
    profile = path.read_text().strip() if path.exists() else "single"
    if profile == "single":
        return 1
    if profile != "burst16":
        raise ValueError(f"USB burst profile={profile!r}; expected single or burst16")
    if not has_bulk_alternate(device, 0, 15):
        raise ValueError("USB burst profile=burst16; expected IF1/alt0 bulk EP1 bMaxBurst=15")
    return 0


def has_bulk_alternate(device: object, alternate: int, burst: int) -> bool:
    """Require a complete SuperSpeed companion for the exact bulk endpoint.

    Example: has_bulk_alternate(device, 0, 15) verifies sixteen-packet bursts.
    """
    if getattr(device, "speed", 0) < 4:
        return False
    interfaces = (
        i
        for i in device.get_active_configuration()
        if (i.bInterfaceNumber, i.bAlternateSetting) == (1, alternate)
    )
    endpoints = (ep for interface in interfaces for ep in interface)
    return any(
        ep.bEndpointAddress == BULK_EP
        and ep.bmAttributes & 3 == 2
        and len(ep.extra_descriptors) >= 6
        and bytes(ep.extra_descriptors)[:3] == bytes((6, 48, burst))
        for ep in endpoints
    )
