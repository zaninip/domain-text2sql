"""Build the system prompt shared by training, evaluation and the demo.

The prompt is assembled from two generated/reviewed files of the domain: ``schema.md`` (built
from the database, so it always states the real columns and the real date range) and
``glossary.md``. Only the sections of the glossary marked ``**Prompt**`` are included; the
``**Notes**`` blocks are written for us and stay out.

Every configuration -- base zero-shot, base few-shot, fine-tuned -- must call this one function,
or the comparison would measure the prompt instead of the model.
"""

from pathlib import Path

INSTRUCTIONS = """
You translate a question about the French electricity system into one DuckDB SQL query.

Rules:
- Answer with the SQL query only: no explanation, no comment, no markdown fence.
- Write a single SELECT (or WITH ... SELECT) statement over the table described below.
- Use the exact column names of the schema.
- Follow every convention of the glossary: they define what the answer must contain.
- Questions come in French or Italian; the SQL is the same either way.
""".strip()

# Rough bound on the prompt size: it only catches a schema or glossary that has grown out of
# hand. The estimate is char-based, so it can be off by a third; the real count is measured in
# phase 3 with the tokenizer of the chosen model, together with the training sequence length
# and the CPU latency it costs. The limit is provisional until then.
CHARS_PER_TOKEN = 3.5
MAX_PROMPT_TOKENS = 3500


class PromptTooLongError(ValueError):
    """Raised when schema and glossary no longer leave room for question and answer."""


def conventions(glossary_md: str) -> str:
    """Keep the ``**Prompt**`` part of every glossary section, drop the ``**Notes**``."""
    kept: list[str] = []
    for section in glossary_md.split("\n## ")[1:]:
        title, _, body = section.partition("\n")
        if "**Prompt**" not in body:
            continue
        rules = body.split("**Prompt**", 1)[1].split("**Notes**", 1)[0]
        kept.append(f"## {title.strip()}\n{rules.strip()}")
    return "# Conventions\n\n" + "\n\n".join(kept)


def approx_tokens(text: str) -> int:
    """Rough token count, good enough to catch a prompt that grew too large."""
    return round(len(text) / CHARS_PER_TOKEN)


def build_system_prompt(schema_md: str, glossary_md: str) -> str:
    """Assemble instructions, schema and conventions into the one shared system prompt."""
    prompt = "\n\n".join([INSTRUCTIONS, schema_md.strip(), conventions(glossary_md)])
    size = approx_tokens(prompt)
    if size > MAX_PROMPT_TOKENS:
        raise PromptTooLongError(f"system prompt is about {size} tokens (max {MAX_PROMPT_TOKENS})")
    return prompt


def system_prompt(domain_dir: Path) -> str:
    """Read ``schema.md`` and ``glossary.md`` of a domain and build its system prompt."""
    schema = (domain_dir / "schema.md").read_text(encoding="utf-8")
    glossary = (domain_dir / "glossary.md").read_text(encoding="utf-8")
    return build_system_prompt(schema, glossary)


def chat_messages(system: str, question: str, sql: str | None = None) -> list[dict[str, str]]:
    """The chat form used everywhere: system, user question, and the SQL when it is known.

    Training passes ``sql`` (the assistant turn is the target); inference leaves it out and
    lets the model produce that turn. Both go through here so the two never drift apart.
    """
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": question},
    ]
    if sql is not None:
        messages.append({"role": "assistant", "content": sql})
    return messages
