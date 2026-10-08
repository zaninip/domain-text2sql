"""Training coverage of the conventions that validation and test ask for.

Checked on the template files, with the split the build would make (same function, same
seed, train-only templates in train), so that it runs without the database.
"""

from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

import yaml

from t2sql.dataset.generate import LANGUAGES, load_templates
from t2sql.dataset.similar import question_tokens
from t2sql.dataset.split import assign_splits

ROOT = Path(__file__).resolve().parents[1]
CONFIG = yaml.safe_load((ROOT / "configs" / "dataset.yaml").read_text(encoding="utf-8"))
TEMPLATE_DIR = ROOT / CONFIG["paths"]["templates"]


def template_splits(templates: list[dict]) -> dict[str, str]:
    """The split of every template, as `split_dataset` assigns it."""
    regular = {t["template_id"]: t["family"] for t in templates if not t.get("train_only")}
    pinned = {t["template_id"]: "train" for t in templates if t.get("train_only")}
    return assign_splits(regular, CONFIG["seed"]) | pinned


def test_every_evaluated_convention_is_trained_by_enough_templates():
    """A convention seen in one train template only is learnt half-way, and validation then
    swings with the seed of the run (docs/decisions.md, 2026-10-08)."""
    _, templates = load_templates(TEMPLATE_DIR)
    splits = template_splits(templates)
    trained: dict[str, set[str]] = defaultdict(set)
    evaluated: dict[str, set[str]] = defaultdict(set)
    for template in templates:
        target = trained if splits[template["template_id"]] == "train" else evaluated
        for convention in template["conventions"]:
            target[convention].add(template["template_id"])

    minimum = CONFIG["min_train_templates_per_convention"]
    gaps = [
        f"{convention}: {len(trained[convention])} train template(s), "
        f"needed by {sorted(evaluated[convention])}"
        for convention in sorted(evaluated)
        if len(trained[convention]) < minimum
    ]
    assert not gaps, f"{len(gaps)} conventions under {minimum} train templates:\n" + "\n".join(gaps)


def test_train_only_variants_do_not_copy_evaluated_wording():
    """Train-only templates teach a convention, not the phrasing of the evaluation questions:
    each of their variants stays under a similarity ceiling to every validation and test
    variant of the same language (docs/decisions.md, 2026-10-08)."""
    _, templates = load_templates(TEMPLATE_DIR)
    splits = template_splits(templates)
    evaluated = [t for t in templates if splits[t["template_id"]] != "train"]
    ceiling = CONFIG["max_train_only_similarity"]
    too_close = []
    for template in (t for t in templates if t.get("train_only")):
        for lang in LANGUAGES:
            for question in template["questions"][lang]:
                tokens = question_tokens(question)
                score = max(
                    SequenceMatcher(None, tokens, question_tokens(q)).ratio()
                    for e in evaluated
                    for q in e["questions"][lang]
                )
                if score >= ceiling:
                    too_close.append(f"{score:.2f} {template['template_id']} [{lang}] {question}")
    listing = "\n".join(sorted(too_close, reverse=True))
    assert not too_close, f"{len(too_close)} variants at or above {ceiling}:\n{listing}"
