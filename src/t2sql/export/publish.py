"""Publish the adapter and the two GGUF files on the Hugging Face Hub, each with its model card.

Run with ``uv run --env-file .env python -m t2sql.export.publish [--dry-run]`` after
``t2sql.export.gguf``. Repos are created private (``publish.private``) and made public by hand
once their pages are checked. Every file is checked on the Hub after the upload.
"""

import argparse
import hashlib
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import httpx
import yaml

from t2sql.eval.run import ROOT, log, resolve_adapter
from t2sql.export.gguf import CONFIG_PATH

ADAPTER_FILES = ("adapter_model.safetensors", "adapter_config.json")
ATTEMPTS = 5  # network calls: tried again after 2, 4, 8, 16 s
T = TypeVar("T")


def retry(what: str, call: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """``call(*args, **kwargs)``, tried again on network errors only (a dropped connection is
    common on some networks); any other error, such as a refused permission, stops at once. An
    upload cut halfway is safe to repeat: the Hub keeps the blocks it already has."""
    for attempt in range(1, ATTEMPTS + 1):
        try:
            return call(*args, **kwargs)
        except httpx.TransportError as error:
            if attempt == ATTEMPTS:
                raise
            log(f"{what}: {error!r}, attempt {attempt + 1}/{ATTEMPTS} in {2**attempt} s")
            time.sleep(2**attempt)
    raise AssertionError("unreachable")


def sha256(data: bytes | Path) -> str:
    """SHA-256 of bytes, or of a file read in blocks (GGUF files are over 1 GB)."""
    if isinstance(data, bytes):
        return hashlib.sha256(data).hexdigest()
    digest = hashlib.sha256()
    with data.open("rb") as content:
        for block in iter(lambda: content.read(1 << 24), b""):
            digest.update(block)
    return digest.hexdigest()


def adapter_files(config: dict[str, Any]) -> dict[str, bytes]:
    """The adapter at the evaluated revision, its card and the base model's licence.

    The adapter config names the 4-bit copy the training loaded; it is pointed at the base the
    adapter was evaluated on (``config["base"]``), which is also what PEFT loads by default.
    """
    from huggingface_hub import hf_hub_download

    repo, revision = retry("adapter revision", resolve_adapter, config["adapter"])
    files = {
        name: Path(retry(name, hf_hub_download, repo, name, revision=revision)).read_bytes()
        for name in ADAPTER_FILES
    }
    adapter_config = json.loads(files["adapter_config.json"])
    adapter_config["base_model_name_or_path"] = config["base"]
    files["adapter_config.json"] = (json.dumps(adapter_config, indent=2) + "\n").encode()
    log(f"adapter from {repo} at {revision}")
    return files


def common_files(card: str, base: str) -> dict[str, bytes]:
    """The model card as README.md, and the Apache 2.0 licence of the base model."""
    from huggingface_hub import hf_hub_download

    return {
        "README.md": (ROOT / card).read_bytes(),
        "LICENSE": Path(retry("LICENSE", hf_hub_download, base, "LICENSE")).read_bytes(),
    }


def upload(repo: str, files: dict[str, bytes | Path], private: bool, dry_run: bool) -> None:
    """One commit with every file of ``repo`` (bytes in memory, or a path on disk), then a
    check that the Hub holds exactly those bytes."""
    from huggingface_hub import CommitOperationAdd, HfApi, hf_hub_download

    for name, data in files.items():
        size = len(data) if isinstance(data, bytes) else data.stat().st_size
        log(f"{repo}/{name}: {size / 1e6:.1f} MB")
    if dry_run:
        return
    api = HfApi()
    retry(f"create {repo}", api.create_repo, repo, private=private, exist_ok=True)
    operations = [CommitOperationAdd(name, data) for name, data in files.items()]
    message = "Upload model files and card"
    retry(f"upload {repo}", api.create_commit, repo, operations, commit_message=message)
    info = retry(f"read {repo}", api.model_info, repo, files_metadata=True)
    on_hub = {f.rfilename: f for f in info.siblings}
    for name, data in files.items():
        local = sha256(data)
        stored = on_hub[name].lfs  # large files: the Hub records their SHA-256
        if stored:
            remote = stored.sha256
        else:  # small files: read back
            path = retry(name, hf_hub_download, repo, name, force_download=True)
            remote = sha256(Path(path).read_bytes())
        if remote != local:
            raise SystemExit(f"{repo}/{name}: SHA-256 on the Hub {remote}, local {local}")
    log(f"{repo}: {len(files)} files checked on the Hub")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--dry-run", action="store_true", help="list the files, upload nothing")
    args = parser.parse_args()
    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    publish, gguf_dir = config["publish"], ROOT / config["paths"]["gguf"]
    adapter = adapter_files(config) | common_files(publish["model_card"], config["base"])
    upload(publish["adapter_repo"], adapter, publish["private"], args.dry_run)
    ggufs: dict[str, bytes | Path] = {
        repo_name: gguf_dir / local for local, repo_name in publish["gguf_files"].items()
    }
    ggufs |= common_files(publish["gguf_card"], config["base"])
    upload(publish["gguf_repo"], ggufs, publish["private"], args.dry_run)


if __name__ == "__main__":
    main()
