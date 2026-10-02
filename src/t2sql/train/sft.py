"""QLoRA supervised fine-tuning (CLAUDE.md §7, phase 4).

Run with ``python -m t2sql.train.sft --check`` to prepare the train split and verify it
without a GPU. Each example becomes token ids and labels: the labels repeat the ids of the
answer (the SQL and the end-of-turn token) and are -100 everywhere else, the value the loss
ignores. The prompt part is built by `format_prompt` of the evaluation, so the model is
trained on exactly the text it will read when evaluated.
"""

import argparse
from pathlib import Path
from typing import Any

import yaml

from t2sql.eval.run import ROOT, format_prompt, load_records, load_tokenizer
from t2sql.prompts import system_prompt

CONFIG_PATH = ROOT / "configs" / "train_qlora.yaml"
IGNORE = -100  # labels with this value add nothing to the loss (PyTorch convention)


def prepare_example(tokenizer: Any, record: dict[str, Any]) -> dict[str, list[int]]:
    """Token ids and labels of one chat example: loss on the answer and its end token only."""
    system, question, answer = (message["content"] for message in record["messages"])
    prompt = format_prompt(tokenizer, system, question)
    text = prompt + answer + tokenizer.eos_token  # eos_token is <|im_end|> for Qwen3
    prompt_ids = tokenizer(prompt)["input_ids"]
    input_ids = tokenizer(text)["input_ids"]
    if input_ids[: len(prompt_ids)] != prompt_ids:
        raise ValueError(f"{record['id']}: the prompt tokens change once the answer follows")
    labels = [IGNORE] * len(prompt_ids) + input_ids[len(prompt_ids) :]
    return {"input_ids": input_ids, "labels": labels}


def check_examples(
    tokenizer: Any, records: list[dict[str, Any]], system: str, max_length: int
) -> list[dict[str, list[int]]]:
    """Prepare every example and stop at the first one that would train the wrong thing."""
    examples = []
    for record in records:
        if record["messages"][0]["content"] != system:
            raise ValueError(f"{record['id']}: stale system prompt, run `make dataset`")
        example = prepare_example(tokenizer, record)
        if len(example["input_ids"]) > max_length:
            raise ValueError(f"{record['id']}: {len(example['input_ids'])} > {max_length}")
        trained = [token for token in example["labels"] if token != IGNORE]
        expected = record["messages"][2]["content"] + tokenizer.eos_token
        if tokenizer.decode(trained) != expected:
            raise ValueError(f"{record['id']}: the loss does not fall on the answer alone")
        examples.append(example)
    return examples


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--check", action="store_true", help="prepare and verify, no training")
    args = parser.parse_args()
    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    paths = {name: ROOT / value for name, value in config["paths"].items()}
    tokenizer = load_tokenizer(config["model"])
    records = load_records(paths["splits"] / "train_chat.jsonl")
    system = system_prompt(paths["domain"])
    examples = check_examples(tokenizer, records, system, config["max_length"])

    first = examples[0]
    trained = [token for token in first["labels"] if token != IGNORE]
    print(f"{records[0]['id']}: {len(first['input_ids'])} tokens, {len(trained)} with loss")
    print("--- tail of the prompt (no loss) ---")
    print(tokenizer.decode(first["input_ids"][-len(trained) - 25 : -len(trained)]))
    print("--- tokens with loss ---")
    print(tokenizer.decode(trained))
    lengths = sorted(len(e["input_ids"]) for e in examples)
    counts = sorted(sum(t != IGNORE for t in e["labels"]) for e in examples)
    print(f"\n{len(examples)} examples checked")
    print(f"length: min {lengths[0]}, max {lengths[-1]} (limit {config['max_length']})")
    print(f"with loss: min {counts[0]}, mean {sum(counts) / len(counts):.0f}, max {counts[-1]}")
    if not args.check:
        raise SystemExit("training is not implemented yet: run with --check")


if __name__ == "__main__":
    main()
