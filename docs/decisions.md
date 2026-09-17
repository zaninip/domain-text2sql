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
