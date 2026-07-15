venv:
	uv venv --clear --force && uv sync

test:
	uv run pytest

test-debug:
	uv run pytest -sv
