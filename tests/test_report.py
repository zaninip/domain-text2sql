"""The evaluation report of t2sql.eval.report, on two small fake runs."""

import json
from pathlib import Path

import pytest

from t2sql.eval.report import breakdown_table, load_runs, outcomes_table
from t2sql.eval.run import run_files, run_name, write_jsonl


def row(family: str, lang: str, outcome: str) -> dict[str, object]:
    return {
        "id": f"{family}-{lang}",
        "family": family,
        "lang": lang,
        "template_id": family,
        "outcome": outcome,
        "latency_s": 1.0,
        "output_tokens": 10,
    }


def save_run(directory: Path, mode: str, rows: list[dict[str, object]]) -> None:
    settings = {"model": "org/Model-1.5B", "split": "val", "mode": mode}
    files = run_files(directory, run_name(settings))
    write_jsonl(rows, files["predictions"])
    files["summary"].write_text(json.dumps(settings), encoding="utf-8")


@pytest.fixture
def runs(tmp_path: Path) -> list[dict[str, object]]:
    # saved few-shot first: the report must still put zero-shot first
    save_run(tmp_path, "few_shot", [row("rate", "fr", "correct"), row("rank", "it", "correct")])
    save_run(
        tmp_path, "zero_shot", [row("rate", "fr", "correct"), row("rank", "it", "syntax_error")]
    )
    return load_runs(tmp_path, "val")


def test_runs_are_ordered_by_model_then_zero_shot_first(runs):
    assert [run["mode"] for run in runs] == ["zero_shot", "few_shot"]
    assert all(len(run["rows"]) == 2 for run in runs)


def test_breakdown_counts_correct_answers_per_value(runs):
    lines = breakdown_table(runs, "family").splitlines()
    assert lines[0] == "| family | n | Model-1.5B zero_shot | Model-1.5B few_shot |"
    assert lines[2] == "| rank | 1 | 0 (0.0 %) | 1 (100.0 %) |"
    assert lines[3] == "| rate | 1 | 1 (100.0 %) | 1 (100.0 %) |"


def test_outcomes_list_correct_and_wrong_first_then_failures(runs):
    lines = outcomes_table(runs).splitlines()
    assert [line.split(" | ")[0] for line in lines[2:]] == [
        "| correct",
        "| wrong_result",
        "| syntax_error",
    ]
    assert lines[2] == "| correct | 1 | 2 |"


def test_no_run_of_another_split_is_read(tmp_path):
    save_run(tmp_path, "zero_shot", [row("rate", "fr", "correct")])
    assert load_runs(tmp_path, "test") == []
