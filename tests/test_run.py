"""Scoring and summary of an evaluation run (t2sql.eval.run), without any model."""

from pathlib import Path

import duckdb
import pytest

from t2sql.db import connect, list_tables
from t2sql.eval.run import (
    append_jsonl,
    generated_length,
    load_model,
    mismatches,
    read_jsonl,
    run_files,
    run_name,
    score_outputs,
    summarize,
)


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
    assert summary["generation_minutes"] == 0.1  # 8 s of summed latencies


@pytest.mark.parametrize("model", ["Qwen3-1.7B", "Qwen2.5-Coder-1.5B-Instruct"])
def test_run_files_keep_dotted_model_names_whole(tmp_path: Path, model):
    files = run_files(tmp_path, f"{model}_val_zero_shot")
    assert files["outputs"].name == f"{model}_val_zero_shot.outputs.jsonl"
    assert files["summary"].name == f"{model}_val_zero_shot.summary.json"
    assert len({path.name for path in files.values()}) == 4


def test_outputs_are_appended_and_read_back(tmp_path: Path):
    path = tmp_path / "run.outputs.jsonl"
    assert read_jsonl(path) == []  # nothing generated yet
    append_jsonl([{"id": "a"}], path)
    append_jsonl([{"id": "b"}, {"id": "c"}], path)  # a second batch, or a resumed run
    assert [row["id"] for row in read_jsonl(path)] == ["a", "b", "c"]


def test_a_resumed_run_must_generate_like_the_first():
    first = {
        "model": "m",
        "split": "val",
        "mode": "zero_shot",
        "generation": {"max_new_tokens": 256},
    }
    first |= {"dtype_used": "float16", "device": "Tesla T4"}
    assert mismatches(first, first | {"device": "Tesla P100"}) == []  # another GPU is fine
    assert mismatches(first, first | {"dtype_used": "float32"}) == ["dtype_used"]
    changed = first | {"generation": {"max_new_tokens": 128}}
    assert mismatches(first, changed) == ["generation"]


def test_no_gpu_means_no_run_unless_allowed(monkeypatch):
    torch = pytest.importorskip("torch")  # the optional `model` extra, absent in CI
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    with pytest.raises(SystemExit, match="no GPU"):
        load_model("any/model", "float16")
