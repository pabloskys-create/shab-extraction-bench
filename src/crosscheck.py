"""Cross-check exploratory field values against the literal source text.

For a given `doc_id`, reads `data/raw/NNNN.txt` (the source notice) and
`data/exploratory/NNNN.json` (an early, not-yet-gold annotation — see
CLAUDE.md: `data/exploratory/` holds the first ~25 annotations, kept as
provenance). For every non-null scalar field (kind `str`, `date`, `int` or
`number` in `src.validate.FIELD_SPECS`), it checks whether that value shows
up literally in the source text and reports three lists: fields whose value
appears exactly once, fields whose value appears more than once (ambiguous
— the value is real but could match the wrong span), and fields whose value
does not appear at all.

Dates are also searched in the Swiss `DD.MM.YYYY` form, since the JSON
stores ISO `YYYY-MM-DD` but the source text never does. Numbers are also
searched in Swiss apostrophe-grouped form (`189'123.50`), since the JSON
stores a plain number but the source text never does.

This does NOT validate correctness: a value can appear in the text and
still be assigned to the wrong field (see CLAUDE.md rule 5 — that kind of
error needs a human, not a substring search). It only flags values that
don't exist in the source at all, which is almost always either a
transcription slip or contamination copied in from another document.

`doc_id`, `schema_version` and `language` are skipped entirely: they are
bookkeeping, never derived by reading the source text (see
`_SKIPPED_FIELDS` below), so checking them can only ever produce noise.

A few remaining fields are still expected to show up as "not found" even
on a correct annotation, because they are normalized rather than
transcribed verbatim: `act_type` (lowercased), `legal_form` and
`seat_canton` (mapped to a code, e.g. "Aktiengesellschaft" -> "AG" -> "VS").
That's expected, not a bug in this tool.

This module only reads `data/raw/` and `data/exploratory/`. It never writes
anything.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
# Allow `python src/crosscheck.py` to find the `src` package (see src/show.py
# for the same pattern) as well as `python -m src.crosscheck` / pytest.
sys.path.insert(0, str(REPO_ROOT))

from src.validate import FIELD_SPECS

DATA_RAW = REPO_ROOT / "data" / "raw"
DATA_EXPLORATORY = REPO_ROOT / "data" / "exploratory"

# Only scalar, literally-transcribable kinds are checked. Composite kinds
# (list_str, list_person, list_person_change, dict, bool) hold structure,
# not a single literal value, and are out of scope here.
_CHECKED_KINDS = frozenset({"str", "date", "int", "number"})

# Bookkeeping fields that are never derived by reading the source text, so
# checking them is pure noise either way: `doc_id` comes from the filename
# and `schema_version` is a hardcoded constant (see src/prefill.py), always
# landing in "missing"; `language` is a corpus-wide constant ("de" — see
# SCHEMA.md "Scope decisions") whose 2-character value spuriously substring-
# matches inside ordinary German words, always landing in "ambiguous".
_SKIPPED_FIELDS = frozenset({"doc_id", "schema_version", "language"})


@dataclass
class FieldCheck:
    field: str
    value: Any
    searched: list[str]  # the literal string(s) looked for
    count: int


@dataclass
class CrosscheckResult:
    unique: list[FieldCheck]
    ambiguous: list[FieldCheck]
    missing: list[FieldCheck]


def _iso_to_swiss_date(iso_value: str) -> str | None:
    """`YYYY-MM-DD` -> `DD.MM.YYYY`. None if `iso_value` isn't that shape."""
    parts = iso_value.split("-")
    if len(parts) != 3:
        return None
    year, month, day = parts
    if not (len(year) == 4 and len(month) == 2 and len(day) == 2):
        return None
    return f"{day}.{month}.{year}"


def _swiss_int(value: int) -> str:
    """`1000000` -> `"1'000'000"`; small values are unaffected, e.g. `223`."""
    return f"{value:,}".replace(",", "'")


def _swiss_money(value: float) -> str:
    """`189123.5` -> `"189'123.50"` — source money always has 2 decimals."""
    return f"{value:,.2f}".replace(",", "'")


def _candidates(kind: str, value: Any) -> list[str]:
    """The literal string(s) to search for, in the order they're tried.
    Deduplicated so a value that formats the same way twice (e.g. a small
    int, where the plain and Swiss-grouped forms coincide) isn't counted
    twice for one real occurrence.
    """
    candidates = [str(value)]
    if kind == "date":
        swiss = _iso_to_swiss_date(value)
        if swiss:
            candidates.append(swiss)
    elif kind in ("int", "number"):
        candidates.append(_swiss_money(value) if kind == "number" else _swiss_int(value))

    seen: list[str] = []
    for candidate in candidates:
        if candidate not in seen:
            seen.append(candidate)
    return seen


def crosscheck_record(text: str, record: dict) -> CrosscheckResult:
    """Check every non-null scalar field of `record` for literal presence
    in `text`. Field order follows `FIELD_SPECS` (i.e. SCHEMA.md order).
    """
    unique: list[FieldCheck] = []
    ambiguous: list[FieldCheck] = []
    missing: list[FieldCheck] = []

    for field, spec in FIELD_SPECS.items():
        if spec["kind"] not in _CHECKED_KINDS or field in _SKIPPED_FIELDS:
            continue
        value = record.get(field)
        if value is None:
            continue

        searched = _candidates(spec["kind"], value)
        count = sum(text.count(candidate) for candidate in searched)
        check = FieldCheck(field=field, value=value, searched=searched, count=count)

        if count == 1:
            unique.append(check)
        elif count > 1:
            ambiguous.append(check)
        else:
            missing.append(check)

    return CrosscheckResult(unique=unique, ambiguous=ambiguous, missing=missing)


def crosscheck_doc(doc_id: str) -> CrosscheckResult:
    """Load `data/raw/{doc_id}.txt` and `data/exploratory/{doc_id}.json`
    and cross-check them. Raises FileNotFoundError if either is missing.
    """
    raw_path = DATA_RAW / f"{doc_id}.txt"
    exploratory_path = DATA_EXPLORATORY / f"{doc_id}.json"

    text = raw_path.read_text(encoding="utf-8")
    record = json.loads(exploratory_path.read_text(encoding="utf-8"))

    return crosscheck_record(text, record)


def _format_check(check: FieldCheck) -> str:
    extra = ""
    if len(check.searched) > 1:
        others = ", ".join(repr(s) for s in check.searched[1:])
        extra = f" (also searched as {others})"
    times = "time" if check.count == 1 else "times"
    return f"  {check.field}: {check.value!r}{extra} — {check.count} {times}"


def _print_section(title: str, checks: list[FieldCheck]) -> None:
    print(f"\n{title} ({len(checks)})")
    for check in checks:
        print(_format_check(check))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Check whether data/exploratory/<doc_id>.json field values appear "
            "literally in data/raw/<doc_id>.txt."
        )
    )
    parser.add_argument("doc_id", help='Document id, e.g. "0001"')
    args = parser.parse_args()

    try:
        result = crosscheck_doc(args.doc_id)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as exc:
        print(f"error: invalid JSON in exploratory record: {exc}", file=sys.stderr)
        sys.exit(1)

    _print_section("Found exactly once", result.unique)
    _print_section("Found several times (ambiguous)", result.ambiguous)
    _print_section("NOT found in the text", result.missing)


if __name__ == "__main__":
    main()
