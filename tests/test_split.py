"""Split rules of t2sql.dataset.split: by template, stratified, reproducible, no leakage."""

import json
from pathlib import Path

from t2sql.dataset.split import SPLITS, assign_splits, split_dataset

# 20 templates over 5 families, the shape phase 2 aims for.
FAMILIES = {f"t{i:02d}": f"family{i % 5}" for i in range(20)}


def test_assignment_is_reproducible():
    assert assign_splits(FAMILIES, seed=1) == assign_splits(FAMILIES, seed=1)
    assert assign_splits(FAMILIES, seed=1) != assign_splits(FAMILIES, seed=2)


def test_assignment_respects_the_target_proportions():
    counts = {split: 0 for split in SPLITS}
    for split in assign_splits(FAMILIES, seed=1).values():
        counts[split] += 1
    assert counts == {"train": 14, "val": 3, "test": 3}


def test_every_split_holds_more_than_one_family():
    assignment = assign_splits(FAMILIES, seed=1)
    for split in SPLITS:
        families = {FAMILIES[t] for t, s in assignment.items() if s == split}
        assert len(families) > 1, f"{split} holds only {families}"


def records(template_id: str, family: str, n: int) -> list[dict]:
    return [
        {
            "id": f"{template_id}#{i}",
            "template_id": template_id,
            "family": family,
            "conventions": ["energy_conversion"],
            "lang": "fr" if i % 2 else "it",
            "question": "q",
            "sql": "SELECT 1",
        }
        for i in range(n)
    ]


def test_split_dataset_writes_files_without_template_leakage(tmp_path: Path):
    clean = tmp_path / "clean.jsonl"
    rows = [r for i, t in enumerate(FAMILIES) for r in records(t, FAMILIES[t], 4 + i)]
    clean.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    report = split_dataset(clean, tmp_path, seed=1)

    seen: dict[str, str] = {}
    total = 0
    for split in SPLITS:
        lines = (tmp_path / f"{split}.jsonl").read_text(encoding="utf-8").splitlines()
        total += len(lines)
        assert len(lines) == report["splits"][split]["examples"]
        for line in lines:
            record = json.loads(line)
            previous = seen.setdefault(record["template_id"], split)
            assert previous == split, f"{record['template_id']} leaks into {split}"
    assert total == len(rows)


def test_report_counts_languages_families_and_conventions(tmp_path: Path):
    clean = tmp_path / "clean.jsonl"
    rows = records("t00", "family0", 10) + records("t01", "family1", 10)
    clean.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    report = split_dataset(clean, tmp_path, seed=1)
    train = report["splits"]["train"]
    assert set(train["by_language"]) <= {"fr", "it"}
    assert train["by_convention"] == {"energy_conversion": train["examples"]}


def test_every_family_keeps_a_template_in_train():
    families = {"a1": "a", "a2": "a", "a3": "a", "b1": "b", "c1": "c", "c2": "c"}
    for seed in range(20):
        assignment = assign_splits(families, seed=seed)
        trained = {families[t] for t, split in assignment.items() if split == "train"}
        assert trained == {"a", "b", "c"}, f"seed {seed}: {assignment}"


def test_a_single_template_family_never_leaves_train():
    families = {f"t{i:02d}": f"family{i % 5}" for i in range(20)} | {"lonely": "alone"}
    assert assign_splits(families, seed=1)["lonely"] == "train"
