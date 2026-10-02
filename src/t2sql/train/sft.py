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
    # Unsloth may swap the name for its own pre-quantized copy of the same weights
    log(f"base weights loaded from {model.config._name_or_path}")
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


def hub_repo(config: dict[str, Any], name: str) -> str:
    """The private Hub repo of one run: one per run, so that runs never overwrite each other."""
    return f"{config['hub']['owner']}/{config['hub']['prefix']}-{name}"


def last_checkpoint(repo_id: str, download_dir: Path) -> str | None:
    """Download the ``last-checkpoint`` folder of a run from the Hub, if there is one.

    It holds everything a resumed run needs: adapter, optimizer state, schedule position and
    random generators, so that training continues from the exact update it stopped at.
    ``download_dir`` must lie outside the run's output folder, which is pushed to the Hub.
    """
    from huggingface_hub import snapshot_download
    from huggingface_hub.errors import RepositoryNotFoundError

    try:
        folder = snapshot_download(
            repo_id, allow_patterns="last-checkpoint/*", local_dir=download_dir
        )
    except RepositoryNotFoundError:
        return None
    checkpoint = Path(folder) / "last-checkpoint"
    return str(checkpoint) if (checkpoint / "trainer_state.json").exists() else None


def train(
    model: Any,
    tokenizer: Any,
    config: dict[str, Any],
    train_examples: list[dict[str, list[int]]],
    val_examples: list[dict[str, list[int]]],
    output_dir: Path,
    max_steps: int | None = None,
    evaluate: bool = True,
    resume_from: str | None = None,
) -> Any:
    """Fine-tune with TRL's SFTTrainer on examples already tokenized and masked.

    ``skip_prepare_dataset`` stops TRL from tokenizing or truncating again, and its collator
    keeps our ``labels``. Every ``save_steps`` updates a checkpoint is saved, pushed to the
    run's private Hub repo, and (unless ``evaluate`` is False) the validation loss computed.
    ``max_steps`` cuts the run short (smoke tests); ``resume_from`` continues a stopped run.
    """
    from datasets import Dataset
    from trl import SFTConfig, SFTTrainer

    settings = config["training"]
    every = settings["save_steps"]
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
        disable_tqdm=True,  # one printed line per log instead of a bar a remote log cannot show
        eval_strategy="steps" if evaluate else "no",
        eval_steps=every,
        save_strategy="steps",
        save_steps=every,
        push_to_hub=True,
        hub_model_id=hub_repo(config, output_dir.name),
        hub_private_repo=True,
        hub_strategy="checkpoint",  # each save pushed, the latest also as `last-checkpoint`
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
    log(f"checkpoints every {every} updates, pushed to {args.hub_model_id} (private)")
    if resume_from:
        log(f"resuming from {resume_from}")
    trainer.train(resume_from_checkpoint=resume_from)
    trainer.push_to_hub(commit_message="end of training")  # waits for the pending pushes too
    return trainer


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--check", action="store_true", help="prepare and verify, no training")
    parser.add_argument("--max-steps", type=int, help="stop after N updates (smoke test)")
    parser.add_argument("--save-steps", type=int, help="overrides `training.save_steps`")
    parser.add_argument("--no-eval", action="store_true", help="skip the validation loss")
    parser.add_argument("--resume", action="store_true", help="continue from the Hub checkpoint")
    args = parser.parse_args()
    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    if args.save_steps:
        config["training"]["save_steps"] = args.save_steps
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
    resume_from = None
    if args.resume:
        download_dir = output_dir.parent / f"{output_dir.name}-from-hub"
        resume_from = last_checkpoint(hub_repo(config, output_dir.name), download_dir)
        if resume_from is None:
            raise SystemExit(f"--resume: no checkpoint of {output_dir.name} on the Hub")
    trainer = train(
        model, tokenizer, config, examples, val_examples, output_dir,
        args.max_steps, not args.no_eval, resume_from,
    )  # fmt: skip
    runtime = trainer.state.log_history[-1].get("train_runtime", 0.0)
    log(f"{trainer.state.global_step} updates in {runtime / 60:.1f} min")
    log(f"peak GPU memory: {torch.cuda.max_memory_allocated() / 2**30:.1f} GiB")


if __name__ == "__main__":
    main()
