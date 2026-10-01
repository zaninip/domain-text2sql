"""Fixed few-shot examples for the base few-shot configuration (CLAUDE.md §7, phase 3).

The examples come from the train split only, so no template of validation or test is ever
shown. They are drawn by a seeded rule rather than picked by hand: one example per family,
languages alternating, so that the set covers every kind of question without being chosen
with the evaluation questions in mind. Every question of a run gets the same examples.
"""

import random
from collections import defaultdict
from typing import Any

LANGUAGES = ("fr", "it")


def pick_examples(train: list[dict[str, Any]], seed: int) -> list[dict[str, Any]]:
    """One train example per family, in family order, languages alternating fr, it, fr...

    For each family a template is drawn at random, then one of its examples in the language
    of that turn. Everything is sorted before drawing, so the result depends on the seed and
    the content of the split only, never on file order.
    """
    by_family: dict[str, set[str]] = defaultdict(set)
    by_template_lang: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in train:
        by_family[record["family"]].add(record["template_id"])
        by_template_lang[record["template_id"], record["lang"]].append(record)

    rng = random.Random(seed)
    examples = []
    for i, family in enumerate(sorted(by_family)):
        lang = LANGUAGES[i % len(LANGUAGES)]
        template = rng.choice(sorted(by_family[family]))
        candidates = sorted(by_template_lang[template, lang], key=lambda r: r["id"])
        examples.append(rng.choice(candidates))
    return examples
