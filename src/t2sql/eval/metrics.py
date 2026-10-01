"""Execution accuracy: does the predicted query return the same answer as the gold query?

The rules follow CLAUDE.md §10. Rows are compared as multisets, unless the template says their
order matters. Column names and column order are ignored: "SELECT energie, region" answers
the same question as "SELECT region, energie". The number of columns is not: a missing or an
extra column is a different answer. Numbers match within a relative tolerance, so a unit
conversion written as `/ 1e6` or `* 1e-6` does not fail on the last float digit.
"""

import itertools
import math
from dataclasses import dataclass
from numbers import Number
from typing import Any

import duckdb

from t2sql.dataset.validate import MAX_GOLD_ROWS, TIMEOUT_S, canonical_order, jsonable
from t2sql.db import QueryTimeoutError, UnsafeSQLError, run_query
from t2sql.eval.extract import extract_sql

REL_TOL = 1e-4
ABS_TOL = 1e-9  # near zero a relative tolerance accepts nothing; this accepts float noise only
MAX_PERMUTED_COLUMNS = 6  # 720 column orders at most; wider results must keep the gold order


def is_number(value: Any) -> bool:
    return isinstance(value, Number) and not isinstance(value, bool)


def cells_equal(gold: Any, predicted: Any) -> bool:
    """Two cells of a result, already made JSON-like by `jsonable`."""
    if is_number(gold) and is_number(predicted):
        return math.isclose(gold, predicted, rel_tol=REL_TOL, abs_tol=ABS_TOL)
    return gold == predicted


def rows_equal(gold_rows: list[list[Any]], predicted_rows: list[list[Any]]) -> bool:
    """Same rows in the same order, cell by cell."""
    return len(gold_rows) == len(predicted_rows) and all(
        len(g) == len(p) and all(cells_equal(a, b) for a, b in zip(g, p, strict=True))
        for g, p in zip(gold_rows, predicted_rows, strict=True)
    )


def same_result(
    gold_rows: list[list[Any]], predicted_rows: list[tuple[Any, ...]], order_matters: bool
) -> bool:
    """Whether a predicted result answers like the gold one (execution accuracy of one example).

    ``gold_rows`` come from the dataset (already JSON-like); ``predicted_rows`` straight from
    DuckDB, converted here with the same `jsonable` so that Decimal and dates compare alike.
    """
    predicted = [[jsonable(cell) for cell in row] for row in predicted_rows]
    if len(predicted) != len(gold_rows):
        return False
    width = len(gold_rows[0]) if gold_rows else 0
    if any(len(row) != width for row in predicted):
        return False
    if width > MAX_PERMUTED_COLUMNS:
        orders = [tuple(range(width))]
    else:
        orders = itertools.permutations(range(width))
    gold = gold_rows if order_matters else canonical_order(gold_rows)
    for order in orders:
        candidate = [[row[i] for i in order] for row in predicted]
        if not order_matters:
            candidate = canonical_order(candidate)
        if rows_equal(gold, candidate):
            return True
    return False


@dataclass(frozen=True)
class Score:
    """How one prediction fared: the SQL actually run, and what happened to it."""

    sql: str
    outcome: str  # "correct", "wrong_result", or the kind of failure (see `failure_kind`)
    error: str | None = None

    @property
    def correct(self) -> bool:
        return self.outcome == "correct"

    @property
    def valid(self) -> bool:
        """The query ran, right or wrong: the "valid SQL rate" of CLAUDE.md §10."""
        return self.outcome in ("correct", "wrong_result")


def failure_kind(error: Exception) -> str:
    """Name the automatic part of the error taxonomy from the exception a query raised."""
    message = str(error)
    if isinstance(error, QueryTimeoutError):
        return "timeout"
    if isinstance(error, UnsafeSQLError):  # messages written by t2sql.db.validate_select
        if message.startswith("cannot parse"):
            return "syntax_error"
        if message.startswith("unknown table"):
            return "unknown_table"
        return "unsafe"
    if isinstance(error, duckdb.ParserException):
        return "syntax_error"
    if isinstance(error, duckdb.CatalogException):  # one class for tables and functions
        return "unknown_function" if "Function" in message else "unknown_table"
    if isinstance(error, duckdb.BinderException):
        if "GROUP BY clause" in message:  # "column x must appear in the GROUP BY clause ..."
            return "group_by_error"
        return "unknown_column" if "column" in message.lower() else "runtime_error"
    return "runtime_error"


def score(
    output: str, record: dict[str, Any], con: duckdb.DuckDBPyConnection, tables: set[str]
) -> Score:
    """Execution accuracy of one example: extract the SQL, run it safely, compare with gold."""
    sql = extract_sql(output)
    if not sql:
        return Score(sql, "no_sql")
    try:
        result = run_query(con, sql, tables, timeout_s=TIMEOUT_S, max_rows=MAX_GOLD_ROWS)
        gold = record["gold"]["rows"]
        matches = not result.truncated and same_result(gold, result.rows, record["order_matters"])
    except (UnsafeSQLError, QueryTimeoutError, duckdb.Error) as error:
        return Score(sql, failure_kind(error), first_line(error))
    except Exception as error:
        # One untrusted output must never stop a whole run. Unexpected failures get their own
        # outcome, so that they show in every summary instead of passing as wrong answers.
        return Score(sql, "scoring_error", f"{type(error).__name__}: {first_line(error)}")
    return Score(sql, "correct" if matches else "wrong_result")


def first_line(error: Exception) -> str:
    lines = str(error).splitlines()
    return lines[0] if lines else type(error).__name__
