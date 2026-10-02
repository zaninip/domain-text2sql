# Manual error sample: Qwen3-1.7B zero-shot, test split

Manual part of the error taxonomy (CLAUDE.md §10). The sample is 21 `wrong_result` predictions
of `results/predictions/Qwen3-1.7B_test_zero_shot.jsonl`, 3 per test template: for each
template, its wrong results sorted by id, then `random.Random(0).sample(..., 3)` with one
generator shared across templates in alphabetical order. One main category per case;
secondary causes in the note. The same ids are to be classified again for the fine-tuned model.

| # | id | Category | Note |
|---:|---|---|---|
| 1 | avg_production_moment_region_year#0032#fr#2 | time | peak hours as 8-12 only (18-20 lost); invented `nature` filter |
| 2 | avg_production_moment_region_year#0013#fr#1 | time | "en semaine" as `mois BETWEEN 1 AND 7` and 8-20 h |
| 3 | avg_production_moment_region_year#0028#it#1 | filter | alias "Paesi della Loira" mapped to Centre-Val de Loire |
| 4 | cmp_last_month_vs_year_before_region#0023#it#4 | time | "mese scorso vs un anno prima" not resolved; share of consumption instead |
| 5 | cmp_last_month_vs_year_before_region#0001#fr#5 | time | one instant one month before the last date; change of a value with itself |
| 6 | cmp_last_month_vs_year_before_region#0014#it#1 | time | max(mois) and max(annee) taken separately; share of consumption instead |
| 7 | mc_hours_share_above_region_year#0109#fr#2 | filter | invented `heure BETWEEN 8 AND 12` and `nature` filters |
| 8 | mc_hours_share_above_region_year#0107#fr#4 | aggregation | sums consumption instead of counting steps; date range cut at June |
| 9 | mc_hours_share_above_region_year#0072#fr#0 | filter | invented hour filters and a single day |
| 10 | rank_top_days_national_year#0020#it#3 | aggregation | ranks half-hour rows, no `GROUP BY date`; MWh named GWh |
| 11 | rank_top_days_national_year#0033#fr#5 | aggregation | groups by region: not the national total per day |
| 12 | rank_top_days_national_year#0024#fr#0 | aggregation | ranks half-hour rows; invented hour filter |
| 13 | rank_top_months_measure_region_year#0022#it#4 | filter | invented `mois IN (1, 2, 3)`; MWh instead of GWh |
| 14 | rank_top_months_measure_region_year#0036#fr#3 | unit | MWh instead of TWh |
| 15 | rank_top_months_measure_region_year#0038#it#3 | aggregation | groups by day instead of month; date range cut at July |
| 16 | rate_avg_coverage_indicator_region_year#0009#fr#4 | filter | invented `code_insee_region = '98'`; load factor (tch) instead of coverage (tco) |
| 17 | rate_avg_coverage_indicator_region_year#0026#it#2 | aggregation | no `AVG`: returns every half-hour value |
| 18 | rate_avg_coverage_indicator_region_year#0005#it#3 | filter | region filtered by a wrong INSEE code ('24') |
| 19 | ts_cumulative_production_region_year#0011#fr#0 | aggregation | yearly total, no running sum; MWh instead of GWh |
| 20 | ts_cumulative_production_region_year#0005#fr#0 | aggregation | sum of all sources, no running sum |
| 21 | ts_cumulative_production_region_year#0027#it#1 | aggregation | monthly values, no running sum; MWh instead of GWh |

## Counts

| Category | Cases |
|---|---:|
| Wrong aggregation | 9 |
| Wrong filter | 6 |
| Wrong time handling | 5 |
| Wrong unit conversion | 1 (+3 as secondary cause) |

## Recurring patterns

- **Granularity:** "days" or "national" answered on half-hour rows or per region.
- **Running sums never written:** no `SUM(...) OVER (ORDER BY mois)` in any answer.
- **Invented filters:** `nature = 'Données définitives'`, hour ranges nobody asked for, region
  filtered by guessed INSEE codes.
- **Time conventions:** peak hours, weekdays and "last month" not applied as the glossary
  defines them.
- **Units:** a column named `_gwh` computed in MWh (the `/ 1000` forgotten).

Nearly every error is a domain convention written in the prompt but not applied, rather than
generic SQL: the behaviour fine-tuning is meant to teach.
