"""Descriptive statistics over an annotated corpus directory.

Reports, for every `*.json` record in a directory (by default
`data/exploratory/`, the early annotations kept as provenance — see
CLAUDE.md):

  * the distribution of `act_type` and of `act_subtypes`
  * the distribution of `legal_form` and of `seat_canton`
  * every key used in `extras`, with the documents that use it
  * the documents whose `uncertain` list is non-empty, and which fields
    they flag
  * the number of people in each person list, and how many documents leave
    each list empty

Percentages are always a share of documents, never of occurrences. That
matters for `act_subtypes`, for the `extras` keys and for the `uncertain`
fields, where one document can contribute several entries: those columns
sum to more than 100% by construction, and a row reads "this share of
documents has that subtype / key", not "this share of subtypes is that
one".

This module only reads. It never writes to the corpus (CLAUDE.md rule 1).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
# Allow `python src/corpus_stats.py` to find the `src` package (see
# src/show.py for the same pattern) as well as `python -m src.corpus_stats`.
sys.path.insert(0, str(REPO_ROOT))

from src.validate import FIELD_SPECS

DEFAULT_CORPUS = REPO_ROOT / "data" / "exploratory"

# Scalar fields whose value distribution is reported, in report order.
_SCALAR_FIELDS = ("act_type", "legal_form", "seat_canton")

# Shown in place of a null value in a distribution, so a nullable field's
# missing values stay visible instead of dropping silently out of the count.
NULL_LABEL = "(null)"


def _person_list_fields() -> tuple[str, ...]:
    """The record fields holding lists of people, in SCHEMA.md order.

    Derived from `FIELD_SPECS` rather than hardcoded, so a person list
    added to the schema shows up here without a second edit.
    """
    return tuple(
        name
        for name, spec in FIELD_SPECS.items()
        if spec["kind"] in ("list_person", "list_person_change")
    )


@dataclass
class Count:
    """One row of a distribution: a value, and the documents holding it."""

    value: str
    docs: list[str]

    @property
    def n(self) -> int:
        return len(self.docs)


@dataclass
class Distribution:
    """A field's value distribution over `total` documents."""

    field: str
    counts: list[Count]
    total: int

    def share(self, count: Count) -> float:
        """`count`'s share of documents, as a percentage."""
        return 100.0 * count.n / self.total if self.total else 0.0


@dataclass
class PersonListStats:
    """One person list's totals across the corpus."""

    field: str
    people: int  # total number of people across all documents
    empty_docs: list[str]  # documents whose list is empty
    total: int  # documents examined

    @property
    def empty_share(self) -> float:
        return 100.0 * len(self.empty_docs) / self.total if self.total else 0.0

    @property
    def per_doc(self) -> float:
        return self.people / self.total if self.total else 0.0


@dataclass
class CorpusStats:
    total: int
    doc_ids: list[str]
    scalars: list[Distribution]
    subtypes: Distribution
    extras: Distribution
    uncertain_fields: Distribution
    uncertain_docs: list[str] = dataclass_field(default_factory=list)
    person_lists: list[PersonListStats] = dataclass_field(default_factory=list)


def _doc_id(record: dict, path: Path) -> str:
    """The record's own `doc_id`, falling back to the filename for a record
    written without one."""
    return str(record.get("doc_id") or path.stem)


def load_records(directory: Path) -> list[tuple[str, dict]]:
    """Load every `*.json` in `directory` as `(doc_id, record)`, sorted by
    filename. Raises `json.JSONDecodeError` on an unreadable record — a
    corpus that does not parse is a problem to report, not to skip past.
    """
    records: list[tuple[str, dict]] = []
    for path in sorted(directory.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        records.append((_doc_id(record, path), record))
    return records


def _sorted_counts(docs_by_value: dict[str, list[str]]) -> list[Count]:
    """Most frequent first; ties broken alphabetically, so the report is
    stable across runs and diffable across corpus versions."""
    counts = [Count(value=value, docs=docs) for value, docs in docs_by_value.items()]
    counts.sort(key=lambda count: (-count.n, count.value))
    return counts


def scalar_distribution(records: list[tuple[str, dict]], field: str) -> Distribution:
    """Distribution of a scalar field. Null becomes `NULL_LABEL`; a record
    missing the field entirely counts as null too."""
    docs_by_value: dict[str, list[str]] = {}
    for doc_id, record in records:
        value = record.get(field)
        label = NULL_LABEL if value is None else str(value)
        docs_by_value.setdefault(label, []).append(doc_id)
    return Distribution(
        field=field, counts=_sorted_counts(docs_by_value), total=len(records)
    )


def _membership_distribution(
    records: list[tuple[str, dict]],
    field: str,
    members: Callable[[dict], Iterable],
) -> Distribution:
    """Distribution over the entries `members(record)` yields for a record.

    A document is counted once per distinct entry, so repeating an entry
    within one record cannot inflate its document count.
    """
    docs_by_value: dict[str, list[str]] = {}
    for doc_id, record in records:
        for value in dict.fromkeys(members(record)):
            docs_by_value.setdefault(str(value), []).append(doc_id)
    return Distribution(
        field=field, counts=_sorted_counts(docs_by_value), total=len(records)
    )


def list_distribution(records: list[tuple[str, dict]], field: str) -> Distribution:
    """Distribution over the entries of a list field, e.g. `act_subtypes`."""
    return _membership_distribution(records, field, lambda record: record.get(field) or [])


def extras_distribution(records: list[tuple[str, dict]]) -> Distribution:
    """Distribution over the keys used in `extras`."""
    return _membership_distribution(
        records, "extras", lambda record: (record.get("extras") or {}).keys()
    )


def uncertain_distribution(records: list[tuple[str, dict]]) -> Distribution:
    """Distribution over the field names listed in `uncertain`."""
    return _membership_distribution(
        records, "uncertain", lambda record: record.get("uncertain") or []
    )


def person_list_stats(records: list[tuple[str, dict]], field: str) -> PersonListStats:
    """Total people in `field` across the corpus, and the documents whose
    `field` is empty."""
    people = 0
    empty_docs: list[str] = []
    for doc_id, record in records:
        entries = record.get(field) or []
        people += len(entries)
        if not entries:
            empty_docs.append(doc_id)
    return PersonListStats(
        field=field, people=people, empty_docs=empty_docs, total=len(records)
    )


def compute(records: list[tuple[str, dict]]) -> CorpusStats:
    """Everything the report needs."""
    return CorpusStats(
        total=len(records),
        doc_ids=[doc_id for doc_id, _ in records],
        scalars=[scalar_distribution(records, name) for name in _SCALAR_FIELDS],
        subtypes=list_distribution(records, "act_subtypes"),
        extras=extras_distribution(records),
        uncertain_fields=uncertain_distribution(records),
        uncertain_docs=[doc_id for doc_id, record in records if record.get("uncertain")],
        person_lists=[
            person_list_stats(records, name) for name in _person_list_fields()
        ],
    )


# --- plain-text report ---


def _format_docs(docs: list[str]) -> str:
    return ", ".join(docs)


def _distribution_lines(
    dist: Distribution, title: str, with_docs: bool = False
) -> list[str]:
    lines = ["", f"--- {title} ---"]
    if not dist.counts:
        lines.append("(none)")
        return lines

    width = max(len(count.value) for count in dist.counts)
    for count in dist.counts:
        line = f"{count.value.ljust(width)}  {count.n:4d}  {dist.share(count):5.1f}%"
        if with_docs:
            line += f"  {_format_docs(count.docs)}"
        lines.append(line)
    return lines


def render_text(stats: CorpusStats) -> str:
    docs = "document" if stats.total == 1 else "documents"
    lines = [f"{stats.total} {docs}"]

    for dist in stats.scalars:
        lines += _distribution_lines(dist, dist.field)
    lines += _distribution_lines(stats.subtypes, "act_subtypes (share of documents)")
    lines += _distribution_lines(stats.extras, "extras keys", with_docs=True)

    share = 100.0 * len(stats.uncertain_docs) / stats.total if stats.total else 0.0
    lines += [
        "",
        "--- uncertain ---",
        (
            f"{len(stats.uncertain_docs)} of {stats.total} {docs} "
            f"({share:.1f}%) with a non-empty uncertain"
        ),
    ]
    if stats.uncertain_docs:
        lines.append(f"documents: {_format_docs(stats.uncertain_docs)}")
    lines += _distribution_lines(stats.uncertain_fields, "uncertain fields", with_docs=True)

    lines += ["", "--- person lists ---"]
    if stats.person_lists:
        width = max(len(item.field) for item in stats.person_lists)
        for item in stats.person_lists:
            lines.append(
                f"{item.field.ljust(width)}  {item.people:4d} people"
                f"  {item.per_doc:5.2f}/doc"
                f"  empty in {len(item.empty_docs):4d} ({item.empty_share:5.1f}%)"
            )
    return "\n".join(lines)


# --- markdown report ---


def _markdown_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |"]
    lines.append("|" + "|".join(["---"] * len(headers)) + "|")
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return lines


def _markdown_distribution(
    dist: Distribution, title: str, value_header: str, with_docs: bool = False
) -> list[str]:
    lines = [f"### {title}", ""]
    if not dist.counts:
        return lines + ["_(none)_", ""]

    headers = [value_header, "n", "%"] + (["documents"] if with_docs else [])
    rows = []
    for count in dist.counts:
        row = [count.value, str(count.n), f"{dist.share(count):.1f}%"]
        if with_docs:
            row.append(_format_docs(count.docs))
        rows.append(row)
    return lines + _markdown_table(headers, rows) + [""]


def render_markdown(stats: CorpusStats) -> str:
    docs = "document" if stats.total == 1 else "documents"
    lines = ["## Corpus statistics", "", f"{stats.total} {docs}.", ""]

    for dist in stats.scalars:
        lines += _markdown_distribution(dist, dist.field, dist.field)

    lines += _markdown_distribution(stats.subtypes, "act_subtypes", "subtype")
    lines += [
        "A document can carry several subtypes, so the column sums above 100%.",
        "",
    ]

    lines += _markdown_distribution(stats.extras, "extras keys", "key", with_docs=True)

    share = 100.0 * len(stats.uncertain_docs) / stats.total if stats.total else 0.0
    lines += [
        "### uncertain",
        "",
        (
            f"{len(stats.uncertain_docs)} of {stats.total} {docs} "
            f"({share:.1f}%) carry a non-empty `uncertain`."
        ),
        "",
    ]
    lines += _markdown_distribution(
        stats.uncertain_fields, "uncertain fields", "field", with_docs=True
    )

    lines += ["### person lists", ""]
    lines += _markdown_table(
        ["list", "people", "people/doc", "empty", "empty %"],
        [
            [
                item.field,
                str(item.people),
                f"{item.per_doc:.2f}",
                str(len(item.empty_docs)),
                f"{item.empty_share:.1f}%",
            ]
            for item in stats.person_lists
        ],
    )
    return "\n".join(lines + [""])


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Print descriptive statistics over an annotated corpus directory "
            "(by default data/exploratory/): the act_type, act_subtypes, "
            "legal_form and seat_canton distributions, the extras keys in use, "
            "the documents flagging something as uncertain, and the size of "
            "each person list."
        )
    )
    parser.add_argument(
        "corpus",
        nargs="?",
        type=Path,
        default=DEFAULT_CORPUS,
        help=f"Directory of annotated *.json (default: {DEFAULT_CORPUS})",
    )
    parser.add_argument(
        "--markdown",
        action="store_true",
        help="Emit markdown tables ready to paste into the README",
    )
    args = parser.parse_args()

    if not args.corpus.is_dir():
        print(f"error: not a directory: {args.corpus}", file=sys.stderr)
        sys.exit(1)

    try:
        records = load_records(args.corpus)
    except json.JSONDecodeError as exc:
        print(f"error: invalid JSON in {args.corpus}: {exc}", file=sys.stderr)
        sys.exit(1)

    if not records:
        print(f"error: no *.json in {args.corpus}", file=sys.stderr)
        sys.exit(1)

    stats = compute(records)
    print(render_markdown(stats) if args.markdown else render_text(stats))


if __name__ == "__main__":
    main()
