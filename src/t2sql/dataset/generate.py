"""Turn YAML question templates into concrete (question, SQL) pairs.

A template directory holds two kinds of documents: catalogues of slot values (``regions``,
``measures``, ``months`` …) and lists of ``templates``. This module loads both and renders the
placeholders of a template against one binding (one value per slot).

Placeholder syntax:

``{slot}``        the slot's ``label``, in the current language
``{slot.attr}``   an explicit attribute (``sql``, ``literal``, ``in``, ``divisor`` …)
``{slot-1}``      an integer slot shifted by a constant (winter starts the year before)

Any value written as ``{fr: ..., it: ...}`` resolves to the current language, wherever it
appears, and an attribute may itself contain placeholders (a season's ``start`` is built from
the bound year), which are rendered in a second pass.
"""

import itertools
import json
import math
import operator
import random
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

LANGUAGES = ("fr", "it")

# Public: the tests use it to compare the slots named by the hand-written variants.
PLACEHOLDER = re.compile(r"\{(\w+)(?:([+-]\d+))?(?:\.(\w+))?\}")
_MAX_DEPTH = 3  # a season's start is one level deep; more means a cycle


class TemplateError(ValueError):
    """Raised when a template or a catalogue is malformed."""


def load_templates(directory: Path) -> tuple[dict[str, list[dict]], list[dict]]:
    """Load every YAML file of ``directory`` into (catalogues by name, list of templates)."""
    catalogues: dict[str, list[dict]] = {}
    templates: list[dict] = []
    for path in sorted(directory.glob("*.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        for key, value in document.items():
            if key == "templates":
                templates.extend(value)
            elif key in catalogues:
                raise TemplateError(f"catalogue {key!r} is defined twice ({path.name})")
            else:
                catalogues[key] = value
    return catalogues, templates


def in_language(value: Any, lang: str) -> Any:
    """Resolve a ``{fr: ..., it: ...}`` map to one language; leave anything else untouched."""
    if isinstance(value, dict) and set(value) == set(LANGUAGES):
        return value[lang]
    return value


def render(text: str, binding: dict[str, Any], lang: str, _depth: int = 0) -> str:
    """Replace every placeholder of ``text`` using ``binding``, in language ``lang``."""

    def replace(match: re.Match[str]) -> str:
        name, offset, attribute = match.groups()
        if name not in binding:
            raise TemplateError(f"no slot {name!r} bound for {text!r}")
        value = binding[name]
        if attribute:
            if isinstance(value, dict) and set(value) == set(LANGUAGES):
                raise TemplateError(
                    f"slot {name!r} is a language map: write {{{name}}} and let it resolve"
                )
            if not isinstance(value, dict) or attribute not in value:
                raise TemplateError(f"slot {name!r} has no attribute {attribute!r}")
            resolved = in_language(value[attribute], lang)
        else:
            bare = value.get("label", value) if isinstance(value, dict) else value
            resolved = in_language(bare, lang)
        if isinstance(resolved, dict):
            raise TemplateError(f"slot {name!r} has no label in {lang!r}")
        if offset:
            if not isinstance(resolved, int):
                raise TemplateError(f"slot {name!r} is not an integer, cannot apply {offset}")
            resolved += int(offset)
        out = str(resolved)
        if "{" in out:
            if _depth >= _MAX_DEPTH:
                raise TemplateError(f"placeholders nested too deep in {text!r}")
            out = render(out, binding, lang, _depth + 1)
        return out

    return PLACEHOLDER.sub(replace, text)


_COMPARISON = re.compile(r"^\s*(\w+)\s*(>=|<=|==|!=|>|<)\s*(\w+)\s*$")
_OPERATORS = {
    ">": operator.gt, "<": operator.lt, ">=": operator.ge,
    "<=": operator.le, "==": operator.eq, "!=": operator.ne,
}  # fmt: skip
_MAX_COMBINATIONS = 200_000


def slot_id(value: Any) -> Any:
    """The identity of a slot value: what makes two combinations the same."""
    return value["id"] if isinstance(value, dict) and "id" in value else value


def sql_literal(text: str) -> str:
    """Quote a string for SQL, doubling inner quotes ("d'Azur" -> "'d''Azur'")."""
    escaped = text.replace("'", "''")
    return f"'{escaped}'"


def slot_candidates(spec: dict, catalogues: dict[str, list[dict]]) -> list[Any]:
    """Every value one slot can take: inline list, integer range, or a filtered catalogue."""
    if "values" in spec:
        return spec["values"]
    if "range" in spec:
        low, high = spec["range"]
        return list(range(low, high + 1))
    if "from" not in spec:
        raise TemplateError(f"slot spec needs 'values', 'range' or 'from': {spec}")
    if spec["from"] not in catalogues:
        raise TemplateError(f"unknown catalogue {spec['from']!r}")
    items = catalogues[spec["from"]]
    keep = spec.get("keep", {})
    for field, wanted in keep.items():
        allowed = wanted if isinstance(wanted, list) else [wanted]
        items = [item for item in items if item.get(field) in allowed]
    if not items:
        raise TemplateError(f"no value left in {spec['from']!r} after {keep}")
    return items


def satisfies(constraints: list[str], combination: dict[str, Any]) -> bool:
    """Check simple ``slot op slot|int`` constraints; anything else is a template error."""
    for constraint in constraints:
        match = _COMPARISON.match(constraint)
        if not match:
            raise TemplateError(f"unsupported constraint {constraint!r}")
        left, symbol, right = match.groups()
        values = []
        for token in (left, right):
            if token in combination:
                values.append(slot_id(combination[token]))
            elif token.lstrip("-").isdigit():
                values.append(int(token))
            else:
                raise TemplateError(f"constraint {constraint!r} names an unknown slot {token!r}")
        if not _OPERATORS[symbol](*values):
            return False
    return True


def combination_weight(combination: dict[str, Any]) -> float:
    """How likely one combination is, relative to the others: the product of its slot weights.

    A slot value may carry ``weight`` (default 1.0) to say how often a real user would ask for
    it: nobody asks for the minimum as often as for the peak, and TWh is the wrong scale for
    one region over one month. Weight 0 removes a value without deleting it.
    """
    weight = 1.0
    for value in combination.values():
        if isinstance(value, dict):
            weight *= float(value.get("weight", 1.0))
    return weight


def sample_combinations(
    template: dict, catalogues: dict[str, list[dict]], seed: int
) -> list[dict[str, Any]]:
    """Pick ``max_instances`` slot combinations, reproducibly and independently per template."""
    pools = {name: slot_candidates(spec, catalogues) for name, spec in template["slots"].items()}
    total = math.prod(len(pool) for pool in pools.values())
    if total > _MAX_COMBINATIONS:
        raise TemplateError(
            f"{template['template_id']}: {total:,} combinations, narrow a range or a catalogue"
        )
    combinations = [
        dict(zip(pools, values, strict=True)) for values in itertools.product(*pools.values())
    ]
    constraints = template.get("constraints", [])
    combinations = [c for c in combinations if satisfies(constraints, c)]
    # Seeding per template keeps the samples of the other templates stable when one is added.
    rng = random.Random(f"{seed}:{template['template_id']}")
    # Weighted sampling without replacement (Efraimidis-Spirakis): give each combination the
    # key -ln(u)/w and keep the smallest. With every weight at 1 this is a plain shuffle.
    keyed = []
    for combination in combinations:
        weight = combination_weight(combination)
        if weight > 0:
            keyed.append((-math.log(1.0 - rng.random()) / weight, combination))
    keyed.sort(key=lambda pair: pair[0])
    wanted = template.get("max_instances", len(keyed))
    return [combination for _, combination in keyed[:wanted]]


def capitalize_first(text: str) -> str:
    """Upper-case the first letter of a question and leave the rest alone.

    A question may start with a slot rendered in lower case ("l'éolien…", "en Bretagne…");
    ``str.capitalize`` would also lower-case the rest, including region names and "PACA".
    """
    return text[:1].upper() + text[1:]


def surface_form(value: Any, lang: str, rng: random.Random, alias_ratio: float) -> Any:
    """Flatten a catalogue entry that carries several surface forms per language.

    Regions are the case: the entry holds an ``id`` (the database value) and, per language, an
    ordered list of forms whose first element is canonical. An alias is picked with probability
    ``alias_ratio``, independently per language, so some questions say "PACA" instead of the
    official name and the model has to map it back. Source groups use the same shape for their
    synonyms ("pulita", "decarbonizzata"). Attributes outside the language lists (``sql``,
    ``weight`` …) are kept; the chosen form only supplies the words.
    """
    if not (isinstance(value, dict) and all(isinstance(value.get(x), list) for x in LANGUAGES)):
        return value
    forms = value[lang]
    use_alias = len(forms) > 1 and rng.random() < alias_ratio
    form = rng.choice(forms[1:]) if use_alias else forms[0]
    shared = {key: item for key, item in value.items() if key not in LANGUAGES}
    return {**shared, **form, "literal": sql_literal(value["id"])}


def build_binding(
    combination: dict[str, Any],
    template: dict,
    lang: str,
    rng: random.Random,
    alias_ratio: float,
) -> dict[str, Any]:
    """One combination, ready to render in one language: surfaces chosen, constants merged."""
    binding = {
        name: surface_form(value, lang, rng, alias_ratio) for name, value in combination.items()
    }
    return binding | template.get("constants", {})


def instances(
    template: dict,
    catalogues: dict[str, list[dict]],
    seed: int,
    alias_ratio: float = 0.25,
) -> Iterator[dict[str, Any]]:
    """Yield one record per (combination, language, question variant) of one template."""
    rng = random.Random(f"{seed}:surface:{template['template_id']}")
    for index, combination in enumerate(sample_combinations(template, catalogues, seed)):
        for lang in LANGUAGES:
            binding = build_binding(combination, template, lang, rng, alias_ratio)
            sql = render(template["sql"], binding, lang).strip()
            # The precondition never contains labels, so one rendering serves both languages.
            require = template.get("require")
            require_sql = render(require, binding, lang).strip() if require else None
            for variant, question in enumerate(template["questions"][lang]):
                yield {
                    "id": f"{template['template_id']}#{index:04d}#{lang}#{variant}",
                    "template_id": template["template_id"],
                    "family": template["family"],
                    "conventions": template["conventions"],
                    "lang": lang,
                    "variant": variant,
                    "question": capitalize_first(render(question, binding, lang)),
                    "sql": sql,
                    "require": require_sql,
                    "slots": {name: slot_id(value) for name, value in combination.items()},
                    "order_matters": template["result"].get("order_matters", False),
                }


def generate(template_dir: Path, out_path: Path, seed: int, alias_ratio: float = 0.25) -> int:
    """Write every template's instances to a JSONL file; return the number of records."""
    catalogues, templates = load_templates(template_dir)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with out_path.open("w", encoding="utf-8") as out:
        for template in templates:
            for record in instances(template, catalogues, seed, alias_ratio):
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
                written += 1
    return written
