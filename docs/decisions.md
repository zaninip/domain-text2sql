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

## 2026-09-25 — Bare questions mean total energy; nominal forms of measures; build reproducible again

- **Convention (README example):** a question naming only a source (or consumption), a place
  and a period, with no word for the quantity ("Eolico Bretagna 2023?"), asks for the total
  energy of the period, in MWh unless a unit is given. Written in glossary §1 and in the user
  definitions. The base model cannot guess it; the fine-tuned model learns it from the
  elliptical variants. The owner wants it cited in the README as a concrete case of what
  fine-tuning teaches that prompting alone does not.
- **Forms:** measures gain `le` (noun with article: "l'éolien", "il nucleare") and `nom` (bare
  noun: "éolien", "eolico"), for elliptical and telegraphic variants. Constraint written next
  to them in measures.yaml, for both languages: nominal forms only, never the subject or
  object of a verb, since "bioénergies" / "bioenergie" is plural. Questions now get their first
  letter upper-cased by the generator, so a variant may start with a lower-case slot.
- **Reproducibility bug:** two identical builds differed on 210 of 5,864 records, since the
  templates added on 2026-09-24/25 (row order of queries without ORDER BY; last digits of
  float averages computed on several threads). `validate` now opens DuckDB with `threads=1`
  (new optional argument of `db.connect`, set before the configuration is locked) and stores
  the gold rows in a canonical order when the template says order does not matter. Checked:
  two consecutive builds give identical checksums for every generated file. The metric was
  never affected (multiset comparison, 1e-4 tolerance), but "make dataset is reproducible" is
  a phase 2 criterion.

## 2026-09-28 — Classification labels are fixed words, not copied from the question

- **Decision:** glossary §8 now lists the labels a classification answer uses: the bare role
  word, singular, in the language of the question ('exportatrice' / 'importatrice',
  'au-dessus' / 'en dessous'; 'esportatrice' / 'importatrice', 'sopra' / 'sotto'), with no
  qualifier such as "nette". It replaces "the labels are the words the question uses".
- **Reason:** the owner's variants ask "which regions exported more than they imported" or
  "whose consumption exceeded the average": good, realistic questions that contain no label
  word to copy; and with "exportatrices nettes" the old rule left 'exportatrice' and
  'exportatrice nette' equally plausible, while the metric compares strings literally.
- **Also:** at the owner's request the first variants of `cls_exchange_role_season` now say
  "nettes" / "nette", so that most variants cannot be read as gross exports (compare
  `agg_exchange_gross_region_year`).

## 2026-09-28 — Region aliases in the prompt aligned with the dataset (fairness fix)

- **Found:** glossary §6 listed the aliases written in phase 1, never realigned with
  `templates/regions.yaml`: 11 aliases the dataset actually uses (Alvernia-Rodano-Alpi, Alta
  Francia, Nuova Aquitania, Paesi della Loira, région parisienne…) were missing, so the base
  model would have been scored on names the prompt never explained; meanwhile the list carried
  the former regions, which duplicated the next rule.
- **Decision:** §6 now lists each of the 12 values once, followed by exactly its aliases from
  `regions.yaml`; the former-region rule is one line (the full mapping moved to Notes, since
  no template asks about former regions). A test checks that every region value and alias of
  `regions.yaml` appears in the system prompt.
- **Effect:** estimated prompt 4451 -> 4252 tokens.

## 2026-09-28 — `must_say` / `must_not_say`: the meaning criterion made testable for close templates

- **Decision:** a template may declare, per language, words every variant must contain
  (`must_say`, at least one of them) and words no variant may contain (`must_not_say`). A test
  checks them on the template text. A word matches as a whole word; a trailing `*` matches the
  start of a word ("cumul*" covers "cumulée", "cumulata"). Declared on 14 templates that have a
  close neighbour: last month vs explicit years, net vs gross exchanges, share in consumption
  vs in production vs average coverage rate, load factor, offshore share, record day vs peak
  instant vs value only, daily energy, cumulative, ranking position.
- **Reason:** raised by the owner on `cmp_last_month_vs_year_before_region`, whose period has no
  slot, so nothing stopped a variant from dropping "le mois dernier" and becoming a question
  with no period. The same gap existed for every contrast pair; criterion 3 of the variant pass
  relied on review alone. Checked with deliberately wrong variants: both kinds are blocked.

## 2026-09-28 — Count answers must be neither zero nor saturated

- **Decision:** the three multi-condition templates whose answer is a count of steps or days
  require, before running the query, that the condition holds on at least one step (day) and
  fails on at least one: `count(*) FILTER (WHERE condition) BETWEEN 1 AND count(*) - 1`.
- **Reason:** raised by the owner for zero counts; measuring showed the opposite end matters as
  much. Out of 20 sampled combinations, "nuclear above 50 % in AURA" gave all 8,759 hours,
  "thermal above bioenergy" every hour of a winter, "PACA net importer" 365 days of 365. A
  saturated count is also the answer of many wrong queries (wrong threshold, wrong sign,
  forgotten comparison), so it scores a wrong model as right. Zeros were already dropped by
  `validate`, but they wasted samples.
- **Consequence to handle in step 3:** these templates now keep 5, 11 and 5 combinations out of
  20 sampled, so they are under-represented. Rather than raising `max_instances` template by
  template, consider making it count the instances that survive validation (oversample, then
  keep the first N valid in sampled order).

## 2026-09-28 — `max_instances` counts the combinations that survive validation

- **Decision:** the generator samples `oversample × max_instances` combinations
  (`configs/dataset.yaml`, `oversample: 5`) in the same weighted order as before, and each
  record carries its combination index (`instance`) and `max_instances`. Validation reads the
  records in sampled order and keeps the first `max_instances` combinations that pass the
  precondition and return a usable answer; the spares are dropped as `oversampled` without
  running their query. A combination counts as valid on its first passing record; a question
  dropped as a duplicate still counts, because the combination answered.
- **Reason:** templates whose filters drop many combinations (saturated counts, pumping,
  offshore, gross exchanges) were under-represented: 5 of 20 combinations kept for
  `mc_hours_share_above_region_year` and `mc_days_net_role_region_year`, 8 for pumping. Raising
  `max_instances` template by template would have to be redone after every change to a
  `require`.
- **Why 5:** the worst template kept 1 combination in 4; 5 gives margin. A longer sample only
  extends the shorter one (same sort), so templates with no drops are unchanged, and the
  build stays reproducible (two runs, identical checksums).
- **Effect:** 42 of 45 templates now reach their quota; the other three have exhausted their
  combination space (13 years for the two single-slot classification templates, 13
  region-years with offshore wind). Examples 9,982 → 10,910 (train 7,916, val 1,794, test
  1,200). Alternative considered: a fixed per-template oversampling factor in the YAML —
  rejected, a single global factor is enough and adds no knob to each template.

## 2026-09-28 — Guards on both sides of each close pair; no production words where `{mesure}` can be consumption

- **Decision:** the guard words are now declared on both templates of each close pair:
  `must_not_say` "last month" on `cmp_yearly_change_pct_region`, "per day / daily" on
  `avg_consumption_moment_region_month`; `ts_monthly_energy_region_year` gets `must_say`
  "mois / mensuel*" and `must_not_say` "cumul* / progressi*", mirroring the cumulative template.
  The two templates whose `mesure` slot includes consumption also forbid "production",
  "produi*" / "produzione", "prodott*".
- **Reason:** raised by the owner during the timeseries pass. Checking it showed a real defect
  of the original template, not only a risk: `cmp_yearly_change_pct_region` said
  "la production {mesure}", which rendered "la production consommée" / "la produzione
  consumata" in 6 of its 10 variants whenever the measure was consumption. The variants now
  say "l'énergie {mesure}", and the forbidden words keep it from coming back.
- **Kept on purpose:** "Production {mesure} cumulée mois par mois" in the cumulative template.
  It is one word away from the monthly template, which is the point of a contrast pair; the
  word that decides ("cumulée") is enforced by `must_say`.

## 2026-09-28 — `make similar`: closest variants across templates, reviewed by eye

- **Decision:** `src/t2sql/dataset/similar.py` lists, for each pair of templates and each
  language, their two most similar variants (difflib ratio on word sequences, each placeholder
  reduced to its slot name). `make similar ARGS="--only rate_ --top 30"`.
- **Reason:** raised by the owner for the rate family. The build only stops on two identical
  rendered questions with different SQL; near-identical wording is the real risk, and it is
  where `must_say` / `must_not_say` must sit. The first run found the closest unguarded pair
  of the project: `agg_consumption_national_month` vs `ts_daily_consumption_national_month`
  (0.94, one word: "quotidienne"), now guarded, and the coverage / load factor pair, now
  guarded both ways. The 1.00 pair at the top (`mc_days_national_consumption_above` /
  `mc_days_national_energy_above`) is intentional: only the unit of the threshold differs.
- **Alternatives:** Jaccard on word sets (ignores order, so misses "part de X dans Y" vs "part
  de Y dans X"); comparing rendered questions (slot values differ between any two instances
  and hide the shared wording).

## 2026-09-28 — Dataset size: one variant per language per combination, `max_instances` 35

- **Decision:** each slot combination is asked with one question variant per language
  (`variants_per_language: 1` in `configs/dataset.yaml`) instead of all six; combination `i`
  takes the next variant along a shuffled cycle per template and language, independently for
  French and Italian. `max_instances` is 35 on every template (was 60, 40 or 20). Result:
  train 2,126 / val 402 / test 490 examples, all within the CLAUDE.md targets; build
  reproducible (two runs, identical checksums).
- **Reason:** with every variant kept, a combination gave 12 examples, so reaching 300-500
  test examples by `max_instances` alone meant ~5 combinations per test template: an accuracy
  resting on five regions or years. One variant per language gives 245 distinct test
  combinations instead of 140 for option "2 variants, max 20" (560 examples, above target).
  Training sees more slot values (regions, aliases, units), where the conventions live;
  paraphrase invariance still comes from each variant appearing in ~6 combinations. The
  system prompt (~4,250 tokens) is repeated on every example, so ~2,100 examples is ~9 M
  tokens per epoch, comfortable within a T4 session. A single `max_instances` for all
  templates weighs every template equally.
- **Known limit:** the rotation is even over the sampled combinations; validation drops some,
  so in templates with many drops (pumping, gross exchanges, source-vs-source hours) variant
  use among kept combinations ranges from 2 to 10 out of 35. Every variant still appears.
  Evening it out would move variant choice into validation; not worth the coupling now.
- **Alternatives:** lowering `max_instances` only (too few combinations per template);
  two variants per language with `max_instances` 20 (fewer combinations, test above target).

## 2026-10-01 — Base model: Qwen3-1.7B, chosen by a zero-shot pilot on validation

- **Decision:** fine-tune `Qwen/Qwen3-1.7B` (Apache 2.0), with thinking mode disabled
  everywhere (`enable_thinking=False` in training, evaluation and demo).
- **Pilot:** two candidates, zero-shot on the validation split (402 examples, 7 templates),
  same prompt, greedy, `max_new_tokens` 256, fp16 on a Kaggle T4, transformers 5.0.0.
  Predictions in `results/predictions/`. The test split was not used.

  | | Qwen3-1.7B | Qwen2.5-Coder-1.5B-Instruct |
  |---|---|---|
  | Execution accuracy | 9.7 % (39) | 8.2 % (33) |
  | Valid SQL rate | 88.8 % | 62.2 % |
  | Correct, French / Italian | 16 / 23 of 201 | 25 / 8 of 201 |
  | Generation time (402 examples) | 30.7 min | 20.0 min |

- **Reason:** the accuracy gap is not evidence (10 examples correct for both, 29 only for
  Qwen3, 23 only for Coder; McNemar p ≈ 0.4; seven templates, so examples are not
  independent). The valid SQL rate is: Coder's failures are systematic. It writes
  `date LIKE '2019-02-%'` on a DATE column 59 times, and it invents column names translated
  from Italian questions (`solare_mw`, `idroelettrica_mw`, `consomma_mw`), which is why it falls
  to 8/201 in Italian. Qwen3 reads the schema (5 invented columns in all), maps the Italian
  region aliases and is balanced across the two languages of the project. Its cost is speed:
  ~50 % slower on GPU, which matters for the CPU demo but keeps it in the same size class.
  Both models get 0 right on the extremes and classification families: that is the room
  fine-tuning has to show.
- **Few-shot check (2026-10-02):** both models rerun on validation with the nine few-shot
  examples (see the few-shot entry below), under the rule fixed before the run.

  | | Qwen3-1.7B | Qwen2.5-Coder-1.5B-Instruct |
  |---|---|---|
  | Execution accuracy | 24.6 % (99) | 23.4 % (94) |
  | Valid SQL rate | 90.5 % | 77.4 % |
  | Correct, French / Italian | 56 / 43 of 201 | 55 / 39 of 201 |
  | Generation time (402 examples) | 35.5 min | 22.9 min |

  55 examples correct only for Qwen3, 50 only for Coder: exact McNemar p = 0.70, no
  difference in either language, so **Qwen3-1.7B stays**. Examples help both a lot (zero-shot
  -> few-shot p < 1e-10), so few-shot is the baseline fine-tuning must beat. They close
  Coder's Italian gap, but Coder copies their surface: in the average family it scores 0/140
  and adds the `GROUP BY heure` of the last example (the hourly profile) in 44 answers, against
  5 for Qwen3. All four configurations score 0 on extremes, classification and the
  weekday/weekend gap.
- **Excluded before the pilot:** Qwen3.5 0.8B / 2B / 4B (Unsloth discourages QLoRA 4-bit on
  them, they need bf16 which the T4 lacks, multimodal); Gemma 4 E2B (5B parameters in total,
  fp16 overflow on T4, multimodal); Qwen2.5-Coder-3B and Qwen2.5-3B (`qwen-research` licence,
  non-commercial, unlike their 1.5B siblings); Llama 3.2 3B (Llama licence); SmolLM3-3B (kept
  as an alternative: French native, but 3B halves the CPU demo speed and it is weak at code).
- **Prompt length, measured:** with the Qwen tokenizer the system prompt is 4,528 tokens
  (3.29 characters per token, not the 3.5 assumed in `prompts.py`, whose estimate says 4,252);
  the full chat prompt is 4,561-4,587 tokens on validation. The estimate is to be recalibrated
  separately.

## 2026-10-01 — Prompt size estimate recalibrated on the Qwen3 tokenizer; ceiling 5000

- **Decision:** in `prompts.py`, `CHARS_PER_TOKEN` 3.5 -> 3.2 and `MAX_PROMPT_TOKENS`
  4500 -> 5000. The estimate of today's system prompt goes from 4,252 to 4,651 (real: 4,528).
- **Reason:** the measured ratio is 3.29 characters per token; with 3.5 the estimate was 6 %
  low and the 4500 ceiling of phase 2 was already exceeded without the check noticing. 3.2
  rounds down, so the estimate errs on the high side, which is the safe side for a ceiling.
  5000 leaves ~350 tokens of growth and keeps a whole training example (prompt, question,
  answer of at most ~150 tokens) near 5k tokens, the sequence length phase 4 will plan for.
- **Alternatives:** counting with the real tokenizer in the test (exact, but ties tests and CI
  to `transformers` and a Hugging Face download); keeping 4500 and trimming the prompt now
  (the TCO/TCH rows), postponed until the prompt actually needs to grow.

## 2026-10-01 — Few-shot mode: nine fixed train examples as earlier chat turns

- **Decision:** in `few_shot` mode every question is preceded by the same nine examples
  (`src/t2sql/eval/few_shot.py`): one train example per family, families in alphabetical
  order, languages alternating fr, it, fr…; template and example drawn with a seeded
  generator (`few_shot.seed: 0` in `configs/eval.yaml`). They are written as earlier turns of
  the conversation (user question, assistant SQL) between the system prompt and the question,
  through the same `chat_messages` used by training and the demo. Their ids are saved in the
  run metadata, and a resumed run with other examples is refused.
- **Reason:** train templates only, so validation and test templates are never shown (CLAUDE.md
  §7). One per family covers every kind of question without choosing with the evaluation
  questions in mind; a seeded draw rather than a hand pick, for the same reason. Chat turns are
  the form chat models are trained on, and they show the shape of the answer (bare SQL) as well
  as its content. Cost measured with the Qwen3 tokenizer: 1,182 tokens more per prompt (4,573
  -> 5,755), about +25 % generation time on GPU.
- **Rule fixed before running it:** the base few-shot configuration is also run with
  Qwen2.5-Coder-1.5B on validation, because Coder's zero-shot failures (LIKE on dates, column
  names translated from Italian) are the kind examples may fix. The base model changes to
  Coder only if Coder few-shot beats Qwen3 few-shot with McNemar p < 0.05 and not in one
  language only; otherwise Qwen3-1.7B stays. The numbers are added to the pilot entry either
  way.
- **Alternatives:** examples appended to the system prompt (simpler, but described rather than
  shown as answers); 3-5 examples (shorter, but which families to leave out is itself a
  choice); hand-picked examples of the hardest conventions (more help to the base model, but
  open to the suspicion of being chosen for the evaluation set); dropping long examples to
  save tokens (they are the most instructive ones: UNPIVOT, ties at the peak).

## 2026-10-02 — Baselines on the test split: phase 3 closed, decision gate passed

- **Result:** Qwen3-1.7B on the test split (490 examples, 7 templates never used before),
  same harness and prompt as the pilot. Tables in `results/report_test.md`.

  | | Zero-shot | Few-shot |
  |---|---|---|
  | Execution accuracy | 14.3 % (70) | 16.5 % (81) |
  | Valid SQL rate | 86.9 % | 82.0 % |
  | Correct, French / Italian | 36 / 34 of 245 | 45 / 36 of 245 |

- **Decision gate (CLAUDE.md §7):** zero-shot is far below ~85 %; we go on to fine-tuning
  with the same model, templates and prompt.
- **Reading the numbers:**
  - Few-shot barely helps on test: 33 examples correct only in zero-shot, 44 only in
    few-shot, exact McNemar p = 0.25, against +15 points on validation. Several of the nine
    examples happen to be close to validation templates (`avg_consumption_hour_season_region`
    vs `avg_consumption_moment_region_month`, `rank_position_region_measure_year` vs
    `rank_top_regions_measure_year`); test has no such neighbours. The test numbers are the
    ones to report.
  - One template carries the zero-shot score: `rate_avg_coverage_indicator_region_year` gives
    50 of the 70 correct answers (the coverage rate is a column, an `AVG` suffices). Without
    it zero-shot is 20/420 (4.8 %). Comparison (last month vs a year before) and timeseries
    (running sum) are 0/70 in both modes.
  - Truncations at 256 tokens (4 zero-shot, 12 few-shot) are not a budget problem: the gold
    queries of those templates are at most 130 tokens; the model writes convoluted ones.
- **Manual error sample:** 21 wrong results of zero-shot, 3 per template, classified in
  `results/error_sample_test.md`: aggregation 9, filter 6, time handling 5, unit 1 (+3 as a
  secondary cause). The recurring causes are domain conventions present in the prompt but not
  applied (daily or national granularity, running sums, peak hours and relative dates, unit
  conversion, region aliases) plus invented filters. The same ids will be classified for the
  fine-tuned model.
- **Validity:** these baselines hold for the current prompt and dataset. If phase 4 changes
  either (e.g. trimming the prompt for sequence length), they are rerun; the model pilot is
  not, since the choice of Qwen3 rests on failures of Qwen2.5-Coder that do not depend on the
  prompt text.

## 2026-10-02 — Training stack: Unsloth on a Kaggle T4, W&B for tracking

- **Decision:** QLoRA with Unsloth (`FastLanguageModel`, 4-bit base, LoRA through PEFT,
  `use_gradient_checkpointing="unsloth"`) and TRL's `SFTTrainer`, in
  `src/t2sql/train/sft.py`, driven by `configs/train_qlora.yaml` and run by
  `notebooks/train_kaggle.ipynb` on one T4 (`CUDA_VISIBLE_DEVICES=0`: with two visible GPUs the
  trainer would use DataParallel, which Unsloth does not support). Tracking with Weights &
  Biases (project `domain-text2sql`).
- **Versions:** Unsloth pins its neighbours, so they are installed by the training notebook
  with the recipe of Unsloth's own Kaggle notebooks, not declared in `pyproject.toml` (uv
  resolves all extras together and would refuse `transformers==4.56.2` next to the
  evaluation's 5.x). Verified on Kaggle: unsloth 2026.9.14, torch 2.10.0+cu128, transformers
  4.56.2, trl 0.22.2, peft 0.19.1, bitsandbytes 0.50.2, xformers 0.0.35. Evaluation stays on
  Kaggle's transformers 5.0.0 for every configuration; the adapter is a plain PEFT file.
- **Loss mask built by us:** each example is the evaluation prompt (`format_prompt`, the same
  function) followed by the SQL and `<|im_end|>`; labels are -100 on the prompt, so the loss
  falls on the answer only (74 tokens on average out of ~4,650). The prompt tokens must be an
  exact prefix of the full example, checked on every example (`sft.py --check`, no GPU), and
  no example may exceed `max_length` 4,800 (longest: 4,723). TRL is told not to re-tokenize
  (`skip_prepare_dataset`); its collator keeps our labels, and the trainer logs the tokens
  with loss in the first batch (smoke test: 55 of 4,636). The examples are tokenized with the
  evaluation tokenizer, not Unsloth's.
- **Reason:** Unsloth's kernels and activation offloading make 4.7k-token sequences fit and
  run about twice as fast on a T4 (peak memory in the smoke test: 5.5 GiB of 14.5). W&B shows
  the curves live while Kaggle trains in the background and gives a public link for the
  README.
- **Known detail:** Unsloth warns that Qwen3 does not accept `num_items_in_batch`, so with
  gradient accumulation each example weighs the same instead of each answer token; answers
  range from 35 to 153 tokens. Accepted: one question, one vote.
- **Alternatives:** plain transformers + PEFT + bitsandbytes (kept as plan B; about twice as
  slow); TRL's `assistant_only_loss` (fails silently on sequences longer than `max_length`,
  and is a template patch we cannot see); MLflow (no live view from a Kaggle session without
  a reachable server).

## 2026-10-02 — Adapters are evaluated merged into the float16 base

- **Decision:** `run.py --mode fine_tuned --adapter <checkpoint> --tag <name>` loads the base
  model in float16, exactly as for the baselines, applies the LoRA adapter with PEFT and merges
  it (`merge_and_unload`). Same prompt (no examples), same greedy decoding, same scoring. The
  tag keeps the outputs of each adapter apart; the adapter path is in the run metadata.
- **Reason:** CLAUDE.md §2.2 asks for the same precision across configurations. The adapter
  was trained on 4-bit weights and is applied to the float16 originals: the standard QLoRA
  practice, with a small mismatch that favours no configuration. Merging gives the base
  model's shape and speed, and is what phase 5 does before GGUF.
- **Kaggle detail:** the image ships torchao 0.10, which PEFT rejects as soon as it loads an
  adapter; the evaluation notebook uninstalls it (nothing here uses it).

## 2026-10-02 — Training plan: one epoch per run, checkpoints as a learning curve

- **Smoke test (20 updates of 16 examples, cosine schedule fitted to 20 steps):** 84 s per
  update, so one epoch (133 updates) takes ~3h05, plus ~8 min for the validation loss.
  *Corrected 2026-10-05:* the 84 s included the final validation loss; the full runs measured
  71-73 s per update, i.e. ~2h45 of training per epoch plus ~10 min per validation loss.
  Training loss 0.72 -> 0.12, validation loss 0.21. Evaluated as an adapter (`smoke20`): **38.6 %**
  execution accuracy on validation against 24.6 % few-shot and 9.7 % zero-shot (81 examples
  right only with the adapter, 25 only few-shot; McNemar p < 1e-7). Still 0 on extremes
  (ties reported with `ex_aequo`), classification (`AVG(...) OVER ()`) and the weekday/weekend
  gap; night hours still written as `heure IN (0, 22)`.
- **Decision:** the centre run is **one epoch**, not two (two would take ~6.5 h, beyond the
  3 h per run of CLAUDE.md and a quarter of the weekly quota). A checkpoint every 33 updates
  (~1/4 epoch) is pushed to the private Hub repo `zaninip/qwen3-1.7b-eco2mix-sql`, both to
  resume a killed session and to measure a learning curve: validation accuracy at 1/4, 1/2
  and 1 epoch, without extra training. Centre settings: r 16, alpha 32, dropout 0, all linear
  layers, lr 2e-4 cosine with 5 % warmup, effective batch 16, AdamW 8-bit, weight decay 0.001,
  seed 0.
- **Sensitivity plan (fixed before any full run):** one factor at a time around the centre,
  one epoch each: learning rate 1e-4 and 4e-4, and the centre again with seed 1 to measure
  the noise of training alone. Alpha is not varied (with Adam, alpha/r and the learning rate
  act on the output in the same way); rank only if quota is left. About 13 h of training and
  3 h of evaluation over one or two weeks.
- **Decision rules (fixed now):** configurations are compared on validation accuracy, paired
  (exact McNemar on the same 402 examples), with the validation loss as a smoother second
  signal. The validation set has only 7 templates, so examples are correlated and a difference
  of a few points is not evidence. The configuration kept is the best one on validation; if
  it does not beat the centre with p < 0.05, the centre is kept (the simpler choice), and the
  earliest checkpoint not significantly worse than the best is preferred (less training,
  less memorised template shape). **Instability flags**, either of which stops the plan for a
  discussion before going on: (a) the two seeds differ with p < 0.05; (b) halving or doubling
  the learning rate loses more than half of the centre's correct answers, with p < 0.05. The
  test split is used once, with the configuration kept.
- **Alternatives:** two epochs at the centre (cost above); a grid over lr x r x epochs
  (impossible within the quota); trimming the prompt to shorten training (it would change the
  baselines; kept as a lever if needed).

## 2026-10-04 — Three train-only templates fill SQL constructs that only validation and test used

- **Finding:** the centre run (one epoch) scored no better than the 20-update smoke adapter on
  validation (c66 36.3 %, c133 35.1 %, smoke20 38.6 %). The cause is in the data: no train
  example contains `LIMIT`, because every "first N" template landed in validation or test by
  the template split. The longer the training, the more the model learns that rankings never
  end with `LIMIT` and unlearns what the base model knew: answers with `LIMIT` on validation
  fall from 124 (smoke20) to 15 (c133), and `rank_top_regions_measure_year` from 47 to 15
  right. An audit with sqlglot of every construct of validation and test absent from train
  found `LIMIT` (validation 70 examples, test 140) and, in one test template
  (`cmp_last_month_vs_year_before_region`, 70), relative dates computed from `max(date)`
  (`year()`, `month()`, a join with the reference date): 43 % of the test examples.
- **Decision:** three templates marked `train_only: true`, kept out of the template split and
  added to train afterwards:
  - `rank_top_sources_national_year`: the first N sources in France over a year (`LIMIT`);
  - `rank_top_instants_consumption_region_year`: the N half-hours of highest consumption in a
    region (`LIMIT`, powers, no conversion);
  - `agg_energy_measure_region_relative_period`: a source's energy in a region "last month" or
    "last year", from `max(date)` (glossary §2).
  Mirror guards added to their close neighbours (`rank_sources_region_year`,
  `ext_peak_timestamp_region_year`) without changing any of their variants.
- **Guarantees, checked:** validation and test files byte-identical (checksums); the 2,126
  earlier train examples byte-identical and in the same order (generation draws per template,
  the split ignores train-only templates); the nine few-shot examples frozen by id in
  `configs/eval.yaml`, so every baseline (zero-shot, few-shot, pilot) stays valid; build
  reproducible (two runs, same files). Train: 2,336 examples, 34 templates; one epoch is 146
  updates.
- **Disclosure:** the gap was found on validation, and the templates were written to fill it.
  They cover generic constructs, not the evaluation questions: other objects (sources,
  half-hours, a plain total) and no percentage change between two periods. Their closest
  variants are still near the evaluation templates by their "first N" wording (`make
  similar`: 0.81 between "Les {n} demi-heures où la consommation a été la plus élevée" and the
  test's "Les {n} mois où la production … a été la plus élevée"); the relative-date template
  shares its convention with the test template on purpose, since it is a glossary rule the
  model must learn.
- **Consequence:** the centre run is trained again on the new train split; the results of the
  first centre run stay in `results/` as the record of the gap.
- **Alternatives:** keeping the data and reporting the gap (the headline would measure a hole
  in the data, not what fine-tuning teaches); changing the split rule to cover constructs and
  rebuilding everything (validation and test would change, so the pilot and every baseline
  would have to be run again).

## 2026-10-05 — Centre run on the d2 train split: checkpoint-66 kept (51.2 % on validation)

- **Runs:** the centre settings trained twice for one epoch, on the first train split (133
  updates, 3h24 with the four validation losses) and on d2, which adds the three train-only
  templates (146 updates, 3h31). Both have their lowest validation loss at half an epoch
  (0.159 first, **0.147 d2**), then 0.165-0.167 while the training loss goes to ~0.002.
- **Validation accuracy (402 examples):**

  | Checkpoint | first split | d2 |
  |---|---|---|
  | 1/4 epoch (33) | 30.6 % | 39.8 % |
  | 1/2 epoch (66) | 36.3 % | **51.2 %** |
  | 1 epoch (133 / 146) | 35.1 % | 49.5 % |

  Base model on the same examples: zero-shot 9.7 %, few-shot 24.6 %; smoke20 38.6 %. d2c66
  against c66: 64 examples right only with d2, 4 only with c66 (McNemar p < 1e-14); against
  few-shot: 121 vs 14 (p < 1e-21). French 102/201, Italian 104/201.
- **Rule applied (fixed beforehand):** d2c66 is the best; d2c146 is not significantly worse
  (16 vs 9, p = 0.23) but trains twice as long; d2c33 is (p < 1e-10). **checkpoint-66 of the
  d2 run is kept** as the centre of the sensitivity plan.
- **What d2 fixed:** `rank_top_regions_measure_year` 27 -> 66 of 70 (answers with `LIMIT`
  on validation: 29 -> 117), and the weekday/weekend gap 0 -> 21 of 70. Still 0 on
  `ext_record_day_production_region_year` (the model now tries the `ex_aequo` shape of the
  peak template and writes an invalid GROUP BY, 37 of 70) and on
  `cls_region_vs_average_consumption_year`. The valid SQL rate drops from 96.0 % to 87.3 %,
  almost entirely on those two templates, where nothing was right anyway.
- **For the sensitivity runs:** one epoch each as planned, evaluated at checkpoint-66 and at
  the end, compared with d2c66.

## 2026-10-08 — Coverage rule: every evaluated convention is trained by at least 2 templates

- **Finding:** the seed replicate of the centre (seed 1) agrees with seed 0 at checkpoint-66
  (51.7 % vs 51.2 %, p = 0.88) but not at the end of the epoch (53.0 % vs 49.5 %, p = 0.024,
  instability flag (a)). All the variation sits in two validation templates,
  `avg_consumption_moment_region_month` (22 to 46 right across seeds, checkpoints and learning
  rates) and `avg_weekday_weekend_gap_consumption_region_year` (11 to 21); the other five give
  158-166 of 262 in every run. Their conventions (time-of-day bands, weekday/weekend) appear in
  a single train template each: the model learns them half-way, and a seed decides. The same
  holds more widely: 14 conventions used by validation or test templates have fewer than 2
  train templates, 7 of them none (17 under 3).
- **Decision:** a design rule, fixed before any new result: every convention that a validation
  or test template uses appears in at least `min_train_templates_per_convention` (2, in
  `configs/dataset.yaml`) train templates. `tests/test_coverage.py` checks it on the template
  files, with the split the build makes (same function and seed; checked identical to the
  build's assignment); it failed on the current templates before any was added, listing the
  gaps. The gaps are filled with `train_only` templates, as for `LIMIT`, so validation and
  test stay byte-identical.
- **Reason:** the claim of the project is that fine-tuning teaches the domain conventions; a
  convention the training barely contains measures the prompt, not the fine-tuning. With the
  rule, the test measures conventions learnt in training applied to question shapes never
  seen, which is how fine-tuning is used. Two templates give each convention two different
  contexts, so that it is not tied to one question shape.
- **Costs and disclosure:** some conventions coincide with one kind of question
  (`offshore_share`, `record_day_daily_total`): covering them twice makes the matching
  validation template more familiar and the validation number more optimistic. The rule is
  applied to every gap alike, decided before the results it will produce, and the test split
  is still used once. Validation and the learning-rate comparison are run again on the new
  train split; the earlier runs stay in `results/` as the record.
- **Wording ceiling (added while writing the templates):** the first drafts of the new
  templates copied the phrasing of the evaluation questions with a slot added (e.g. the test's
  "En {annee}, quel a été en moyenne le taux de couverture {mesure.de} {region.in} ?" plus
  "{moment.in}"): their closest validation/test variant had a median word-sequence similarity
  of 0.73, against 0.50 for the original train templates (90th percentile 0.91 vs 0.74). That
  teaches the surface of the test questions, not the convention. A second rule caps it: every
  train-only variant stays under `max_train_only_similarity` (0.75, about the 90th percentile of
  the original train), checked by `tests/test_coverage.py`; 93 variants were rewritten. Two
  templates also gained a precondition: the "above/below the regional average" questions keep
  only years where every region has a value, since a NULL ("not available", glossary §7) would
  be labelled by how SQL treats NULL in a CASE, not by the data.
- **Result (train split d3):** 13 new train-only templates (16 in all with the three of d2),
  train 3,218 examples in 47 templates; validation and test byte-identical, the 2,126 original
  train examples byte-identical, build reproducible; every evaluated convention now has at
  least 2 train templates. One epoch is 202 updates (~4 h of training at ~71 s per update).
- **Baselines:** zero-shot and the model pilot do not depend on the train split and stay
  valid. Few-shot is a recipe (one train example per family, seed 0): it is drawn again from
  the new train split and run again on validation and test, so that both few-shot and
  fine-tuning use the same training data; the ids frozen on 2026-10-04 are replaced and the
  earlier few-shot results kept as the record. The new draw changes all nine examples (one random
  generator runs through the families in order, so one more template in a family moves the
  draws after it); four come from train-only templates; 997 tokens instead of 1,092.
- **Alternatives:** keeping the data and reporting the gaps as a limitation (the headline would
  be weighed down by conventions the training did not teach); editing the two validation
  templates (moving the yardstick); a threshold of 3 (about 23 templates instead of 13, an
  epoch of ~4h45 instead of ~3h55 per run: robustness bought at almost twice the review and
  GPU cost); 2 for conventions that are a filter, a column or a ratio and 3 for those needing
  a window or nested queries (saves only 3 templates on 3, since most gaps are of the second
  kind, and adds a classification that is harder to defend than one threshold).
