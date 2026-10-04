"""Scoring and summary of an evaluation run (t2sql.eval.run), without any model."""

from pathlib import Path

import duckdb
import pytest

from t2sql.db import connect, list_tables
from t2sql.eval.run import (
    append_jsonl,
    few_shot_examples,
    generated_length,
    load_model,
    mismatches,
    read_jsonl,
    run_files,
    run_name,
    score_outputs,
    summarize,
    write_jsonl,
)


def test_run_name_drops_the_organisation():
    config = {"model": "Qwen/Qwen3-1.7B", "split": "val", "mode": "zero_shot"}
    assert run_name(config) == "Qwen3-1.7B_val_zero_shot"


def test_each_adapter_gets_its_own_run_name():
    config = {"model": "Qwen/Qwen3-1.7B", "split": "val", "mode": "fine_tuned", "tag": "smoke20"}
    assert run_name(config) == "Qwen3-1.7B_val_fine_tuned_smoke20"


def test_an_adapter_is_merged_into_the_base_weights(tmp_path):
    """A tiny random Qwen3 on the CPU: the merged model is a plain model, and the adapter
    changes its outputs (its B matrices are set to non-zero values, as after training)."""
    torch = pytest.importorskip("torch")
    peft = pytest.importorskip("peft")
    from transformers import Qwen3Config, Qwen3ForCausalLM

    config = Qwen3Config(
        vocab_size=64, hidden_size=16, intermediate_size=32, num_hidden_layers=1,
        num_attention_heads=2, num_key_value_heads=1, head_dim=8,
    )  # fmt: skip
    torch.manual_seed(0)
    Qwen3ForCausalLM(config).save_pretrained(tmp_path / "base")
    lora = peft.get_peft_model(
        Qwen3ForCausalLM.from_pretrained(tmp_path / "base"),
        peft.LoraConfig(r=2, target_modules=["q_proj", "v_proj"]),
    )
    for name, weight in lora.named_parameters():
        if "lora_B" in name:
            torch.nn.init.normal_(weight)
    lora.save_pretrained(tmp_path / "adapter")

    ids = torch.tensor([[1, 2, 3]])
    base = load_model(str(tmp_path / "base"), "float16", allow_cpu=True)
    merged = load_model(
        str(tmp_path / "base"), "float16", allow_cpu=True, adapter=str(tmp_path / "adapter")
    )
    assert type(merged) is type(base)  # merged: no PEFT wrapper left
    assert not torch.allclose(base(ids).logits, merged(ids).logits)


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


def test_a_few_shot_run_resumes_only_with_the_same_examples():
    few_shot = {"model": "m", "split": "val", "mode": "few_shot", "few_shot_ids": ["a", "b"]}
    assert mismatches(few_shot, few_shot) == []
    other = few_shot | {"few_shot_ids": ["a", "c"]}
    assert mismatches(few_shot, other) == ["few_shot_ids"]


def test_a_fine_tuned_run_resumes_only_with_the_same_adapter():
    fine_tuned = {"model": "m", "split": "val", "mode": "fine_tuned", "adapter": "ckpt-20"}
    assert mismatches(fine_tuned, fine_tuned | {"adapter": "ckpt-40"}) == ["adapter"]


def test_a_fine_tuned_run_gets_no_few_shot_examples(tmp_path):
    # no train split in tmp_path: reading it would fail
    assert few_shot_examples({"mode": "fine_tuned"}, tmp_path) == []


def test_no_gpu_means_no_run_unless_allowed(monkeypatch):
    torch = pytest.importorskip("torch")  # the optional `model` extra, absent in CI
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    with pytest.raises(SystemExit, match="no GPU"):
        load_model("any/model", "float16")


def test_frozen_few_shot_ids_are_used_in_their_order(tmp_path):
    train = [{"id": i, "question": "q", "sql": "SELECT 1"} for i in ("a", "b", "c")]
    write_jsonl(train, tmp_path / "train.jsonl")
    config = {"mode": "few_shot", "few_shot": {"seed": 0, "ids": ["c", "a"]}}
    assert [e["id"] for e in few_shot_examples(config, tmp_path)] == ["c", "a"]
    with pytest.raises(SystemExit, match="not in the train split"):
        few_shot_examples(config | {"few_shot": {"seed": 0, "ids": ["z"]}}, tmp_path)
