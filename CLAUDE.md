# CLAUDE.md — Energy Text-to-SQL: fine-tuned vs base LLM

This file guides Claude Code on this repository. Read it fully before starting any task.

## 1. Project goal

A portfolio project showing end-to-end LLM fine-tuning for a real task:

- **Task:** translate natural-language questions (FR / EN / IT) about the French electricity system into SQL, run them, and show the result as a table and chart.
- **Data:** RTE éCO2mix open data (via ODRÉ), loaded into a local **DuckDB** file.
- **Model:** a small open instruct/coder model (1.5B–3B), fine-tuned with QLoRA on a free GPU (Kaggle or Colab).
- **Demo:** a web app (Gradio on Hugging Face Spaces, CPU) that runs the **base model** and the **fine-tuned model** side by side on the same question, so the user sees the difference directly.
- **Deliverables:** GitHub repo, fine-tuned model on Hugging Face Hub with a model card, public Space, README with results.

The repo will later host a second domain (tennis results). Keep domain-specific code isolated (see §5), but do **not** build an abstract framework now: finish éCO2mix end to end first, generalize when the second domain arrives.

## 2. Guiding principles

1. **Fine-tuning teaches behaviour, not facts.** The model learns the schema usage, domain conventions and SQL patterns. It is not meant to memorize data values.
2. **Fair comparison.** Base and fine-tuned models share the same base checkpoint, the same system prompt (including the schema), the same decoding settings (greedy) and the same quantization.
3. **Honest evaluation.** Always report three configurations: base zero-shot, base few-shot, fine-tuned. Split train/test **by template**, never by example.
4. **Never invent column names, units or dataset identifiers.** Inspect the real data first and derive everything from it. If something is uncertain, stop and ask.
5. **Work phase by phase** (§7). Do not start a phase until the previous one meets its "Done when" criteria and the user has confirmed.
6. **Log decisions** in `docs/decisions.md` (date, decision, reason, alternatives considered).

## 2.1 Working rhythm (important)

The user is learning this stack while building it. He wants to understand the code as it is written, not receive finished files. Optimize for his understanding, not for speed.

- **Small increments.** Write **one function, or one small module, at a time** — roughly 30–60 lines of new code per step. Never generate several files or a whole pipeline in one go.
- **Explain before writing.** Before each increment, state in two or three sentences what it will do and why this approach. After writing, walk through the non-obvious parts: library-specific APIs, any pattern that is idiomatic to LLM/fine-tuning work, and anything that would be hard to guess from the code alone.
- **Stop and check.** End each increment by asking whether it is clear and whether to continue. Do not chain increments without an answer.
- **Run it early.** Prefer increments that can be executed and inspected immediately (print a sample, a row count, a generated prompt) over code that only works once everything else exists.
- **Flag the decision points.** When a line encodes a choice that could reasonably have gone another way (a hyperparameter, a filter threshold, a prompt-formatting detail), say so explicitly rather than letting it pass as a default.
- **No silent refactoring.** If earlier code needs changing, say what changes and why before touching it.
- When the user asks to go faster on a specific part, follow him — but return to this rhythm afterwards unless told otherwise.

**Scope.** This rhythm governs work done in the main conversation. A subagent whose own definition tells it to work autonomously — for example the `webapp-builder` subagent used for phase 6 — is exempt: it implements its task in full and reports a summary, without step-by-step explanation or check-in questions.

## 3. Tech stack

- Python 3.11+, dependency management with `uv`, `pyproject.toml`
- `duckdb` for data, `sqlglot` for SQL parsing / validation / normalization
- `pandas` or `polars` for data wrangling, `pyarrow` for Parquet
- Training: `unsloth` (preferred on T4) or `transformers` + `trl` + `peft` + `bitsandbytes`
- Tracking: Weights & Biases or MLflow (pick one, record the choice in `docs/decisions.md`)
- Inference for evaluation on GPU: `vllm` if it installs cleanly on the notebook image, otherwise batched `transformers.generate`
- Export: `llama.cpp` conversion + quantization to GGUF
- Web app: `gradio` + `llama-cpp-python`, charts with `plotly`
- Quality: `ruff` (lint + format), `pytest`, `pre-commit`, type hints everywhere
- Secrets via environment variables / `.env` (never committed): `HF_TOKEN`, `WANDB_API_KEY`, API key for the paraphrasing LLM

## 4. Compute constraints (free GPU only)

- Target hardware: **NVIDIA T4 (16 GB)** on Kaggle (preferred: longer sessions and a weekly GPU quota) or Colab free. Check current quotas before planning runs.
- T4 has **no bf16** support: use fp16. No FlashAttention 2.
- Sessions can be killed: **push checkpoints to a private Hugging Face Hub repo** at regular intervals and make training resumable.
- Notebooks in `notebooks/` must be **thin wrappers**: install the package from the repo, load a config, call functions from `src/`. No business logic in notebooks.
- Keep total training time per run within a single session (aim for < 3 h).
- The demo runs on **CPU** (Spaces free tier): quantized GGUF models only.

## 5. Repository layout

```
.
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── Makefile                      # data, dataset, eval, export, app targets
├── configs/
│   ├── train_qlora.yaml
│   └── eval.yaml
├── src/t2sql/                    # domain-agnostic code
│   ├── db.py                     # safe read-only DuckDB execution
│   ├── prompts.py                # system prompt builder (schema + glossary)
│   ├── dataset/
│   │   ├── generate.py           # templates -> (question, sql) pairs
│   │   ├── paraphrase.py         # LLM paraphrasing with on-disk cache
│   │   ├── validate.py           # execute, filter, deduplicate
│   │   └── split.py              # split by template_id
│   ├── train/
│   │   └── sft.py                # QLoRA SFT, resumable
│   ├── eval/
│   │   ├── run.py                # generate SQL for a model + mode
│   │   ├── metrics.py            # execution accuracy etc.
│   │   └── report.py             # tables, per-family charts, error taxonomy
│   └── export/
│       └── gguf.py               # merge adapter, convert, quantize
├── domains/
│   └── eco2mix/                  # everything specific to éCO2mix
│       ├── ingest.py             # download + load into DuckDB + views
│       ├── schema.md             # generated from the DB, human-reviewed
│       ├── glossary.md           # domain conventions (see §6)
│       ├── templates/            # question/SQL templates (YAML)
│       └── demo_examples.yaml    # curated questions with gold SQL
├── app/
│   ├── app.py                    # Gradio app
│   └── requirements.txt          # Space dependencies
├── notebooks/
│   ├── train_kaggle.ipynb
│   └── eval_kaggle.ipynb
├── data/                         # gitignored (raw, DuckDB file, datasets)
├── results/                      # eval outputs committed as small JSON/CSV
├── docs/
│   └── decisions.md
└── tests/
```

## 6. Data: éCO2mix

**Source:** ODRÉ (Open Data Réseaux Énergies) open data portal, éCO2mix datasets (regional and national, consolidated/definitive data). Use the portal's export API to download Parquet or CSV.

Before writing any ingestion code:

- Look up the exact dataset identifiers and export URLs on the portal; do not guess them.
- Check and record the **license** of the data in the README.
- Download, then inspect: column names, types, time granularity, time zone handling, units, coverage (years, regions), missing values.

**Ingestion rules:**

- Load raw data into `data/eco2mix.duckdb`.
- Create clean **views** used by the model: ASCII `snake_case` names, explicit units in names where useful, a proper timestamp column plus convenience columns (date, year, month, hour, local time). Record the naming choice (French vs English column names) in `docs/decisions.md`.
- Keep the schema compact: the full schema must fit in the system prompt with room to spare.
- Generate `domains/eco2mix/schema.md` from the DB (table, column, type, unit, description).
- Keep the DuckDB file small enough to ship inside the Space. Measure its size; if it is too large, restrict the time range and document it.

**Glossary (`glossary.md`)** — domain conventions the base model is expected to get wrong. Verify each one against the data and the RTE documentation before writing it down. Candidates:

- Power vs energy: if values are average power in MW per time step, energy in MWh = sum(MW) × step duration in hours. Converting to GWh / TWh.
- What "renewables" includes (and whether pumped storage is excluded).
- Coverage rates / shares, and how they are defined in the source.
- "Peak" hours, seasons, winter definition, weekdays vs weekends.
- Official region names and common aliases ("PACA", "AURA", "Hauts-de-France"…).
- Exchanges: sign convention for imports/exports.
- Relative dates ("last year", "this month") are resolved against the **latest date in the dataset**, which is stated in the system prompt. This keeps gold answers deterministic.
- Time zone: which one the user means by default (local French time).

## 7. Phases

### Phase 0 — Scaffolding
- `uv` project, `ruff`, `pytest`, `pre-commit`, `Makefile`, `.gitignore`, `.env.example`, empty `docs/decisions.md`.
- **Done when:** `make lint test` passes on an empty test suite; CI (GitHub Actions) runs lint + tests.

### Phase 1 — Data
- Implement `domains/eco2mix/ingest.py` (§6), clean views, `schema.md`, first draft of `glossary.md`.
- Implement `src/t2sql/db.py` with the safety rules of §9.
- **Done when:** `make data` builds the DB from scratch; a handful of hand-written reference queries return sensible values (checked against published RTE figures where possible); DB size is recorded.

### Phase 2 — Dataset
- Templates in YAML, each with: `template_id`, `family` (aggregation, comparison, ranking, time series, share/ratio, peak/extremes, multi-condition…), typed slots (region, source, period, granularity…), question variants in FR/IT, SQL template.
- Aim for ~40–80 templates spread across families; many should encode glossary conventions.
- Pipeline: generate → execute → drop errors, empty results and all-NULL results → paraphrase questions with a larger LLM (cached on disk, seeded) → deduplicate (normalized text + fuzzy matching) → split **by `template_id`** (e.g. 70/15/15 train/val/test templates).
- Output: chat-format JSONL (`system`, `user`, `assistant`), where `system` is built by `prompts.py` and `assistant` contains only the SQL.
- Target size: 2k–5k train examples, 300–500 test examples.
- **Done when:** `make dataset` is reproducible; a stats report (examples per family, language, template) is written to `results/`; 30 random samples have been reviewed by the user.

### Phase 3 — Baseline evaluation (before any training)
- Choose the base model (§8). Evaluate **zero-shot** and **few-shot** (fixed few-shot examples drawn from train templates only).
- Metrics (§10), per-family breakdown, error taxonomy.
- **Decision gate:** if base zero-shot execution accuracy is already very high (roughly > 85%), stop and discuss with the user: smaller model, harder templates, or more convention-heavy questions.
- **Done when:** baseline results are in `results/` and summarized in `docs/decisions.md`.

### Phase 4 — QLoRA training
- `src/t2sql/train/sft.py` driven by `configs/train_qlora.yaml`.
- 4-bit base, LoRA on all linear layers; starting point: r=16, alpha=16–32, dropout 0–0.05, lr 2e-4 with cosine schedule, 1–3 epochs, fp16. Tune on the validation set only.
- Loss on assistant tokens only. Same system prompt as in evaluation.
- Periodic checkpoint push to a private HF repo; resumable. Log GPU, duration, and peak memory.
- **Done when:** fine-tuned model evaluated on the test set with the same harness; results table (3 configurations) in `results/`.

### Phase 5 — Export
- Merge adapter into the base weights, convert to GGUF, quantize (Q4_K_M as default; optionally Q8_0).
- Quantize the **base model with the same settings** for the demo.
- Re-run the evaluation on both GGUF models (CPU or GPU via llama.cpp) to measure quantization degradation.
- Publish the fine-tuned model (adapter + merged GGUF) on HF Hub with a model card: base model, data and license, training setup, metrics, intended use, limitations.
- **Done when:** both GGUF models load in `llama-cpp-python` locally and the model card is published.

### Phase 6 — Web app
- See §11. Deploy to Hugging Face Spaces (CPU).
- **Done when:** the Space is public, curated examples respond quickly, free-text questions work end to end.

### Phase 7 — README and polish
- README: demo GIF at the top, one-paragraph pitch, results table, cost/time of training, architecture diagram, "When to fine-tune vs RAG vs prompting" section, how to reproduce, licenses.
- Optional: store arena votes in an external database (e.g. Supabase). It must fail gracefully: the app keeps working if the database is unreachable or paused.

## 8. Base model choice

- 1.5B–3B instruct or coder model with good SQL and multilingual ability (e.g. the Qwen coder/instruct family). Check what is current when starting.
- **Check the license of the exact size you pick**: licenses can differ between sizes of the same family. Prefer Apache-2.0 or MIT.
- Must be supported by Unsloth/TRL and convertible to GGUF by llama.cpp.
- Record the choice and the reasons in `docs/decisions.md`.

## 9. Safe SQL execution (mandatory)

Model-generated SQL is untrusted input, including in the public demo.

- Open DuckDB with `read_only=True`.
- Parse with `sqlglot` (DuckDB dialect): accept exactly **one** statement, `SELECT` or `WITH … SELECT` only. Reject everything else before execution.
- Disable external access and lock the configuration (`enable_external_access = false`, `lock_configuration = true`) right after connecting.
- Enforce a timeout (run in a worker thread and call `connection.interrupt()` on expiry) and a maximum number of returned rows.
- Never interpolate user text into SQL in application code.
- Unit tests must cover: DDL/DML rejected, multiple statements rejected, file-reading functions rejected, timeout triggered.

## 10. Evaluation protocol

- **Execution accuracy:** result of predicted SQL equals result of gold SQL. Compare as multisets of rows; ignore column names and column order; numeric tolerance (relative 1e-4); respect row order only when the gold query has an `ORDER BY` that matters (flag it in the template).
- Also report: valid SQL rate, exact-match after `sqlglot` normalization (secondary), mean latency, output tokens.
- Break down by family and by language.
- Error taxonomy (automatic where possible, manual sample otherwise): syntax error, unknown column/table, wrong filter, wrong aggregation, wrong unit conversion, wrong time handling, other.
- Greedy decoding, fixed `max_new_tokens`, identical prompts across configurations. Strip code fences and extract the first SQL statement with one shared function.
- Save raw predictions to `results/` so tables can be regenerated without rerunning models.

## 11. Web app specification

- **Main tab — side by side.** One question box; example chips (curated questions where the difference is clear, in FR/IT). Two panels: "Base model" and "Fine-tuned model". Each shows generated SQL (highlighted), result table, auto chart (time column → line; categorical → bar; single value → big number), latency. For curated examples, show a correct/incorrect badge against the gold answer; for free questions show "no reference answer".
- **Arena tab.** Blind A/B: the two answers are shown in random order, the user votes, then the models are revealed.
- **Results tab.** Evaluation table and per-family chart loaded from `results/`.
- **About tab.** What the project shows, when fine-tuning is (and is not) the right tool, links to repo and model card.
- Run the two models concurrently if CPU allows, otherwise sequentially with streaming. Precompute answers for curated examples at startup or ship them as a cache, so first impressions are instant.
- Clean, modern look; mobile-friendly; no raw stack traces shown to users.

## 12. Code conventions

- `src` layout, type hints, docstrings on public functions, small modules.
- Configuration in YAML; no magic numbers in code.
- Deterministic seeds for generation, splitting, training.
- Tests for: template rendering, validation filters, split leakage (no `template_id` shared between splits), metrics, SQL safety.
- Small, focused commits with clear messages.

## 13. Later: second domain (tennis)

Not in scope now. When it starts: add `domains/tennis/`, then extract what turned out to be duplicated. Check the data license (some popular tennis datasets are non-commercial) and prefer derived columns in views (e.g. number of sets played, retirement flag) over making the model parse raw score strings.
