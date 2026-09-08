#!/bin/bash
cd "$(dirname "$0")/.."
exec .venv/bin/python hagibis_re.py extend --seconds 0
