"""Score a run's parsed responses against the gold standard.

Reads `runs/<run_id>/run.json` (for the split and the doc_ids) and
`runs/<run_id>/parsed/NNNN.json` (written by `parse_responses.py`), and
the gold of that split: `data/exploratory/` for dev, `data/holdout/gold/`
for holdout. Gold is opened read-only; nothing is ever written outside
`results/<run_id>/`, which receives:

    summary.json   every metric, with 95% bootstrap intervals for the
                   headline ones
    fields.csv     one row per (document, field[, item, attribute]) with
                   the gold value, the prediction and the outcome — the
                   input for failure analysis

What is scored is CLAUDE.md "Benchmark design (decided)": every data
field, act_type, act_subtypes and the three person lists; `extras`
separately. Decisions taken for this module (see the design discussion
of 2026-10-11):

* Scalars get five outcomes — correct, correct_null, omission,
  hallucination, wrong — in two comparison modes. *exact* compares JSON
  values as they are (100000 and 100000.0 differ, as do "a" and "A").
  *normalized* applies Unicode NFC, strips and collapses whitespace,
  casefolds, and compares numbers by value — also against a string
  holding a clean number ("100000.00", but not "100'000" or "CHF 100000").
  Nothing more: "von Schangnau" vs "Schangnau" stays wrong. A bool is
  never equal to a number.
* A document whose parse_status is not "ok" gets a sixth outcome,
  `no_output`, on every field. It counts against accuracy but is kept out
  of the five so that format failures do not inflate omissions.
* A field listed in the gold record's `uncertain` is excluded for that
  document only — for a list field (persons_*, act_subtypes) or `extras`,
  the whole list/object.
* Set fields (act_subtypes, alternative_names) use the five outcomes with
  set equality, an empty list counting as null. act_subtypes also gets
  label-level precision/recall/F1, micro and macro.
* Persons are matched one-to-one by normalized name (`name_new` for
  persons_changed), in order of appearance. A matched pair's other
  attributes get the five outcomes, exact and normalized.
* extras: key precision/recall/F1 (a key outside SCHEMA.md's registry is
  always a false positive) and value accuracy on the shared keys. `zweck`
  is a prefix match; `loeschung_reason` a containment match (see
  zweck_match / loeschung_reason_match).
* Intervals: percentile bootstrap resampling *documents*, never fields.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import operator
import random
import re
import statistics
import sys
import unicodedata
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from src import parse_responses, validate
from src.run_eval import git_commit_hash, git_is_dirty

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNS_DIR = REPO_ROOT / "runs"
DEFAULT_RESULTS_DIR = REPO_ROOT / "results"
GOLD_DIRS = {
    "dev": REPO_ROOT / "data" / "exploratory",
    "holdout": REPO_ROOT / "data" / "holdout" / "gold",
}

DEFAULT_ITERATIONS = 10_000
DEFAULT_SEED = 20261011

# A predicted zweck must share at least this many characters with the gold
# one (or all of the gold one, if shorter) — otherwise "Die" would be a
# prefix of every purpose that starts with "Die".
ZWECK_MIN_PREFIX = 100

CORE_KEYS = parse_responses.CORE_KEYS
SCALAR_KINDS = frozenset({"str", "date", "int", "number"})
SCALAR_FIELDS: tuple[str, ...] = tuple(k for k in CORE_KEYS if validate.FIELD_SPECS[k]["kind"] in SCALAR_KINDS)
SET_FIELDS: tuple[str, ...] = tuple(k for k in CORE_KEYS if validate.FIELD_SPECS[k]["kind"] == "list_str")
# Scalars and sets together: the fields that get the five outcomes at
# document level and make up the headline core accuracy.
FIELD_LEVEL_FIELDS: tuple[str, ...] = tuple(k for k in CORE_KEYS if k in SCALAR_FIELDS or k in SET_FIELDS)

# The field-level fields split in two fixed groups, decided on 2026-10-11
# before any model was scored (CLAUDE.md, "Benchmark design (decided)").
# HEADER_FIELDS are the 19 that prefill.py fills in at least one dev
# document: formulaic header/identity data, where the rule baseline is
# already perfect on every value it fills. COMPREHENSION_FIELDS are the
# ones prefill.py never fills: they require reading what the act changes.
# Reporting the two apart keeps the easy header from masking the hard part.
HEADER_FIELDS: tuple[str, ...] = (
    "act_type",
    "company_name_full",
    "company_name_base",
    "status_suffix",
    "alternative_names",
    "uid",
    "legal_form",
    "seat_municipality",
    "seat_canton",
    "address_care_of",
    "address_street",
    "address_postcode",
    "address_municipality",
    "tagesregister_nr",
    "tagesregister_date",
    "prior_publication_shab_nr",
    "prior_publication_date",
    "prior_publication_id",
    "authority",
)
COMPREHENSION_FIELDS: tuple[str, ...] = (
    "act_subtypes",
    "company_name_new",
    "company_name_previous",
    "act_date",
    "canton_previous",
    "canton_new",
    "capital_new_chf",
    "capital_previous_chf",
    "domicile_new",
    "domicile_previous",
)
FIELD_GROUPS: dict[str, tuple[str, ...]] = {
    "comprehension": COMPREHENSION_FIELDS,
    "header": HEADER_FIELDS,
}

PERSON_NAME_KEYS = {
    "persons_added": "name",
    "persons_removed": "name",
    "persons_changed": "name_new",
}
PERSON_LIST_FIELDS = parse_responses.PERSON_LIST_FIELDS

CORRECT = "correct"
CORRECT_NULL = "correct_null"
OMISSION = "omission"
HALLUCINATION = "hallucination"
WRONG = "wrong"
NO_OUTPUT = "no_output"
OUTCOMES = (CORRECT, CORRECT_NULL, OMISSION, HALLUCINATION, WRONG, NO_OUTPUT)
EXCLUDED = "excluded_uncertain"

# Outcomes for list items (persons, extras keys) rather than values.
MATCHED = "matched"
MISSED = "missed"
SPURIOUS = "spurious"
UNREGISTERED = "spurious_unregistered"

MODES = ("exact", "normalized")

_WHITESPACE_RE = re.compile(r"\s+")
# A string that is a bare number: digits, at most one decimal point. No
# thousands separators, no currency — "100'000" and "CHF 100000" are not.
_CLEAN_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


class EvaluationError(Exception):
    """The run cannot be scored as specified (missing parsed file, missing
    gold, unknown split). Always fatal: a silently skipped document would
    change the denominator of every number reported."""


# --- value comparison ---


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return unicodedata.normalize("NFC", text.casefold())


def canonical(value: Any, *, normalized: bool) -> Any:
    """A hashable, type-tagged form of a JSON value for comparison.

    The tags keep bool apart from numbers (in Python, True == 1). In exact
    mode int and float are also kept apart; in normalized mode numbers
    compare by value and strings are normalize_text()-ed.
    """
    if value is None:
        return ("null",)
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, (int, float)):
        if normalized:
            return ("num", float(value))
        return (type(value).__name__, value)
    if isinstance(value, str):
        return ("str", normalize_text(value) if normalized else value)
    if isinstance(value, list):
        return ("list", tuple(canonical(v, normalized=normalized) for v in value))
    if isinstance(value, dict):
        return (
            "dict",
            tuple(sorted((k, canonical(v, normalized=normalized)) for k, v in value.items())),
        )
    return ("other", repr(value))


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _clean_number(text: str) -> float | None:
    text = text.strip()
    return float(text) if _CLEAN_NUMBER_RE.fullmatch(text) else None


def values_equal(gold: Any, pred: Any, *, normalized: bool) -> bool:
    """Exact or normalized equality (see canonical()). In normalized mode
    only, a number and a string holding a clean number ("100000",
    "100000.00") are equal when their values are. Two strings are still
    compared as text, so "007" and "7" stay different."""
    if normalized:
        for number, text in ((gold, pred), (pred, gold)):
            if _is_number(number) and isinstance(text, str):
                parsed = _clean_number(text)
                return parsed is not None and parsed == float(number)
    return canonical(gold, normalized=normalized) == canonical(pred, normalized=normalized)


def classify(gold: Any, pred: Any, *, equal: bool) -> str:
    """One of the five outcomes, given whether the two values already
    compared equal. None is the only null here; callers map [] to None for
    set fields."""
    if gold is None and pred is None:
        return CORRECT_NULL
    if gold is None:
        return HALLUCINATION
    if pred is None:
        return OMISSION
    return CORRECT if equal else WRONG


def _as_set(value: Any, *, normalized: bool) -> frozenset:
    """A list field as a set of canonical elements. A non-list, non-null
    prediction (e.g. a bare string where a list was asked for) is scored as
    a one-element list rather than repaired or discarded."""
    if value is None:
        return frozenset()
    items = value if isinstance(value, list) else [value]
    return frozenset(canonical(v, normalized=normalized) for v in items)


def classify_set(gold: Any, pred: Any, *, normalized: bool) -> str:
    gold_set = _as_set(gold, normalized=normalized)
    pred_set = _as_set(pred, normalized=normalized)
    return classify(gold_set or None, pred_set or None, equal=gold_set == pred_set)


# --- special extras matchers ---


def _strip_trailing_ellipsis(text: str) -> str:
    text = text.rstrip()
    for ellipsis in ("…", "..."):
        if text.endswith(ellipsis):
            return text[: -len(ellipsis)].rstrip()
    return text


def zweck_match(gold: Any, pred: Any, *, normalized: bool) -> bool:
    """Prefix match: after removing a trailing "…" or "..." from both, the
    shorter must be a prefix of the longer, and must be at least
    ZWECK_MIN_PREFIX characters long — or the whole gold zweck, when that
    is shorter."""
    if not (isinstance(gold, str) and isinstance(pred, str)):
        return values_equal(gold, pred, normalized=normalized)
    if normalized:
        gold, pred = normalize_text(gold), normalize_text(pred)
    gold, pred = _strip_trailing_ellipsis(gold), _strip_trailing_ellipsis(pred)
    shorter = min(len(gold), len(pred))
    if shorter < min(ZWECK_MIN_PREFIX, len(gold)):
        return False
    return gold[:shorter] == pred[:shorter]


def loeschung_reason_match(gold: Any, pred: Any, *, normalized: bool) -> bool:
    """Either string contains the other."""
    if not (isinstance(gold, str) and isinstance(pred, str)):
        return values_equal(gold, pred, normalized=normalized)
    if normalized:
        gold, pred = normalize_text(gold), normalize_text(pred)
    if not gold or not pred:
        return gold == pred
    return gold in pred or pred in gold


EXTRAS_MATCHERS: dict[str, Callable[..., bool]] = {
    "zweck": zweck_match,
    "loeschung_reason": loeschung_reason_match,
}


def extras_value_match(key: str, gold: Any, pred: Any, *, normalized: bool) -> bool:
    matcher = EXTRAS_MATCHERS.get(key, values_equal)
    return matcher(gold, pred, normalized=normalized)


# --- persons ---


def match_persons(
    gold: list[dict], pred: list[dict], name_key: str
) -> tuple[list[tuple[dict, dict]], list[dict], list[dict]]:
    """(pairs, unmatched_gold, unmatched_pred).

    One-to-one by normalized name; each gold person, in order, takes the
    first still-unused prediction with the same name, so namesakes pair up
    in order of appearance. A prediction with no name never matches.
    """
    used = [False] * len(pred)
    pairs: list[tuple[dict, dict]] = []
    unmatched_gold: list[dict] = []
    for gold_person in gold:
        gold_name = gold_person.get(name_key)
        found = None
        for i, pred_person in enumerate(pred):
            if used[i] or not isinstance(pred_person.get(name_key), str) or not isinstance(gold_name, str):
                continue
            if normalize_text(pred_person[name_key]) == normalize_text(gold_name):
                found = i
                break
        if found is None:
            unmatched_gold.append(gold_person)
        else:
            used[found] = True
            pairs.append((gold_person, pred[found]))
    unmatched_pred = [p for i, p in enumerate(pred) if not used[i]]
    return pairs, unmatched_gold, unmatched_pred


# --- one document ---


def _json_cell(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _row(doc_id: str, field: str, gold: Any, pred: Any, exact: str, norm: str, item: str = "", attribute: str = "") -> dict:
    return {
        "doc_id": doc_id,
        "field": field,
        "item": item,
        "attribute": attribute,
        "gold": _json_cell(gold),
        "prediction": _json_cell(pred),
        "outcome_exact": exact,
        "outcome_normalized": norm,
    }


def _person_label(person: dict, name_key: str) -> str:
    name = person.get(name_key)
    return name if isinstance(name, str) else _json_cell(name)


def score_document(
    gold: dict[str, Any], parsed: dict[str, Any], extras_registry: set[str]
) -> tuple[list[dict], Counter]:
    """(rows, tally) for one document.

    The tally is a flat Counter with tuple keys — summed across documents
    for point estimates, and kept per document for the bootstrap:
        ("field", field, mode, outcome)
        ("excluded", field)
        ("subtype", label, "tp"|"fp"|"fn")
        ("persons", list_field, "tp"|"fp"|"fn")
        ("person_attr", list_field, attribute, mode, outcome)
        ("extras_key", "tp"|"fp"|"fn")
        ("extras_key_unregistered",)
        ("extras_value", key, mode, "correct"|"wrong")
        ("parse_status", status)
    """
    doc_id = gold["doc_id"]
    uncertain = set(gold.get("uncertain") or [])
    has_output = parsed["parse_status"] == "ok"
    pred = parsed["data"] if has_output else {}
    rows: list[dict] = []
    tally: Counter = Counter()
    tally[("parse_status", parsed["parse_status"])] += 1

    for field in FIELD_LEVEL_FIELDS:
        gold_value = gold.get(field)
        pred_value = pred.get(field)
        if field in uncertain:
            tally[("excluded", field)] += 1
            rows.append(_row(doc_id, field, gold_value, pred_value, EXCLUDED, EXCLUDED))
            continue
        outcomes = {}
        for mode in MODES:
            normalized = mode == "normalized"
            if not has_output:
                outcomes[mode] = NO_OUTPUT
                # Remembered so value/null accuracy can charge it to the
                # right denominator.
                gold_is_null = not _as_set(gold_value, normalized=False) if field in SET_FIELDS else gold_value is None
                tally[("no_output_gold", field, mode, "null" if gold_is_null else "value")] += 1
            elif field in SET_FIELDS:
                outcomes[mode] = classify_set(gold_value, pred_value, normalized=normalized)
            else:
                equal = values_equal(gold_value, pred_value, normalized=normalized)
                outcomes[mode] = classify(gold_value, pred_value, equal=equal)
            tally[("field", field, mode, outcomes[mode])] += 1
        rows.append(_row(doc_id, field, gold_value, pred_value, outcomes["exact"], outcomes["normalized"]))

    if "act_subtypes" not in uncertain:
        gold_labels = _as_set(gold.get("act_subtypes"), normalized=True)
        pred_labels = _as_set(pred.get("act_subtypes"), normalized=True)
        for label in gold_labels | pred_labels:
            name = label[1] if label[0] == "str" else repr(label)
            if label in gold_labels and label in pred_labels:
                tally[("subtype", name, "tp")] += 1
            elif label in gold_labels:
                tally[("subtype", name, "fn")] += 1
            else:
                tally[("subtype", name, "fp")] += 1

    for field, keys in PERSON_LIST_FIELDS:
        name_key = PERSON_NAME_KEYS[field]
        gold_people = gold.get(field) or []
        pred_people = pred.get(field) or []
        if field in uncertain:
            tally[("excluded", field)] += 1
            rows.append(_row(doc_id, field, gold_people, pred_people, EXCLUDED, EXCLUDED))
            continue
        if not has_output:
            for person in gold_people:
                tally[("persons", field, "fn")] += 1
                rows.append(_row(doc_id, field, person, None, NO_OUTPUT, NO_OUTPUT, item=_person_label(person, name_key)))
            continue
        pairs, missed, spurious = match_persons(gold_people, pred_people, name_key)
        tally[("persons", field, "tp")] += len(pairs)
        tally[("persons", field, "fn")] += len(missed)
        tally[("persons", field, "fp")] += len(spurious)
        for gold_person, pred_person in pairs:
            label = _person_label(gold_person, name_key)
            rows.append(_row(doc_id, field, gold_person.get(name_key), pred_person.get(name_key), MATCHED, MATCHED, item=label))
            for attribute in keys:
                if attribute == name_key:
                    continue
                g, p = gold_person.get(attribute), pred_person.get(attribute)
                outcomes = {}
                for mode in MODES:
                    equal = values_equal(g, p, normalized=mode == "normalized")
                    outcomes[mode] = classify(g, p, equal=equal)
                    tally[("person_attr", field, attribute, mode, outcomes[mode])] += 1
                rows.append(_row(doc_id, field, g, p, outcomes["exact"], outcomes["normalized"], item=label, attribute=attribute))
        for person in missed:
            rows.append(_row(doc_id, field, person, None, MISSED, MISSED, item=_person_label(person, name_key)))
        for person in spurious:
            rows.append(_row(doc_id, field, None, person, SPURIOUS, SPURIOUS, item=_person_label(person, name_key)))

    gold_extras = gold.get("extras") or {}
    pred_extras = pred.get("extras") or {}
    if "extras" in uncertain:
        tally[("excluded", "extras")] += 1
        rows.append(_row(doc_id, "extras", gold_extras, pred_extras, EXCLUDED, EXCLUDED))
    elif not has_output:
        for key, value in gold_extras.items():
            tally[("extras_key", "fn")] += 1
            rows.append(_row(doc_id, "extras", value, None, NO_OUTPUT, NO_OUTPUT, item=key))
    else:
        for key, gold_value in gold_extras.items():
            if key in pred_extras and key in extras_registry:
                tally[("extras_key", "tp")] += 1
                outcomes = {}
                for mode in MODES:
                    match = extras_value_match(key, gold_value, pred_extras[key], normalized=mode == "normalized")
                    outcomes[mode] = CORRECT if match else WRONG
                    tally[("extras_value", key, mode, outcomes[mode])] += 1
                rows.append(_row(doc_id, "extras", gold_value, pred_extras[key], outcomes["exact"], outcomes["normalized"], item=key))
            else:
                tally[("extras_key", "fn")] += 1
                rows.append(_row(doc_id, "extras", gold_value, pred_extras.get(key), MISSED, MISSED, item=key))
        for key, pred_value in pred_extras.items():
            if key in extras_registry and key in gold_extras:
                continue
            tally[("extras_key", "fp")] += 1
            outcome = SPURIOUS if key in extras_registry else UNREGISTERED
            if outcome == UNREGISTERED:
                tally[("extras_key_unregistered",)] += 1
            rows.append(_row(doc_id, "extras", gold_extras.get(key), pred_value, outcome, outcome, item=key))

    return rows, tally


# --- metrics over a tally ---

Ratio = tuple[float, float]  # (numerator, denominator)


def _sum_where(tally: Counter, predicate: Callable[[tuple], bool]) -> int:
    return sum(count for key, count in tally.items() if predicate(key))


def field_accuracy(tally: Counter, mode: str, fields: tuple[str, ...] | None = None) -> Ratio:
    def scored(key: tuple) -> bool:
        return key[0] == "field" and key[2] == mode and (fields is None or key[1] in fields)

    correct = _sum_where(tally, lambda k: scored(k) and k[3] in (CORRECT, CORRECT_NULL))
    return correct, _sum_where(tally, scored)


def value_accuracy(tally: Counter, mode: str, fields: tuple[str, ...]) -> Ratio:
    """correct / (correct + omission + wrong), only where the gold has a
    value; a no_output document counts as a failure."""
    correct = sum(tally[("field", f, mode, CORRECT)] for f in fields)
    failed = sum(
        tally[("field", f, mode, OMISSION)] + tally[("field", f, mode, WRONG)] + tally[("no_output_gold", f, mode, "value")]
        for f in fields
    )
    return correct, correct + failed


def null_accuracy(tally: Counter, mode: str, fields: tuple[str, ...]) -> Ratio:
    """correct_null / (correct_null + hallucination), only where the gold
    is null; a no_output document counts as a failure."""
    correct = sum(tally[("field", f, mode, CORRECT_NULL)] for f in fields)
    failed = sum(tally[("field", f, mode, HALLUCINATION)] + tally[("no_output_gold", f, mode, "null")] for f in fields)
    return correct, correct + failed


def f1_ratio(tp: int, fp: int, fn: int) -> Ratio:
    """F1 as a ratio of sums, 2TP / (2TP + FP + FN), so it bootstraps like
    any other ratio."""
    return 2 * tp, 2 * tp + fp + fn


def prf(tp: int, fp: int, fn: int) -> dict[str, Any]:
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": _div(tp, tp + fp),
        "recall": _div(tp, tp + fn),
        "f1": _div(*f1_ratio(tp, fp, fn)),
    }


def _div(num: float, den: float) -> float | None:
    return num / den if den else None


def _persons_counts(tally: Counter, field: str) -> tuple[int, int, int]:
    return tuple(tally[("persons", field, kind)] for kind in ("tp", "fp", "fn"))  # type: ignore[return-value]


def _person_attr_accuracy(tally: Counter, field: str, mode: str) -> Ratio:
    def scored(key: tuple) -> bool:
        return key[0] == "person_attr" and key[1] == field and key[3] == mode

    correct = _sum_where(tally, lambda k: scored(k) and k[4] in (CORRECT, CORRECT_NULL))
    return correct, _sum_where(tally, scored)


def _subtype_counts(tally: Counter) -> tuple[int, int, int]:
    return tuple(  # type: ignore[return-value]
        _sum_where(tally, lambda k, kind=kind: k[0] == "subtype" and k[2] == kind) for kind in ("tp", "fp", "fn")
    )


def _extras_value_accuracy(tally: Counter, mode: str) -> Ratio:
    def scored(key: tuple) -> bool:
        return key[0] == "extras_value" and key[2] == mode

    return _sum_where(tally, lambda k: scored(k) and k[3] == CORRECT), _sum_where(tally, scored)


def headline_metrics() -> dict[str, Callable[[Counter], Ratio]]:
    """The metrics that get a bootstrap interval, each a function from a
    tally to (numerator, denominator)."""
    metrics: dict[str, Callable[[Counter], Ratio]] = {
        "core_accuracy_exact": lambda t: field_accuracy(t, "exact"),
        "core_accuracy_normalized": lambda t: field_accuracy(t, "normalized"),
    }
    for group, group_fields in FIELD_GROUPS.items():
        metrics[f"group_accuracy_normalized:{group}"] = lambda t, g=group_fields: field_accuracy(t, "normalized", g)
        metrics[f"group_value_accuracy_normalized:{group}"] = lambda t, g=group_fields: value_accuracy(t, "normalized", g)
        metrics[f"group_null_accuracy_normalized:{group}"] = lambda t, g=group_fields: null_accuracy(t, "normalized", g)
    for field in FIELD_LEVEL_FIELDS:
        metrics[f"field_accuracy_normalized:{field}"] = lambda t, f=field: field_accuracy(t, "normalized", (f,))
        metrics[f"value_accuracy_normalized:{field}"] = lambda t, f=field: value_accuracy(t, "normalized", (f,))
        metrics[f"null_accuracy_normalized:{field}"] = lambda t, f=field: null_accuracy(t, "normalized", (f,))
    for field, _keys in PERSON_LIST_FIELDS:
        metrics[f"persons_found_f1:{field}"] = lambda t, f=field: f1_ratio(*_persons_counts(t, f))
        metrics[f"persons_attribute_accuracy_normalized:{field}"] = lambda t, f=field: _person_attr_accuracy(
            t, f, "normalized"
        )
    metrics["act_subtypes_micro_f1"] = lambda t: f1_ratio(*_subtype_counts(t))
    metrics["extras_key_f1"] = lambda t: f1_ratio(t[("extras_key", "tp")], t[("extras_key", "fp")], t[("extras_key", "fn")])
    metrics["extras_value_accuracy_normalized"] = lambda t: _extras_value_accuracy(t, "normalized")
    metrics["parse_failure_rate"] = lambda t: (
        t[("parse_status", "failed")] + t[("parse_status", "no_response")],
        sum(c for k, c in t.items() if k[0] == "parse_status"),
    )
    return metrics


# --- bootstrap ---


def bootstrap_ci(
    per_doc: list[Counter],
    metrics: dict[str, Callable[[Counter], Ratio]],
    *,
    iterations: int,
    seed: int,
) -> dict[str, dict[str, Any]]:
    """95% percentile intervals, resampling whole documents with
    replacement. A resample whose denominator is 0 for a metric is skipped
    for that metric; `n_valid` reports how many were usable."""
    n = len(per_doc)
    vectors = {}
    for name, fn in metrics.items():
        ratios = [fn(t) for t in per_doc]
        vectors[name] = ([r[0] for r in ratios], [r[1] for r in ratios])

    rng = random.Random(seed)
    samples: dict[str, list[float]] = {name: [] for name in metrics}
    indices = range(n)
    for _ in range(iterations):
        weights = [0] * n
        for i in rng.choices(indices, k=n):
            weights[i] += 1
        for name, (nums, dens) in vectors.items():
            den = sum(map(operator.mul, weights, dens))
            if den:
                samples[name].append(sum(map(operator.mul, weights, nums)) / den)

    intervals = {}
    for name, values in samples.items():
        if len(values) < 2:
            intervals[name] = {"ci_low": None, "ci_high": None, "n_valid": len(values)}
            continue
        cuts = statistics.quantiles(values, n=40, method="inclusive")
        intervals[name] = {"ci_low": cuts[0], "ci_high": cuts[-1], "n_valid": len(values)}
    return intervals


# --- summary ---


def _outcome_block(tally: Counter, key_prefix: tuple, mode: str) -> dict[str, Any]:
    counts = {outcome: tally[(*key_prefix, mode, outcome)] for outcome in OUTCOMES}
    scored = sum(counts.values())
    return {**counts, "accuracy": _div(counts[CORRECT] + counts[CORRECT_NULL], scored)}


def _accuracy_trio(tally: Counter, mode: str, fields: tuple[str, ...]) -> dict[str, float | None]:
    return {
        "accuracy": _div(*field_accuracy(tally, mode, fields)),
        "value_accuracy": _div(*value_accuracy(tally, mode, fields)),
        "null_accuracy": _div(*null_accuracy(tally, mode, fields)),
    }


def build_summary(
    tally: Counter,
    per_doc: list[Counter],
    parsed_records: list[dict],
    *,
    run_info: dict[str, Any],
    iterations: int,
    seed: int,
) -> dict[str, Any]:
    metrics = headline_metrics()
    intervals = bootstrap_ci(per_doc, metrics, iterations=iterations, seed=seed)
    headline = {}
    for name, fn in metrics.items():
        num, den = fn(tally)
        headline[name] = {"value": _div(num, den), **intervals[name], "numerator": num, "denominator": den}

    groups = {
        group: {"fields": list(group_fields), **{mode: _accuracy_trio(tally, mode, group_fields) for mode in MODES}}
        for group, group_fields in FIELD_GROUPS.items()
    }

    fields = {}
    for field in FIELD_LEVEL_FIELDS:
        fields[field] = {
            "group": next(g for g, fs in FIELD_GROUPS.items() if field in fs),
            "excluded_uncertain": tally[("excluded", field)],
            **{
                mode: {**_outcome_block(tally, ("field", field), mode), **_accuracy_trio(tally, mode, (field,))}
                for mode in MODES
            },
        }

    labels = sorted({k[1] for k in tally if k[0] == "subtype"})
    per_label = {label: prf(*(tally[("subtype", label, kind)] for kind in ("tp", "fp", "fn"))) for label in labels}
    label_f1s = [v["f1"] for v in per_label.values() if v["f1"] is not None]
    label_ps = [v["precision"] if v["precision"] is not None else 0.0 for v in per_label.values()]
    label_rs = [v["recall"] if v["recall"] is not None else 0.0 for v in per_label.values()]
    act_subtypes = {
        "excluded_uncertain": tally[("excluded", "act_subtypes")],
        "micro": prf(*_subtype_counts(tally)),
        "macro": {
            "labels": len(per_label),
            "precision": _div(sum(label_ps), len(label_ps)),
            "recall": _div(sum(label_rs), len(label_rs)),
            "f1": _div(sum(label_f1s), len(per_label)),
        },
        "per_label": per_label,
    }

    persons = {}
    for field, keys in PERSON_LIST_FIELDS:
        name_key = PERSON_NAME_KEYS[field]
        attributes = {}
        for attribute in keys:
            if attribute == name_key:
                continue
            attributes[attribute] = {mode: _outcome_block(tally, ("person_attr", field, attribute), mode) for mode in MODES}
        persons[field] = {
            "excluded_uncertain": tally[("excluded", field)],
            "found": prf(*_persons_counts(tally, field)),
            "attribute_accuracy": {mode: _div(*_person_attr_accuracy(tally, field, mode)) for mode in MODES},
            "attributes": attributes,
        }

    value_keys = sorted({k[1] for k in tally if k[0] == "extras_value"})
    extras = {
        "excluded_uncertain": tally[("excluded", "extras")],
        "keys": {
            **prf(tally[("extras_key", "tp")], tally[("extras_key", "fp")], tally[("extras_key", "fn")]),
            "fp_unregistered": tally[("extras_key_unregistered",)],
        },
        "values": {
            "accuracy": {mode: _div(*_extras_value_accuracy(tally, mode)) for mode in MODES},
            "per_key": {
                key: {
                    mode: {
                        CORRECT: tally[("extras_value", key, mode, CORRECT)],
                        WRONG: tally[("extras_value", key, mode, WRONG)],
                    }
                    for mode in MODES
                }
                for key in value_keys
            },
        },
    }

    parse_summary = parse_responses.build_parse_summary(parsed_records, run_info["run_id"])
    total = parse_summary["total"]
    format_metrics = {
        "status_counts": parse_summary["status_counts"],
        "parse_failure_rate": _div(
            parse_summary["status_counts"]["failed"] + parse_summary["status_counts"]["no_response"], total
        ),
        "repairs_total": parse_summary["repairs_total"],
        "docs_with_invented_keys": sum(1 for r in parsed_records if r["extra_keys"]),
        "invented_keys_counts": parse_summary["extra_keys_counts"],
        "missing_keys_counts": parse_summary["missing_keys_counts"],
        "unregistered_extras_keys_counts": parse_summary["unregistered_extras_keys_counts"],
    }

    return {
        "run": run_info,
        "bootstrap": {
            "iterations": iterations,
            "seed": seed,
            "method": "percentile, 95%, resampling documents with replacement",
        },
        "headline": headline,
        "groups": groups,
        "fields": fields,
        "act_subtypes": act_subtypes,
        "persons": persons,
        "extras": extras,
        "format": format_metrics,
    }


# --- run-level driver ---


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def gold_dir_for_split(split: str) -> Path:
    if split not in GOLD_DIRS:
        raise EvaluationError(f"unknown split {split!r} in run.json")
    return GOLD_DIRS[split]


def evaluate_run(
    run_dir: Path,
    *,
    gold_dir: Path | None = None,
    iterations: int = DEFAULT_ITERATIONS,
    seed: int = DEFAULT_SEED,
    extras_registry: set[str] | None = None,
) -> tuple[dict[str, Any], list[dict]]:
    """(summary, rows) for one run. Reads only; writes nothing."""
    run_config = _load_json(run_dir / "run.json")
    if gold_dir is None:
        gold_dir = gold_dir_for_split(run_config["split"])
    if extras_registry is None:
        extras_registry = parse_responses._load_extras_registry()

    doc_ids = run_config["doc_ids"]
    missing_parsed = [d for d in doc_ids if not (run_dir / "parsed" / f"{d}.json").is_file()]
    if missing_parsed:
        raise EvaluationError(
            f"no parsed/ file for {', '.join(missing_parsed)} — run parse_responses.py on this run first"
        )
    missing_gold = [d for d in doc_ids if not (gold_dir / f"{d}.json").is_file()]
    if missing_gold:
        raise EvaluationError(f"no gold file in {gold_dir} for {', '.join(missing_gold)}")

    gold_hash = hashlib.sha256()
    rows: list[dict] = []
    per_doc: list[Counter] = []
    parsed_records: list[dict] = []
    for doc_id in doc_ids:
        gold_bytes = (gold_dir / f"{doc_id}.json").read_bytes()
        gold_hash.update(doc_id.encode() + b"\0" + gold_bytes)
        gold = json.loads(gold_bytes.decode("utf-8"))
        parsed = _load_json(run_dir / "parsed" / f"{doc_id}.json")
        doc_rows, doc_tally = score_document(gold, parsed, extras_registry)
        rows.extend(doc_rows)
        per_doc.append(doc_tally)
        parsed_records.append(parsed)

    tally: Counter = Counter()
    for doc_tally in per_doc:
        tally.update(doc_tally)

    try:
        gold_display = str(gold_dir.relative_to(REPO_ROOT))
    except ValueError:
        gold_display = str(gold_dir)
    run_info = {
        "run_id": run_config["run_id"],
        "split": run_config["split"],
        "backend": run_config.get("backend"),
        "model_requested": run_config.get("model_requested"),
        "params": run_config.get("params"),
        "n_docs": len(doc_ids),
        "gold_dir": gold_display.replace("\\", "/"),
        "gold_sha256": gold_hash.hexdigest(),
        "evaluator_git_commit": git_commit_hash(),
        "evaluator_git_dirty": git_is_dirty(),
    }
    summary = build_summary(tally, per_doc, parsed_records, run_info=run_info, iterations=iterations, seed=seed)
    return summary, rows


# Screen order: the comprehension part first, the formulaic header and the
# global figure (dominated by the header) last.
SCREEN_METRICS = (
    "group_value_accuracy_normalized:comprehension",
    "group_null_accuracy_normalized:comprehension",
    "persons_found_f1:persons_added",
    "persons_found_f1:persons_removed",
    "persons_found_f1:persons_changed",
    "act_subtypes_micro_f1",
    "extras_key_f1",
    "group_accuracy_normalized:header",
    "core_accuracy_normalized",
)


def _fmt(value: float | None) -> str:
    return "  n/a" if value is None else f"{value:.3f}"


def format_headline(headline: dict[str, dict[str, Any]]) -> str:
    lines = []
    for name in SCREEN_METRICS:
        m = headline[name]
        lines.append(
            f"{name:<48} {_fmt(m['value'])}  [{_fmt(m['ci_low'])} - {_fmt(m['ci_high'])}]"
            f"  ({m['numerator']}/{m['denominator']})"
        )
    return "\n".join(lines)


ROW_COLUMNS = ("doc_id", "field", "item", "attribute", "gold", "prediction", "outcome_exact", "outcome_normalized")


def write_outputs(out_dir: Path, summary: dict[str, Any], rows: list[dict]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (out_dir / "fields.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=ROW_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Score a run's parsed responses against the gold standard.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR)
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    parser.add_argument("--gold-dir", type=Path, default=None, help="Override the split's gold directory (tests only)")
    parser.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    run_dir = args.runs_dir / args.run_id
    if not (run_dir / "run.json").is_file():
        print(f"error: no run.json at {run_dir}", file=sys.stderr)
        return 2
    try:
        summary, rows = evaluate_run(run_dir, gold_dir=args.gold_dir, iterations=args.iterations, seed=args.seed)
    except EvaluationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    out_dir = args.results_dir / args.run_id
    write_outputs(out_dir, summary, rows)
    print(format_headline(summary["headline"]))
    print(f"wrote {out_dir / 'summary.json'} and {out_dir / 'fields.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
