"""Filtering rules of t2sql.dataset.validate."""

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from t2sql.dataset.generate import TemplateError
from t2sql.dataset.validate import (
    canonical_order,
    jsonable,
    judge,
    normalize_question,
    validate,
)
from t2sql.db import QueryResult


def result(rows, columns=("a", "b"), truncated=False):
    return QueryResult(columns=list(columns), rows=rows, truncated=truncated, elapsed_s=0.0)


@pytest.mark.parametrize(
    ("outcome", "reason"),
    [
        (result([]), "empty"),
        (result([(None, None)]), "all_null"),
        (result([(0, 0), (0, None)]), "all_zero"),
        (result([(1, 2)], truncated=True), "too_many_rows"),
        (result([(0, 5)]), None),
        (result([("2021-01-01", 0)]), "all_zero"),  # labels do not rescue a zero answer
        (result([("2021-01-01", 4539)]), None),
        (result([(Decimal("0.0"),)]), "all_zero"),  # DuckDB's type for SUM(int) * 0.5
        (result([(Decimal("5188.5"),)]), None),
    ],
)
def test_judge(outcome, reason):
    assert judge(outcome) == reason


def test_jsonable_keeps_timestamps_readable():
    assert jsonable(datetime(2021, 3, 25, 15, 30)) == "2021-03-25 15:30:00"


def test_jsonable_keeps_plain_dates_readable():
    assert jsonable(date(2023, 1, 25)) == "2023-01-25"


def test_jsonable_turns_decimals_into_numbers():
    assert jsonable(Decimal("784066.0")) == 784066.0
    assert isinstance(jsonable(Decimal("0.5")), float)
    assert jsonable(None) is None
    assert jsonable(2300) == 2300


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "test.duckdb"
    with duckdb.connect(str(path)) as writer:
        writer.execute("CREATE TABLE t AS SELECT range AS x, 0 AS zero FROM range(5)")
    return path


def write_records(path: Path, records: list[dict]) -> Path:
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8"
    )
    return path


def test_validate_keeps_answers_and_drops_the_rest(tmp_path: Path, db: Path):
    raw = write_records(
        tmp_path / "raw.jsonl",
        [
            {"id": "ok", "question": "Q1 ?", "sql": "SELECT sum(x) AS s FROM t"},
            {"id": "empty", "question": "Q2 ?", "sql": "SELECT x FROM t WHERE x > 99"},
            {"id": "zero", "question": "Q3 ?", "sql": "SELECT sum(zero) AS s FROM t"},
            {"id": "broken", "question": "Q4 ?", "sql": "SELECT nope FROM t"},
        ],
    )
    kept, dropped = validate(raw, tmp_path / "clean.jsonl", db)
    assert kept == 1
    assert dropped == {"empty": 1, "all_zero": 1, "sql_error": 1}
    record = json.loads((tmp_path / "clean.jsonl").read_text(encoding="utf-8"))
    assert record["gold"] == {"columns": ["s"], "rows": [[10]]}


def test_precondition_drops_the_instance_without_running_the_query(tmp_path: Path, db: Path):
    raw = write_records(
        tmp_path / "raw.jsonl",
        [
            {
                "id": "a",
                "question": "Q1 ?",
                "sql": "SELECT sum(x) FROM t",
                "require": "SELECT count(*) > 99 FROM t",
            },
            {
                "id": "b",
                "question": "Q2 ?",
                "sql": "SELECT sum(x) FROM t",
                "require": "SELECT count(*) > 1 FROM t",
            },
        ],
    )
    kept, dropped = validate(raw, tmp_path / "clean.jsonl", db)
    assert (kept, dropped) == (1, {"precondition": 1})


def test_a_broken_precondition_is_a_template_error(tmp_path: Path, db: Path):
    raw = write_records(
        tmp_path / "raw.jsonl",
        [{"id": "a", "question": "Q ?", "sql": "SELECT 1", "require": "SELECT nope FROM t"}],
    )
    with pytest.raises(TemplateError, match="precondition"):
        validate(raw, tmp_path / "clean.jsonl", db)


def test_identical_questions_with_the_same_sql_are_deduplicated(tmp_path: Path, db: Path):
    raw = write_records(
        tmp_path / "raw.jsonl",
        [
            {"id": "a", "question": "Combien ?", "sql": "SELECT sum(x) FROM t"},
            {"id": "b", "question": "  combien ?  ", "sql": "SELECT sum(x) FROM t"},
            {"id": "c", "question": "Autre chose ?", "sql": "SELECT sum(x) FROM t"},
        ],
    )
    kept, dropped = validate(raw, tmp_path / "clean.jsonl", db)
    assert (kept, dropped) == (2, {"duplicate": 1})


def test_identical_questions_with_different_sql_stop_the_build(tmp_path: Path, db: Path):
    raw = write_records(
        tmp_path / "raw.jsonl",
        [
            {"id": "a", "question": "Combien ?", "sql": "SELECT sum(x) FROM t"},
            {"id": "b", "question": "Combien ?", "sql": "SELECT avg(x) FROM t"},
        ],
    )
    with pytest.raises(TemplateError, match="same question, two answers"):
        validate(raw, tmp_path / "clean.jsonl", db)


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("Combien ?", "combien"),
        ("Combien  d'énergie ?", "Combien d'énergie?"),
        ("Combien ?", "Combien !"),
    ],
)
def test_normalize_question_ignores_case_spacing_and_final_mark(first, second):
    assert normalize_question(first) == normalize_question(second)


def test_canonical_order_handles_mixed_cells():
    rows = [["b", 2.0, None], ["a", 10, "x"], [None, 1, "y"], ["a", 2, "x"]]
    expected = [[None, 1, "y"], ["a", 2, "x"], ["a", 10, "x"], ["b", 2.0, None]]
    assert canonical_order(rows) == expected


def test_gold_rows_are_sorted_only_when_order_does_not_matter(tmp_path: Path, db: Path):
    sql = "SELECT x FROM t ORDER BY x DESC"
    raw = write_records(
        tmp_path / "raw.jsonl",
        [
            {"id": "free", "question": "Q1 ?", "sql": sql, "order_matters": False},
            {"id": "ranked", "question": "Q2 ?", "sql": sql + " ", "order_matters": True},
        ],
    )
    validate(raw, tmp_path / "clean.jsonl", db)
    lines = (tmp_path / "clean.jsonl").read_text(encoding="utf-8").splitlines()
    free, ranked = (json.loads(line)["gold"]["rows"] for line in lines)
    assert free == [[0], [1], [2], [3], [4]]
    assert ranked == [[4], [3], [2], [1], [0]]
