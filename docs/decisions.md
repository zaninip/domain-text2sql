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

## 2026-09-22 — Questions in French and Italian only (English dropped for éCO2mix)

- **Decision:** templates carry FR and IT question variants; English is dropped for this
  domain and kept for the tennis domain later. CLAUDE.md §1 still says FR/EN/IT and should be
  updated by the owner.
- **Reason:** each language multiplies the hand-written surface forms (region names, locative
  prepositions, source adjectives); two languages already cover the multilingual claim, and
  the freed effort goes into more SQL skeletons, which is what the split by template needs.
- **Alternatives:** keep English (more surface work per template, no new SQL behaviour).

## 2026-09-22 — Template format

- **Decision:** one YAML file per family under `domains/eco2mix/templates/`, each template with
  `template_id`, `family`, `conventions` (error taxonomy in phase 3), `description`, typed
  `slots` with per-language labels and SQL attributes, `max_instances` (seeded sampling),
  2 question variants per language, `sql`, `result.columns`, `order_matters`. Placeholders:
  `{slot}` renders the label in the current language, `{slot.attr}` an explicit attribute
  (`.sql`, `.literal`, `.divisor`, `.in`).
- **Reason:** one definition drives question and SQL; attributes keep language and SQL apart;
  the generator owns escaping (`d'Azur` -> `d''Azur`), never the template author.
- **Alternatives:** Jinja templates (more power than needed, harder to validate), one file per
  template (too many files), SQL written per instance (defeats the split by template).

## 2026-09-22 — Region surface forms in `templates/regions.yaml`

- **Decision:** the 12 regions with, per language, an ordered list of surface forms
  (`name` + locative `in`), the first canonical and the rest aliases (PACA, IDF, AURA,
  historical regions, Italian names). `id` is the exact database value.
- **Reason:** French requires a per-region preposition ("en Bretagne" vs "dans les
  Hauts-de-France"), so it cannot be hard-coded in the question text; using aliases as slot
  values turns glossary §6 into training signal instead of documentation.

## 2026-09-22 — Gold SQL never rounds; all-zero results are dropped

- **Decision:** gold SQL returns raw computed values (no `ROUND`); `validate.py` drops
  instances whose result is empty, all NULL, or all zero.
- **Reason:** execution accuracy uses a 1e-4 relative tolerance, so a rounded gold would score
  a correct unrounded prediction as wrong; rounding belongs to the app. Sources absent from a
  region are 0 (not NULL) from 2021 on, so "nuclear in Île-de-France in 2023" would otherwise
  produce a useless 0 MWh example.

## 2026-09-22 — Historical regions stay out of the training data

- **Decision:** slot values contain only true aliases of the same territory (PACA, AURA, IDF,
  région parisienne, Italian names). Names of the pre-2016 regions (Alsace, Picardie,
  Aquitaine…) were removed from `regions.yaml` and moved to glossary §6 as a rule: answer with
  the current region that contains them.
- **Reason:** each old region is entirely inside exactly one current region, but is much
  smaller, so gold SQL mapping "Alsace" to "Grand Est" would train a false equality and the
  demo would show a number roughly three times too large with no warning. Stating the rule in
  the prompt keeps the behaviour defined and identical for the three models, without asserting
  the equality in the training data.
- **Alternatives:** keep the aliases (false equality trained); drop them entirely (undefined
  behaviour, likely an empty table in the demo).

## 2026-09-23 — Slot values carry a weight; sampling is weighted, not uniform

- **Decision:** a slot value may declare `weight` (default 1.0) and `sample_combinations` draws
  with it (Efraimidis-Spirakis, without replacement). First uses: `min` 0.15 against `max` 1.0
  in the extremes family, and TWh 0.1 / MWh 0.6 / GWh 1.0 for energy units.
- **Reason:** uniform sampling made half the extremes questions ask for a minimum, which no
  real user asks for, and a third of the energy questions ask for TWh, where a region over a
  month answers "0.017". The dataset has to look like the questions the demo will receive.
- **Alternatives:** delete the rare values (the model would then be unable to answer a
  legitimate question about a minimum); duplicate the frequent values in the YAML (same effect,
  unreadable). Weight 0 remains available to disable a value without deleting it.

## 2026-09-23 — Anomalous published values are kept, and disclosed to the demo users

- **Decision:** the database keeps exactly what RTE publishes, anomalies included. The glossary
  gains a third audience marker, `**Caveats**`, extracted by `prompts.caveats()` and meant
  for the About tab of the demo; caveats never enter the system prompt.
- **Reason:** correcting a published value would make our answers disagree with the official
  site and would require a robust definition of "anomaly", which the data does not support (a
  hydro turbine really can start within one step, a biomass plant cannot). The model learns SQL
  patterns, not values, so an artefact costs nothing in training; a user reading "2300 MW"
  deserves an explanation, so the honest place for it is the app, not the prompt.
- **Evidence:** 17 isolated spikes in 2.84 M rows (value above 5x both neighbours). The largest,
  bioenergies in Île-de-France on 2021-03-25 15:30 (143 -> 2300 -> 145 MW), comes with an equal
  jump in consumption and a balanced residual, so the source injected it into two columns.
- **Alternatives:** null out the outliers (breaks agreement with RTE, arbitrary threshold);
  say nothing to users (cheaper, less honest for a portfolio about data quality).

## 2026-09-23 — Monthly spot checks against the raw export, in `make data`

- **Decision:** `verify_monthly_totals` compares three monthly sums (consumption Jan 2024 and
  Jun 2025, wind Nov 2023) between the clean table and the Parquet export, and fails on any
  difference. March is deliberately excluded.
- **Reason:** outside March the export holds no duplicate row, so the two must agree to the
  last MWh; a handful of spot checks catches a cleaning change that silently shifts the
  numbers, at no cost. Verified to have teeth: pointed at March 2024 the check fails, with a
  gap of 94,004 MW — the hour the export counts twice.
- **Measured:** 11 months out of 12 match the export exactly in every year; only March differs,
  by 45 to 63 GWh nationally, which is exactly the energy of the 24 duplicated rows. Our
  yearly total is therefore lower than the published one by about 0.01%, and it is the correct
  one.
- **Alternatives:** compare every month (slower, and a wall of output for no extra safety);
  compare nothing (a future change to the cleaning would go unnoticed).

## 2026-09-23 — Question variants are hand-written; no paraphrasing service

- **Decision:** `paraphrase.py` is dropped. The several phrasings per language a template needs
  are written by hand in its `questions:` block (target 5-6 per language, against 2 today), by
  the owner with the help of his Claude subscription.
- **Reason:** the agreed design already paraphrased the *placeholder* form of a question, which
  is exactly a line of the YAML file, so the manual route produces the same artefact while
  being reviewed by a human, free of an API key and of a cache, and trivially reproducible.
- **Guardrails added, since the variants are now hand-written:** two tests assert that every
  variant of a template names the same slots, and that no declared slot is missing from the
  questions (a question that does not name a slot its SQL filters on is undetermined);
  `validate.py` drops an exact repeat of a question and raises when two templates give the
  same question two different answers.

## 2026-09-24 — Bug fixed: DECIMAL results were stored as text in the gold

- **What happened:** DuckDB returns `DECIMAL` for an integer sum multiplied by 0.5 without a
  division, i.e. every answer in MWh and every count of hours. `validate.jsonable` turned
  DECIMAL into a string, and `judge` did not count it as a number. Two consequences since the
  first version: MWh and hour answers were stored as text (`'784066.0'`), which the phase 3
  metric would have compared with a float and scored as wrong; and zeros of that type escaped
  the `all_zero` filter, so 82 degenerate examples sat in the dataset.
- **Fix:** DECIMAL becomes a float in the gold, and `judge` treats any `numbers.Number` as
  numeric. Tests cover both. Found while checking the first multi-condition answers, where
  `0.0` hours were being kept.
- **Related:** the monthly timeseries now has a `require` precondition, since the month numbers
  of an all-NULL series hid it from the degenerate-answer filter.

## 2026-09-24 — Prompt budget ceiling raised to 4500 estimated tokens for phase 2

- **Decision:** `MAX_PROMPT_TOKENS` 3500 -> 4500, as the ceiling for the whole of phase 2.
- **Reason:** the multi-condition family needed four new glossary rules (hours from steps,
  days, national value per instant, share at each step) that the base model must see to be
  judged fairly; the prompt grew to ~3510. More conventions will follow with the remaining
  templates. The real cost (training sequence length, CPU latency) is measured in phase 3 with
  the chosen tokenizer; if trimming is needed, the first candidate is the twelve TCO/TCH rows
  of the schema, whose descriptions repeat glossary §5.

## 2026-09-24 — The unit of a threshold decides between power and energy

- **Decision:** glossary §1 states that a threshold in MW/GW is a power compared at each
  instant, and one in MWh/GWh/TWh an energy compared with a sum over the period. The template
  `mc_days_national_energy_above` is the twin of `mc_days_national_consumption_above`: the
  first variant of each language is worded identically, only the unit changes.
- **Reason:** raised by the owner — "consumption" suggests energy in everyday language, while
  RTE uses it for the instantaneous demand in GW. Both readings are legitimate; the unit is
  what disambiguates, and it is exactly the kind of convention a base model misses.
- **Also confirmed:** "days above 70 GW" = at least one half-hour above the threshold that
  day; "days as a net exporter" = negative exchange balance over the whole day.

## 2026-09-24 — Ranking family: tie-break on the label, RANK() for positions, sources by column

- **Decision:** glossary §8 now fixes three result conventions: a ranking orders by the value
  and then by the label, and keeps N rows with LIMIT; a position in a ranking is
  `RANK() OVER (ORDER BY value DESC)`, returned with its value; production sources listed as
  rows are named by their column (`'eolien_mw'`), the natural output of UNPIVOT.
- **Reason:** without a tie-break the rows kept by LIMIT can depend on an arbitrary order,
  and without a naming rule the same correct answer could say `'eolien_mw'`, `'Éolien'` or
  `'eolico'`, which the metric compares literally. The demo can map column names to readable
  labels at display time.
- **Bug fixed on the way:** `validate.jsonable` passed `sep=" "` to `date.isoformat()`, which
  only `datetime` accepts; no query had returned a plain DATE column before the ranking of
  days. Dates and datetimes are now handled separately (datetime first, being a subclass).

## 2026-09-24 — Comparison family; relative dates computed from the data in the gold

- **Decision:** four comparison templates added (two regions, same month of two years, winter
  against summer, last month against the same month a year earlier). The relative-date gold
  computes "last month" from `max(date)` instead of writing 2026 and 6; glossary §2 now says
  both forms are valid.
- **Reason:** the result is identical today, but a gold written from `max(date)` stays right
  when the data is refreshed, while literals would silently point to a month that is no
  longer "last". A model that reads the end date in the prompt and writes the literals is
  still scored correct, since the metric compares results, not text.
- **Detail:** the same-month template fixes the unit to GWh; with a free unit slot the product
  of its slots would exceed the 200,000-combination guard.

## 2026-09-25 — Source groups as a catalogue with synonyms; aggregation gaps filled

- **Decision:** a `source_groups` catalogue (renewable, low-carbon, fossil) with the same shape
  as the regions: per language an ordered list of forms, the first canonical, the others
  synonyms ("propre", "décarbonée", "pulita", "a basse emissioni di carbonio"). To allow it,
  `surface_form` now keeps every attribute of the entry (`sql`, `weight`…) and takes only the
  words from the chosen form; it used to keep only `id`, which was enough for regions.
- **Templates added:** group energy by region and month (which answers the owner's first
  seed question, "energia pulita in AURA a gennaio 2026" = 12,711,723 MWh), group energy for
  France by year, gross exports/imports of a region, energy consumed by pumped storage.
- **Glossary:** STEP (stations de transfert d'énergie par pompage) is now named in §4, since a
  question may use the French technical term and the base model must be able to read it.

## 2026-09-25 — "Green" means low-carbon; the interpretation rules are shown to the users

- **Decision:** "verte" / "verde" are synonyms of low-carbon energy, nuclear included, and the
  glossary Prompt section says so explicitly. The glossary gains a fourth audience marker,
  `**Definitions**`, holding in plain words how a question is read; `prompts.definitions()`
  extracts it for the About tab (CLAUDE.md §11), and tests assert it never enters the prompt.
- **Reason:** everyday usage of "green energy" is ambiguous about nuclear, so the choice has to
  be written where the models read it and where the users read it. The owner wants users to
  know the rules behind an answer, not only the limits of the data.
- **Refactoring:** `caveats()` and `definitions()` share one extractor; a block now runs to the
  next marker. Checked by comparing the system prompt before and after: identical.
- **Open for phase 6:** caveats and definitions are written in English, like the rest of the
  glossary; they will need translating if the app speaks French and Italian.

## 2026-09-25 — Share/ratio family built as contrast pairs; offshore share on total wind

- **Decision:** six rate templates, designed so that similar words lead to different formulas:
  share in consumption (sum over sum of consumption) against share in production (sum of the
  six sources as denominator) against the average coverage rate of a region (`AVG(tco_x_pct)`);
  plus the average load factor (`AVG(tch_x_pct)`), a region's weight in national consumption
  (FILTER in the numerator only) and the offshore share of wind.
- **Evidence the pairs discriminate:** wind in Bretagne 2023 gives 12.447 % of consumption,
  38.886 % of production, and an average coverage rate of 12.628 %; the last two numbers of a
  pair differ far beyond the metric tolerance.
- **Offshore share:** the denominator is `eolien_mw`, now written in glossary §5. The source
  does not keep total wind equal to onshore + offshore (Normandie 2024: 40.552 % against
  40.485 %), so without the rule a reasonable model could be scored wrong.

## 2026-09-25 — Every family keeps at least one template in train

- **Decision:** `assign_splits` sends the first template met of each family to train, the
  others follow the proportional rule as before. Families will also be brought to at least
  three templates, so that validation and test cover them too.
- **Reason:** with the average family added, the three single-template families
  (classification, extremes, timeseries) all fell outside train. The fine-tuned model would
  have been tested on SQL skeletons it never met, which measures invention, not the learning
  of conventions the project is about. The standard setting is unseen templates of known
  families.
- **Also fixed on the way:** "at 19h" with no date = the whole hour (`heure = 19`, both
  half-hours), confirmed by the owner; average daily energy = mean of the daily totals.

## 2026-09-25 — Timeseries family; moving averages left out

- **Decision:** four timeseries templates added (hourly profile, yearly trend over an inclusive
  range of years, national consumption day by day, cumulative production month by month).
  Glossary: "between 2015 and 2024" includes both years; "cumulative month by month" is
  `SUM(SUM(x) * 0.5) OVER (ORDER BY mois)`; an hourly profile groups by `heure`.
- **Not done:** moving averages. "7-day moving average in February" is ambiguous on the first
  days of the month (include late January or not), and such a question is rare for a demo
  user; it would cost a convention for little value.

## 2026-09-25 — Default units are trained, and a named quantity wins over a mismatched unit

- **Decision:** the energy-unit catalogue gains a fourth entry with no unit in the question
  (weight 0.4, about 17 % of the energy questions), answered in MWh; questions now end with
  `{unite.suffix}` (", en GWh" or nothing) instead of ", en {unite}". Power templates with a
  fixed "en MW" get one extra variant without the unit, answered in MW. Glossary §8 states
  both defaults; §1 adds that a question naming the quantity ("energy", "power") is read by
  that word when the unit does not match ("energy in MW" = MWh).
- **Reason:** raised by the owner. The MWh default was in the prompt, but none of the 5,266
  examples omitted the unit, although it is the most natural way to ask; the model would have
  known the rule without ever seeing it applied. "Energy in MW" was undefined: the unit rule
  had been written for ambiguous words ("consumption"), and whoever writes "energy in MW" has
  most likely swapped MW and MWh.
- **Not done:** no training examples with a mismatched unit; they would be deliberately wrong
  questions. To revisit if the phase 3 baseline shows the case matters.

## 2026-09-25 — Schema rows sharing a description are merged (prompt 4389 -> 4133 tokens)

- **Decision:** `write_schema_md` puts columns with the same type, unit and description on one
  row; the six TCO and six TCH columns now share one description each and take two rows
  instead of twelve. Every column name is still spelled out; the definitions stay in
  glossary §5.
- **Reason:** the estimated prompt had reached 4389 tokens against the 4500 ceiling of phase 2,
  with templates still to write; the twelve rows repeated the same sentence six times each.
  Listing the names in full, rather than a `tco_<source>_pct` pattern, avoids asking a small
  model to rebuild a column name.

## 2026-09-25 — Template writing complete: 45 templates, every family has at least three

- **Added last:** the national consumption extremum as a value (summed per instant, in MW or GW
  through a new `power_units` catalogue), the record day of production (daily totals, earliest
  day plus ties), the main source of every region (UNPIVOT, QUALIFY row_number), and every
  region above or below the regional average (window AVG over one row per region).
- **Glossary:** "the day with the most X" compares daily totals, not the power peak; labels in
  an answer are the words of the question, in its language; "the average of the regions" is the
  mean of the twelve regional totals.
- **Sanity checks worth keeping:** the 2015 national peak comes out at 91.9 GW, the figure RTE
  published; Occitanie's main source in 2023 is hydro because nuclear fell to 4.7 TWh that year
  (Golfech outages), a real event rather than a bug.
