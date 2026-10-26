# Agent rules — fl2000-re

Userspace probe for the Hagibis FL2000DX (`1d5c:2000`) + IT66121. `extend` creates a
CGVirtualDisplay (private API) so WindowServer has one extra desktop, then pumps that framebuffer to
the FL2000. One FL2000 chip = cloned HDMI, not two independent screens.

## Code style

- Functions: 4-20 lines. Split if longer.
- Files: under 500 lines (target 200-300). Split by responsibility.
- Package is `fl2000_re/`. `hagibis_re.py` is a CLI shim only.
- Names: specific and unique. Avoid `data`, `handler`, `Manager`. Prefer names that return <5 grep
  hits.
- Types: explicit on public functions.
- No code duplication. Extract shared logic.
- Early returns. Max 2 levels of indentation for control flow.
- Exception messages must include the offending value and expected shape.

## Comments

- Keep intent/provenance comments. Don't strip them on refactor.
- Write WHY, not WHAT.
- Docstrings on public functions: intent + one usage example.
- Reference hardware constraints in-line (Dell lock, USB 2.0 budget, ZLP).

## Tests

- Tests run with a single command: `make check`
- Or: `.venv/bin/pytest -q`
- Every new function gets a test. Bug fixes get a regression test.
- Do not hit USB in unit tests. Inject `find_device` / a fake `dev` on `FL2000`.
- Tests must be F.I.R.S.T.

## Dependencies

- Inject USB via `FL2000(dev=..., find_device=...)`. Do not `sys.exit` in the library.
- Config lives in `fl2000_re/registers.py` and `fl2000_re/video_modes.py`.

## Structure

```
fl2000_re/registers.py    USB IDs + MMIO
fl2000_re/fl2000_usb.py   EP0 + I2C
fl2000_re/it66121.py      HDMI transmitter
fl2000_re/pixels.py       RGB565/332 + dword swap
fl2000_re/video_modes.py  timings + USB2 budget
fl2000_re/letterbox.py    aspect-preserving fit
fl2000_re/probe.py        dump/detect/edid
fl2000_re/hdmi.py         mode-set + bulk claim
fl2000_re/stream.py       bars + paced clone + extend
fl2000_re/frame_buffer.py shared frame buffer (memcpy in/out)
fl2000_re/virtual_display.py  spawn CGVirtualDisplay helper
fl2000_re/cli.py          argparse
native/hagibis_virtual_display.m  WindowServer extra screen
tests/test_<module>.py    mirrors the package
```

## Formatting

- `ruff check` + `ruff format`. Prettier for markdown/json/yaml. Don't bikeshed.

## Logging

- Plain text for user-facing CLI (Portuguese).
- Structured JSON only if adding debug/observability logs.

## Hardware caveats (do not "simplify" these away)

- Default clone/extend HDMI is 720x480 RGB565 @ 60 Hz (CEA 480p 16:9). 640x480 4:3 is stretched on
  the Dell 16:9. 720p RGB565 overruns USB 2.0. 800x600 RGB332 never locked.
- USB 3 changes the ceiling: 1440x900/1080p timings from Fresco's `big_table_16bit_r0` lock and
  stream, but flicker through the HDMI→VGA adapter above ~80 MHz. The original 1024x768@60 (65 MHz)
  later showed confirmed USB partial timeouts (2026-10-25). The LaunchAgent is testing 1024x768@50
  (54 MHz; custom 49.849 Hz raster), preserving virtual 1280x960. It is not yet proof of long-term
  stability; named `1024x768` remains 60 Hz. Native modes need `--underscan 1.0`; the 0.92 default
  rescales and costs ~150 ms/frame.
- Every bulk frame MUST end with a zero-length packet. The chip's EOF is the ZLP (REG_ACLK bit 28),
  not the transfer's short packet; the reference driver submits one per frame unconditionally.
  Gating it on 512-byte alignment broke 1440x900 (2026-10-23).
- RLE chip erratum: every compressed raster MUST finish with a literal pixel, never a repeat count
  (`FrescoLogic/FL2000/src/fl2000_compression.c`, final-run termination). A software decoder
  roundtrip alone does not verify this restriction. Keep `tests/test_rle_hardware_erratum.py`.
- Clone/extend compression belongs in the isolated capture child, outside the frame publication
  lock. USB consumes complete `EncodedWireFrame` snapshots without waiting or re-encoding. Native
  RLE must stay byte-identical to the reference AND satisfy the final-literal erratum.
- The LaunchAgent plist needs `ProcessType=Interactive`. At 2.6 MB/frame, Standard QoS capped the
  pump at ~44 fps.
- `extend` makes the virtual desktop 1x at exactly the HDMI mode (1024x768 for the agent). Do not
  make it HiDPI — that downscales UI on the Dell again.
- `--virtual WxH` must match the HDMI mode's aspect or the fit letterboxes: 1280x960 → 1024x768 is
  the agent profile (UI ~20% smaller). Small downscales take the direct LANCZOS path and the
  0.7/350/2 unsharp; the 2x oversample capped capture at ~10 fps (2026-10-24).
- Reading preference (2026-10-26): user rejected 640x480 text quality. Local profile file
  `~/Library/Application Support/fl2000-re/video-mode` is `1024x768` (standard 60 Hz), virtual
  1280x960. Preserve this resolution; do not automatically downgrade it to mask transport faults.
- The capture child runs `nice(5)`: at ~45 ms/grab it must not steal ticks from the 60 Hz bulk pump.
  Keep it unless the child is measured as the bottleneck.
- Both Hagibis HDMIs always show the same FL2000 stream. Native USB-C HDMI is a separate GPU pipe
  and is unrelated.
- REG_ACLK bit 28 must stay set (EOF = ZLP) or bulk NAKs forever.
- REG_STATUS bits 8/9 are underflow/overflow respectively (Fresco `struct vga_status`). Earlier
  labels were reversed. Normal overflow latching must not be misdiagnosed as underflow.
- Kill the mirror by exact PID. Never `pkill -f hagibis_re.py`.
- After Access denied / claim fail: unplug the Hagibis USB-A, then `diskutil unmountDisk` the
  FL2000DX fake CD if it remounts.

## Defensive programming

- Timeouts for USB EP0 (2s), bulk (2s), I2C (20 polls).
- Do not add retries on bulk NAK — tell the user to unplug/replug.
- Do not walk CoreGraphics CF arrays (display/mode enumeration) through raw `ctypes`:
  `CFArrayGetCount` segfaults Python on a stale pointer (2026-09-19). Use the ObjC helper or
  `system_profiler`. Run diagnostics with `python -u` so a crash still flushes the output.
- Do not call `CGImageGetWidth` / `CGWindowListCreateImage` from the USB process (`mss.grab` does
  both). A stale CGImage is SIGSEGV, not a Python exception (2026-09-19, `0x39e37920`). Preview via
  `screencapture` (subprocess). Live grab stays in the capture child; if that child dies, keep
  sending the last frame and print `capture_worker_exit_message`.
