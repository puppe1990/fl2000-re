"""Capture child lifecycle and nonblocking complete-frame snapshots."""

from __future__ import annotations

import contextlib
import multiprocessing as mp

from fl2000_re.capture_worker import _capture_child
from fl2000_re.frame_buffer import new_shared_frame
from fl2000_re.stream_metrics import SharedCaptureMetrics
from fl2000_re.video_modes import VideoMode

MAX_CAPTURE_RESTARTS = 5


class CaptureProcess:
    """Owns the capture child and its shared frame so a dead child can be respawned.

    Pumping the last frame during a restart keeps the HDMI lit; a child killed by
    the OS (SIGKILL) or wedged mid-grab must never freeze the desktop forever
    (2026-09-20: signal 9 left a frozen frame and 15k "0.0 fps" log lines).
    """

    def __init__(
        self,
        frame: bytes,
        mode: VideoMode,
        display_id: int | None,
        underscan: float,
        stretch_x: float,
        cursor: bool,
        restarts_left: int = MAX_CAPTURE_RESTARTS,
        capture_fps: int | None = None,
    ) -> None:
        self._mode = mode
        self._display_id = display_id
        self._underscan = underscan
        self._stretch_x = stretch_x
        self._cursor = cursor
        self._restarts_left = restarts_left
        self._capture_fps = mode.freq if capture_fps is None else capture_fps
        self.sent_frames = 0
        self.run_id: str | None = None
        self.first_capture_logged = False
        self.n = len(frame)
        self.shm = new_shared_frame(frame)
        self.frame_lock = mp.Lock()
        self._last_complete_frame = frame
        self._last_snapshot_counter = 1
        self.snapshot_fresh = False
        self.frame_at = 0.0
        self.metrics = SharedCaptureMetrics()
        self.counter = mp.Value("i", 1)
        self.grab_ms = mp.Value("d", 0.0)
        self.idle = mp.Value("i", 0)
        self.stop = mp.Event()
        self.proc = None
        self._baseline = self.counter.value

    def start(self) -> None:
        self._baseline = self.counter.value
        self.proc = mp.Process(target=_capture_child, args=self._worker_args(), daemon=True)
        self.proc.start()

    def _worker_args(self) -> tuple:
        return (
            self._mode.width,
            self._mode.height,
            self._mode.bpp,
            self._display_id,
            self.shm,
            self.n,
            self.counter,
            self.stop,
            self._underscan,
            self._stretch_x,
            self._cursor,
            self.grab_ms,
            self._capture_fps,
            self.idle,
            self.frame_lock,
            self.metrics,
        )

    def alive(self) -> bool:
        return self.proc is not None and self.proc.is_alive()

    def has_frame(self) -> bool:
        """True once the current child has produced a frame (import+first grab done)."""
        return self.counter.value != self._baseline

    def exitcode(self) -> int | None:
        return None if self.proc is None else self.proc.exitcode

    def buffer(self):
        return self.shm.get_obj() if hasattr(self.shm, "get_obj") else self.shm

    def snapshot(self) -> bytes:
        """Copy a complete frame without delaying USB if the writer is busy.

        Example: send_frame(fl, capture.snapshot())
        """
        self.snapshot_fresh = False
        if not self.frame_lock.acquire(block=False):
            return self._last_complete_frame
        try:
            generation = self.counter.value
            if generation == self._last_snapshot_counter:
                return self._last_complete_frame
            self._last_complete_frame = bytes(self.buffer())
            self._last_snapshot_counter = generation
            self.snapshot_fresh = True
            self.frame_at = self.metrics.values[9]
        finally:
            self.frame_lock.release()
        return self._last_complete_frame

    def restart(self) -> bool:
        if self._restarts_left <= 0:
            return False
        self._restarts_left -= 1
        self._kill_current()
        self.start()
        print(f"  reiniciando a captura ({self._restarts_left} tentativas restantes).", flush=True)
        return True

    def _kill_current(self) -> None:
        if self.proc is None:
            return
        with contextlib.suppress(Exception):
            self.proc.terminate()
        with contextlib.suppress(Exception):
            self.proc.join(timeout=1)
        self.proc = None
        # A terminated writer may have owned the old lock; never reuse it.
        self.frame_lock = mp.Lock()

    def stop_and_join(self, timeout: float = 1.0) -> None:
        self.stop.set()
        if self.proc is None:
            return
        with contextlib.suppress(Exception):
            self.proc.join(timeout=timeout)
