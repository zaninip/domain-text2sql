"""Export the fine-tuned model to GGUF: merge the adapter, convert, quantize.

Run with ``python -m t2sql.export.gguf [--config configs/export.yaml]``. Everything runs on the
CPU. The adapter may sit in a private Hub repo: ``HF_TOKEN`` must be set (``uv run --env-file
.env ...``).
"""

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

from t2sql.eval.run import ROOT, log, resolve_adapter

CONFIG_PATH = ROOT / "configs" / "export.yaml"


def merge(base: str, adapter_spec: str, dtype: str, out_dir: Path) -> dict[str, Any]:
    """Base weights + LoRA adapter merged into one plain model, saved with its tokenizer.

    The base is loaded at ``dtype`` even on a CPU (unlike `t2sql.eval.run.load_model`, which
    runs float32 there): the merge then happens at the precision of the evaluated model. The
    tokenizer goes along because it carries the chat template, which the GGUF file embeds.
    """
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    repo, revision = resolve_adapter(adapter_spec)
    log(f"adapter {repo} at revision {revision}")
    model = AutoModelForCausalLM.from_pretrained(base, dtype=getattr(torch, dtype))
    model = PeftModel.from_pretrained(model, repo, revision=revision).merge_and_unload()
    model.save_pretrained(out_dir)
    AutoTokenizer.from_pretrained(base).save_pretrained(out_dir)
    meta = {"base": base, "adapter": repo, "adapter_revision": revision, "dtype": dtype}
    (out_dir / "export_meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    log(f"merged model: {out_dir}")
    return meta


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    args = parser.parse_args()
    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    paths = {name: ROOT / value for name, value in config["paths"].items()}
    merge(config["base"], config["adapter"], config["dtype"], paths["merged"])


if __name__ == "__main__":
    main()
