# fl2000-re

Userspace reverse-engineering probe (Python) for the **Hagibis USB Display Adapter** on Apple
Silicon. The dongle is a **Fresco Logic FL2000DX** (`1d5c:2000`) plus an **IT66121** HDMI
transmitter — not DisplayLink, and not a native extra macOS display.

## What already works on a MacBook Air M2

- register reads over USB vendor control (`bRequest` 64/65)
- IT66121 on I2C `0x4C` (vendor `0x4954`, device `0x612`)
- EDID from the HDMI sink
- color bars **640×480 @ 60 fps** (RGB565, USB 2.0)
- **mirror** the Mac screen onto HDMI at **640×480 @ 60 Hz** RGB565 with letterbox. 800×600 RGB332
  streams at 60 fps over USB but the Dell never locks a picture.

Plug **one** HDMI cable into the Hagibis. One FL2000 chip = one output.

## Run

```bash
./bin/setup
.venv/bin/python hagibis_re.py dump
.venv/bin/python hagibis_re.py detect
.venv/bin/python hagibis_re.py edid
.venv/bin/python hagibis_re.py bars --seconds 12
.venv/bin/python hagibis_re.py mirror --seconds 0
```

This pushes raw pixels to the dongle. macOS still will not treat it as a display you can drag
windows onto.

Code lives in `fl2000_re/` (one module per concern). `hagibis_re.py` is the CLI shim. Agent rules:
`AGENTS.md`.

## Dev: one command

```bash
make check
```

That is ruff + pytest + Prettier. CI runs the same on every push/PR.
