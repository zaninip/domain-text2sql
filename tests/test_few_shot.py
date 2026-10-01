"""The fixed few-shot examples of t2sql.eval.few_shot."""

from t2sql.eval.few_shot import pick_examples


def record(family: str, template_id: str, lang: str, n: int) -> dict[str, str]:
    return {
        "id": f"{template_id}-{lang}-{n}",
        "family": family,
        "template_id": template_id,
        "lang": lang,
    }


TRAIN = [
    record(family, template_id, lang, n)
    for family, templates in {
        "aggregation": ["agg_a", "agg_b"],
        "ranking": ["rank_a"],
        "rate": ["rate_a", "rate_b", "rate_c"],
    }.items()
    for template_id in templates
    for lang in ("fr", "it")
    for n in range(4)
]


def test_one_example_per_family_languages_alternating():
    examples = pick_examples(TRAIN, seed=0)
    assert [e["family"] for e in examples] == ["aggregation", "ranking", "rate"]
    assert [e["lang"] for e in examples] == ["fr", "it", "fr"]


def test_the_seed_decides_not_the_file_order():
    ids = [e["id"] for e in pick_examples(TRAIN, seed=0)]
    assert [e["id"] for e in pick_examples(TRAIN[::-1], seed=0)] == ids
    assert any([e["id"] for e in pick_examples(TRAIN, seed=s)] != ids for s in range(1, 10))
