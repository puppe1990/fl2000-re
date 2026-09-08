# Agent rules — fl2000-re

Userspace probe for the Hagibis FL2000DX (`1d5c:2000`) + IT66121. Not a macOS display driver. One
FL2000 chip = cloned HDMI, not two independent screens.

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
fl2000_re/stream.py       bars + paced clone
fl2000_re/cli.py          argparse
tests/test_<module>.py    mirrors the package
```

## Formatting

- `ruff check` + `ruff format`. Prettier for markdown/json/yaml. Don't bikeshed.

## Logging

- Plain text for user-facing CLI (Portuguese).
- Structured JSON only if adding debug/observability logs.

## Hardware caveats (do not "simplify" these away)

- Default clone is 720x480 RGB565 @ 60 Hz (CEA 480p 16:9). 640x480 4:3 is stretched on the Dell
  16:9. 720p RGB565 overruns USB 2.0. 800x600 RGB332 never locked.
- REG_ACLK bit 28 must stay set (EOF = ZLP) or bulk NAKs forever.
- Kill the mirror by exact PID. Never `pkill -f hagibis_re.py`.
- After Access denied / claim fail: unplug the Hagibis USB-A, then `diskutil unmountDisk` the
  FL2000DX fake CD if it remounts.

## Defensive programming

- Timeouts for USB EP0 (2s), bulk (2s), I2C (20 polls).
- Do not add retries on bulk NAK — tell the user to unplug/replug.
