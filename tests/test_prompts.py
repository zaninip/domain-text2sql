"""The system prompt shared by every configuration (CLAUDE.md §2, fair comparison)."""

from pathlib import Path

import pytest

from t2sql.prompts import (
    MAX_PROMPT_TOKENS,
    PromptTooLongError,
    approx_tokens,
    build_system_prompt,
    caveats,
    chat_messages,
    conventions,
    definitions,
    system_prompt,
)

DOMAIN_DIR = Path(__file__).resolve().parents[1] / "domains" / "eco2mix"

GLOSSARY = """# Glossary

Intro paragraph that is not a section.

## 1. Power vs energy

**Prompt**

- Energy is SUM(MW) * 0.5.

**Notes**

- A residual of 1 MW is a source artefact.

## 2. Time

**Prompt**

- Winter starts on 21 December.

## 3. Internal only

Nothing for the prompt here.
"""


def test_conventions_keeps_rules_and_drops_notes():
    text = conventions(GLOSSARY)
    assert "Energy is SUM(MW) * 0.5." in text
    assert "Winter starts on 21 December." in text
    assert "source artefact" not in text
    assert "Intro paragraph" not in text


def test_conventions_skips_a_section_without_rules():
    assert "Internal only" not in conventions(GLOSSARY)


def test_build_system_prompt_contains_the_three_parts():
    prompt = build_system_prompt("# Table `t`\n\n| column |\n|---|\n| x |", GLOSSARY)
    assert "DuckDB SQL query" in prompt  # instructions
    assert "# Table `t`" in prompt  # schema
    assert "# Conventions" in prompt  # glossary rules


def test_a_prompt_that_grew_out_of_hand_is_refused():
    with pytest.raises(PromptTooLongError, match="max"):
        build_system_prompt("x" * (MAX_PROMPT_TOKENS * 4), GLOSSARY)


# --- the real domain --------------------------------------------------------------------------


def test_real_prompt_states_schema_and_conventions():
    prompt = system_prompt(DOMAIN_DIR)
    for expected in (
        "eco2mix",
        "consommation_mw",
        "tco_eolien_pct",
        "2026-06-30",  # the last date, against which relative dates resolve
        "Provence-Alpes-Côte d''Azur",  # quoted the way SQL needs it
        "SUM(x_mw) * 0.5",
        "isodow",
    ):
        assert expected in prompt, f"{expected!r} missing from the system prompt"


def test_real_prompt_hides_the_notes_written_for_humans():
    prompt = system_prompt(DOMAIN_DIR)
    for hidden in ("**Notes**", "source artefacts", "no template should target them"):
        assert hidden not in prompt


def test_real_prompt_stays_within_the_budget():
    assert approx_tokens(system_prompt(DOMAIN_DIR)) <= MAX_PROMPT_TOKENS


def test_chat_messages_carry_the_answer_only_when_it_is_known():
    training = chat_messages("SYS", "question ?", "SELECT 1")
    assert [m["role"] for m in training] == ["system", "user", "assistant"]
    assert training[2]["content"] == "SELECT 1"
    inference = chat_messages("SYS", "question ?")
    assert [m["role"] for m in inference] == ["system", "user"]


def test_caveats_are_extracted_for_the_demo_users():
    text = (
        GLOSSARY
        + """
## 9. Known data caveats

**Caveats**

- One published value is visibly wrong, and is
  kept as published.
- Coverage ends mid-2026.
"""
    )
    items = caveats(text)
    assert items == [
        "One published value is visibly wrong, and is kept as published.",
        "Coverage ends mid-2026.",
    ]


def test_caveats_never_reach_the_model():
    glossary = Path(DOMAIN_DIR / "glossary.md").read_text(encoding="utf-8")
    prompt = system_prompt(DOMAIN_DIR)
    assert caveats(glossary), "the domain must declare its caveats"
    for caveat in caveats(glossary):
        assert caveat[:40] not in prompt


def test_definitions_are_extracted_for_the_demo_users():
    glossary = (DOMAIN_DIR / "glossary.md").read_text(encoding="utf-8")
    items = definitions(glossary)
    assert len(items) >= 10
    assert any("nuclear" in item and "green" in item for item in items)


def test_definitions_never_reach_the_model():
    glossary = (DOMAIN_DIR / "glossary.md").read_text(encoding="utf-8")
    prompt = system_prompt(DOMAIN_DIR)
    for item in definitions(glossary):
        assert item[:40] not in prompt


def test_a_block_stops_at_the_next_marker():
    text = "# G\n\n## 1. S\n\n**Definitions**\n\n- shown\n\n**Notes**\n\n- hidden\n"
    assert definitions(text) == ["shown"]
