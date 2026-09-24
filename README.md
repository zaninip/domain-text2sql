# Energy Text-to-SQL: fine-tuned vs base LLM

Translate natural-language questions (FR / IT) about the French electricity system
(RTE éCO2mix open data) into SQL, run them on DuckDB, and compare a small base LLM against
its QLoRA fine-tuned version side by side.

Work in progress. See [`docs/decisions.md`](docs/decisions.md) for the decision log.

## Setup

```bash
make install   # uv sync + pre-commit hooks
make check     # lint + tests
make data      # download éCO2mix once, build data/eco2mix.duckdb, regenerate schema.md
```

## Data

- **Source:** RTE éCO2mix regional consolidated/definitive data, published on
  [ODRÉ](https://odre.opendatasoft.com/explore/dataset/eco2mix-regional-cons-def/)
  (dataset `eco2mix-regional-cons-def`): 12 metropolitan regions, 30-minute steps,
  2013 to mid-2026.
- **License:** [Licence Ouverte v2.0 (Etalab)](https://www.etalab.gouv.fr/licence-ouverte-open-licence/)
  — free reuse with attribution to RTE / ODRÉ.
- The export (~88 MB Parquet) is downloaded once by `make data`; the clean DuckDB file is
  ~65 MB (`data/` is gitignored). Schema: [`domains/eco2mix/schema.md`](domains/eco2mix/schema.md),
  conventions: [`domains/eco2mix/glossary.md`](domains/eco2mix/glossary.md).
