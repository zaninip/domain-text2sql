# éCO2mix glossary — domain conventions

Every statement below was checked against the data in `data/eco2mix.duckdb`; conventions that
are choices rather than facts (seasons, peak hours, source groups) are recorded in
`docs/decisions.md`.
Items in the "Prompt" sections are meant to be included in the system prompt; "Notes" are for
humans (dataset generation, evaluation) only.

## 1. Power vs energy

**Prompt**

- Every `*_mw` column is an average power over one 30-minute step (the step is stated in
  `schema.md`, derived from the data). It is not energy.
- Energy over a period: `SUM(x_mw) * 0.5` MWh. GWh = MWh / 1000, TWh = MWh / 1e6.
- "Average consumption/production" over a period → `AVG(x_mw)`; "total consumption/production"
  or "energy" → `SUM(x_mw) * 0.5`. Never sum MW without the step factor when energy is asked.
- Peak / maximum / minimum power → `MAX(x_mw)` / `MIN(x_mw)` on the 30-minute values.
- Total production = `thermique + nucleaire + eolien + solaire + hydraulique + bioenergies`
  (there is no production column). Use `COALESCE(col, 0)` inside the sum because `nucleaire_mw`
  and `pompage_mw` are NULL where the region has no plant.
- Balance (holds up to rounding): `consommation = production + pompage + ech_physiques`.

**Notes**

- Balance residual is ≤ 1 MW in 96% of rows; a few hundred rows (mostly 2013 and 2025) have
  larger residuals — source artefacts, not to be "fixed".

## 2. Time

**Prompt**

- `date_heure` is local French time (Europe/Paris), naive. Compare with plain literals:
  `date_heure = '2020-02-13 19:00'`. Use `date`, `annee`, `mois`, `heure` for filters and
  grouping instead of extracting from `date_heure`.
- The dataset ends on the date given in the schema (currently 2026-06-30). Relative dates are
  resolved against that date, not today's date: "last month" = June 2026, "this year" = 2026
  (partial, January–June), "last year" = 2025.
- Day of week: `isodow(date)` (1 = Monday … 7 = Sunday). Weekend = `isodow(date) IN (6, 7)`.
- Seasons follow the astronomical calendar, with fixed conventional boundaries (the real
  equinox/solstice dates move by ±1 day depending on the year; this approximation is accepted):
  spring = 20 March – 20 June, summer = 21 June – 21 September, autumn = 22 September –
  20 December, winter = 21 December – 19 March.
- Winter spans two calendar years. "Winter 2023" (single year) = 21 Dec 2022 – 19 Mar 2023,
  i.e. the winter that ends in that year: `date BETWEEN '2022-12-21' AND '2023-03-19'`.
  "Winter 2023-24" (two years) = 21 Dec 2023 – 19 Mar 2024.
- Peak hours: morning peak `heure BETWEEN 8 AND 12`, evening peak
  `heure BETWEEN 18 AND 20`; "peak hours" without qualifier = both. Off-peak = the rest.
- "Night": `heure < 6 OR heure >= 22`.

**Notes**

- The source ignores DST: after cleaning, the spring change day has 46 rows per region
  (23 real hours) and the autumn change day has 48 rows instead of 50 (the repeated 02:00–02:59
  hour is present only once). Daily energy on those two days is therefore slightly off; no
  template should target them.
- Because timestamps are local, a "day" is a local calendar day, matching RTE publications.

## 3. Generation sources and groups

**Prompt**

- Sources: `thermique` (fossil: gas, coal, oil), `nucleaire`, `eolien` (wind), `solaire`,
  `hydraulique`, `bioenergies`.
- Renewables (RTE convention): `eolien + solaire + hydraulique +
  bioenergies`. Pumped-storage pumping (`pompage_mw`) is not subtracted.
- Low-carbon / decarbonised: renewables + `nucleaire`.
- Fossil = `thermique_mw` alone.
- Wind split: `eolien_terrestre_mw` (onshore) and `eolien_offshore_mw` are meaningful from
  2024 only and NULL before. For any wind question that is not explicitly onshore/offshore, use `eolien_mw`.

**Notes**

- The source has the wind split columns filled with 0 in 2021–2023 (not measured);
  `load_clean` sets them to NULL before 2024 so that "not available" is always NULL.
- Regions without nuclear plants: Bourgogne-Franche-Comté, Bretagne, Île-de-France,
  Pays de la Loire, Provence-Alpes-Côte d'Azur. Regions without pumped storage: Centre-Val de
  Loire, Île-de-France, Normandie, Nouvelle-Aquitaine, Pays de la Loire (Hauts-de-France: NULL
  in 2013–2014 only). In those regions the column is NULL up to 2020 and 0 from 2021.

## 4. Sign conventions

**Prompt**

- `pompage_mw` ≤ 0: power consumed by pumped-storage pumps. Pumping energy = `-SUM(pompage_mw) * 0.5`.
- `ech_physiques_mw`: net physical exchanges with neighbouring regions and countries.
  Positive = the region imports, negative = the region exports. "Exported energy" of a region
  = `-SUM(ech_physiques_mw) * 0.5` restricted to `ech_physiques_mw < 0` only if the question
  asks for gross exports; otherwise net balance = `SUM(ech_physiques_mw) * 0.5`.

## 5. Coverage rate (TCO) and load factor (TCH)

**Prompt**

- `tco_<source>_pct` — *taux de couverture*: share of the region's consumption covered by that
  source at that step, `100 * source_mw / consommation_mw`. Can exceed 100 when the region
  produces more than it consumes. Available from 2020-01-01; NULL before.
- `tch_<source>_pct` — *taux de charge*: load factor, `100 * source_mw / installed capacity`.
  Installed capacity is not in the dataset, so TCH cannot be recomputed: use the column.
  Available from 2020-01-01; NULL before, and always NULL where the source is absent.
- "Share of source X in consumption" over a period → recompute from the MW columns
  (`100 * SUM(x_mw) / SUM(consommation_mw)`), do not average `tco_*` values (an average of
  ratios is not the ratio of sums). "Average coverage rate" as a question about the TCO
  indicator itself → `AVG(tco_x_pct)`.
- "Share of source X in production" → `100 * SUM(x_mw) / SUM(total production)`.

**Notes**

- TCO checked on Bretagne 2024-06-15 12:00: wind 789 / consumption 2071 = 38.1 % = `tco_eolien`.

## 6. Regions

**Prompt**

- 12 metropolitan regions; Corsica and overseas territories are not in the dataset.
  National total = sum over the 12 regions (checked against RTE national figures within rounding).
- `region` values are the official names with accents and hyphens:
  Auvergne-Rhône-Alpes, Bourgogne-Franche-Comté, Bretagne, Centre-Val de Loire, Grand Est,
  Hauts-de-France, Île-de-France, Normandie, Nouvelle-Aquitaine, Occitanie, Pays de la Loire,
  Provence-Alpes-Côte d'Azur.
- Aliases to map: PACA → Provence-Alpes-Côte d'Azur; AURA / Rhône-Alpes / Auvergne →
  Auvergne-Rhône-Alpes; IDF / Paris region → Île-de-France; BFC / Bourgogne / Franche-Comté →
  Bourgogne-Franche-Comté; CVL → Centre-Val de Loire; Nord / Nord-Pas-de-Calais / Picardie →
  Hauts-de-France; Alsace / Lorraine / Champagne → Grand Est; Aquitaine / Poitou / Limousin →
  Nouvelle-Aquitaine; Midi-Pyrénées / Languedoc → Occitanie; Brittany → Bretagne;
  Normandy → Normandie. Italian: Bretagna → Bretagne, Normandia → Normandie,
  Isola di Francia → Île-de-France, Provenza → Provence-Alpes-Côte d'Azur.
- `code_insee_region` is text ('11', '24', …); prefer filtering on `region`.

## 7. NULL and data-quality rules

**Prompt**

- A NULL means "not available", never zero. `SUM` and `AVG` ignore NULLs; when adding columns
  row by row, wrap each in `COALESCE(col, 0)`.
- The `nature` column ('Données définitives' up to 2024, 'Données consolidées' from 2025) is
  not a filter for normal questions.

**Notes**

- 108 rows of 2013 have NULL `eolien_mw` (source had "-" / "ND"); the first step of
  2013-01-01 00:00 has NULL consumption in all regions.
