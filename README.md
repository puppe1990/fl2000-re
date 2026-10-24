# fl2000-re

Userspace reverse-engineering probe (Python) for the **Hagibis USB Display Adapter** on Apple
Silicon. The dongle is a **Fresco Logic FL2000DX** (`1d5c:2000`) plus an **IT66121** HDMI
transmitter — not DisplayLink, and not a native extra macOS display.

## What already works on a MacBook Air M2

- register reads over USB vendor control (`bRequest` 64/65)
- IT66121 on I2C `0x4C` (vendor `0x4954`, device `0x612`)
- EDID from the HDMI sink
- color bars **640×480 @ 60 fps** (RGB565, USB 2.0)
- **extend a real extra macOS desktop** (private `CGVirtualDisplay`), then pump that screen to the
  Hagibis. Drag windows onto the display named **Hagibis**. Both Hagibis HDMIs still show the same
  image.
- **1440×900 @ 60 Hz** (Dell P2016 native) and **1920×1080** / **1920×1080@30** timings from
  Fresco's official `big_table_16bit_r0`. All need a SuperSpeed link. Through the HDMI→VGA adapter
  they lock but flicker at high pixel clocks, so the LaunchAgent runs **1024×768** (65 MHz, the last
  stable mode); the P2016 OSD rejects 1080p outright.
- **mirror** the Mac screen onto HDMI at **720×480 @ 60 Hz** RGB565 (CEA 480p 16:9). 640×480 4:3
  stretched on the Dell; 800×600 RGB332 never locked.

Plug **one** HDMI cable into the Hagibis. One FL2000 chip = one output.

## Run

```bash
./bin/setup
.venv/bin/python hagibis_re.py dump
.venv/bin/python hagibis_re.py detect
.venv/bin/python hagibis_re.py edid
.venv/bin/python hagibis_re.py bars --seconds 12
.venv/bin/python hagibis_re.py bars --seconds 12 --mode 1440x900
.venv/bin/python hagibis_re.py mirror --seconds 0
.venv/bin/python hagibis_re.py extend --seconds 0 --mode 1440x900 --underscan 1.0
.venv/bin/python hagibis_re.py extend --seconds 0 --mode 1024x768 --underscan 1.0 --virtual 1280x960
```

`--mode` is per command: `mirror`/`extend` default to 720×480, `bars` to 640×480. Native modes need
`--underscan 1.0` (the 0.92 default is analog overscan compensation).

`--virtual WxH` draws the desktop larger and downscales it to the HDMI mode: `1280x960` → `1024x768`
gives a ~20% smaller UI at the stable 65 MHz clock. Keep the same aspect as the mode, or the fit
letterboxes (1280×800 → 1024×768 leaves bars top and bottom). The LaunchAgent uses 1280x960.

Capture follows screen motion as well as the pointer: changing more than 8% of a sampled frame keeps
video active even with a parked mouse. During motion, scaling uses BILINEAR plus SHARPEN to reduce
latency; after 0.5 s without significant changes, the text LANCZOS/unsharp filter returns. HDMI
continues receiving frames at the mode refresh, including repeated frames. This is adaptive capture,
not a guarantee of 60 distinct video frames per second.

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
