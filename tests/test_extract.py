"""SQL extraction from raw model outputs (t2sql.eval.extract)."""

import pytest

from t2sql.eval.extract import extract_sql

QUERY = "SELECT AVG(eolien_mw) FROM eco2mix WHERE annee = 2022"


@pytest.mark.parametrize(
    "output",
    [
        QUERY,
        QUERY + ";",
        f"```sql\n{QUERY}\n```",
        f"```\n{QUERY};\n```",
        f"```sql\n{QUERY}",  # generation stopped before the closing fence
        f"<think>\n\n</think>\n\n{QUERY}",  # Qwen3 with thinking disabled
        f"Voici la requête :\n{QUERY}",
        f"{QUERY};\nSELECT 1;",  # a second statement is ignored
        f"{QUERY}\n\nCette requête calcule la moyenne.",  # prose after a blank line
        f"Here is the query:\n```sql\n{QUERY}\n```\nIt averages the wind power.",
    ],
)
def test_the_query_is_found(output):
    assert extract_sql(output) == QUERY


def test_lower_case_and_with_queries():
    assert extract_sql("select 1") == "select 1"
    cte = "WITH n AS (SELECT 1 AS x)\nSELECT x FROM n"
    assert extract_sql(f"```sql\n{cte}\n```") == cte


def test_a_semicolon_inside_a_string_does_not_cut():
    query = "SELECT region FROM eco2mix WHERE region = 'a;b'"
    assert extract_sql(query + "; SELECT 2") == query


def test_statements_that_are_not_queries_are_extracted_for_the_safety_check():
    assert extract_sql("DROP TABLE eco2mix;") == "DROP TABLE eco2mix"
    assert extract_sql("```sql\nINSTALL httpfs\n```") == "INSTALL httpfs"


def test_with_in_prose_is_not_sql():
    assert extract_sql("I would start with the eco2mix table.") == ""


def test_no_sql_gives_an_empty_string():
    assert extract_sql("") == ""
    assert extract_sql("Je ne peux pas répondre.") == ""
