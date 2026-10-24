"""Bounded stream telemetry, separate from the USB transport and watchdog."""

from __future__ import annotations

import multiprocessing as mp
from dataclasses import dataclass

STATS_INTERVAL_S = 1.0


@dataclass(frozen=True)
class CaptureTimings:
    capture_ms: float = 0.0
    scale_ms: float = 0.0
    pack_ms: float = 0.0
    requested_fps: float = 20.0
    effective_fps: float = 20.0
    mode: str = "texto"
    backend: str = "pillow"
    cached: bool = False
    backoff_ms: float = 0.0


class SharedCaptureMetrics:
    """Latest approximate stage timings; no cross-process lock can block USB.

    Example: metrics.record(CaptureTimings(), captured_at=1.0)
    """

    def __init__(self) -> None:
        self.values = mp.RawArray("d", 10)
        self.record(CaptureTimings(), 0.0)

    def record(self, timings: CaptureTimings, captured_at: float) -> None:
        self.values[:] = (
            timings.capture_ms,
            timings.scale_ms,
            timings.pack_ms,
            timings.requested_fps,
            timings.effective_fps,
            ("texto", "movimento", "repouso").index(timings.mode),
            int(timings.backend == "vimage"),
            int(timings.cached),
            timings.backoff_ms,
            captured_at,
        )

    def read(self) -> CaptureTimings:
        v = self.values[:]
        return CaptureTimings(
            *v[:5],
            mode=("texto", "movimento", "repouso")[int(v[5])],
            backend="vimage" if v[6] else "pillow",
            cached=bool(v[7]),
            backoff_ms=v[8],
        )


class PumpMetrics:
    """Track delivered capture generations, repeats and worst inter-send gap.

    Example: metrics.record(1.0, 1.003, fresh=True, frame_at=0.99)
    """

    def __init__(self, target_fps: int) -> None:
        self.period = 1.0 / target_fps
        self.last_start: float | None = None
        self.fresh = self.repeated = self.late = 0
        self.max_gap_ms = self.max_usb_ms = self.max_age_ms = 0.0

    def record(self, start: float, end: float, fresh: bool, frame_at: float) -> None:
        gap = 0.0 if self.last_start is None else start - self.last_start
        self.last_start = start
        self.fresh += int(fresh)
        self.repeated += int(not fresh)
        self.late += int(gap > self.period * 1.5)
        self.max_gap_ms = max(self.max_gap_ms, gap * 1000)
        self.max_usb_ms = max(self.max_usb_ms, (end - start) * 1000)
        if frame_at > 0:
            self.max_age_ms = max(self.max_age_ms, (end - frame_at) * 1000)

    def format_interval(self, dt: float, timings: CaptureTimings) -> str:
        text = (
            f"modo {timings.mode}/{timings.backend} alvo {timings.requested_fps:.0f} "
            f"orcamento {timings.effective_fps:.1f} fps "
            f"etapas captura/escala/pack {timings.capture_ms:.1f}/{timings.scale_ms:.1f}/{timings.pack_ms:.1f}ms "
            f"novas {self.fresh / dt:.1f} fps repetidas {self.repeated / dt:.1f} fps "
            f"pausa-max {self.max_gap_ms:.0f}ms USB-max {self.max_usb_ms:.0f}ms "
            f"idade-max {self.max_age_ms:.0f}ms atrasos {self.late} "
            f"cache {'sim' if timings.cached else 'nao'} recuo {timings.backoff_ms:.0f}ms"
        )
        self.fresh = self.repeated = self.late = 0
        self.max_gap_ms = self.max_usb_ms = self.max_age_ms = 0.0
        return text


def format_stream_stats(
    label: str,
    capture_fps: float,
    usb_fps: float,
    grab_ms: float,
    cursor: bool,
    target_fps: int = 60,
    capture_target_fps: int | None = None,
    parked: bool = False,
    timings: CaptureTimings | None = None,
) -> str:
    """One Portuguese status line for the live extend/mirror log.

    Example: format_stream_stats("estendendo", 7.4, 60.0, 134.0, True)
    """
    method = "mss+cursor" if cursor else "mss"
    cap_target = target_fps if capture_target_fps is None else capture_target_fps
    bottleneck = _stream_bottleneck(
        capture_fps, usb_fps, target_fps, cursor, cap_target, parked=parked
    )
    if timings is not None:
        method = "pipeline"
        bottleneck = bottleneck.replace(" — tente --no-cursor", "")
        if usb_fps >= target_fps * 0.85 and timings.requested_fps > timings.effective_fps * 1.2:
            stages = {
                "captura": timings.capture_ms,
                "escala": timings.scale_ms,
                "pack": timings.pack_ms,
            }
            bottleneck = "limite: " + max(stages, key=stages.get)
    return (
        f"  {label}  captura {capture_fps:.1f} fps ({method} {grab_ms:.0f}ms)  "
        f"USB {usb_fps:.1f} fps  {bottleneck}"
    )


def _stream_bottleneck(
    capture_fps: float,
    usb_fps: float,
    target_fps: int,
    cursor: bool,
    capture_target_fps: int,
    parked: bool = False,
) -> str:
    if usb_fps < target_fps * 0.85:
        return "gargalo: USB"
    if parked:
        return "ponteiro parado"
    if capture_fps < capture_target_fps * 0.85:
        hint = " — tente --no-cursor" if cursor else ""
        return f"gargalo: captura{hint}"
    return "gargalo: nenhum"


def _maybe_print_stats(
    now: float,
    last_log: float,
    last_sent: int,
    last_cap: int,
    sent: int,
    counter,
    grab_ms,
    label: str,
    cursor: bool,
    target_fps: int,
    capture_target_fps: int | None = None,
    parked: bool = False,
    pump_metrics: PumpMetrics | None = None,
    capture_metrics: SharedCaptureMetrics | None = None,
) -> tuple[float, int, int]:
    if now - last_log < STATS_INTERVAL_S:
        return last_log, last_sent, last_cap
    dt = now - last_log
    detail = ""
    timings = None if capture_metrics is None else capture_metrics.read()
    if timings is not None:
        capture_target_fps = timings.effective_fps
        parked = timings.mode == "repouso"
        if pump_metrics is not None:
            detail = "  " + pump_metrics.format_interval(dt, timings)
    print(
        format_stream_stats(
            label,
            (counter.value - last_cap) / dt,
            (sent - last_sent) / dt,
            grab_ms.value,
            cursor,
            target_fps,
            capture_target_fps,
            parked,
            timings,
        )
        + detail,
        flush=True,
    )
    return now, sent, counter.value
