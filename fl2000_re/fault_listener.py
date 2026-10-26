"""Persist correlated application/HDMI/kernel evidence without opening USB."""

from __future__ import annotations

import json
import os
import plistlib
import subprocess
import time
import uuid
from collections import deque
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

KernelCollector = Callable[[str, object], list[dict[str, object]]]

FAULT_EVENTS = {"usb_frame_incomplete", "usb_transfer_error", "usb_pipeline_close_error"}


class LogFollower:
    """Follow complete UTF-8 lines across rename and copy-truncate rotations.

    Example: line_and_history = follower.read_line()
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._file = path.open("rb")
        initial = os.fstat(self._file.fileno())
        self._inode, self._history_end = initial.st_ino, initial.st_size
        self._pending = b""

    def _refresh(self) -> bool:
        try:
            current = self.path.stat()
        except FileNotFoundError:
            return False
        if current.st_ino != self._inode:
            self._file.close()
            self._file = self.path.open("rb")
            self._inode = current.st_ino
            self._pending, self._history_end = b"", 0
        if current.st_size < self._file.tell():
            self._file.seek(0)
            self._pending, self._history_end = b"", 0
        return True

    def read_line(self) -> tuple[str, bool] | None:
        """Return only complete lines, marking startup history for replay suppression.

        Example: follower.read_line() is None while a writer has not flushed LF.
        """
        if not self._refresh():
            return None
        fragment = self._file.readline()
        if not fragment:
            return None
        self._pending += fragment
        if not self._pending.endswith(b"\n"):
            return None
        line, self._pending = self._pending.decode("utf-8", "replace"), b""
        return line.rstrip("\r\n"), bool(
            self._history_end and self._file.tell() <= self._history_end
        )

    def close(self) -> None:
        """Close the file descriptor; repeated close is harmless.

        Example: follower.close()
        """
        self._file.close()


class FaultContext:
    """Retain bounded pre-fault state for the current stream session only.

    Example: bundle = context.consume(timestamped_log_line)
    """

    def __init__(self) -> None:
        self.session: dict[str, object] = {}
        self.wire: dict[str, object] = {}
        self.links: deque[dict[str, object]] = deque(maxlen=20)
        self.stats: deque[str] = deque(maxlen=8)

    def consume(self, line: str) -> dict[str, object] | None:
        """Update current context and return a complete snapshot only on USB failure.

        Example: context.consume(timestamped_json_line)
        """
        if "estendendo" in line or "espelhando" in line:
            self.stats.append(line)
        if "{" not in line:
            return None
        try:
            event = json.loads(line[line.index("{") :])
        except json.JSONDecodeError:
            return None
        if not isinstance(event, dict):
            return None
        return self._record_event(line, event)

    def _record_event(self, line: str, event: dict[str, object]) -> dict[str, object] | None:
        kind = event.get("event")
        if not isinstance(kind, str):
            return None
        if kind == "stream_session_start":
            self.session = event
            self.wire = {}
            self.links.clear()
            self.stats.clear()
        if kind == "usb_wire_compression":
            self.wire = event
        if kind == "hdmi_link_state":
            previous = self.links[-1].get("video_stable") if self.links else None
            self.links.append(event)
            if previous is True and event.get("video_stable") is False:
                return self._bundle(line, event, "hdmi_instability_bundle")
        if kind not in FAULT_EVENTS:
            return None
        return self._bundle(line, event, "usb_fault_bundle")

    def _bundle(self, line: str, event: dict[str, object], kind: str) -> dict[str, object]:
        return {
            "event": kind,
            "bundle_id": uuid.uuid4().hex[:12],
            "fault_at": line[1:20],
            "fault": event,
            "session": self.session,
            "wire_compression": self.wire,
            "link_states": list(self.links),
            "recent_stats": list(self.stats),
        }


def kernel_fault_events(fault_at: str, address: object = None) -> list[dict[str, object]]:
    """Read the persisted kernel window; no dependency on live stream delivery.

    Example: kernel_fault_events("2026-10-26 15:42:08", 2)
    """
    at = datetime.strptime(fault_at, "%Y-%m-%d %H:%M:%S")
    predicate = 'process == "kernel" AND eventMessage CONTAINS[c] "endpoint 0x01"'
    if isinstance(address, int):
        predicate += f' AND eventMessage CONTAINS[c] "device {address} ("'
    result = subprocess.run(
        [
            "/usr/bin/log",
            "show",
            "--style",
            "ndjson",
            "--start",
            str(at - timedelta(seconds=3)),
            "--end",
            str(at + timedelta(seconds=1)),
            "--predicate",
            predicate,
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    return _kernel_rows(result.stdout)


def _kernel_rows(output: str) -> list[dict[str, object]]:
    rows = []
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and "eventMessage" in event:
            rows.append(
                {key: event.get(key) for key in ("timestamp", "messageType", "eventMessage")}
            )
    return rows[-64:]


def _append_record(path: Path, record: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as output:
        output.write(json.dumps(record, ensure_ascii=False) + "\n")


def _capture_fault(
    bundle: dict[str, object], output: Path, sleep: Callable[[float], None], collect: Callable
) -> None:
    _append_record(output, bundle)
    if bundle["event"] != "usb_fault_bundle":
        return
    sleep(2)
    record = {"event": "usb_fault_kernel", "bundle_id": bundle["bundle_id"]}
    try:
        record["kernel_events"] = collect(bundle["fault_at"], bundle["session"].get("usb_address"))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        record["error"] = str(exc)
    _append_record(output, record)


def run_listener(
    source: Path,
    output: Path,
    *,
    stop: Callable[[], bool] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    collect: KernelCollector = kernel_fault_events,
) -> None:
    """Follow future faults persistently while seeding context from existing logs.

    Example: run_listener(application_log, fault_bundles)
    """
    context, follower = FaultContext(), LogFollower(source)
    try:
        while stop is None or not stop():
            entry = follower.read_line()
            if entry is None:
                sleep(0.1)
                continue
            line, historical = entry
            bundle = context.consume(line)
            if bundle is not None and not historical:
                _capture_fault(bundle, output, sleep, collect)
    finally:
        follower.close()


def listener_plist(repo: Path, python: Path, logs: Path) -> str:
    """Render a persistent listener that reads logs and never claims the adapter.

    Example: listener_plist(repo_root, python_executable, user_logs)
    """
    config = {
        "Label": "com.fl2000-re.usb-listener",
        "WorkingDirectory": str(repo),
        "ProgramArguments": [str(python), "-u", "-m", "fl2000_re.fault_listener"],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 15,
        "ProcessType": "Interactive",
        "Nice": 10,
        "StandardOutPath": str(logs / "fl2000-re-usb-listener.log"),
        "StandardErrorPath": str(logs / "fl2000-re-usb-listener.log"),
    }
    return plistlib.dumps(config).decode()


def main() -> None:
    """Run the installed per-user listener with the existing extend log.

    Example: python -m fl2000_re.fault_listener
    """
    logs = Path.home() / "Library" / "Logs"
    print(json.dumps({"event": "usb_fault_listener_started", "pid": os.getpid()}), flush=True)
    run_listener(logs / "fl2000-re-extend.log", logs / "fl2000-re-faults.jsonl")


if __name__ == "__main__":
    main()
