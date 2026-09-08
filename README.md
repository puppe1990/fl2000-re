# fl2000-re

Userspace reverse-engineering probe (Python) for the **Hagibis USB Display Adapter** on Apple
Silicon. The dongle is a **Fresco Logic FL2000DX** (`1d5c:2000`) plus an **IT66121** HDMI
transmitter — not DisplayLink, and not a native extra macOS display.

## What already works on a MacBook Air M2

- register reads over USB vendor control (`bRequest` 64/65)
- IT66121 on I2C `0x4C` (vendor `0x4954`, device `0x612`)
- EDID from the HDMI sink
- color bars **640×480 @ 60 fps** (RGB565, USB 2.0)
- **mirror** the Mac screen onto HDMI at **720×480 @ 60 Hz** RGB565 (CEA 480p 16:9). 640×480 4:3
  stretched on the Dell; 800×600 RGB332 never locked.
- **extend** a real extra macOS desktop (private `CGVirtualDisplay`) at 720×480, then pump that
  screen to the Hagibis. Drag windows onto the display named **Hagibis**. Both Hagibis HDMIs still
  show the same image.

Plug **one** HDMI cable into the Hagibis. One FL2000 chip = one output.

## Run

```bash
./bin/setup
.venv/bin/python hagibis_re.py dump
.venv/bin/python hagibis_re.py detect
.venv/bin/python hagibis_re.py edid
.venv/bin/python hagibis_re.py bars --seconds 12
.venv/bin/python hagibis_re.py mirror --seconds 0
.venv/bin/python hagibis_re.py extend --seconds 0
```

`mirror` clones the Air. `extend` creates a WindowServer display you can drag windows onto, then
clones **that** display to HDMI. `CGVirtualDisplay` is a private API and can break on a macOS
update. Run `extend` from Terminal.app with Screen Recording permission.

Code lives in `fl2000_re/` (one module per concern). `hagibis_re.py` is the CLI shim. Agent rules:
`AGENTS.md`.

## Dev: one command

```bash
make check
```

That is ruff + pytest + Prettier. CI runs the same on every push/PR.
