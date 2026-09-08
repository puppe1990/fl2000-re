#!/usr/bin/env python3
"""CLI shim. Implementation lives in fl2000_re/ (one concern per module).

.venv/bin/python hagibis_re.py dump|detect|edid|bars|mirror
"""

from fl2000_re.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
