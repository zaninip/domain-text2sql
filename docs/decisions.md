# Decision log

Format: date, decision, reason, alternatives considered.

## 2026-09-17 — Python 3.12 as project interpreter

- **Decision:** pin `.python-version` to 3.12 (package declares `>=3.11`).
- **Reason:** the ML stack (torch, bitsandbytes, unsloth, llama-cpp-python) and the Kaggle/Colab images lag behind the latest CPython; 3.12 is the safest common denominator. Local machine has 3.14, which `uv` ignores in favour of the pinned version.
- **Alternatives:** 3.14 (system default, too new for the training stack), 3.11 (fine, but 3.12 is what current notebook images ship).

## 2026-09-17 — Tooling: uv + ruff + pytest + pre-commit, hatchling build

- **Decision:** `uv` for environments and lockfile, `ruff` for lint and format (single tool), `pytest`, `pre-commit` with ruff + basic hygiene hooks, `hatchling` as build backend with `src/` layout.
- **Reason:** minimal, fast, standard; `src/` layout guarantees tests run against the installed package, not the working directory.
- **Alternatives:** poetry (slower, own lockfile format), black + isort + flake8 (three tools where ruff does all), setuptools (more boilerplate).

## 2026-09-21 — Data source: ODRÉ `eco2mix-regional-cons-def`, full history

- **Decision:** use the regional consolidated/definitive éCO2mix dataset (Licence Ouverte v2.0),
  downloaded once as a Parquet export; keep the whole range (2013-01-01 → 2026-06-30).
- **Reason:** the regional dataset gives 12 regions × 30-min steps, richer questions than the
  national one; the clean DuckDB is ~65 MiB, small enough for the Space. Long history enables
  multi-year comparison questions.
- **Alternatives:** national dataset (no regional dimension); real-time datasets (unstable,
  excluded by design); restrict to 2020+ (~30 MiB, but loses ten years of history).

## 2026-09-21 — One clean table, built directly from the Parquet (no raw table, no views)

- **Decision:** `ingest.py` builds a single table `eco2mix` with cleaning in the
  `CREATE TABLE ... AS SELECT`; the Parquet on disk is the immutable raw. Deviates from the
  "raw table + clean views" layout of CLAUDE.md §6.
- **Reason:** a raw copy would double the DB size without adding information; a view would
  redo casts and the DST filter at every query; the model must never see the unclean shape.
- **Alternatives:** raw table + view (rejected for size), pandas cleaning (DuckDB does it in
  one SQL statement in ~2 s).

## 2026-09-21 — Cleaning rules found by inspection

- `eolien` is text in the export (108 rows of 2013 with "-"/"ND") → `TRY_CAST` to INTEGER.
- Source has 48 rows per calendar day regardless of DST: spring day contains 2 phantom rows per
  region (02:00/02:30 local, copies of 03:00/03:30) → dropped (336 rows); autumn day misses the
  repeated 02:00–02:59 hour → unrecoverable, documented in the glossary.
- `column_30` (all NULL) and `stockage/destockage_batterie` (all 0) are not loaded.
- `eolien_terrestre/offshore` are 0 in 2021–2023 (not measured) → set to NULL before 2024.
- Sources absent from a region are NULL up to 2020 and 0 from 2021 → kept as is (NULL means
  "not available"; `SUM` ignores it, `AVG` is not distorted by artificial zeros).
- Time step verified in code (`check_time_step`): 30 min everywhere after cleaning; the
  energy factor in `schema.md` is derived from it, never hard-coded.
- Regional sum of consumption matches RTE national figures within 1 MW on two reference
  instants (checked at every `make data`).

## 2026-09-21 — Column names: French as in the source, with unit suffix

- **Decision:** keep the source identifiers (`consommation_mw`, `nucleaire_mw`,
  `tco_eolien_pct`, …); convenience columns `date`, `annee`, `mois`, `heure`.
- **Reason:** the project shows the model learning domain conventions, which are French
  (TCO/TCH, RTE vocabulary); consistent with RTE documentation; questions are FR/EN/IT anyway.
- **Alternatives:** English names (more readable for non-French readers, but TCO/TCH
  translations would be our invention).

## 2026-09-21 — Timestamps stored as naive local time (Europe/Paris)

- **Decision:** `date_heure` is a naive `TIMESTAMP` in local French time, converted from the
  source `TIMESTAMPTZ`.
- **Reason:** literals like `'2020-02-13 19:00'` must mean the same on every machine (the
  Space runs in UTC); RTE publishes figures in local time; naive local timestamps are unique
  per region because the source drops the repeated autumn hour.
- **Alternatives:** keep `TIMESTAMPTZ` (results depend on the session time zone), store UTC
  (every question would need a conversion).

## 2026-09-21 — Domain conventions fixed for question templates

- Seasons: astronomical with fixed boundaries (spring 20 Mar, summer 21 Jun, autumn 22 Sep,
  winter 21 Dec); "winter 2023" = the winter ending in 2023; "winter 2023-24" explicit.
  Meteorological months rejected by the owner; exact per-year solstice dates rejected as
  needless complexity (±1 day).
- Peak hours 8–12 and 18–20; night 22–6; weekend `isodow IN (6, 7)`.
- Renewables = wind + solar + hydro + bioenergy (RTE convention, pumping not subtracted);
  low-carbon = renewables + nuclear.
- "Share of X" is recomputed as `SUM(x) / SUM(consommation)`, never `AVG(tco_x)`.
- Relative dates resolve against the last date in the dataset, stated in the schema.

## 2026-09-21 — Safe SQL execution: sqlglot allow-list + DuckDB read-only barrier

- **Decision:** `t2sql.db.validate_select` accepts one `exp.Query` (SELECT / WITH / set
  operations) whose tables are in an allow-list (DB tables + the query's own CTEs) and which
  calls no file-reading function; execution runs on a per-call `cursor()` in a thread with
  `interrupt()` on timeout and `fetchmany(max_rows + 1)` for the row cap. The connection is
  `read_only=True` with `enable_external_access=false` and `lock_configuration=true`.
- **Reason:** the parser layer gives clear errors before execution; the DuckDB layer holds
  even if the parser is bypassed. The table allow-list catches `FROM 'file.parquet'` and
  `FROM read_csv(...)` (both parse as `exp.Table`) without listing every table function.
- **Alternatives:** function allow-list (too restrictive for model output), regex checks
  (fragile), `duckdb` `query_timeout` (not exposed in the Python API).
