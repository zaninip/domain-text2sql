"""List the closest question variants of different templates, to review contrast pairs by eye.

The build already stops when two templates render the same question with different SQL. This
report looks one step earlier: variants that are *almost* the same, where one word decides the
answer ("consommation" or "production", "de la France" or "quotidienne de la France"). Each pair
it shows should differ by a word that `must_say` / `must_not_say` guards, or by a slot.

Run with ``make similar`` (optionally ``ARGS="--only rate_ --top 30"``).
"""

import argparse
import itertools
import re
from difflib import SequenceMatcher
from pathlib import Path

import yaml

from t2sql.dataset.generate import LANGUAGES, PLACEHOLDER, load_templates

ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = ROOT / "configs" / "dataset.yaml"


def question_tokens(text: str) -> list[str]:
    """Lower-cased words, with each placeholder reduced to its slot: "{region.in}" -> "<region>".

    Comparing templates rather than rendered questions ignores the slot values, which differ
    between any two instances and would hide the wording they share.
    """
    text = PLACEHOLDER.sub(lambda match: f" <{match.group(1)}> ", text.lower())
    return re.findall(r"<\w+>|[\w']+", text)


def closest_pairs(templates: list[dict], only: str = "") -> list[tuple[float, str, tuple, tuple]]:
    """For each pair of templates and each language, their two most similar variants.

    Returns (score, lang, (template_id, index, text), (template_id, index, text)), best first.
    ``only`` keeps the pairs where one of the two template ids starts with it.
    """
    best: dict[tuple[str, str, str], tuple] = {}
    for lang in LANGUAGES:
        variants = [
            (template["template_id"], index, text, question_tokens(text))
            for template in templates
            for index, text in enumerate(template["questions"][lang])
        ]
        for a, b in itertools.combinations(variants, 2):
            if a[0] == b[0] or (only and not (a[0].startswith(only) or b[0].startswith(only))):
                continue
            score = SequenceMatcher(None, a[3], b[3], autojunk=False).ratio()
            key = (lang, *sorted((a[0], b[0])))
            if key not in best or score > best[key][0]:
                best[key] = (score, lang, a[:3], b[:3])
    return sorted(best.values(), key=lambda pair: -pair[0])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", default="", help="template id prefix, e.g. rate_")
    parser.add_argument("--top", type=int, default=20, help="number of pairs to print")
    args = parser.parse_args()
    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    _, templates = load_templates(ROOT / config["paths"]["templates"])
    for score, lang, a, b in closest_pairs(templates, args.only)[: args.top]:
        print(f"{score:.2f} [{lang}] {a[0]}#{a[1]}  vs  {b[0]}#{b[1]}")
        print(f"       {a[2]}")
        print(f"       {b[2]}")


if __name__ == "__main__":
    main()
