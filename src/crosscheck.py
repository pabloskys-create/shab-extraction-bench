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

`doc_id`, `schema_version`, `language`, `notes` and `uncertain` are skipped
entirely: they are bookkeeping or the annotator's own commentary, never
derived by reading the source text (see `_SKIPPED_FIELDS` below), so
checking them can only ever produce noise.

A few remaining fields are still expected to show up as "not found" even
on a correct annotation, because they are normalized rather than
transcribed verbatim: `act_type` (lowercased) and `legal_form`,
`seat_canton`, `canton_previous`, `canton_new` (mapped to a code, e.g.
"Aktiengesellschaft" -> "AG", "Sitten" -> "VS"). That's expected, not a
bug in this tool; batch mode marks them as such.

Called without a `doc_id`, the CLI runs in batch mode over every
`data/exploratory/*.json` whose `_verified` flag is true (the unverified
ones are prefill output, not annotations, so their values are expected to
be rough). Batch mode reports only the "NOT found in the text" section per
document, and exits non-zero if any document has one. The normalized
fields below don't count as a finding on their own — they are missing on
every document by construction — but they are still listed, marked,
whenever their document has a real one.

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
# `notes` is the annotator's own prose about the document and `uncertain`
# holds field names, not values — neither is read off the source text.
_SKIPPED_FIELDS = frozenset(
    {"doc_id", "schema_version", "language", "notes", "uncertain"}
)

# Fields that are normalized rather than transcribed verbatim, so they land
# in "missing" even on a correct annotation (see the module docstring).
# They are still reported — flagging them silently would hide a real error
# in one of them — but the batch listing marks them as expected.
_NORMALIZED_FIELDS = frozenset(
    {"act_type", "legal_form", "seat_canton", "canton_previous", "canton_new"}
)


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


@dataclass
class BatchEntry:
    """One document's batch outcome: either a `result` or an `error`."""

    doc_id: str
    result: CrosscheckResult | None = None
    error: str | None = None


def _is_verified(record: dict) -> bool:
    """True only for a record explicitly marked `"_verified": true`.
    Records without the flag are treated as unverified (see src/prefill.py,
    which writes `"_verified": false`).
    """
    return record.get("_verified") is True


def crosscheck_verified() -> list[BatchEntry]:
    """Cross-check every verified `data/exploratory/*.json`, in doc_id order.

    Unverified records are skipped entirely (no entry is returned for them).
    A document whose raw text is missing or whose JSON is unreadable yields
    an entry carrying an `error` instead of a result, so one bad file does
    not abort the run.
    """
    entries: list[BatchEntry] = []

    for exploratory_path in sorted(DATA_EXPLORATORY.glob("*.json")):
        doc_id = exploratory_path.stem
        try:
            record = json.loads(exploratory_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            entries.append(BatchEntry(doc_id=doc_id, error=f"invalid JSON: {exc}"))
            continue

        if not _is_verified(record):
            continue

        raw_path = DATA_RAW / f"{doc_id}.txt"
        try:
            text = raw_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            entries.append(BatchEntry(doc_id=doc_id, error=f"no source text at {raw_path}"))
            continue

        entries.append(BatchEntry(doc_id=doc_id, result=crosscheck_record(text, record)))

    return entries


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


def _run_batch() -> int:
    """Print the "NOT found in the text" fields of every verified document.
    Only fields outside `_NORMALIZED_FIELDS` count as a finding; the
    normalized ones are listed, marked, under a document that has a real
    finding, since they are part of the picture when reviewing it.

    Returns the process exit code: non-zero if any document reported a
    finding, or could not be read at all.
    """
    entries = crosscheck_verified()
    flagged = 0

    for entry in entries:
        if entry.error is not None:
            print(f"{entry.doc_id}: error: {entry.error}", file=sys.stderr)
            flagged += 1
            continue

        assert entry.result is not None
        missing = entry.result.missing
        if not any(check.field not in _NORMALIZED_FIELDS for check in missing):
            continue

        flagged += 1
        print(f"\n{entry.doc_id} — NOT found in the text ({len(missing)})")
        for check in missing:
            note = " [normalized, expected]" if check.field in _NORMALIZED_FIELDS else ""
            print(f"{_format_check(check)}{note}")

    checked = len(entries)
    docs = "document" if checked == 1 else "documents"
    print(f"\n{checked} verified {docs} checked, {flagged} with findings.")
    return 1 if flagged else 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Check whether data/exploratory/<doc_id>.json field values appear "
            "literally in data/raw/<doc_id>.txt. Without a doc_id, checks every "
            "verified exploratory record and reports only the fields that do "
            "not appear in the text (exit code 1 if there are any)."
        )
    )
    parser.add_argument("doc_id", nargs="?", help='Document id, e.g. "0001"')
    args = parser.parse_args()

    if args.doc_id is None:
        sys.exit(_run_batch())

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
