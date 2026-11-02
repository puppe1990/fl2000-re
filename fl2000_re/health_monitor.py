"""Watch stream and evidence-collector health without touching USB or restarting video."""

from __future__ import annotations

import json
import plistlib
import subprocess
import time
from datetime import datetime
from pathlib import Path

from fl2000_re.fault_listener import FAULT_EVENTS, LogFollower
from fl2000_re.launch_agent import rotate_extend_log

LABEL = "com.fl2000-re.health-monitor"
STALE_SECONDS = 30


class DisplayHealth:
    """Track delivery and bridge evidence separately. Example: DisplayHealth().consume(line)."""

    def __init__(self) -> None:
        self.session: dict[str, object] = {}
        self.delivery_at = 0.0
        self.link_at = 0.0
        self.video_stable: bool | None = None
        self.last_fault: dict[str, object] = {}
        self.last_hdmi_loss_at = 0.0
        self.counts = {"sessions": 0, "usb_faults": 0, "hdmi_losses": 0}
        self.active = False
        self.session_started_at = 0.0
        self.session_baseline = dict(self.counts)
        self.observer_enabled: bool | None = None

    def consume(self, line: str) -> None:
        """Accept complete timestamped lines. Example: health.consume('[2026-10-29 ...]')."""
        try:
            at = datetime.strptime(line[1:20], "%Y-%m-%d %H:%M:%S").timestamp()
        except ValueError:
            return
        if "estendendo" in line and "USB " in line:
            self.delivery_at = at
        if "{" not in line:
            return
        try:
            event = json.loads(line[line.index("{") :])
        except json.JSONDecodeError:
            return
        if isinstance(event, dict):
            self._event(event, at)

    def _event(self, event: dict[str, object], at: float) -> None:
        kind = event.get("event")
        if kind == "stream_session_start":
            self.session, self.active = event, True
            self.session_started_at = at
            self.session_baseline = dict(self.counts)
            self.observer_enabled = None
            self.delivery_at, self.link_at, self.video_stable = 0.0, 0.0, None
            self.counts["sessions"] += 1
        if kind == "stream_session_end":
            self.active = False
        if kind in FAULT_EVENTS:
            self.last_fault = {"at": at, "run_id": self.session.get("run_id"), **event}
            self.counts["usb_faults"] += 1
        if event.get("run_id") != self.session.get("run_id"):
            return
        if kind == "hdmi_link_watch_disabled":
            self.observer_enabled = False
        if kind == "hdmi_link_state":
            self.observer_enabled = True
            self._link(event, at)

    def _link(self, event: dict[str, object], at: float) -> None:
        stable = event.get("video_stable")
        if self.video_stable is True and stable is False:
            self.counts["hdmi_losses"] += 1
            self.last_hdmi_loss_at = at
        self.video_stable = stable if isinstance(stable, bool) else None
        self.link_at = at

    def snapshot(self, now: float, services: dict[str, bool]) -> dict[str, object]:
        """Report software evidence, never physical VGA visibility. Example: health.snapshot(0, {})."""
        delivery_age = max(0.0, now - self.delivery_at) if self.delivery_at else None
        link_age = max(0.0, now - self.link_at) if self.link_at else None
        return {
            "checked_at": datetime.fromtimestamp(now).isoformat(timespec="seconds"),
            "services": services,
            "session": self.session,
            "session_age_s": max(0.0, now - self.session_started_at)
            if self.session_started_at
            else None,
            "session_counts": {
                key: self.counts[key] - self.session_baseline[key]
                for key in ("usb_faults", "hdmi_losses")
            },
            "usb_observer_enabled": self.observer_enabled,
            "delivery": "recebendo"
            if self.active and delivery_age is not None and delivery_age < STALE_SECONDS
            else "sem_sinal_recente",
            "delivery_age_s": delivery_age,
            "bridge": self.video_stable
            if link_age is not None and link_age < STALE_SECONDS
            else None,
            "bridge_age_s": link_age,
            "counts_since_monitor_start": dict(self.counts),
            "last_usb_fault": self.last_fault,
            "last_hdmi_loss_at": self.last_hdmi_loss_at or None,
            "assessment": self.assessment(now),
            "physical_image": "não observável por software após HDMI→VGA",
        }

    def assessment(self, now: float) -> str:
        """Retain failures across restarts; never label USB delivery as visual stability.

        Example: health.assessment(now) returns queda_recente after a timeout.
        """
        fault_at = self.last_fault.get("at", 0.0)
        last_loss = max(float(fault_at), self.last_hdmi_loss_at)
        if last_loss and now - last_loss < 600:
            return "queda_recente"
        if self.observer_enabled is not True:
            return "observacao_parcial_sem_transmissor"
        return "em_observacao_sem_confirmacao_visual_automatica"


def service_running(label: str) -> bool:
    """Inspect only the named launchd service. Example: service_running('com.fl2000-re.extend')."""
    try:
        result = subprocess.run(
            ["launchctl", "list", label], capture_output=True, text=True, timeout=3, check=False
        )
        return result.returncode == 0 and '"PID" =' in result.stdout
    except (OSError, subprocess.SubprocessError):
        return False


def publish_status(logs: Path, status: dict[str, object], previous: str) -> str:
    """Atomically publish status and bounded transition history. Example: publish_status(logs, {}, '')."""
    output = logs / "fl2000-re-health.json"
    temporary = output.with_suffix(".tmp")
    temporary.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(output)
    signature = json.dumps(
        [status[k] for k in ("services", "delivery", "bridge", "counts_since_monitor_start")]
    )
    if signature != previous:
        history = logs / "fl2000-re-health-history.jsonl"
        rotate_extend_log(history)
        with history.open("a") as stream:
            stream.write(json.dumps(status, ensure_ascii=False) + "\n")
    return signature


def monitor_plist(repo: Path, logs: Path) -> bytes:
    """Render a login-persistent supervisor. Example: monitor_plist(repo, logs)."""
    return plistlib.dumps(
        {
            "Label": LABEL,
            "WorkingDirectory": str(repo),
            "RunAtLoad": True,
            "KeepAlive": True,
            "ThrottleInterval": 15,
            "Nice": 10,
            "ProgramArguments": [
                str(repo / ".venv/bin/python"),
                "-u",
                "-m",
                "fl2000_re.health_monitor",
            ],
            "StandardOutPath": str(logs / "fl2000-re-health-monitor.log"),
            "StandardErrorPath": str(logs / "fl2000-re-health-monitor.log"),
        }
    )


def main() -> None:
    """Continuously monitor logs and both services. Example: python -m fl2000_re.health_monitor."""
    logs = Path.home() / "Library/Logs"
    logs.mkdir(parents=True, exist_ok=True)
    source = logs / "fl2000-re-extend.log"
    source.touch(exist_ok=True)
    health, previous = DisplayHealth(), ""
    follower = LogFollower(source)
    try:
        while True:
            for _ in range(10000):
                entry = follower.read_line()
                if entry is None:
                    break
                health.consume(entry[0])
            services = {
                name: service_running("com.fl2000-re." + name)
                for name in ("extend", "usb-listener")
            }
            previous = publish_status(logs, health.snapshot(time.time(), services), previous)
            time.sleep(5)
    finally:
        follower.close()


if __name__ == "__main__":
    main()
