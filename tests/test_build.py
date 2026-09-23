"""The chat files the trainer reads (t2sql.dataset.build)."""

import json
from pathlib import Path

from t2sql.dataset.build import write_chat_files
from t2sql.dataset.split import SPLITS

DOMAIN_DIR = Path(__file__).resolve().parents[1] / "domains" / "eco2mix"


def test_write_chat_files_mirrors_every_split(tmp_path: Path):
    for index, split in enumerate(SPLITS):
        records = [
            {
                "id": f"{split}#{i}",
                "template_id": f"t{index}",
                "question": "Quelle production éolienne en Bretagne en 2023 ?",
                "sql": "SELECT SUM(eolien_mw) * 0.5 FROM eco2mix",
            }
            for i in range(index + 1)
        ]
        (tmp_path / f"{split}.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8"
        )

    sizes = write_chat_files(tmp_path, DOMAIN_DIR)

    assert set(sizes) == set(SPLITS)
    systems = set()
    for index, split in enumerate(SPLITS):
        lines = (tmp_path / f"{split}_chat.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) == index + 1
        chat = json.loads(lines[0])
        assert chat["id"] == f"{split}#0"
        roles = [m["role"] for m in chat["messages"]]
        assert roles == ["system", "user", "assistant"]
        assert chat["messages"][2]["content"].startswith("SELECT")
        systems.add(chat["messages"][0]["content"])
    assert len(systems) == 1, "every split must carry the very same system prompt"
