"""Execute every generated SQL and keep only the instances that actually answer something.

A generated pair is dropped when its query fails, returns nothing, or returns a degenerate
answer (all NULL, all zero). The last case is not hypothetical: sources absent from a region
are stored as 0 from 2021 on, so "nuclear energy in Île-de-France in 2023" is a well-formed
question whose answer is 0 MWh — useless to train on and misleading in the demo.

Instances that survive keep their gold result, so phase 3 can score a prediction without
re-running the reference query.
"""

import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb

from t2sql.dataset.generate import TemplateError
from t2sql.db import QueryResult, QueryTimeoutError, UnsafeSQLError, connect, list_tables, run_query

MAX_GOLD_ROWS = 1000
TIMEOUT_S = 20.0


def jsonable(value: Any) -> Any:
    """Make one cell of a result JSON-serializable, keeping its exact value."""
    if isinstance(value, datetime | date):
        return value.isoformat(sep=" ")
    if isinstance(value, int | float | str | bool) or value is None:
        return value
    return str(value)  # Decimal, Interval and friends: keep the text, never guess a float


def judge(result: QueryResult) -> str | None:
    """Return why a result is unusable, or None when it is a real answer."""
    if result.truncated:
        return "too_many_rows"
    if not result.rows:
        return "empty"
    cells = [cell for row in result.rows for cell in row]
    if all(cell is None for cell in cells):
        return "all_null"
    numbers = [c for c in cells if isinstance(c, int | float) and not isinstance(c, bool)]
    if numbers and all(number == 0 for number in numbers):
        return "all_zero"
    return None


def evaluate_sql(
    sql: str, con: duckdb.DuckDBPyConnection, tables: set[str]
) -> tuple[str | None, dict | None]:
    """Run one query; return (reason to drop it, gold result)."""
    try:
        result = run_query(con, sql, tables, timeout_s=TIMEOUT_S, max_rows=MAX_GOLD_ROWS)
    except UnsafeSQLError as error:
        return f"unsafe: {error}", None
    except QueryTimeoutError:
        return "timeout", None
    except duckdb.Error as error:
        return f"sql_error: {type(error).__name__}", None
    reason = judge(result)
    if reason:
        return reason, None
    gold = {
        "columns": result.columns,
        "rows": [[jsonable(cell) for cell in row] for row in result.rows],
    }
    return None, gold


def holds(sql: str, con: duckdb.DuckDBPyConnection, tables: set[str]) -> bool:
    """Whether a template precondition is satisfied for one instance (see `require`)."""
    try:
        result = run_query(con, sql, tables, timeout_s=TIMEOUT_S, max_rows=1)
    except (UnsafeSQLError, QueryTimeoutError, duckdb.Error) as error:
        raise TemplateError(f"precondition failed to run: {error}\n{sql}") from error
    return bool(result.rows and result.rows[0][0])


def validate(raw_path: Path, out_path: Path, db_path: Path) -> tuple[int, Counter]:
    """Filter ``raw_path`` into ``out_path``; return (kept records, reasons for the rest)."""
    con = connect(db_path)
    tables = list_tables(con)
    cache: dict[str, tuple[str | None, dict | None]] = {}
    preconditions: dict[str, bool] = {}
    dropped: Counter = Counter()
    kept = 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with raw_path.open(encoding="utf-8") as source, out_path.open("w", encoding="utf-8") as out:
        for line in source:
            record = json.loads(line)
            precondition = record.get("require")
            if precondition:
                if precondition not in preconditions:
                    preconditions[precondition] = holds(precondition, con, tables)
                if not preconditions[precondition]:
                    dropped["precondition"] += 1
                    continue
            sql = record["sql"]
            if sql not in cache:  # variants and languages share one query: run it once
                cache[sql] = evaluate_sql(sql, con, tables)
            reason, gold = cache[sql]
            if reason:
                dropped[reason.split(":")[0]] += 1
                continue
            out.write(json.dumps(record | {"gold": gold}, ensure_ascii=False) + "\n")
            kept += 1
    return kept, dropped
