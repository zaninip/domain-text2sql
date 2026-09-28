"""Generate SQL with one model on one split, then score every prediction.

Run with ``python -m t2sql.eval.run [--config configs/eval.yaml] [--show-prompt] [--limit N]``.
The model, split and mode come from the config, so the model pilot and the three baseline
configurations all go through the same code, the same prompt and the same decoding.
"""

import argparse
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

import duckdb
import yaml

from t2sql.db import connect, list_tables
from t2sql.eval.metrics import score
from t2sql.prompts import chat_messages, system_prompt

ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = ROOT / "configs" / "eval.yaml"


def load_records(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    """The examples of one split, in file order (the first ``limit`` only, if set)."""
    with path.open(encoding="utf-8") as lines:
        records = [json.loads(line) for line in lines]
    return records[:limit] if limit else records


def format_prompt(tokenizer: Any, system: str, question: str) -> str:
    """The exact text the model reads, up to the point where it starts its answer.

    The model's own chat template turns the shared messages into text. ``enable_thinking``
    is a Qwen3 switch: False makes the template open and close an empty thinking block, so the
    model answers directly. Templates that do not know the flag ignore it.
    """
    return tokenizer.apply_chat_template(
        chat_messages(system, question),
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


def load_model(name: str, dtype: str) -> Any:
    """The model, on the GPU when there is one.

    ``dtype`` (float16 on a T4) applies on the GPU only: on a CPU half precision is slow or
    unsupported, so local smoke tests run in float32 and may differ slightly from Kaggle.
    """
    import torch
    from transformers import AutoModelForCausalLM

    device = "cuda" if torch.cuda.is_available() else "cpu"
    torch_dtype = getattr(torch, dtype) if device == "cuda" else torch.float32
    return AutoModelForCausalLM.from_pretrained(name, dtype=torch_dtype).to(device).eval()


GENERATION_FACTS = ("device", "dtype_used", "torch", "transformers", "generation_minutes")


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
    tokenizer: Any, model: Any, prompts: list[str], max_new_tokens: int, batch_size: int
) -> list[dict[str, Any]]:
    """Greedy decoding, several prompts at a time; one output per prompt, in order.

    Prompts of a batch are padded on the left, so that all of them end where the answer
    starts. The reported latency is the batch time divided by its size: an average.
    """
    import torch

    eos = model.generation_config.eos_token_id
    stop_ids = {tokenizer.pad_token_id, *(eos if isinstance(eos, list) else [eos])}
    outputs = []
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
        for row in new_tokens.tolist():
            outputs.append(
                {
                    "output": tokenizer.decode(row, skip_special_tokens=True),
                    "output_tokens": generated_length(row, stop_ids),
                    "latency_s": round(seconds, 3),
                }
            )
    return outputs


def run_name(config: dict[str, Any]) -> str:
    """File name of one run: model (without its organisation), split and mode."""
    return f"{config['model'].split('/')[-1]}_{config['split']}_{config['mode']}"


def score_outputs(
    records: list[dict[str, Any]],
    outputs: list[dict[str, Any]],
    con: duckdb.DuckDBPyConnection,
    tables: set[str],
) -> list[dict[str, Any]]:
    """One saved row per example: what the model wrote, the SQL run, and how it went.

    The raw output is kept next to the extracted SQL, so that a later change to the
    extraction can be re-scored (`--rescore`) without running the model again.
    """
    rows = []
    for record, output in zip(records, outputs, strict=True):
        result = score(output["output"], record, con, tables)
        rows.append(
            {
                "id": record["id"],
                "template_id": record["template_id"],
                "family": record["family"],
                "lang": record["lang"],
                **output,
                "sql": result.sql,
                "outcome": result.outcome,
                "error": result.error,
            }
        )
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Headline numbers of a run (CLAUDE.md §10); breakdowns come from the report."""
    n = len(rows)
    outcomes = Counter(row["outcome"] for row in rows)
    return {
        "examples": n,
        "execution_accuracy": outcomes["correct"] / n,
        "valid_sql_rate": (outcomes["correct"] + outcomes["wrong_result"]) / n,
        "mean_latency_s": sum(row["latency_s"] for row in rows) / n,
        "mean_output_tokens": sum(row["output_tokens"] for row in rows) / n,
        "outcomes": dict(outcomes.most_common()),
    }


def write_jsonl(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    path.write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--show-prompt", action="store_true", help="print one prompt and stop")
    parser.add_argument("--limit", type=int, help="first N examples only (overrides the config)")
    parser.add_argument(
        "--rescore", action="store_true", help="score the saved outputs again, no generation"
    )
    for key in ("model", "split", "mode"):
        parser.add_argument(f"--{key}", help=f"overrides `{key}` of the config")
    args = parser.parse_args()
    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    if args.limit:
        config["limit"] = args.limit
    config |= {key: getattr(args, key) for key in ("model", "split", "mode") if getattr(args, key)}
    if config["mode"] != "zero_shot":
        raise SystemExit(f"mode {config['mode']!r} is not implemented yet")
    paths = {name: ROOT / value for name, value in config["paths"].items()}
    records = load_records(paths["splits"] / f"{config['split']}.jsonl", config["limit"])
    out_path = paths["predictions"] / f"{run_name(config)}.jsonl"
    summary_path = out_path.with_suffix(".summary.json")
    settings = config["generation"]
    run_info: dict[str, Any] = {"model": config["model"], "split": config["split"]}
    run_info |= {"mode": config["mode"], "generation": settings}

    if args.rescore:
        with out_path.open(encoding="utf-8") as lines:
            saved = {row["id"]: row for row in map(json.loads, lines)}
        records = [record for record in records if record["id"] in saved]
        keys = ("output", "output_tokens", "latency_s")
        outputs = [{key: saved[record["id"]][key] for key in keys} for record in records]
        if summary_path.exists():  # where and how the outputs were generated, kept as it was
            previous = json.loads(summary_path.read_text(encoding="utf-8"))
            run_info |= {key: previous[key] for key in GENERATION_FACTS if key in previous}
    else:
        tokenizer = load_tokenizer(config["model"])
        system = system_prompt(paths["domain"])
        prompts = [format_prompt(tokenizer, system, record["question"]) for record in records]
        if args.show_prompt:
            tokens = len(tokenizer(prompts[0])["input_ids"])
            print(prompts[0][:300], "\n[...]\n", prompts[0][-400:], sep="")
            print(f"\n{tokens} tokens")
            return
        model = load_model(config["model"], settings["dtype"])
        run_info |= environment(model)
        began = time.perf_counter()
        outputs = generate(
            tokenizer, model, prompts, settings["max_new_tokens"], settings["batch_size"]
        )
        run_info["generation_minutes"] = round((time.perf_counter() - began) / 60, 1)

    con = connect(paths["database"])
    rows = score_outputs(records, outputs, con, list_tables(con))
    write_jsonl(rows, out_path)
    summary = run_info | summarize(rows)
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"predictions: {out_path}")


if __name__ == "__main__":
    main()
