"""Closest-variant report of t2sql.dataset.similar."""

from t2sql.dataset.similar import closest_pairs, question_tokens


def test_placeholders_are_reduced_to_their_slot():
    tokens = question_tokens("Part {mesure.de} dans la consommation {region.in} en {annee-1} ?")
    assert tokens == ["part", "<mesure>", "dans", "la", "consommation", "<region>", "en", "<annee>"]


def template(template_id: str, fr: list[str]) -> dict:
    return {"template_id": template_id, "questions": {"fr": fr, "it": ["x"]}}


def test_closest_pairs_compare_only_different_templates():
    templates = [
        template("a", ["Part dans la consommation ?", "Part dans la consommation, en % ?"]),
        template("b", ["Part dans la production ?"]),
    ]
    pairs = closest_pairs(templates)
    fr = [pair for pair in pairs if pair[1] == "fr"]
    assert len(fr) == 1  # one best pair per couple of templates and language
    score, _, first, second = fr[0]
    assert {first[0], second[0]} == {"a", "b"}
    assert 0.5 < score < 1.0
