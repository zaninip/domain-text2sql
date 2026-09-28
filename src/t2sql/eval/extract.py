"""Turn a model's raw output into the one SQL statement to run.

Every configuration (base zero-shot, base few-shot, fine-tuned) and the demo go through this
single function (CLAUDE.md §10), so no model is favoured by a more lenient parser. It removes
Qwen3's thinking block and code fences, keeps the first statement and drops prose around it.
"""

import re

from sqlglot.errors import TokenError
from sqlglot.tokens import Tokenizer, TokenType

THINK = re.compile(r"<think>.*?</think>|</?think>", re.DOTALL)
FENCE = re.compile(r"```[\w-]*\n?(.*?)(?:```|\Z)", re.DOTALL)  # an unclosed fence runs to the end
# Queries, and the statements a model must not write: extracted too, so that the safety check
# rejects them and the report counts them as unsafe instead of "no SQL".
SQL_START = re.compile(
    r"^\s*(SELECT|WITH|INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|ATTACH|COPY|PRAGMA|SET|INSTALL"
    r"|LOAD)\b",
    re.IGNORECASE | re.MULTILINE,
)
BLANK_LINE = re.compile(r"\n\s*\n")


def first_statement(sql: str) -> str:
    """Cut at the first semicolon outside string literals and quoted names."""
    try:
        tokens = Tokenizer().tokenize(sql)
    except TokenError:  # unterminated string and the like: fall back to a plain split
        return sql.split(";", 1)[0]
    for token in tokens:
        if token.token_type == TokenType.SEMICOLON:
            return sql[: token.start]
    return sql


def extract_sql(output: str) -> str:
    """The SQL statement in a model output, or "" when there is none."""
    text = THINK.sub("", output)
    fenced = FENCE.search(text)
    if fenced:
        text = fenced.group(1)
    else:
        start = SQL_START.search(text)
        if not start:
            return ""
        text = text[start.start(1) :]  # from the keyword, not from the blank lines before it
        # Without a fence, prose often follows the query after a blank line; gold SQL never
        # contains one, so the first blank line ends the statement.
        text = BLANK_LINE.split(text, maxsplit=1)[0]
    return first_statement(text).strip()
