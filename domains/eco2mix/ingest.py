"""Download RTE éCO2mix regional data from ODRÉ and load it into DuckDB.

Run as a script: ``uv run python domains/eco2mix/ingest.py``.
"""

from datetime import timedelta
from pathlib import Path

import duckdb
import httpx

# ODRÉ export API (Opendatasoft Explore v2.1). One call downloads the whole dataset;
# the portal allows 50k calls per dataset per month, so never call this in a loop.
DATASET_ID = "eco2mix-regional-cons-def"
EXPORT_URL = (
    f"https://odre.opendatasoft.com/api/explore/v2.1/catalog/datasets/{DATASET_ID}/exports/parquet"
)

ROOT = Path(__file__).resolve().parents[2]
RAW_PATH = ROOT / "data" / "raw" / f"{DATASET_ID}.parquet"
DB_PATH = ROOT / "data" / "eco2mix.duckdb"
TABLE = "eco2mix"
LOCAL_TZ = "Europe/Paris"

_CHUNK_BYTES = 1 << 20  # 1 MiB per streamed chunk
_LOG_EVERY_BYTES = 50 << 20  # print progress every 50 MiB


def download_raw(dest: Path = RAW_PATH, url: str = EXPORT_URL) -> Path:
    """Download the full Parquet export once; skip if ``dest`` already exists."""
    if dest.exists():
        print(f"raw file already present, skipping download: {dest}")
        return dest

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")  # incomplete downloads never look valid
    done = 0
    with httpx.stream("GET", url, timeout=httpx.Timeout(30.0, read=600.0)) as resp:
        resp.raise_for_status()
        with tmp.open("wb") as f:
            for chunk in resp.iter_bytes(_CHUNK_BYTES):
                f.write(chunk)
                done += len(chunk)
                if done % _LOG_EVERY_BYTES < _CHUNK_BYTES:
                    print(f"  downloaded {done / (1 << 20):.0f} MiB", flush=True)
    tmp.replace(dest)
    print(f"saved {dest} ({dest.stat().st_size / (1 << 20):.1f} MiB)")
    return dest


# Cleaning rules (see docs/decisions.md and glossary.md):
# - column names: French as in the source, with a unit suffix (_mw, _pct);
# - timestamps become naive local time so that literals behave the same in any session TZ;
# - the source repeats 02:00/02:30 on the spring DST day with copied values: those rows have
#   a local wall-clock string that does not match the real instant -> dropped by the WHERE;
# - `eolien` is text in the export because of a few "-"/"ND" values in 2013 -> TRY_CAST;
# - `column_30` (all NULL) and `stockage/destockage_batterie` (all 0) are not loaded;
# - NULLs for sources absent from a region (nuclear, pumping before 2021) are kept as NULL.
_POWER_COLS = [
    "consommation", "thermique", "nucleaire", "eolien", "solaire", "hydraulique",
    "pompage", "bioenergies", "ech_physiques", "eolien_terrestre", "eolien_offshore",
]  # fmt: skip
_RATE_COLS = [
    f"{kind}_{src}"
    for src in ("thermique", "nucleaire", "eolien", "solaire", "hydraulique", "bioenergies")
    for kind in ("tco", "tch")
]
# Onshore/offshore wind columns exist since 2021 but are filled with 0 until 2023 (observed in
# the data: they only start matching `eolien` in 2024). "Not measured" must be NULL, not 0.
_WIND_SPLIT_COLS = {"eolien_terrestre", "eolien_offshore"}
_WIND_SPLIT_FROM = 2024


# Real instant expressed as naive local wall-clock time.
_LOCAL_TS = f"(date_heure AT TIME ZONE '{LOCAL_TZ}')"
# Keeps only rows whose source wall-clock string matches the real instant (drops DST phantoms).
_REAL_ROWS = f"strftime({_LOCAL_TS}, '%Y-%m-%d %H:%M') = date || ' ' || heure"


def check_time_step(raw: Path = RAW_PATH) -> timedelta:
    """Return the regular time step, after checking the only gaps are the autumn DST hour."""
    sql = f"""
    WITH steps AS (
        SELECT date_heure - lag(date_heure) OVER (PARTITION BY libelle_region ORDER BY date_heure)
                   AS step,
               month(date_heure) AS m, heure
        FROM read_parquet('{raw.as_posix()}') WHERE {_REAL_ROWS}
    )
    SELECT step, count(*) AS n, bool_and(m = 10 AND heure = '02:00') AS autumn_only
    FROM steps WHERE step IS NOT NULL GROUP BY step ORDER BY n DESC
    """
    rows = duckdb.sql(sql).fetchall()
    regular: timedelta = rows[0][0]
    for step, n, autumn_only in rows[1:]:
        if step != regular + timedelta(hours=1) or not autumn_only:
            raise ValueError(f"unexpected time step {step} ({n} rows)")
    n_gaps = sum(r[1] for r in rows[1:])
    print(f"regular time step: {regular}; {rows[0][1]:,} steps + {n_gaps} autumn DST gaps")
    return regular


def load_clean(raw: Path = RAW_PATH, db: Path = DB_PATH) -> None:
    """Build the clean table from the Parquet export into a fresh database file."""
    db.unlink(missing_ok=True)  # DuckDB never shrinks a file: rebuild from scratch
    local_ts = _LOCAL_TS
    sep = ",\n        "
    power = sep.join(
        f"CASE WHEN year({local_ts}) < {_WIND_SPLIT_FROM} THEN NULL "
        f"ELSE TRY_CAST({c} AS INTEGER) END AS {c}_mw"
        if c in _WIND_SPLIT_COLS
        else f"TRY_CAST({c} AS INTEGER) AS {c}_mw"
        for c in _POWER_COLS
    )
    rates = sep.join(f"{c} AS {c}_pct" for c in _RATE_COLS)
    sql = f"""
    CREATE OR REPLACE TABLE {TABLE} AS
    SELECT
        code_insee_region,
        libelle_region AS region,
        nature,
        {local_ts} AS date_heure,
        CAST({local_ts} AS DATE) AS date,
        CAST(year({local_ts}) AS INTEGER) AS annee,
        CAST(month({local_ts}) AS INTEGER) AS mois,
        CAST(hour({local_ts}) AS INTEGER) AS heure,
        {power},
        {rates}
    FROM read_parquet('{raw.as_posix()}')
    WHERE {_REAL_ROWS}
    ORDER BY region, date_heure
    """
    with duckdb.connect(str(db)) as con:
        con.execute(sql)
        n_rows = con.execute(f"SELECT count(*) FROM {TABLE}").fetchone()[0]
    print(f"table {TABLE}: {n_rows:,} rows; {db} = {db.stat().st_size / (1 << 20):.1f} MiB")


# National consumption (MW) published on rte-france.com; the regional sum (12 regions, no Corsica)
# differs only by integer rounding. Tolerance covers rounding, not a wrong hour or time zone.
_REFERENCE_MW = {"2025-08-01 08:00": 41061, "2020-02-13 19:00": 73907}
_REF_TOLERANCE_MW = 5


def verify_reference_values(db: Path = DB_PATH) -> None:
    """Fail if the regional sum of consumption disagrees with published national figures."""
    with duckdb.connect(str(db), read_only=True) as con:
        for ts, expected in _REFERENCE_MW.items():
            got = con.execute(
                f"SELECT sum(consommation_mw) FROM {TABLE} WHERE date_heure = ?", [ts]
            ).fetchone()[0]
            if got is None or abs(got - expected) > _REF_TOLERANCE_MW:
                raise ValueError(f"{ts}: consumption {got} MW, expected {expected} MW")
            print(f"reference check {ts}: {got} MW vs RTE {expected} MW  ok")


SCHEMA_PATH = ROOT / "domains" / "eco2mix" / "schema.md"

# column -> (unit, description). Written into schema.md next to the type read from the DB,
# so this dict must cover every column of the clean table (checked in write_schema_md).
_COLUMN_DOCS: dict[str, tuple[str, str]] = {
    "code_insee_region": ("", "INSEE code of the region (2 digits, text)"),
    "region": ("", "Region name with accents, e.g. 'Île-de-France', 'Provence-Alpes-Côte d'Azur'"),
    "nature": ("", "'Données définitives' (up to 2024) or 'Données consolidées' (2025 onwards)"),
    "date_heure": ("", "Start of the time step, local French time (naive TIMESTAMP)"),
    "date": ("", "Calendar date of date_heure"),
    "annee": ("", "Year of date_heure"),
    "mois": ("", "Month of date_heure (1-12)"),
    "heure": ("", "Hour of date_heure (0-23)"),
    "consommation_mw": ("MW", "Average electricity consumption over the step"),
    "thermique_mw": ("MW", "Fossil thermal generation (gas, coal, oil)"),
    "nucleaire_mw": ("MW", "Nuclear generation; NULL before 2021 in regions without plants"),
    "eolien_mw": ("MW", "Total wind generation (onshore + offshore)"),
    "solaire_mw": ("MW", "Solar generation"),
    "hydraulique_mw": ("MW", "Hydro generation (run-of-river, lakes, pumped-storage turbining)"),
    "pompage_mw": ("MW", "Pumped-storage pumping, <= 0; NULL before 2021 where no plant"),
    "bioenergies_mw": ("MW", "Bioenergy generation (biomass, biogas, waste)"),
    "ech_physiques_mw": ("MW", "Net physical exchanges: > 0 imports, < 0 exports"),
    "eolien_terrestre_mw": ("MW", "Onshore wind generation; NULL before 2024"),
    "eolien_offshore_mw": ("MW", "Offshore wind generation; NULL before 2024"),
}
_COLUMN_DOCS |= {
    f"tco_{src}_pct": ("%", f"Coverage rate: {src} generation / consumption; NULL before 2020")
    for src in ("thermique", "nucleaire", "eolien", "solaire", "hydraulique", "bioenergies")
}
_COLUMN_DOCS |= {
    f"tch_{src}_pct": ("%", f"Load factor: {src} generation / installed capacity; NULL before 2020")
    for src in ("thermique", "nucleaire", "eolien", "solaire", "hydraulique", "bioenergies")
}


def write_schema_md(step: timedelta, db: Path = DB_PATH, out: Path = SCHEMA_PATH) -> None:
    """Generate schema.md from the DB: table facts, then one row per column."""
    with duckdb.connect(str(db), read_only=True) as con:
        cols = con.execute(f"DESCRIBE {TABLE}").fetchall()
        n_rows, first, last = con.execute(
            f"SELECT count(*), min(date_heure), max(date_heure) FROM {TABLE}"
        ).fetchone()
    missing = {c[0] for c in cols} ^ set(_COLUMN_DOCS)
    if missing:
        raise ValueError(f"columns without docs or docs without column: {missing}")

    lines = [
        f"# Table `{TABLE}`",
        "",
        f"- Source: RTE éCO2mix regional data (ODRÉ dataset `{DATASET_ID}`), 12 metropolitan "
        "regions (Corsica excluded).",
        f"- One row per region and {step.seconds // 60}-minute step; power values are averages over"
        f" the step. Energy in MWh = sum(MW) x {step.total_seconds() / 3600:g} h.",
        f"- Timestamps are local French time ({LOCAL_TZ}). Range: {first} to {last}. "
        f"{n_rows:,} rows.",
        "",
        "| column | type | unit | description |",
        "|---|---|---|---|",
    ]
    for name, ctype, *_ in cols:
        unit, desc = _COLUMN_DOCS[name]
        lines.append(f"| {name} | {ctype} | {unit} | {desc} |")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(cols)} columns)")


if __name__ == "__main__":
    raw_path = download_raw()
    time_step = check_time_step(raw_path)
    load_clean(raw_path)
    verify_reference_values()
    write_schema_md(time_step)
