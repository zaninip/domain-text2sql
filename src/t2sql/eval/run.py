"""Generate SQL with one model on one split, then score every prediction.

Run with ``python -m t2sql.eval.run [--config configs/eval.yaml] [--limit N] [--show-prompt]``.
The model, split and mode come from the config, so the model pilot and the three baseline
configurations all go through the same code, the same prompt and the same decoding.

A run is safe to interrupt: raw outputs are appended batch by batch to
``<run>.outputs.jsonl`` and a new launch only generates what is missing. Scoring then reads
that file, so ``--rescore`` re-scores without any model.
"""

import argparse
import json
import time
from collections import Counter
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import duckdb
import yaml

from t2sql.db import connect, list_tables
from t2sql.eval.few_shot import pick_examples
from t2sql.eval.metrics import score
from t2sql.prompts import chat_messages, system_prompt

ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = ROOT / "configs" / "eval.yaml"
# What makes outputs comparable: a resumed run must match on all of these.
GENERATION_KEYS = ("model", "split", "mode", "few_shot_ids", "adapter", "generation", "dtype_used")
# The three configurations of CLAUDE.md §2.3: fine_tuned is the base model plus a LoRA adapter,
# prompted like zero_shot (no examples)
MODES = ("zero_shot", "few_shot", "fine_tuned")
SCORE_LOG_EVERY = 50


def log(message: str) -> None:
    """One timestamped line, flushed at once so that remote logs show progress live."""
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def load_records(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    """The examples of one split, in file order (the first ``limit`` only, if set)."""
    with path.open(encoding="utf-8") as lines:
        records = [json.loads(line) for line in lines]
    return records[:limit] if limit else records


def few_shot_examples(config: dict[str, Any], splits_dir: Path) -> list[dict[str, Any]]:
    """The fixed examples of a few-shot run, drawn from the train split; none otherwise."""
    if config["mode"] != "few_shot":
        return []
    return pick_examples(load_records(splits_dir / "train.jsonl"), config["few_shot"]["seed"])


def format_prompt(
    tokenizer: Any, system: str, question: str, examples: list[dict[str, Any]] = ()
) -> str:
    """The exact text the model reads, up to the point where it starts its answer.

    The model's own chat template turns the shared messages into text. ``enable_thinking``
    is a Qwen3 switch: False makes the template open and close an empty thinking block, so the
    model answers directly. Templates that do not know the flag ignore it.
    """
    pairs = [(example["question"], example["sql"]) for example in examples]
    return tokenizer.apply_chat_template(
        chat_messages(system, question, examples=pairs),
        tokenize=False,
        add_generation_prompt=True,
        enable_thinking=False,
    )


def load_tokenizer(name: str) -> Any:
    """The model's tokenizer, padding on the left (see `generate`)."""
    from transformers import AutoTokenizer  # heavy import, only when a model is needed

    tokenizer = AutoTokenizer.from_pretrained(name, padding_side="left")
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    return tokenizer


def load_model(name: str, dtype: str, allow_cpu: bool = False, adapter: str | None = None) -> Any:
    """The model on the GPU; on a CPU only when explicitly allowed.

    A full evaluation on a CPU takes days, so a missing GPU stops the run at once instead of
    letting it crawl until a time limit kills it. ``dtype`` (float16 on a T4) applies on the
    GPU only: half precision is slow or unsupported on a CPU, which runs float32.

    ``adapter`` (a checkpoint folder or a Hub repo) is a LoRA adapter put on the same base
    weights, at the same precision as the baselines, then merged into them: the merged model
    has the base model's shape and speed.
    """
    import torch
    from transformers import AutoModelForCausalLM

    if not torch.cuda.is_available() and not allow_cpu:
        raise SystemExit("no GPU visible to torch: refusing to run (use --allow-cpu locally)")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    torch_dtype = getattr(torch, dtype) if device == "cuda" else torch.float32
    model = AutoModelForCausalLM.from_pretrained(name, dtype=torch_dtype).to(device)
    if adapter:
        from peft import PeftModel

        model = PeftModel.from_pretrained(model, adapter).merge_and_unload()
    return model.eval()


def environment(model: Any) -> dict[str, str]:
    """What the outputs were really generated with, which may differ from the config: the
    configured dtype applies on a GPU only (see `load_model`)."""
    import torch
    import transformers

    on_gpu = model.device.type == "cuda"
    return {
        "device": torch.cuda.get_device_name(model.device) if on_gpu else "cpu",
        "dtype_used": str(model.dtype).removeprefix("torch."),
        "torch": torch.__version__,
        "transformers": transformers.__version__,
    }


def generated_length(tokens: list[int], stop_ids: set[int]) -> int:
    """How many tokens the model wrote before its end token (or the padding after it)."""
    for position, token in enumerate(tokens):
        if token in stop_ids:
            return position
    return len(tokens)


def generate(
    tokenizer: Any,
    model: Any,
    prompts: list[str],
    ids: list[str],
    max_new_tokens: int,
    batch_size: int,
) -> Iterator[list[dict[str, Any]]]:
    """Greedy decoding, one batch at a time; yields the outputs of each batch as it ends.

    Prompts of a batch are padded on the left, so that all of them end where the answer
    starts. The reported latency is the batch time divided by its size: an average.
    """
    import torch

    eos = model.generation_config.eos_token_id
    stop_ids = {tokenizer.pad_token_id, *(eos if isinstance(eos, list) else [eos])}
    for start in range(0, len(prompts), batch_size):
        batch = prompts[start : start + batch_size]
        encoded = tokenizer(batch, return_tensors="pt", padding=True).to(model.device)
        began = time.perf_counter()
        with torch.inference_mode():
            generated = model.generate(
                **encoded,
                do_sample=False,  # greedy: always the most probable next token
                temperature=None,  # the model's own sampling defaults, unused when greedy
                top_p=None,
                top_k=None,
                max_new_tokens=max_new_tokens,
                pad_token_id=tokenizer.pad_token_id,
            )
        seconds = (time.perf_counter() - began) / len(batch)
        new_tokens = generated[:, encoded["input_ids"].shape[1] :]  # drop the prompt
        yield [
            {
                "id": example_id,
                "output": tokenizer.decode(row, skip_special_tokens=True),
                "output_tokens": generated_length(row, stop_ids),
                "latency_s": round(seconds, 3),
            }
            for example_id, row in zip(
                ids[start : start + batch_size], new_tokens.tolist(), strict=True
            )
        ]


def run_name(config: dict[str, Any]) -> str:
    """File name of one run: model (without its organisation), split, mode and adapter tag.

    The tag tells adapters apart (smoke test, checkpoints, sensitivity runs), so that their
    outputs never overwrite each other.
    """
    name = f"{config['model'].split('/')[-1]}_{config['split']}_{config['mode']}"
    return f"{name}_{config['tag']}" if config.get("tag") else name


def run_files(directory: Path, name: str) -> dict[str, Path]:
    """The files of one run. Built by concatenation: model names contain dots ("Qwen3-1.7B"),
    which `Path.with_suffix` would take for an extension and cut."""
    return {
        kind: directory / f"{name}{suffix}"
        for kind, suffix in [
            ("outputs", ".outputs.jsonl"),
            ("meta", ".outputs.meta.json"),
            ("predictions", ".jsonl"),
            ("summary", ".summary.json"),
        ]
    }


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as lines:
        return [json.loads(line) for line in lines if line.strip()]


def append_jsonl(rows: list[dict[str, Any]], path: Path) -> None:
    """Add rows at the end of a file, flushed, so that a killed run keeps them."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as out:
        out.writelines(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)


def write_jsonl(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    path.write_text(text, encoding="utf-8")


def mismatches(saved: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """Keys on which saved outputs were generated differently from the current run."""
    return [key for key in GENERATION_KEYS if saved.get(key) != current.get(key)]


def score_outputs(
    records: list[dict[str, Any]],
    outputs: list[dict[str, Any]],
    con: duckdb.DuckDBPyConnection,
    tables: set[str],
    progress: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    """One saved row per example: what the model wrote, the SQL run, and how it went.

    The raw output is kept next to the extracted SQL, so that a later change to the
    extraction can be re-scored (`--rescore`) without running the model again.
    """
    rows = []
    for count, (record, output) in enumerate(zip(records, outputs, strict=True), start=1):
        result = score(output["output"], record, con, tables)
        rows.append(
            {
                "id": record["id"],
                "template_id": record["template_id"],
                "family": record["family"],
                "lang": record["lang"],
                **{key: value for key, value in output.items() if key != "id"},
                "sql": result.sql,
                "outcome": result.outcome,
                "error": result.error,
            }
        )
        if progress and (count % SCORE_LOG_EVERY == 0 or count == len(records)):
            progress(f"scored {count}/{len(records)}")
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Headline numbers of a run (CLAUDE.md §10); breakdowns come from the report."""
    n = len(rows)
    outcomes = Counter(row["outcome"] for row in rows)
    return {
        "examples": n,
        "execution_accuracy": outcomes["correct"] / n,
        "valid_sql_rate": (outcomes["correct"] + outcomes["wrong_result"]) / n,
        "generation_minutes": round(sum(row["latency_s"] for row in rows) / 60, 1),
        "mean_latency_s": sum(row["latency_s"] for row in rows) / n,
        "mean_output_tokens": sum(row["output_tokens"] for row in rows) / n,
        "outcomes": dict(outcomes.most_common()),
    }


def generate_missing(
    config: dict[str, Any],
    records: list[dict[str, Any]],
    examples: list[dict[str, Any]],
    paths: dict[str, Path],
    outputs_path: Path,
    meta_path: Path,
    allow_cpu: bool,
) -> None:
    """Generate the examples not yet in ``outputs_path``, appending each batch as it ends.

    ``examples`` are the few-shot turns put before every question (empty in zero-shot).
    """
    done = {row["id"] for row in read_jsonl(outputs_path)}
    pending = [record for record in records if record["id"] not in done]
    log(f"{config['model']} on {config['split']}: {len(done)} done, {len(pending)} to generate")
    if not pending:
        return
    settings = config["generation"]
    tokenizer = load_tokenizer(config["model"])
    model = load_model(config["model"], settings["dtype"], allow_cpu, config.get("adapter"))
    meta = {key: config[key] for key in ("model", "split", "mode")}
    # Keys added only when used, so that the metadata of earlier runs keeps its shape
    if examples:
        meta["few_shot_ids"] = [example["id"] for example in examples]
    if config.get("adapter"):
        meta |= {"adapter": config["adapter"], "tag": config["tag"]}
    meta |= {"generation": settings, **environment(model)}
    log(f"model loaded on {meta['device']} ({meta['dtype_used']})")
    if done:
        saved = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        if different := mismatches(saved, meta):
            raise SystemExit(f"{outputs_path} was generated with other {different}: delete it")
    else:
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    system = system_prompt(paths["domain"])
    prompts = [format_prompt(tokenizer, system, record["question"], examples) for record in pending]
    ids = [record["id"] for record in pending]
    batches = -(-len(prompts) // settings["batch_size"])  # ceiling division
    began, generated = time.perf_counter(), 0
    batch_outputs = generate(
        tokenizer, model, prompts, ids, settings["max_new_tokens"], settings["batch_size"]
    )
    for number, outputs in enumerate(batch_outputs, start=1):
        append_jsonl(outputs, outputs_path)
        generated += len(outputs)
        elapsed = time.perf_counter() - began
        left = elapsed / generated * (len(prompts) - generated) / 60
        log(
            f"batch {number}/{batches}: {generated}/{len(prompts)} examples, "
            f"{elapsed / 60:.1f} min elapsed, ~{left:.1f} min left"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--show-prompt", action="store_true", help="print one prompt and stop")
    parser.add_argument("--limit", type=int, help="first N examples only (overrides the config)")
    parser.add_argument("--rescore", action="store_true", help="score saved outputs, no model")
    parser.add_argument("--allow-cpu", action="store_true", help="run without a GPU (smoke tests)")
    for key in ("model", "split", "mode"):
        parser.add_argument(f"--{key}", help=f"overrides `{key}` of the config")
    parser.add_argument("--out", help="overrides `paths.predictions` (smoke tests write apart)")
    parser.add_argument("--adapter", help="LoRA checkpoint folder or Hub repo (fine_tuned mode)")
    parser.add_argument("--tag", help="short name of the adapter, part of the file names")
    args = parser.parse_args()
    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    if args.limit:
        config["limit"] = args.limit
    keys = ("model", "split", "mode", "adapter", "tag")
    config |= {key: getattr(args, key) for key in keys if getattr(args, key)}
    if args.out:
        config["paths"]["predictions"] = args.out
    if config["mode"] not in MODES:
        raise SystemExit(f"mode {config['mode']!r} is not one of {MODES}")
    if (config["mode"] == "fine_tuned") != bool(config.get("adapter") and config.get("tag")):
        raise SystemExit("--adapter and --tag go together, with --mode fine_tuned only")
    paths = {name: ROOT / value for name, value in config["paths"].items()}
    records = load_records(paths["splits"] / f"{config['split']}.jsonl", config["limit"])
    examples = few_shot_examples(config, paths["splits"])
    files = run_files(paths["predictions"], run_name(config))
    outputs_path, meta_path = files["outputs"], files["meta"]

    if args.show_prompt:
        tokenizer = load_tokenizer(config["model"])
        system = system_prompt(paths["domain"])
        prompt = format_prompt(tokenizer, system, records[0]["question"], examples)
        print(prompt.replace(system, f"[system prompt, {len(system)} characters]"))
        print(f"{len(tokenizer(prompt)['input_ids'])} tokens")
        return
    if not args.rescore:
        generate_missing(config, records, examples, paths, outputs_path, meta_path, args.allow_cpu)

    outputs = {row["id"]: row for row in read_jsonl(outputs_path)}
    records = [record for record in records if record["id"] in outputs]
    if not records:
        raise SystemExit(f"no outputs to score in {outputs_path}")
    con = connect(paths["database"])
    rows = score_outputs(
        records, [outputs[r["id"]] for r in records], con, list_tables(con), progress=log
    )
    write_jsonl(rows, files["predictions"])
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    summary = meta | summarize(rows)
    files["summary"].write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)
    log(f"predictions: {files['predictions']}")


if __name__ == "__main__":
    main()
