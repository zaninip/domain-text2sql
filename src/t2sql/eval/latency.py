"""Latency of a GGUF model on the CPU, with the exact prompt of the evaluation.

Run with ``python -m t2sql.eval.latency data/gguf/model.gguf [more.gguf ...]``. For each model,
one test question per template is answered twice: "cold", after ``reset()``, so the whole
prompt is processed; then "warm", where llama-cpp-python reuses the longest prefix shared with
the previous request (the system prompt), so only the question is processed. The second is
what the demo would see once its cache is filled.
"""

import argparse
import json
import time
from pathlib import Path
from typing import Any

import psutil
import yaml

from t2sql.eval.run import CONFIG_PATH, ROOT, format_prompt, load_records, load_tokenizer, log
from t2sql.prompts import system_prompt


def latency_questions(records: list[dict[str, Any]], per_template: int) -> list[dict[str, Any]]:
    """The first ``per_template`` questions of each template, by id: fixed across runs."""
    chosen: list[dict[str, Any]] = []
    for template in sorted({record["template_id"] for record in records}):
        same = sorted((r for r in records if r["template_id"] == template), key=lambda r: r["id"])
        chosen += same[:per_template]
    return chosen


def timed_answer(llm: Any, prompt: str, max_tokens: int) -> dict[str, Any]:
    """Stream one greedy answer; the first chunk marks the end of the prompt processing."""
    start = time.perf_counter()
    first, chunks = None, []
    for chunk in llm(prompt, max_tokens=max_tokens, temperature=0.0, stream=True):
        first = first or time.perf_counter()
        chunks.append(chunk["choices"][0]["text"])
    end = time.perf_counter()
    output = "".join(chunks)
    # streaming may hold text back (stop sequences, split characters): count tokens, not chunks
    tokens = len(llm.tokenize(output.encode("utf-8"), add_bos=False))
    return {
        "first_token_s": round(first - start, 2),
        "total_s": round(end - start, 2),
        "output_tokens": tokens,
        "tokens_per_s": round((tokens - 1) / (end - first), 1) if tokens > 1 else None,
        "output": output,
    }


def measure(path: Path, prompts: list[str], config: dict[str, Any]) -> dict[str, Any]:
    """Cold then warm answers of one model to every prompt, and the memory of the process."""
    from llama_cpp import Llama  # optional `gguf` extra

    cpu, max_tokens = config["cpu"], config["generation"]["max_new_tokens"]
    process = psutil.Process()
    before = process.memory_info().rss
    llm = Llama(str(path), n_ctx=cpu["n_ctx"], n_threads=cpu["threads"], verbose=False)
    runs = {"cold": [], "warm": []}
    for prompt in prompts:
        llm.reset()  # forget the previous request: nothing to reuse
        runs["cold"].append(timed_answer(llm, prompt, max_tokens))
        log(f"{path.name} cold: {runs['cold'][-1]['first_token_s']} s to the first token")
    for prompt in prompts:  # each one follows another question: the system prompt is shared
        runs["warm"].append(timed_answer(llm, prompt, max_tokens))
        log(f"{path.name} warm: {runs['warm'][-1]['first_token_s']} s to the first token")
    # weights (memory-mapped pages actually read) + KV cache + buffers of this model
    memory_gb = round((process.memory_info().rss - before) / 1e9, 2)
    llm.close()
    return {"model": path.name, "threads": cpu["threads"], "memory_gb": memory_gb, **runs}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("gguf", type=Path, nargs="+")
    parser.add_argument("--out", type=Path, default=ROOT / "results" / "latency_cpu.json")
    args = parser.parse_args()
    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    paths = {name: ROOT / value for name, value in config["paths"].items()}
    records = latency_questions(
        load_records(paths["splits"] / "test.jsonl"), config["cpu"]["latency_questions"]
    )
    tokenizer, system = load_tokenizer(config["model"]), system_prompt(paths["domain"])
    prompts = [format_prompt(tokenizer, system, record["question"]) for record in records]
    log(f"{len(prompts)} questions, prompts of {len(tokenizer(prompts[0])['input_ids'])} tokens")
    results = {"ids": [record["id"] for record in records], "models": []}
    for path in args.gguf:  # one model at a time, closed before the next one is loaded
        results["models"].append(measure(path, prompts, config))
    args.out.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    log(f"results: {args.out}")


if __name__ == "__main__":
    main()
