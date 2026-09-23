"""Split the validated dataset by template, never by example.

Two examples of the same template share their SQL skeleton, so putting one in train and the
other in test would measure memorisation instead of generalisation. Whole templates therefore
move together, and families are interleaved before dealing so that validation and test do not
end up holding a single family.
"""

import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

SPLITS = ("train", "val", "test")
PROPORTIONS = {"train": 0.70, "val": 0.15, "test": 0.15}


def interleave_families(templates: dict[str, str], seed: int) -> list[str]:
    """Order template ids so that consecutive ones belong to different families."""
    by_family: dict[str, list[str]] = defaultdict(list)
    for template_id, family in sorted(templates.items()):
        by_family[family].append(template_id)
    rng = random.Random(f"{seed}:split")
    for family in by_family.values():
        rng.shuffle(family)
    ordered: list[str] = []
    while any(by_family.values()):
        for family in sorted(by_family):
            if by_family[family]:
                ordered.append(by_family[family].pop())
    return ordered


def assign_splits(templates: dict[str, str], seed: int) -> dict[str, str]:
    """Map every template id to a split, keeping each split close to its target share."""
    assigned: Counter = Counter()
    result: dict[str, str] = {}
    for position, template_id in enumerate(interleave_families(templates, seed), start=1):
        # Give the template to whichever split is furthest behind its target so far;
        # ties go to the earlier split of SPLITS, which keeps the result deterministic.
        def behind(split: str, at: int = position) -> tuple[float, int]:
            return (PROPORTIONS[split] * at - assigned[split], -SPLITS.index(split))

        chosen = max(SPLITS, key=behind)
        result[template_id] = chosen
        assigned[chosen] += 1
    return result


def describe(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts a human (and the README) wants to see about one split."""
    return {
        "examples": len(records),
        "templates": sorted({r["template_id"] for r in records}),
        "by_family": dict(Counter(r["family"] for r in records)),
        "by_language": dict(Counter(r["lang"] for r in records)),
        "by_template": dict(Counter(r["template_id"] for r in records)),
        "by_convention": dict(Counter(c for r in records for c in r["conventions"])),
    }


def split_dataset(clean_path: Path, out_dir: Path, seed: int) -> dict[str, Any]:
    """Write train/val/test JSONL files and return the statistics report."""
    records = [json.loads(line) for line in clean_path.open(encoding="utf-8")]
    families = {r["template_id"]: r["family"] for r in records}
    assignment = assign_splits(families, seed)

    out_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"seed": seed, "proportions": PROPORTIONS, "splits": {}}
    for split in SPLITS:
        chosen = [r for r in records if assignment[r["template_id"]] == split]
        path = out_dir / f"{split}.jsonl"
        with path.open("w", encoding="utf-8") as out:
            for record in chosen:
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
        report["splits"][split] = describe(chosen)
    report["assignment"] = assignment
    return report
