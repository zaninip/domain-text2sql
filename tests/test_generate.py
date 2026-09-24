"""Rendering rules of t2sql.dataset.generate."""

import json
import random
from pathlib import Path

import pytest

from t2sql.dataset.generate import (
    PLACEHOLDER,
    TemplateError,
    combination_weight,
    generate,
    instances,
    load_templates,
    render,
    sample_combinations,
    satisfies,
    slot_candidates,
    sql_literal,
    surface_form,
)

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "domains" / "eco2mix" / "templates"

BINDING = {
    "mesure": {"id": "eolien", "sql": "eolien_mw", "label": {"fr": "éolienne", "it": "eolica"}},
    "region": {"label": "Bretagne", "in": "en Bretagne", "literal": "'Bretagne'"},
    "annee": 2023,
    "saison": {"id": "hiver", "start": "{annee-1}-12-21", "end": "{annee}-03-19"},
    "role": {"fr": "exportatrice", "it": "esportatrice"},
}


def test_bare_placeholder_uses_the_label_of_the_language():
    assert render("énergie {mesure}", BINDING, "fr") == "énergie éolienne"
    assert render("energia {mesure}", BINDING, "it") == "energia eolica"


def test_bare_placeholder_keeps_a_plain_label():
    assert render("{region} {annee}", BINDING, "it") == "Bretagne 2023"


def test_attribute_access():
    assert render("SUM({mesure.sql})", BINDING, "fr") == "SUM(eolien_mw)"
    assert render("produite {region.in}", BINDING, "fr") == "produite en Bretagne"
    assert render("region = {region.literal}", BINDING, "fr") == "region = 'Bretagne'"


def test_language_map_resolves_outside_a_slot_dict():
    assert render("'{role}'", BINDING, "it") == "'esportatrice'"


def test_integer_offset():
    assert render("{annee-1}/{annee}/{annee+2}", BINDING, "fr") == "2022/2023/2025"


def test_nested_attribute_is_rendered_against_the_same_binding():
    assert render("{saison.start}..{saison.end}", BINDING, "fr") == "2022-12-21..2023-03-19"


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("{inconnu}", "no slot"),
        ("{mesure.nope}", "no attribute"),
        ("{region-1}", "not an integer"),
        ("{role.fr}", "language map"),  # hardcoding a language must not be possible
    ],
)
def test_rendering_errors(text, reason):
    with pytest.raises(TemplateError, match=reason):
        render(text, BINDING, "fr")


# --- the real template directory -------------------------------------------------------------


def test_real_templates_load():
    catalogues, templates = load_templates(TEMPLATE_DIR)
    assert {"regions", "measures", "months", "seasons", "moments", "energy_units"} <= set(
        catalogues
    )
    assert len(catalogues["regions"]) == 12
    assert len({t["template_id"] for t in templates}) == len(templates)


def test_every_template_declares_the_required_fields():
    _, templates = load_templates(TEMPLATE_DIR)
    for template in templates:
        missing = {"template_id", "family", "conventions", "slots", "questions", "sql", "result"}
        missing -= set(template)
        assert not missing, f"{template.get('template_id')} misses {missing}"
        assert set(template["questions"]) == {"fr", "it"}


def test_duplicate_catalogue_is_rejected(tmp_path: Path):
    (tmp_path / "a.yaml").write_text("regions:\n  - {id: X}\n", encoding="utf-8")
    (tmp_path / "b.yaml").write_text("regions:\n  - {id: Y}\n", encoding="utf-8")
    with pytest.raises(TemplateError, match="defined twice"):
        load_templates(tmp_path)


# --- slots, constraints, sampling -------------------------------------------------------------

CATALOGUES = {
    "measures": [
        {"id": "eolien", "kind": "production", "sql": "eolien_mw"},
        {"id": "consommation", "kind": "demand", "sql": "consommation_mw"},
    ],
}


def test_slot_candidates_from_values_and_range():
    assert slot_candidates({"values": [1, 2]}, {}) == [1, 2]
    assert slot_candidates({"range": [2020, 2023]}, {}) == [2020, 2021, 2022, 2023]


def test_slot_candidates_filters_a_catalogue():
    spec = {"from": "measures", "keep": {"kind": "production"}}
    assert [m["id"] for m in slot_candidates(spec, CATALOGUES)] == ["eolien"]
    spec = {"from": "measures", "keep": {"kind": ["production", "demand"]}}
    assert len(slot_candidates(spec, CATALOGUES)) == 2


@pytest.mark.parametrize(
    ("spec", "reason"),
    [
        ({}, "needs"),
        ({"from": "nope"}, "unknown catalogue"),
        ({"from": "measures", "keep": {"kind": "storage"}}, "no value left"),
    ],
)
def test_slot_candidates_errors(spec, reason):
    with pytest.raises(TemplateError, match=reason):
        slot_candidates(spec, CATALOGUES)


def test_sql_literal_doubles_inner_quotes():
    assert sql_literal("Provence-Alpes-Côte d'Azur") == "'Provence-Alpes-Côte d''Azur'"


def test_satisfies_compares_slots_and_integers():
    assert satisfies(["b > a"], {"a": 2019, "b": 2024})
    assert not satisfies(["b > a"], {"a": 2024, "b": 2019})
    assert satisfies(["a >= 2013"], {"a": 2013})
    assert not satisfies(["m != eolien"], {"m": {"id": "eolien"}, "eolien": {"id": "eolien"}})


@pytest.mark.parametrize(
    ("constraint", "reason"),
    [("a === b", "unsupported"), ("a > zzz", "unknown slot")],
)
def test_satisfies_errors(constraint, reason):
    with pytest.raises(TemplateError, match=reason):
        satisfies([constraint], {"a": 1})


SAMPLE_TEMPLATE = {
    "template_id": "t",
    "slots": {"a": {"range": [2013, 2025]}, "b": {"range": [2013, 2025]}},
    "constraints": ["b > a"],
    "max_instances": 10,
}


def test_sampling_is_reproducible_and_respects_constraints():
    first = sample_combinations(SAMPLE_TEMPLATE, {}, seed=1)
    assert first == sample_combinations(SAMPLE_TEMPLATE, {}, seed=1)
    assert first != sample_combinations(SAMPLE_TEMPLATE, {}, seed=2)
    assert len(first) == 10
    assert all(combo["b"] > combo["a"] for combo in first)


def test_sampling_is_independent_between_templates():
    other = SAMPLE_TEMPLATE | {"template_id": "other"}
    mine = sample_combinations(SAMPLE_TEMPLATE, {}, seed=1)
    assert mine != sample_combinations(other, {}, seed=1)


def test_sampling_refuses_an_unbounded_product():
    huge = {
        "template_id": "huge",
        "slots": {name: {"range": [0, 99]} for name in "abcde"},
    }
    with pytest.raises(TemplateError, match="combinations"):
        sample_combinations(huge, {}, seed=1)


# --- records ----------------------------------------------------------------------------------


def test_instances_share_one_sql_across_languages_and_variants():
    catalogues, templates = load_templates(TEMPLATE_DIR)
    template = next(t for t in templates if t["template_id"] == "ts_monthly_energy_region_year")
    records = list(instances(template, catalogues, seed=1))
    first = [r for r in records if r["id"].endswith(("#fr#0", "#fr#1")) and "#0000#" in r["id"]]
    assert len({r["sql"] for r in first}) == 1
    assert len({r["question"] for r in first}) == 2
    assert all(r["order_matters"] for r in records)


def test_constants_are_rendered_in_the_language_of_the_question():
    catalogues, templates = load_templates(TEMPLATE_DIR)
    template = next(t for t in templates if t["template_id"] == "cls_exchange_role_season")
    records = list(instances(template, catalogues, seed=1))
    assert any("'esportatrice'" in r["sql"] for r in records if r["lang"] == "it")
    assert any("'exportatrice'" in r["sql"] for r in records if r["lang"] == "fr")


def test_generate_writes_one_json_object_per_line(tmp_path: Path):
    out = tmp_path / "raw.jsonl"
    written = generate(TEMPLATE_DIR, out, seed=1)
    lines = out.read_text(encoding="utf-8").splitlines()
    assert written == len(lines)
    record = json.loads(lines[0])
    assert {"id", "template_id", "family", "conventions", "lang", "question", "sql"} <= set(record)


# --- weights ----------------------------------------------------------------------------------


def test_combination_weight_multiplies_and_defaults_to_one():
    assert combination_weight({"a": {"id": "x"}, "b": 2023}) == 1.0
    assert combination_weight({"a": {"id": "x", "weight": 0.2}, "b": {"id": "y"}}) == 0.2
    assert combination_weight({"a": {"weight": 0.5}, "b": {"weight": 0.5}}) == 0.25


WEIGHTED_TEMPLATE = {
    "template_id": "weighted",
    "slots": {
        "pick": {
            "values": [
                {"id": "often", "weight": 10},
                {"id": "rarely", "weight": 1},
                {"id": "never", "weight": 0},
            ]
        },
        "year": {"range": [1900, 1999]},
    },
    "max_instances": 60,
}


def test_weight_zero_removes_a_value():
    picked = {c["pick"]["id"] for c in sample_combinations(WEIGHTED_TEMPLATE, {}, seed=1)}
    assert "never" not in picked


def test_a_heavier_value_is_drawn_more_often():
    chosen = [c["pick"]["id"] for c in sample_combinations(WEIGHTED_TEMPLATE, {}, seed=1)]
    assert chosen.count("often") > 3 * chosen.count("rarely")


def test_weighted_sampling_stays_reproducible():
    first = sample_combinations(WEIGHTED_TEMPLATE, {}, seed=3)
    assert first == sample_combinations(WEIGHTED_TEMPLATE, {}, seed=3)
    assert first != sample_combinations(WEIGHTED_TEMPLATE, {}, seed=4)


# --- hand-written question variants -------------------------------------------------------------


def slot_names(text: str) -> set[str]:
    """The slots a question mentions, ignoring which attribute of them it uses."""
    return {match.group(1) for match in PLACEHOLDER.finditer(text)}


def test_every_variant_of_a_template_mentions_the_same_slots():
    """A variant that drops a slot asks a vaguer question than its SQL answers."""
    _, templates = load_templates(TEMPLATE_DIR)
    for template in templates:
        variants = {
            (lang, index): slot_names(question)
            for lang, questions in template["questions"].items()
            for index, question in enumerate(questions)
        }
        reference = variants[("fr", 0)]
        for key, slots in variants.items():
            assert slots == reference, (
                f"{template['template_id']} variant {key} mentions {sorted(slots)}, "
                f"but fr[0] mentions {sorted(reference)}"
            )


def test_every_slot_of_a_template_appears_in_its_questions():
    """A slot the questions never name makes the answer undetermined."""
    _, templates = load_templates(TEMPLATE_DIR)
    for template in templates:
        asked = slot_names(template["questions"]["fr"][0])
        unused = set(template["slots"]) - asked
        assert not unused, f"{template['template_id']} never asks about {sorted(unused)}"


# --- entities with several surface forms --------------------------------------------------------


def test_surface_form_keeps_the_attributes_of_the_entry():
    group = {
        "id": "bas_carbone",
        "sql": "a + b",
        "fr": [{"label": "bas-carbone"}, {"label": "propre"}],
        "it": [{"label": "pulita"}, {"label": "decarbonizzata"}],
    }
    rng = random.Random(0)
    chosen = [surface_form(group, "it", rng, alias_ratio=0.5) for _ in range(40)]
    assert {c["label"] for c in chosen} == {"pulita", "decarbonizzata"}
    assert all(c["sql"] == "a + b" and c["id"] == "bas_carbone" for c in chosen)
    assert "fr" not in chosen[0] and "it" not in chosen[0]


def test_surface_form_leaves_ordinary_values_alone():
    plain = {"id": "eolien", "sql": "eolien_mw", "label": {"fr": "éolienne", "it": "eolica"}}
    assert surface_form(plain, "fr", random.Random(0), alias_ratio=1.0) is plain
