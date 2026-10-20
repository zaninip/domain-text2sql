"""Export the fine-tuned model to GGUF: merge the adapter, convert, quantize.

Run with ``python -m t2sql.export.gguf [--config configs/export.yaml]``. Everything runs on the
CPU. The adapter may sit in a private Hub repo: ``HF_TOKEN`` must be set (``uv run --env-file
.env ...``).
"""

import argparse
import hashlib
import json
import os
import subprocess
import zipfile
from pathlib import Path
from typing import Any

import httpx
import yaml

from t2sql.eval.run import ROOT, log, resolve_adapter

CONFIG_PATH = ROOT / "configs" / "export.yaml"
LLAMA_CPP_REPO = "https://github.com/ggml-org/llama.cpp"
# the tokenizer of the base repo, chat template included, plus its licence for publication
TOKENIZER_FILES = ("tokenizer.json", "tokenizer_config.json", "vocab.json", "merges.txt", "LICENSE")


def save_hf(base: str, adapter_spec: str | None, dtype: str, out_dir: Path) -> dict[str, Any]:
    """The base weights, with a LoRA adapter merged in if given, saved as one plain model.

    Both models of the demo go through here, so that they reach the conversion in the same form:
    loaded at ``dtype`` even on a CPU (unlike `t2sql.eval.run.load_model`, which runs float32
    there), so the merge happens at the precision of the evaluated model; saved by
    ``save_pretrained``, which leaves out the copy of the tied output head that the base repo
    ships (converted as is, that copy would be quantized apart from the embeddings). The
    tokenizer goes along because it carries the chat template, which the GGUF file embeds: its
    original files are copied, not saved again, since the transformers of the conversion script
    cannot read what a newer transformers writes.
    """
    import shutil

    import torch
    from huggingface_hub import hf_hub_download
    from transformers import AutoModelForCausalLM

    model = AutoModelForCausalLM.from_pretrained(base, dtype=getattr(torch, dtype))
    meta: dict[str, Any] = {"base": base, "dtype": dtype, "adapter": None}
    if adapter_spec:
        from peft import PeftModel

        repo, revision = resolve_adapter(adapter_spec)
        log(f"adapter {repo} at revision {revision}")
        model = PeftModel.from_pretrained(model, repo, revision=revision).merge_and_unload()
        meta |= {"adapter": repo, "adapter_revision": revision}
    model.save_pretrained(out_dir)
    for name in TOKENIZER_FILES:
        shutil.copy(hf_hub_download(base, name), out_dir / name)
    (out_dir / "export_meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    log(f"saved model: {out_dir}")
    return meta


def fetch_llama_cpp(tag: str, binaries: str, sha256: str, out_dir: Path) -> Path:
    """The llama.cpp source (conversion script) and prebuilt tools at one build; kept if present.

    The tools archive is checked against its published SHA-256 before it is unpacked.
    Returns the folder of the tools (``llama-quantize`` and friends).
    """
    source, tools = out_dir / "src", out_dir / "bin"
    if not source.exists():
        log(f"cloning llama.cpp {tag}")
        clone = ["git", "clone", "-q", "--depth", "1", "--branch", tag, LLAMA_CPP_REPO]
        subprocess.run([*clone, str(source)], check=True)
    if not tools.exists():
        url = f"{LLAMA_CPP_REPO}/releases/download/{tag}/{binaries}"
        log(f"downloading {url}")
        archive = httpx.get(url, follow_redirects=True, timeout=120).raise_for_status().content
        if (digest := hashlib.sha256(archive).hexdigest()) != sha256:
            raise SystemExit(f"{binaries}: SHA-256 {digest}, expected {sha256}")
        zip_path = out_dir / binaries
        zip_path.write_bytes(archive)
        with zipfile.ZipFile(zip_path) as zipped:
            zipped.extractall(tools)
    return tools


def convert(hf_dir: Path, out_file: Path, llama_cpp_dir: Path) -> None:
    """A Hugging Face model folder to one float16 GGUF file, with llama.cpp's own script.

    The script pins its dependencies (torch, transformers) to versions other than this
    project's, so it runs in a throwaway environment built from its requirements file.
    """
    source = llama_cpp_dir / "src"
    requirements = source / "requirements" / "requirements-convert_hf_to_gguf.txt"
    environment = ["uv", "run", "--isolated", "--no-project", "--with-requirements"]
    environment += [str(requirements), "--with", str(source / "gguf-py")]
    script = [str(source / "convert_hf_to_gguf.py"), str(hf_dir), "--outtype", "f16"]
    out_file.parent.mkdir(parents=True, exist_ok=True)
    log(f"converting {hf_dir} -> {out_file}")
    subprocess.run([*environment, "python", *script, "--outfile", str(out_file)], check=True)


def quantize(f16_file: Path, out_file: Path, kind: str, tools_dir: Path) -> None:
    """One quantized GGUF (``kind``: Q4_K_M, Q8_0...) from the float16 one, by llama-quantize."""
    tool = tools_dir / ("llama-quantize.exe" if os.name == "nt" else "llama-quantize")
    log(f"quantizing {f16_file.name} -> {out_file.name}")
    subprocess.run([str(tool), str(f16_file), str(out_file), kind], check=True)


def export(name: str, hf_dir: Path, paths: dict[str, Path], kinds: list[str], tools: Path) -> None:
    """The float16 GGUF of one model, then its quantizations; files already made are kept."""
    f16 = paths["gguf"] / f"{name}-f16.gguf"
    if not f16.exists():
        convert(hf_dir, f16, paths["llama_cpp"])
    for kind in kinds:
        if not (out_file := paths["gguf"] / f"{name}-{kind}.gguf").exists():
            quantize(f16, out_file, kind, tools)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    args = parser.parse_args()
    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    paths = {name: ROOT / value for name, value in config["paths"].items()}
    llama = config["llama_cpp"]
    tools = fetch_llama_cpp(llama["tag"], llama["binaries"], llama["sha256"], paths["llama_cpp"])
    for name, adapter in (("fine-tuned", config["adapter"]), ("base", None)):
        hf_dir = paths["hf"] / name
        if not (hf_dir / "export_meta.json").exists():  # written last by `save_hf`
            save_hf(config["base"], adapter, config["dtype"], hf_dir)
        export(name, hf_dir, paths, config["quantizations"], tools)


if __name__ == "__main__":
    main()
