UV ?= uv

.PHONY: install lint format test check

install:
	$(UV) sync --all-groups
	$(UV) run pre-commit install

lint:
	$(UV) run ruff check .
	$(UV) run ruff format --check .

format:
	$(UV) run ruff check --fix .
	$(UV) run ruff format .

test:
	$(UV) run pytest

check: lint test
