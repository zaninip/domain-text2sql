"""Execution accuracy rules of t2sql.eval.metrics (CLAUDE.md §10)."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from t2sql.db import connect, list_tables
from t2sql.eval.metrics import cells_equal, same_result, score


@pytest.mark.parametrize(
    ("gold", "predicted", "expected"),
    [
        (1000.0, 1000.05, True),  # within 1e-4 relative
        (1000.0, 1001.0, False),
        (12, 12.0, True),  # a count as int or float
        (0.0, 1e-12, True),  # float noise around zero
        (0.0, 0.001, False),
        ("Bretagne", "Bretagne", True),
        ("Bretagne", "bretagne", False),
        (None, None, True),
        (None, 0, False),
    ],
)
def test_cells_equal(gold, predicted, expected):
    assert cells_equal(gold, predicted) is expected


def test_duckdb_types_are_converted_like_the_gold():
    gold = [["2023-01-10", 1234.5]]
    assert same_result(gold, [(date(2023, 1, 10), Decimal("1234.5"))], order_matters=False)


def test_column_names_and_order_are_ignored():
    gold = [["Bretagne", 10.0], ["Normandie", 20.0]]
    assert same_result(gold, [(20.0, "Normandie"), (10.0, "Bretagne")], order_matters=False)


def test_a_missing_or_extra_column_is_a_different_answer():
    gold = [[3, 1500.0]]
    assert not same_result(gold, [(3,)], order_matters=False)
    assert not same_result(gold, [(3, 1500.0, "Bretagne")], order_matters=False)


def test_rows_are_a_multiset_unless_order_matters():
    gold = [["Bretagne", 10.0], ["Normandie", 20.0]]
    swapped = [("Normandie", 20.0), ("Bretagne", 10.0)]
    assert same_result(gold, swapped, order_matters=False)
    assert not same_result(gold, swapped, order_matters=True)


def test_duplicates_count_in_a_multiset():
    gold = [[1], [1], [2]]
    assert not same_result(gold, [(1,), (2,), (2,)], order_matters=False)
    assert not same_result(gold, [(1,), (2,)], order_matters=False)


def test_empty_results():
    assert same_result([], [], order_matters=False)
    assert not same_result([[1]], [], order_matters=False)


@pytest.fixture
def db(tmp_path: Path):
    path = tmp_path / "test.duckdb"
    with duckdb.connect(str(path)) as writer:
        writer.execute("CREATE TABLE t AS SELECT range AS x FROM range(5)")
    con = connect(path)
    return con, list_tables(con)


RECORD = {"gold": {"columns": ["s"], "rows": [[10]]}, "order_matters": False}


@pytest.mark.parametrize(
    ("output", "outcome"),
    [
        ("```sql\nSELECT sum(x) FROM t;\n```", "correct"),
        ("SELECT sum(x) + 1 FROM t", "wrong_result"),
        ("Je ne sais pas.", "no_sql"),
        ("SELECT sum(x FROM t", "syntax_error"),
        ("SELECT sum(y) FROM t", "unknown_column"),
        ("SELECT sum(x) FROM u", "unknown_table"),
        ("SELECT somme(x) FROM t", "unknown_function"),
        ("DROP TABLE t", "unsafe"),
    ],
)
def test_score_names_the_outcome(db, output, outcome):
    con, tables = db
    result = score(output, RECORD, con, tables)
    assert result.outcome == outcome
    assert result.correct is (outcome == "correct")
    assert result.valid is (outcome in ("correct", "wrong_result"))
