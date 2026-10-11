"""Trivial baseline: predict, for every field, its most frequent gold value.

The modes are computed from the *development* gold only (data/exploratory/)
and the same prediction is used for every document of any split — on the
holdout it is never recomputed from the holdout's own gold, which would
leak the answers into the floor the models are compared against.

A mode that occurs only once is no mode at all — every value of a
unique-per-document field like `uid` is a one-off, and picking one of them
would be arbitrary — so such a field predicts null (or [] / {} for list and
object fields, matching the schema's "empty, not null" convention).
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from src import validate
from src.parse_responses import CORE_KEYS

REPO_ROOT = Path(__file__).resolve().parents[2]
DEV_GOLD_DIR = REPO_ROOT / "data" / "exploratory"


def empty_value(field: str) -> Any:
    kind = validate.FIELD_SPECS[field]["kind"]
    if kind.startswith("list_"):
        return []
    if kind == "dict":
        return {}
    return None


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def most_frequent_values(dev_records: list[dict[str, Any]]) -> dict[str, Any]:
    """{field: predicted value} for every key of the prompt's output
    contract. Ties go to the value whose canonical JSON sorts first, so the
    result never depends on file order."""
    modes: dict[str, Any] = {}
    for field in CORE_KEYS:
        counts = Counter(_canonical_json(record.get(field)) for record in dev_records)
        if not counts:
            modes[field] = empty_value(field)
            continue
        best_count = max(counts.values())
        if best_count < 2:
            modes[field] = empty_value(field)
            continue
        best = min(value for value, count in counts.items() if count == best_count)
        modes[field] = json.loads(best)
    return modes


def load_dev_records(dev_gold_dir: Path = DEV_GOLD_DIR) -> list[dict[str, Any]]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(dev_gold_dir.glob("*.json"))]
