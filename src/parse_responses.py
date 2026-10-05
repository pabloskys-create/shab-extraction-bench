"""Normalize raw model responses into form — never content — for scoring.

Reads `runs/<run_id>/raw/NNNN.json` (written by `run_eval.py`) and writes
`runs/<run_id>/parsed/NNNN.json`: the model's JSON with only shape-level
repairs applied (markdown fences, surrounding prose, trailing commas,
missing/invented keys, duplicate keys) plus a full record of everything
that was corrected. `raw/` is never modified. This module never scores
anything and never touches a scalar *value* — a capital printed as a
string stays a string; that normalization belongs to the evaluator.

Core rule: nothing is ever discarded silently. Every repair and every
dropped/added/renamed piece of structure is logged on the output record,
and `parse_summary.json` aggregates those logs across a run.

Key lists are sourced from validate.py / SCHEMA.md, never hand-duplicated
here — see CORE_KEYS, PERSON_KEYS_ORDER/PERSON_CHANGE_KEYS_ORDER (and the
drift-guard tests on both) and extras_registry_from_schema().
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from src import validate
from src.run_eval import ConfigError, select_doc_ids

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNS_DIR = REPO_ROOT / "runs"
SCHEMA_MD_PATH = REPO_ROOT / "SCHEMA.md"

# The prompt's output contract has exactly these 33 keys: every FIELD_SPECS
# entry except the six validate.py/SCHEMA.md define but the prompt doesn't
# ask the model to produce.
EXCLUDED_CORE_KEYS = frozenset({"doc_id", "schema_version", "language", "uncertain", "notes", "_verified"})
CORE_KEYS: tuple[str, ...] = tuple(k for k in validate.FIELD_SPECS if k not in EXCLUDED_CORE_KEYS)

# validate.PERSON_KEYS / PERSON_CHANGE_KEYS are frozensets — no order to
# borrow. These ordered tuples mirror SCHEMA.md's own Person/PersonChange
# examples; test_core_keys_match_prompt_output_contract's siblings assert
# their *sets* equal validate.py's, so a schema edit there breaks a test
# here instead of silently drifting out of sync.
PERSON_KEYS_ORDER: tuple[str, ...] = (
    "name",
    "nationality",
    "heimatort",
    "domicile",
    "role",
    "signature",
    "uid",
    "stammanteile",
)
PERSON_CHANGE_KEYS_ORDER: tuple[str, ...] = (
    "name_new",
    "name_previous",
    "domicile_new",
    "domicile_previous",
    "role_new",
    "role_previous",
    "signature_new",
    "signature_previous",
    "nationality_new",
    "nationality_previous",
    "heimatort_new",
    "heimatort_previous",
    "stammanteile_new",
    "stammanteile_previous",
)

PERSON_LIST_FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("persons_added", PERSON_KEYS_ORDER),
    ("persons_removed", PERSON_KEYS_ORDER),
    ("persons_changed", PERSON_CHANGE_KEYS_ORDER),
)

# Stashed by _dedupe_pairs_hook on every JSON object (at any nesting depth)
# that had a repeated key. Popped back out and relocated to the right part
# of the output record before anything else runs, so it's never mistaken
# for a model-invented key by the core/person/extras key checks.
_DUPLICATE_MARKER = "__shab_duplicate_keys__"

FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)```", re.DOTALL)
TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


# --- extras key registry, parsed from SCHEMA.md rather than hand-copied ---


def extras_registry_from_schema(schema_text: str) -> set[str]:
    """The `extras` keys SCHEMA.md documents as registered.

    Splits the "## `extras` — key registry" section into bullets on
    "\\n- ", then for each bullet reads only the text up to its first
    " — " — so an example value quoted inside a bullet's description
    (e.g. `` `opting-out` ``) is never mistaken for a registered key name.
    """
    section = schema_text.split("## `extras`")[1]
    keys: set[str] = set()
    for bullet in section.split("\n- ")[1:]:
        keys_part = bullet.split(" — ", 1)[0]
        keys.update(re.findall(r"`([a-zA-Z_]+)`", keys_part))
    return keys


def _load_extras_registry() -> set[str]:
    return extras_registry_from_schema(SCHEMA_MD_PATH.read_text(encoding="utf-8"))


# --- text-level repairs ---


def strip_markdown_fence(text: str) -> tuple[str, bool]:
    match = FENCE_RE.search(text)
    if match:
        return match.group(1), True
    return text, False


def extract_first_json_object(text: str) -> tuple[str | None, bool]:
    """The first balanced `{...}` substring, found with a string/escape-aware
    brace count (not a naive regex, so a literal brace inside a JSON string
    value can't desync the depth count).

    Returns (candidate, found_a_strict_subset) — found_a_strict_subset is
    True only when the candidate is not simply the whole (stripped) text,
    i.e. there really was something extracted away.
    """
    start = text.find("{")
    if start == -1:
        return None, False
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start : i + 1]
                return candidate, candidate != text.strip()
    return None, False


def remove_trailing_commas(text: str) -> tuple[str, int]:
    return TRAILING_COMMA_RE.subn(r"\1", text)


def _dedupe_pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """object_pairs_hook for json.loads: keeps the last value for a
    repeated key (same as json.loads's own default behaviour) but also
    records which keys repeated, under _DUPLICATE_MARKER."""
    obj: dict[str, Any] = {}
    counts: Counter[str] = Counter()
    for key, value in pairs:
        counts[key] += 1
        obj[key] = value
    duplicates = sorted(key for key, count in counts.items() if count > 1)
    if duplicates:
        obj[_DUPLICATE_MARKER] = duplicates
    return obj


def parse_json_object(response_text: str) -> tuple[dict | None, dict[str, Any], str | None]:
    """Run the three safe repairs (fence, then surrounding-text extraction,
    then trailing commas) and parse. Never closes truncated JSON, never
    adds quotes, never invents anything: if the text still isn't valid
    JSON after these, this returns (None, repairs-so-far, error-message).
    """
    repairs = {
        "markdown_fence_stripped": False,
        "extracted_from_surrounding_text": False,
        "trailing_commas_removed": 0,
    }

    text, fenced = strip_markdown_fence(response_text)
    repairs["markdown_fence_stripped"] = fenced

    candidate, found_subset = extract_first_json_object(text)
    if candidate is not None:
        if found_subset:
            repairs["extracted_from_surrounding_text"] = True
        text = candidate
    else:
        text = text.strip()

    text, n_commas = remove_trailing_commas(text)
    repairs["trailing_commas_removed"] = n_commas

    try:
        obj = json.loads(text, object_pairs_hook=_dedupe_pairs_hook)
    except json.JSONDecodeError as exc:
        return None, repairs, str(exc)

    if not isinstance(obj, dict):
        return None, repairs, f"parsed JSON is a {type(obj).__name__}, not an object"

    return obj, repairs, None


# --- structural normalization ---


def normalize_core_keys(
    obj: dict[str, Any], core_keys: tuple[str, ...] = CORE_KEYS
) -> tuple[dict[str, Any], list[str], list[str]]:
    """(normalized, missing_keys, extra_keys) — normalized has exactly
    `core_keys`, in that canonical order; a missing key is added as null,
    an extra (model-invented) key is dropped."""
    missing = [k for k in core_keys if k not in obj]
    extra = [k for k in obj if k not in core_keys]
    normalized = {k: obj.get(k, None) for k in core_keys}
    return normalized, missing, extra


def coerce_person_list_field(value: Any) -> tuple[list[Any], bool, str | None]:
    """(as_list, was_replaced, original_type_name).

    A proper list passes through untouched. null becomes []; a single
    object is wrapped in a one-element list rather than discarded;
    anything else becomes [] — all three logged by the caller under
    person_lists_replaced, with the original type name.
    """
    if isinstance(value, list):
        return value, False, None
    original_type = type(value).__name__
    if isinstance(value, dict):
        return [value], True, original_type
    return [], True, original_type


def normalize_person_list(
    items: list[Any], allowed_keys: tuple[str, ...]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """(normalized_items, issues, dropped).

    issues: one entry per item that had missing/extra/duplicate keys,
    naming its index. dropped: one entry per non-dict element (its index
    and Python type name) — removed, never silently kept as-is.
    """
    normalized: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []

    for index, item in enumerate(items):
        if not isinstance(item, dict):
            dropped.append({"index": index, "value_type": type(item).__name__})
            continue
        item = dict(item)
        duplicates = item.pop(_DUPLICATE_MARKER, [])
        missing = [k for k in allowed_keys if k not in item]
        extra = [k for k in item if k not in allowed_keys]
        normalized.append({k: item.get(k, None) for k in allowed_keys})
        if missing or extra or duplicates:
            issue: dict[str, Any] = {"index": index}
            if missing:
                issue["missing_keys"] = missing
            if extra:
                issue["extra_keys"] = extra
            if duplicates:
                issue["duplicate_keys"] = duplicates
            issues.append(issue)

    return normalized, issues, dropped


def normalize_extras(
    value: Any, registry: set[str]
) -> tuple[dict[str, Any], bool, list[str], list[str], list[str]]:
    """(kept, replaced, null_keys, unregistered_keys, duplicate_keys).

    Not an object at all -> {} and replaced=True. A null-valued key is
    dropped (logged in null_keys). A key not in SCHEMA.md's registry is
    kept — the evaluator decides what to do with it — but logged.
    """
    if not isinstance(value, dict):
        return {}, True, [], [], []
    value = dict(value)
    duplicates = value.pop(_DUPLICATE_MARKER, [])
    null_keys = [k for k, v in value.items() if v is None]
    kept = {k: v for k, v in value.items() if v is not None}
    unregistered = [k for k in kept if k not in registry]
    return kept, False, null_keys, unregistered, duplicates


def _empty_analysis() -> dict[str, Any]:
    return {
        "duplicate_keys": [],
        "missing_keys": [],
        "extra_keys": [],
        "person_list_issues": {"persons_added": [], "persons_removed": [], "persons_changed": []},
        "person_list_elements_dropped": {"persons_added": [], "persons_removed": [], "persons_changed": []},
        "person_lists_replaced": {},
        "extras_replaced": False,
        "extras_null_keys": [],
        "unregistered_extras_keys": [],
        "extras_duplicate_keys": [],
    }


def parse_response(raw: dict[str, Any], *, extras_registry: set[str] | None = None) -> dict[str, Any]:
    """Build one parsed/<doc_id>.json record from one loaded raw/<doc_id>.json."""
    if extras_registry is None:
        extras_registry = _load_extras_registry()

    base = {"doc_id": raw.get("doc_id")}

    if raw.get("status") == "error":
        return {
            **base,
            "parse_status": "no_response",
            "data": None,
            "error": raw.get("error"),
            "repairs": {
                "markdown_fence_stripped": False,
                "extracted_from_surrounding_text": False,
                "trailing_commas_removed": 0,
            },
            **_empty_analysis(),
        }

    response_text = raw.get("response_text") or ""
    obj, repairs, parse_error = parse_json_object(response_text)

    if obj is None:
        return {
            **base,
            "parse_status": "failed",
            "data": None,
            "error": {"message": parse_error},
            "repairs": repairs,
            **_empty_analysis(),
        }

    top_duplicates = obj.pop(_DUPLICATE_MARKER, [])
    normalized, missing_keys, extra_keys = normalize_core_keys(obj)

    person_list_issues: dict[str, list[dict[str, Any]]] = {}
    person_list_dropped: dict[str, list[dict[str, Any]]] = {}
    person_lists_replaced: dict[str, str] = {}

    for field_name, allowed_keys in PERSON_LIST_FIELDS:
        as_list, replaced, original_type = coerce_person_list_field(normalized[field_name])
        if replaced:
            person_lists_replaced[field_name] = original_type
        items, issues, dropped = normalize_person_list(as_list, allowed_keys)
        normalized[field_name] = items
        person_list_issues[field_name] = issues
        person_list_dropped[field_name] = dropped

    extras_kept, extras_replaced, extras_null_keys, unregistered, extras_duplicates = normalize_extras(
        normalized["extras"], extras_registry
    )
    normalized["extras"] = extras_kept

    return {
        **base,
        "parse_status": "ok",
        "data": normalized,
        "error": None,
        "repairs": repairs,
        "duplicate_keys": top_duplicates,
        "missing_keys": missing_keys,
        "extra_keys": extra_keys,
        "person_list_issues": person_list_issues,
        "person_list_elements_dropped": person_list_dropped,
        "person_lists_replaced": person_lists_replaced,
        "extras_replaced": extras_replaced,
        "extras_null_keys": extras_null_keys,
        "unregistered_extras_keys": unregistered,
        "extras_duplicate_keys": extras_duplicates,
    }


def build_parse_summary(records: list[dict[str, Any]], run_id: str) -> dict[str, Any]:
    status_counts = {"ok": 0, "failed": 0, "no_response": 0}
    repairs_total: Counter[str] = Counter()
    missing_keys_counts: Counter[str] = Counter()
    extra_keys_counts: Counter[str] = Counter()
    extras_null_keys_counts: Counter[str] = Counter()
    unregistered_extras_keys_counts: Counter[str] = Counter()

    for record in records:
        status_counts[record["parse_status"]] += 1
        for name, value in record["repairs"].items():
            repairs_total[name] += int(value) if isinstance(value, bool) else value
        missing_keys_counts.update(record["missing_keys"])
        extra_keys_counts.update(record["extra_keys"])
        extras_null_keys_counts.update(record["extras_null_keys"])
        unregistered_extras_keys_counts.update(record["unregistered_extras_keys"])

    return {
        "run_id": run_id,
        "total": len(records),
        "status_counts": status_counts,
        "repairs_total": dict(repairs_total),
        "missing_keys_counts": dict(missing_keys_counts),
        "extra_keys_counts": dict(extra_keys_counts),
        "extras_null_keys_counts": dict(extras_null_keys_counts),
        "unregistered_extras_keys_counts": dict(unregistered_extras_keys_counts),
    }


# --- CLI ---


def discover_raw_doc_ids(raw_dir: Path) -> list[str]:
    return sorted(p.stem for p in raw_dir.glob("*.json"))


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Normalize raw model responses into form-corrected JSON (never scores them)."
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR)
    parser.add_argument("--docs", default=None, help="Comma-separated doc_ids, e.g. 0013,0019")
    parser.add_argument("--limit", type=int, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    run_dir = args.runs_dir / args.run_id
    raw_dir = run_dir / "raw"

    if not raw_dir.is_dir():
        print(f"error: no raw/ directory at {raw_dir}", file=sys.stderr)
        return 2

    all_ids = discover_raw_doc_ids(raw_dir)
    try:
        doc_ids = select_doc_ids(all_ids, args.docs, args.limit)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    extras_registry = _load_extras_registry()
    parsed_dir = run_dir / "parsed"
    parsed_dir.mkdir(parents=True, exist_ok=True)

    records = []
    for doc_id in doc_ids:
        raw = json.loads((raw_dir / f"{doc_id}.json").read_text(encoding="utf-8"))
        record = parse_response(raw, extras_registry=extras_registry)
        (parsed_dir / f"{doc_id}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        records.append(record)
        print(f"{doc_id}: {record['parse_status']}")

    summary = build_parse_summary(records, args.run_id)
    (run_dir / "parse_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
