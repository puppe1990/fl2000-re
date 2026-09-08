.PHONY: check setup

setup:
	./bin/setup

check:
	.venv/bin/ruff check .
	.venv/bin/ruff format --check .
	.venv/bin/pytest -q
	npm run format:check
