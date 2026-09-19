"""Poll the FL2000 status register and log the monitor link as JSON lines.

The P2016 over the HDMI→VGA adapter passes no DDC/EDID, so the chip's own
REG_STATUS is the only monitor-side telemetry available: line-buffer under/over
flow, HDMI/monitor/EDID events and the scanout frame counter.
"""

from __future__ import annotations

import contextlib
import json
import time
from collections.abc import Iterator

from fl2000_re.fl2000_usb import FL2000, status_flags, status_frame_count
from fl2000_re.registers import REG_STATUS

DEFAULT_INTERVAL_S = 0.5


def status_snapshot(fl: FL2000) -> dict[str, object]:
    """One REG_STATUS reading as a JSON-ready record.

    Example: status_snapshot(fl)["flags"] == ["hdmi", "monitor"]
    """
    raw = fl.reg_read(REG_STATUS)
    return {"raw": f"0x{raw:08X}", "frame": status_frame_count(raw), "flags": status_flags(raw)}


def poll_status(
    fl: FL2000, seconds: float, interval: float = DEFAULT_INTERVAL_S
) -> Iterator[dict[str, object]]:
    """Yield one snapshot every interval; seconds <= 0 runs until interrupted."""
    t0 = time.monotonic()
    while seconds <= 0 or time.monotonic() - t0 < seconds:
        yield status_snapshot(fl)
        time.sleep(interval)


def frame_rate(first_frame: int, last_frame: int, elapsed: float) -> float:
    """Scanout fps from two counter samples (16-bit counter, may wrap)."""
    if elapsed <= 0:
        return 0.0
    return ((last_frame - first_frame) & 0xFFFF) / elapsed


def cmd_monitor_log(fl: FL2000, seconds: float, out_path: str | None = None) -> int:
    """Print REG_STATUS as JSON lines and a summary; optionally mirror to out_path."""
    first_frame: int | None = None
    last_frame = 0
    seen: set[str] = set()
    samples = 0
    t0 = time.monotonic()
    with contextlib.ExitStack() as stack:
        sink = stack.enter_context(open(out_path, "w")) if out_path else None
        try:
            for snap in poll_status(fl, seconds):
                line = json.dumps(snap, separators=(",", ":"))
                print(line, flush=True)
                if sink:
                    sink.write(line + "\n")
                first_frame = snap["frame"] if first_frame is None else first_frame
                last_frame = snap["frame"]
                seen.update(snap["flags"])
                samples += 1
        except KeyboardInterrupt:
            pass
    if not samples or first_frame is None:
        print("# nenhuma amostra (dongle fora?)")
        return 1
    elapsed = max(0.001, time.monotonic() - t0)
    fps = frame_rate(first_frame, last_frame, elapsed)
    print(f"# resumo: {samples} amostras em {elapsed:.1f}s, {fps:.1f} fps, flags: {sorted(seen)}")
    return 0
