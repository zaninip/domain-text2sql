"""Safe execution of untrusted (model-generated) SQL against a read-only DuckDB file.

Two layers: ``validate_select`` rejects anything that is not a single read-only query at the
parser level (sqlglot); the executor then relies on DuckDB's own read-only / no-external-access
settings as a second barrier.
"""

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import sqlglot
from sqlglot import exp

DIALECT = "duckdb"

# Functions that read from the filesystem or the network. DuckDB also blocks them once
# `enable_external_access` is off; rejecting them here gives a clear error before execution.
_FORBIDDEN_FUNCTIONS = frozenset(
    {
        "read_csv", "read_csv_auto", "read_parquet", "parquet_scan", "read_json",
        "read_json_auto", "read_json_objects", "read_ndjson", "read_text", "read_blob",
        "glob", "sniff_csv", "parquet_metadata", "parquet_schema",
    }
)  # fmt: skip


class UnsafeSQLError(ValueError):
    """Raised when SQL is not a single read-only SELECT over the allowed tables."""


def _function_name(func: exp.Func) -> str:
    # For a known function `name` is its first argument; the function name is `sql_name()`.
    return (func.name if isinstance(func, exp.Anonymous) else func.sql_name()).lower()


def validate_select(sql: str, allowed_tables: set[str]) -> str:
    """Return ``sql`` normalized by sqlglot, or raise ``UnsafeSQLError``.

    Accepts exactly one statement, which must be a query (``SELECT``, ``WITH ... SELECT``,
    set operations), reading only from ``allowed_tables`` or from its own CTEs, and calling
    none of the file-reading functions.
    """
    try:
        statements = sqlglot.parse(sql, read=DIALECT)
    except sqlglot.errors.ParseError as e:
        raise UnsafeSQLError(f"cannot parse SQL: {e}") from e
    if len(statements) != 1:
        raise UnsafeSQLError(f"expected exactly one statement, got {len(statements)}")
    stmt = statements[0]
    if not isinstance(stmt, exp.Query):
        raise UnsafeSQLError(f"only SELECT queries are allowed, got {type(stmt).__name__}")

    ctes = {c.alias.lower() for c in stmt.find_all(exp.CTE)}
    visible = {t.lower() for t in allowed_tables} | ctes
    for table in stmt.find_all(exp.Table):
        if table.name.lower() not in visible:
            raise UnsafeSQLError(f"unknown table: {table.name!r}")
    for func in stmt.find_all(exp.Func):
        if _function_name(func) in _FORBIDDEN_FUNCTIONS:
            raise UnsafeSQLError(f"forbidden function: {_function_name(func)}")
    return stmt.sql(dialect=DIALECT)


class QueryTimeoutError(TimeoutError):
    """Raised when a query exceeds the time budget and has been interrupted."""


@dataclass(frozen=True)
class QueryResult:
    """Rows of a successful query, truncated to the requested maximum."""

    columns: list[str]
    rows: list[tuple[Any, ...]]
    truncated: bool
    elapsed_s: float


def connect(db_path: Path, threads: int | None = None) -> duckdb.DuckDBPyConnection:
    """Open the DuckDB file read-only with external access disabled and the config locked.

    ``threads=1`` makes aggregations deterministic (row order and the last digits of float
    sums), which the dataset build needs to be reproducible; it has to be set here, before
    the configuration is locked. Leave it to None to use every core.
    """
    con = duckdb.connect(str(db_path), read_only=True)
    if threads is not None:
        con.execute(f"SET threads = {int(threads)}")
    con.execute("SET enable_external_access = false")
    con.execute("SET lock_configuration = true")  # no later SET can undo the lines above
    return con


def list_tables(con: duckdb.DuckDBPyConnection) -> set[str]:
    """Names of the tables and views the model is allowed to query."""
    return {row[0] for row in con.execute("SHOW TABLES").fetchall()}


def run_query(
    con: duckdb.DuckDBPyConnection,
    sql: str,
    allowed_tables: set[str],
    timeout_s: float = 10.0,
    max_rows: int = 1000,
) -> QueryResult:
    """Validate then execute ``sql`` with a time budget and a row cap.

    Raises ``UnsafeSQLError`` (rejected before execution), ``QueryTimeoutError`` (interrupted) or
    ``duckdb.Error`` (valid shape but fails at bind/run time, e.g. unknown column).
    """
    safe_sql = validate_select(sql, allowed_tables)
    cur = con.cursor()  # own connection object: safe to run and interrupt independently
    outcome: list[QueryResult | BaseException] = []

    def work() -> None:
        start = time.perf_counter()
        try:
            cur.execute(safe_sql)
            rows = cur.fetchmany(max_rows + 1)  # one extra row tells us if we truncated
            columns = [d[0] for d in cur.description or []]
            elapsed = time.perf_counter() - start
            outcome.append(QueryResult(columns, rows[:max_rows], len(rows) > max_rows, elapsed))
        except BaseException as e:  # re-raised in the calling thread
            outcome.append(e)

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    worker.join(timeout_s)
    if worker.is_alive():
        cur.interrupt()
        worker.join()
        raise QueryTimeoutError(f"query interrupted after {timeout_s} s")
    result = outcome[0]
    if isinstance(result, BaseException):
        raise result
    return result
