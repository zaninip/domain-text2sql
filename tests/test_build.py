"""The chat files the trainer reads (t2sql.dataset.build)."""

import json
from pathlib import Path

from t2sql.dataset.build import write_chat_files, write_sample
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


def test_sample_spreads_over_templates_and_languages(tmp_path: Path):
    records = [
        {
            "id": f"{template}#{lang}#{i}",
            "template_id": template,
            "lang": lang,
            "family": "f",
            "conventions": ["energy_conversion"],
            "question": "q ?",
            "sql": "SELECT 1",
            "gold": {"columns": ["a"], "rows": [[i]]},
        }
        for template in ("t0", "t1", "t2")
        for lang in ("fr", "it")
        for i in range(20)
    ]
    clean = tmp_path / "clean.jsonl"
    clean.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")

    out = tmp_path / "sample.md"
    assert write_sample(clean, out, seed=1, count=12) == 12

    text = out.read_text(encoding="utf-8")
    assert text.count("\n## ") == 12
    for template in ("t0", "t1", "t2"):
        assert text.count(f"`{template}`") == 4  # 12 examples over 3 templates x 2 languages


def test_sample_is_reproducible(tmp_path: Path):
    clean = tmp_path / "clean.jsonl"
    records = [
        {
            "id": f"x#{i}",
            "template_id": "t",
            "lang": "fr",
            "family": "f",
            "conventions": [],
            "question": f"q{i} ?",
            "sql": "SELECT 1",
            "gold": {"columns": ["a"], "rows": [[i]]},
        }
        for i in range(50)
    ]
    clean.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    write_sample(clean, tmp_path / "a.md", seed=7, count=5)
    write_sample(clean, tmp_path / "b.md", seed=7, count=5)
    assert (tmp_path / "a.md").read_text("utf-8") == (tmp_path / "b.md").read_text("utf-8")
