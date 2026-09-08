# fl2000-re

Userspace reverse-engineering probe (Python) for the **Hagibis USB Display Adapter** on Apple
Silicon. The dongle is a **Fresco Logic FL2000DX** (`1d5c:2000`) plus an **IT66121** HDMI
transmitter — not DisplayLink, and not a native extra macOS display.

## What already works on a MacBook Air M2

- register reads over USB vendor control (`bRequest` 64/65)
- IT66121 on I2C `0x4C` (vendor `0x4954`, device `0x612`)
- EDID from the HDMI sink
- color bars **640×480 @ 60 fps** (RGB565, USB 2.0)
- **mirror** the Mac screen onto that HDMI at **1280×720** (`mirror`)

Plug **one** HDMI cable into the Hagibis. One FL2000 chip = one output.

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python hagibis_re.py dump
python hagibis_re.py detect
python hagibis_re.py edid
python hagibis_re.py bars --seconds 12
python hagibis_re.py mirror --seconds 0
```

This pushes raw pixels to the dongle. macOS still will not treat it as a display you can drag
windows onto.

## Dev: lint, tests, Prettier, pre-commit

```bash
npm ci
pre-commit install
pre-commit run --all-files
pytest -q
ruff check .
ruff format --check .
npm run format:check
```

CI (GitHub Actions) runs Prettier, Ruff, and pytest on every push/PR.
