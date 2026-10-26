"""Identify the running source, USB link and frame profile across service restarts."""

from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from pathlib import Path
from typing import Protocol

from fl2000_re.fl2000_usb import FL2000
from fl2000_re.video_modes import VideoMode


class CaptureReadiness(Protocol):
    run_id: str | None
    first_capture_logged: bool
    snapshot_fresh: bool


def source_fingerprint(root: Path) -> str:
    """Hash runtime source and the virtual-display helper, including uncommitted edits.

    Example: source_fingerprint(repo_root) differs after a Python runtime edit.
    """
    paths = sorted((root / "fl2000_re").glob("*.py"))
    paths += [
        root / "native" / "hagibis_virtual_display.m",
        root / "native" / "hagibis_virtual_display",
    ]
    digest = hashlib.sha256()
    for path in paths:
        if path.is_file():
            digest.update(str(path.relative_to(root)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def git_revision(root: Path) -> str:
    """Identify the checked-out commit without requiring a clean worktree.

    Example: git_revision(repo_root) returns a short commit or 'unknown'.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


def start_stream_session(
    fl: FL2000, mode: VideoMode, display_id: int | None, capture_fps: int
) -> str:
    """Write the identity needed to distinguish a restart from a source/profile change.

    Example: run_id = start_stream_session(fl, mode, 18, 20)
    """
    root = Path(__file__).resolve().parent.parent
    run_id = uuid.uuid4().hex[:12]
    try:
        fingerprint = source_fingerprint(root)
    except OSError:
        fingerprint = "unavailable"
    print(
        json.dumps(
            {
                "event": "stream_session_start",
                "run_id": run_id,
                "commit": git_revision(root),
                "source_hash": fingerprint,
                "hdmi": [mode.width, mode.height, mode.freq],
                "pixel_clock_hz": mode.pixclk,
                "frame_bytes": mode.width * mode.height * mode.bpp,
                "bulk_block_bytes": mode.width * mode.height * mode.bpp,
                "capture_fps": capture_fps,
                "display_id": display_id,
                "usb_speed": getattr(fl.dev, "speed", None),
                "usb_bus": getattr(fl.dev, "bus", None),
                "usb_address": getattr(fl.dev, "address", None),
            }
        ),
        flush=True,
    )
    return run_id


def end_stream_session(run_id: str, exit_code: int, sent: int, elapsed_s: float) -> None:
    """Record a completed or interrupted session beside its original source identity.

    Example: end_stream_session(run_id, 1, 42, 2.0)
    """
    print(
        json.dumps(
            {
                "event": "stream_session_end",
                "run_id": run_id,
                "exit_code": exit_code,
                "sent_frames": sent,
                "elapsed_s": round(elapsed_s, 3),
            }
        ),
        flush=True,
    )


def record_frame_ready(capture: CaptureReadiness, sent: int, elapsed_s: float) -> None:
    """Report first transport and first live capture delivery for one session.

    Example: record_frame_ready(capture, 1, 0.02)
    """
    run_id = getattr(capture, "run_id", None)
    if run_id is None:
        return
    if sent == 1:
        print(
            json.dumps(
                {
                    "event": "stream_first_frame_sent",
                    "run_id": run_id,
                    "elapsed_ms": round(elapsed_s * 1000, 1),
                }
            ),
            flush=True,
        )
    if not getattr(capture, "first_capture_logged", False) and capture.snapshot_fresh:
        capture.first_capture_logged = True
        print(
            json.dumps(
                {
                    "event": "stream_first_capture_sent",
                    "run_id": run_id,
                    "elapsed_ms": round(elapsed_s * 1000, 1),
                }
            ),
            flush=True,
        )
