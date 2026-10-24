"""Detect changing screen content so video does not inherit mouse-idle throttling."""

from __future__ import annotations

import time

import numpy as np

from fl2000_re.capture import Grabber
from fl2000_re.letterbox import fit_rgb888

MOTION_HOLD_S = 0.5
MOTION_PIXEL_DELTA = 24
MOTION_FRACTION = 0.08
SAMPLE_GRID = 48


class MotionCapture:
    """Use a cheap motion filter for changing content and the text filter when still.

    Example: capture = MotionCapture(grab, 1024, 768, 1.0, 1.0); rgb = capture()
    """

    def __init__(
        self, grab: Grabber, width: int, height: int, underscan: float, stretch_x: float
    ) -> None:
        self.grab = grab
        self.width, self.height = width, height
        self.underscan, self.stretch_x = underscan, stretch_x
        self.previous_sample: np.ndarray | None = None
        self.previous_size: tuple[int, int] | None = None
        self.motion_until = 0.0
        self.moving = False

    def __call__(self) -> bytes:
        rgb, width, height = self.grab()
        self.observe(rgb, width, height, time.monotonic())
        return fit_rgb888(
            rgb,
            width,
            height,
            self.width,
            self.height,
            self.underscan,
            self.stretch_x,
            fast_motion=self.moving,
        )

    def observe(self, rgb: bytes, width: int, height: int, now: float) -> None:
        """Sample spatial changes; a cursor or clock alone must not trigger video mode.

        Example: capture.observe(rgb, 1280, 960, now=1.0)
        """
        pixels = np.frombuffer(rgb, dtype=np.uint8).reshape(height, width, 3)
        sample = pixels[:: max(1, height // SAMPLE_GRID), :: max(1, width // SAMPLE_GRID)].astype(
            np.int16
        )
        previous = self.previous_sample if self.previous_size == (width, height) else None
        self.previous_size = (width, height)
        self.previous_sample = sample
        if previous is not None and previous.shape == sample.shape:
            changed = np.max(np.abs(sample - previous), axis=2) >= MOTION_PIXEL_DELTA
            if np.mean(changed) >= MOTION_FRACTION:
                self.motion_until = now + MOTION_HOLD_S
        self.moving = now < self.motion_until
