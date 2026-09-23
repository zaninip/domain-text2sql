"""Filtering rules of t2sql.dataset.validate."""

import json
from datetime import datetime
from pathlib import Path

import duckdb
import pytest

from t2sql.dataset.generate import TemplateError
from t2sql.dataset.validate import jsonable, judge, validate
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
    ],
)
def test_judge(outcome, reason):
    assert judge(outcome) == reason


def test_jsonable_keeps_timestamps_readable():
    assert jsonable(datetime(2021, 3, 25, 15, 30)) == "2021-03-25 15:30:00"
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
            {"id": "ok", "sql": "SELECT sum(x) AS s FROM t"},
            {"id": "empty", "sql": "SELECT x FROM t WHERE x > 99"},
            {"id": "zero", "sql": "SELECT sum(zero) AS s FROM t"},
            {"id": "broken", "sql": "SELECT nope FROM t"},
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
            {"id": "a", "sql": "SELECT sum(x) FROM t", "require": "SELECT count(*) > 99 FROM t"},
            {"id": "b", "sql": "SELECT sum(x) FROM t", "require": "SELECT count(*) > 1 FROM t"},
        ],
    )
    kept, dropped = validate(raw, tmp_path / "clean.jsonl", db)
    assert (kept, dropped) == (1, {"precondition": 1})


def test_a_broken_precondition_is_a_template_error(tmp_path: Path, db: Path):
    raw = write_records(
        tmp_path / "raw.jsonl",
        [{"id": "a", "sql": "SELECT 1", "require": "SELECT nope FROM t"}],
    )
    with pytest.raises(TemplateError, match="precondition"):
        validate(raw, tmp_path / "clean.jsonl", db)
