# Table `eco2mix`

- Source: RTE éCO2mix regional data (ODRÉ dataset `eco2mix-regional-cons-def`), 12 metropolitan regions (Corsica excluded).
- One row per region and 30-minute step; power values are averages over the step. Energy in MWh = sum(MW) x 0.5 h.
- Timestamps are local French time (Europe/Paris). Range: 2013-01-01 00:00:00 to 2026-06-30 23:30:00. 2,838,768 rows.

| column | type | unit | description |
|---|---|---|---|
| code_insee_region | VARCHAR |  | INSEE code of the region (2 digits, text) |
| region | VARCHAR |  | Region name with accents, e.g. 'Île-de-France', 'Provence-Alpes-Côte d'Azur' |
| nature | VARCHAR |  | 'Données définitives' (up to 2024) or 'Données consolidées' (2025 onwards) |
| date_heure | TIMESTAMP |  | Start of the time step, local French time (naive TIMESTAMP) |
| date | DATE |  | Calendar date of date_heure |
| annee | INTEGER |  | Year of date_heure |
| mois | INTEGER |  | Month of date_heure (1-12) |
| heure | INTEGER |  | Hour of date_heure (0-23) |
| consommation_mw | INTEGER | MW | Average electricity consumption over the step |
| thermique_mw | INTEGER | MW | Fossil thermal generation (gas, coal, oil) |
| nucleaire_mw | INTEGER | MW | Nuclear generation; NULL before 2021 in regions without plants |
| eolien_mw | INTEGER | MW | Total wind generation (onshore + offshore) |
| solaire_mw | INTEGER | MW | Solar generation |
| hydraulique_mw | INTEGER | MW | Hydro generation (run-of-river, lakes, pumped-storage turbining) |
| pompage_mw | INTEGER | MW | Pumped-storage pumping, <= 0; NULL before 2021 where no plant |
| bioenergies_mw | INTEGER | MW | Bioenergy generation (biomass, biogas, waste) |
| ech_physiques_mw | INTEGER | MW | Net physical exchanges: > 0 imports, < 0 exports |
| eolien_terrestre_mw | INTEGER | MW | Onshore wind generation; NULL before 2024 |
| eolien_offshore_mw | INTEGER | MW | Offshore wind generation; NULL before 2024 |
| tco_thermique_pct | DOUBLE | % | Coverage rate: thermique generation / consumption; NULL before 2020 |
| tch_thermique_pct | DOUBLE | % | Load factor: thermique generation / installed capacity; NULL before 2020 |
| tco_nucleaire_pct | DOUBLE | % | Coverage rate: nucleaire generation / consumption; NULL before 2020 |
| tch_nucleaire_pct | DOUBLE | % | Load factor: nucleaire generation / installed capacity; NULL before 2020 |
| tco_eolien_pct | DOUBLE | % | Coverage rate: eolien generation / consumption; NULL before 2020 |
| tch_eolien_pct | DOUBLE | % | Load factor: eolien generation / installed capacity; NULL before 2020 |
| tco_solaire_pct | DOUBLE | % | Coverage rate: solaire generation / consumption; NULL before 2020 |
| tch_solaire_pct | DOUBLE | % | Load factor: solaire generation / installed capacity; NULL before 2020 |
| tco_hydraulique_pct | DOUBLE | % | Coverage rate: hydraulique generation / consumption; NULL before 2020 |
| tch_hydraulique_pct | DOUBLE | % | Load factor: hydraulique generation / installed capacity; NULL before 2020 |
| tco_bioenergies_pct | DOUBLE | % | Coverage rate: bioenergies generation / consumption; NULL before 2020 |
| tch_bioenergies_pct | DOUBLE | % | Load factor: bioenergies generation / installed capacity; NULL before 2020 |
