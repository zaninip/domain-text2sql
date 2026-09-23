UV ?= uv

.PHONY: install lint format test check data dataset

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

# Download the eCO2mix export once (data/raw/), rebuild data/eco2mix.duckdb and schema.md
data:
	$(UV) run python domains/eco2mix/ingest.py

# Generate, execute-and-filter, then split the question/SQL dataset (configs/dataset.yaml)
dataset:
	$(UV) run python -m t2sql.dataset.build
