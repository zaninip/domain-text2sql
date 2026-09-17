# Energy Text-to-SQL: fine-tuned vs base LLM

Translate natural-language questions (FR / EN / IT) about the French electricity system
(RTE éCO2mix open data) into SQL, run them on DuckDB, and compare a small base LLM against
its QLoRA fine-tuned version side by side.

Work in progress. See [`docs/decisions.md`](docs/decisions.md) for the decision log.

## Setup

```bash
make install   # uv sync + pre-commit hooks
make check     # lint + tests
```
