"""Capture metadata must remain readable while its publisher is suspended."""

import contextlib
import threading

from fl2000_re.capture_process import CaptureProcess
from fl2000_re.video_modes import MODE_640x480


def test_paused_publisher_cannot_block_metadata_or_last_complete_frame():
    capture = CaptureProcess(bytes(512), MODE_640x480, None, 1.0, 1.0, False)
    locked, release, read_done = threading.Event(), threading.Event(), threading.Event()
    observed = []

    def paused_writer():
        with contextlib.ExitStack() as stack:
            stack.enter_context(capture.frame_lock)
            for value in (capture.counter, capture.grab_ms, capture.idle):
                if hasattr(value, "get_lock"):
                    stack.enter_context(value.get_lock())
            locked.set()
            release.wait(5)

    def feeder_reads():
        observed.append((capture.has_frame(), capture.grab_ms.value, capture.idle.value))
        observed.append(capture.snapshot())
        read_done.set()

    writer = threading.Thread(target=paused_writer)
    reader = threading.Thread(target=feeder_reads)
    writer.start()
    try:
        assert locked.wait(2)
        reader.start()
        assert read_done.wait(1), "USB feeder waited for paused capture publisher"
        assert observed == [(False, 0.0, 0), bytes(512)]
    finally:
        release.set()
        writer.join(2)
        if reader.ident is not None:
            reader.join(2)


def test_single_writer_counter_and_telemetry_survive_capture_restart():
    capture = CaptureProcess(bytes(512), MODE_640x480, None, 1.0, 1.0, False)
    capture.counter.value += 1
    capture.grab_ms.value = 12.5
    capture.idle.value = 1
    assert capture.has_frame()
    assert capture.grab_ms.value == 12.5
    assert capture.idle.value == 1
    capture._kill_current()
    assert capture.counter.value == 2
