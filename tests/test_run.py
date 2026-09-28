"""Scoring and summary of an evaluation run (t2sql.eval.run), without any model."""

from pathlib import Path

import duckdb
import pytest

from t2sql.db import connect, list_tables
from t2sql.eval.run import generated_length, run_name, score_outputs, summarize


def test_run_name_drops_the_organisation():
    config = {"model": "Qwen/Qwen3-1.7B", "split": "val", "mode": "zero_shot"}
    assert run_name(config) == "Qwen3-1.7B_val_zero_shot"


def test_generated_length_stops_at_the_end_token():
    assert generated_length([5, 6, 7, 2, 0, 0], stop_ids={2, 0}) == 3
    assert generated_length([5, 6, 7], stop_ids={2}) == 3  # cut by max_new_tokens


@pytest.fixture
def db(tmp_path: Path):
    path = tmp_path / "test.duckdb"
    with duckdb.connect(str(path)) as writer:
        writer.execute("CREATE TABLE t AS SELECT range AS x FROM range(5)")
    con = connect(path)
    return con, list_tables(con)


def record(i: int) -> dict:
    return {
        "id": f"t#{i}",
        "template_id": "t",
        "family": "aggregation",
        "lang": "fr",
        "gold": {"columns": ["s"], "rows": [[10]]},
        "order_matters": False,
    }


def test_score_outputs_and_summary(db):
    con, tables = db
    outputs = [
        {"output": "SELECT sum(x) FROM t", "output_tokens": 6, "latency_s": 1.0},
        {"output": "SELECT sum(x) + 1 FROM t", "output_tokens": 8, "latency_s": 2.0},
        {"output": "SELECT sum(y) FROM t", "output_tokens": 6, "latency_s": 3.0},
        {"output": "Je ne sais pas.", "output_tokens": 4, "latency_s": 2.0},
    ]
    rows = score_outputs([record(i) for i in range(4)], outputs, con, tables)
    assert [row["outcome"] for row in rows] == [
        "correct",
        "wrong_result",
        "unknown_column",
        "no_sql",
    ]
    assert rows[0]["output"] == "SELECT sum(x) FROM t"  # the raw output is kept
    summary = summarize(rows)
    assert summary["execution_accuracy"] == 0.25
    assert summary["valid_sql_rate"] == 0.5
    assert summary["mean_latency_s"] == 2.0
    assert summary["mean_output_tokens"] == 6.0
