.PHONY: check setup helper

setup:
	./bin/setup

helper: native/hagibis_virtual_display

native/hagibis_virtual_display: native/hagibis_virtual_display.m
	clang -fobjc-arc -O2 -framework Foundation -framework CoreGraphics -o $@ $<

check:
	.venv/bin/ruff check .
	.venv/bin/ruff format --check .
	.venv/bin/pytest -q
	npm run format:check
