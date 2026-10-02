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

from t2sql.eval.run import ROOT, format_prompt, load_records, load_tokenizer, log
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


def load_model(config: dict[str, Any]) -> Any:
    """The base model with its weights quantized to 4 bits and frozen (the Q of QLoRA).

    Unsloth must be imported before transformers: it replaces some of its classes with faster
    versions when imported. Only Unsloth's model is used: the examples are tokenized by the
    evaluation tokenizer (`load_tokenizer`), so that training and evaluation read the same ids.
    """
    from unsloth import FastLanguageModel  # GPU only: installed by the Kaggle notebook

    model, _ = FastLanguageModel.from_pretrained(
        model_name=config["model"],
        max_seq_length=config["max_length"],
        load_in_4bit=True,
        dtype=None,  # float16 on a T4, which has no bfloat16
    )
    return model


def add_adapter(model: Any, config: dict[str, Any]) -> Any:
    """Add the trainable LoRA matrices to every target layer; the base weights stay frozen."""
    from unsloth import FastLanguageModel

    lora = config["lora"]
    return FastLanguageModel.get_peft_model(
        model,
        r=lora["r"],
        lora_alpha=lora["alpha"],
        lora_dropout=lora["dropout"],
        target_modules=lora["target_modules"],
        bias="none",
        use_gradient_checkpointing="unsloth",  # activations offloaded to CPU RAM: long inputs
        random_state=config["seed"],  # the random start of the A matrices
    )


def trainable_parameters(model: Any) -> int:
    """How many weights the training changes: the LoRA matrices only (17.4 M expected)."""
    return sum(p.numel() for p in model.parameters() if p.requires_grad)


def run_name(config: dict[str, Any], smoke: bool = False) -> str:
    """Name of a training run, for its folder and W&B: the settings the sensitivity plan varies."""
    lora, training = config["lora"], config["training"]
    name = f"{config['model'].split('/')[-1]}-r{lora['r']}-lr{training['learning_rate']:g}"
    return f"{name}-s{config['seed']}" + ("-smoke" if smoke else "")


def train(
    model: Any,
    tokenizer: Any,
    config: dict[str, Any],
    train_examples: list[dict[str, list[int]]],
    val_examples: list[dict[str, list[int]]],
    output_dir: Path,
    max_steps: int | None = None,
) -> Any:
    """Fine-tune with TRL's SFTTrainer on examples already tokenized and masked.

    ``skip_prepare_dataset`` stops TRL from tokenizing or truncating again, and its collator
    keeps our ``labels``. The validation loss is computed and a checkpoint saved at the end of
    each epoch. ``max_steps`` cuts the run short (the smoke test).
    """
    from datasets import Dataset
    from trl import SFTConfig, SFTTrainer

    settings = config["training"]
    args = SFTConfig(
        output_dir=str(output_dir),
        run_name=output_dir.name,
        num_train_epochs=settings["epochs"],
        max_steps=max_steps or -1,
        learning_rate=settings["learning_rate"],
        lr_scheduler_type=settings["lr_scheduler"],
        warmup_ratio=settings["warmup_ratio"],
        per_device_train_batch_size=settings["batch_size"],
        per_device_eval_batch_size=settings["batch_size"],
        gradient_accumulation_steps=settings["gradient_accumulation"],
        optim=settings["optimizer"],
        weight_decay=settings["weight_decay"],
        fp16=True,  # a T4 has no bfloat16
        bf16=False,
        logging_steps=settings["logging_steps"],
        eval_strategy="epoch",
        save_strategy="epoch",
        seed=config["seed"],
        report_to=config["tracking"]["report_to"],
        packing=False,  # packing would mix examples and lose the loss mask
        dataset_kwargs={"skip_prepare_dataset": True},
    )
    trainer = SFTTrainer(
        model=model,
        processing_class=tokenizer,
        args=args,
        train_dataset=Dataset.from_list(train_examples),
        eval_dataset=Dataset.from_list(val_examples),
    )
    batch = next(iter(trainer.get_train_dataloader()))
    trained = int((batch["labels"] != IGNORE).sum())
    log(f"first batch: {trained} of {batch['input_ids'].numel()} tokens carry the loss")
    trainer.train()
    return trainer


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--check", action="store_true", help="prepare and verify, no training")
    parser.add_argument("--max-steps", type=int, help="stop after N updates (smoke test)")
    args = parser.parse_args()
    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    paths = {name: ROOT / value for name, value in config["paths"].items()}
    # Unsloth must be imported before transformers, which `load_tokenizer` imports
    model = None if args.check else load_model(config)
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
    if args.check:
        return

    import torch

    val_records = load_records(paths["splits"] / "val_chat.jsonl")
    val_examples = check_examples(tokenizer, val_records, system, config["max_length"])
    model = add_adapter(model, config)
    log(f"trainable parameters: {trainable_parameters(model):,}")
    output_dir = paths["output"] / run_name(config, smoke=bool(args.max_steps))
    trainer = train(model, tokenizer, config, examples, val_examples, output_dir, args.max_steps)
    runtime = trainer.state.log_history[-1].get("train_runtime", 0.0)
    log(f"{trainer.state.global_step} updates in {runtime / 60:.1f} min")
    log(f"peak GPU memory: {torch.cuda.max_memory_allocated() / 2**30:.1f} GiB")


if __name__ == "__main__":
    main()
