#!/bin/bash
cd "$(dirname "$0")/.."
exec .venv/bin/python hagibis_re.py mirror --seconds 0 --monitor 1
