"""Safety rules of t2sql.db (CLAUDE.md §9): validation, read-only execution, timeout, row cap."""

from pathlib import Path

import duckdb
import pytest

from t2sql.db import (
    QueryTimeoutError,
    UnsafeSQLError,
    connect,
    list_tables,
    run_query,
    validate_select,
)

TABLES = {"eco2mix"}


# --- validate_select (no database needed) ---------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT region, sum(consommation_mw) FROM eco2mix GROUP BY 1",
        "WITH t AS (SELECT * FROM eco2mix) SELECT count(*) FROM t",
        "SELECT 1 UNION ALL SELECT 2",
        "SELECT * FROM eco2mix WHERE annee IN (SELECT max(annee) FROM eco2mix)",
        "SELECT * FROM eco2mix;",
    ],
)
def test_accepts_read_only_queries(sql):
    assert validate_select(sql, TABLES)


@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        ("DROP TABLE eco2mix", "only SELECT"),
        ("DELETE FROM eco2mix", "only SELECT"),
        ("INSERT INTO eco2mix SELECT * FROM eco2mix", "only SELECT"),
        ("CREATE TABLE x AS SELECT 1", "only SELECT"),
        ("COPY eco2mix TO 'out.csv'", "only SELECT"),
        ("SELECT 1; DROP TABLE eco2mix", "exactly one statement"),
        ("SELECT * FROM 'data/x.parquet'", "unknown table"),
        ("SELECT * FROM read_csv('/etc/passwd')", "unknown table"),
        ("SELECT read_text('/etc/passwd')", "forbidden function"),
        ("SELECT * FROM eco2mix WHERE x IN (SELECT y FROM secrets)", "unknown table"),
        ("SELECT FROM WHERE", "cannot parse"),
    ],
)
def test_rejects_unsafe_sql(sql, reason):
    with pytest.raises(UnsafeSQLError, match=reason):
        validate_select(sql, TABLES)


def test_returns_normalized_sql():
    assert validate_select("select sum(x) from eco2mix", TABLES) == "SELECT SUM(x) FROM eco2mix"


# --- execution on a tiny temporary database --------------------------------------------------


@pytest.fixture
def db(tmp_path: Path) -> duckdb.DuckDBPyConnection:
    path = tmp_path / "test.duckdb"
    with duckdb.connect(str(path)) as writer:
        writer.execute("CREATE TABLE t AS SELECT range AS x FROM range(10)")
    return connect(path)


def test_run_query_returns_columns_and_rows(db):
    result = run_query(db, "SELECT x, x * 2 AS y FROM t ORDER BY x", list_tables(db))
    assert result.columns == ["x", "y"]
    assert result.rows[:2] == [(0, 0), (1, 2)]
    assert not result.truncated
    assert result.elapsed_s >= 0


def test_run_query_caps_rows(db):
    result = run_query(db, "SELECT x FROM t", list_tables(db), max_rows=3)
    assert len(result.rows) == 3
    assert result.truncated


def test_run_query_times_out(db):
    endless = (
        "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) SELECT count(*) FROM r"
    )
    with pytest.raises(QueryTimeoutError):
        run_query(db, endless, list_tables(db), timeout_s=0.5)
    assert run_query(db, "SELECT 1", list_tables(db)).rows == [(1,)]  # connection still usable


def test_run_query_propagates_binder_errors(db):
    with pytest.raises(duckdb.Error):
        run_query(db, "SELECT nope FROM t", list_tables(db))


def test_connection_is_read_only_and_locked(db):
    with pytest.raises(duckdb.Error):
        db.execute("CREATE TABLE evil AS SELECT 1")
    with pytest.raises(duckdb.Error):
        db.execute("SET enable_external_access = true")
    with pytest.raises(duckdb.Error):
        db.execute("SELECT * FROM read_csv('pyproject.toml')")
