"""Training examples of t2sql.train.sft, with a character-level fake tokenizer (no model)."""

import pytest

from t2sql.train.sft import (
    IGNORE,
    check_examples,
    hub_repo,
    last_checkpoint,
    prepare_example,
    run_name,
    with_overrides,
)


class FakeTokenizer:
    """One token per character; ``merge`` (if set) becomes a single token, like a BPE merge."""

    eos_token = "<E>"

    def __init__(self, merge: str | None = None):
        self.merge = merge

    def apply_chat_template(self, messages, tokenize, add_generation_prompt, enable_thinking):
        text = "".join(f"<{m['role']}>{m['content']}" for m in messages)
        return text + ("<assistant>" if add_generation_prompt else "")

    def __call__(self, text: str) -> dict[str, list[int]]:
        ids, i = [], 0
        while i < len(text):
            if self.merge and text.startswith(self.merge, i):
                ids.append(0)
                i += len(self.merge)
            else:
                ids.append(ord(text[i]))
                i += 1
        return {"input_ids": ids}

    def decode(self, ids: list[int]) -> str:
        return "".join(self.merge if i == 0 else chr(i) for i in ids)


RECORD = {
    "id": "t#0",
    "messages": [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "q ?"},
        {"role": "assistant", "content": "SELECT 1"},
    ],
}
PROMPT = "<system>SYS<user>q ?<assistant>"


def test_the_loss_falls_on_the_answer_and_its_end_token_only():
    example = prepare_example(FakeTokenizer(), RECORD)
    assert example["labels"][: len(PROMPT)] == [IGNORE] * len(PROMPT)
    trained = [token for token in example["labels"] if token != IGNORE]
    assert FakeTokenizer().decode(trained) == "SELECT 1<E>"
    assert len(example["labels"]) == len(example["input_ids"])


def test_a_prompt_whose_tokens_change_before_the_answer_is_refused():
    with pytest.raises(ValueError, match="prompt tokens change"):
        prepare_example(FakeTokenizer(merge=">S"), RECORD)  # ">" of the prompt + "S" of SELECT


def test_examples_longer_than_the_limit_are_refused():
    full_length = len(PROMPT) + len("SELECT 1<E>")
    assert len(check_examples(FakeTokenizer(), [RECORD], "SYS", full_length)) == 1
    with pytest.raises(ValueError, match=f"{full_length} > {full_length - 1}"):
        check_examples(FakeTokenizer(), [RECORD], "SYS", full_length - 1)


def test_a_dataset_built_with_another_system_prompt_is_refused():
    with pytest.raises(ValueError, match="stale system prompt"):
        check_examples(FakeTokenizer(), [RECORD], "NEW SYS", 100)


def test_each_run_has_its_own_private_hub_repo():
    config = {"hub": {"owner": "zaninip", "prefix": "eco2mix-sql"}}
    assert hub_repo(config, "Qwen3-1.7B-r16-lr0.0002-s0") == (
        "zaninip/eco2mix-sql-Qwen3-1.7B-r16-lr0.0002-s0"
    )


def test_resume_needs_a_complete_checkpoint_on_the_hub(tmp_path, monkeypatch):
    hub = pytest.importorskip("huggingface_hub")
    downloaded = tmp_path / "last-checkpoint"
    downloaded.mkdir(parents=True)
    monkeypatch.setattr(hub, "snapshot_download", lambda *args, **kwargs: tmp_path)
    assert last_checkpoint("owner/run", tmp_path) is None  # folder without trainer state
    (downloaded / "trainer_state.json").write_text("{}")
    assert last_checkpoint("owner/run", tmp_path) == str(downloaded)


def test_the_data_version_keeps_runs_on_different_train_splits_apart():
    config = {"model": "Qwen/Qwen3-1.7B", "seed": 0, "lora": {"r": 16}}
    config["training"] = {"learning_rate": 2e-4}
    assert run_name(config) == "Qwen3-1.7B-r16-lr0.0002-s0"  # the first centre run
    assert run_name(config | {"data": "d2"}) == "Qwen3-1.7B-d2-r16-lr0.0002-s0"


def test_sensitivity_overrides_change_the_run_name_and_leave_the_config_alone():
    config = {"model": "Qwen/Qwen3-1.7B", "data": "d2", "seed": 0, "lora": {"r": 16}}
    config["training"] = {"learning_rate": 2e-4, "save_steps": 33}
    assert run_name(with_overrides(config, learning_rate=1e-4)) == "Qwen3-1.7B-d2-r16-lr0.0001-s0"
    assert run_name(with_overrides(config, seed=1)) == "Qwen3-1.7B-d2-r16-lr0.0002-s1"
    assert with_overrides(config, save_steps=2)["training"]["save_steps"] == 2
    assert config["training"] == {"learning_rate": 2e-4, "save_steps": 33}  # not mutated
    assert with_overrides(config) == config
