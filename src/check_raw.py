"""Check that every pasted `data/raw/NNNN.txt` is the document the sampling
manifest assigned to that doc_id.

The corpus is built by hand: the sample manifest names 92 publications and
their detail URLs, and a human opens each URL and pastes the notice into
`data/raw/<doc_id>.txt`. That loop has exactly the failure modes you would
expect — a page not pasted, a paste truncated, two pastes landing in each
other's file, the same page pasted twice — and every one of them silently
corrupts the benchmark: a swapped pair produces two documents whose gold
annotation describes a different notice than the text a model is shown.

This module reads only `data/sampling/manifest_sample.json` and
`data/raw/`. It never writes anything, and it never repairs anything: a
mismatch is reported for a human to resolve (see CLAUDE.md rules 2 and 5).

The check is that the first line of the pasted text — the headline SHAB
prints above the notice — equals the `title` the manifest recorded for that
doc_id, exactly. Only the line terminator and surrounding whitespace are
ignored; the headline is copied verbatim from the same page in both cases,
so anything else differing means the wrong page was pasted.

Reported separately:

  missing            the manifest names a doc_id with no .txt at all
  short              under MIN_LENGTH characters — a truncated or empty paste
  headline mismatch  first line is not the expected title (both are shown)
  swapped            first line is another sampled doc_id's title, naming it.
                     A subset of the mismatches, split out because it is the
                     one failure with an unambiguous fix
  duplicate content  two files with byte-identical text
  repeated UID       two documents sharing a CHE-XXX.XXX.XXX

The last two also compare against the documents already in `data/raw/` that
the manifest does not cover (0001-0028, the exploratory corpus), because
pasting a notice that was already annotated is precisely what the exclusion
step in `src/sample.py` exists to prevent — a collision there is worth the
same alarm.

On repeated UIDs: two *different* publications may legitimately concern the
same company (a Mutation and a later Löschung, say), so a repeat is not
proof of an error on its own. It is reported because in a corpus sampled
from three single-day listings it is far more often a document pasted twice
under two doc_ids, which the identical-content check alone will miss when
the two pastes differ in trailing whitespace.

Documents whose notice carries no UID at all (rubrics such as
Arbeitszeitbewilligungen or lost securities, which name no register entry)
are counted, not reported: absent is not a defect. See CLAUDE.md rule 5.

Exit status is 0 only when every check is clean.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

DATA_RAW = REPO_ROOT / "data" / "raw"
DEFAULT_MANIFEST = REPO_ROOT / "data" / "sampling" / "manifest_sample.json"

# A paste this short is not a SHAB notice: the shortest real one in the
# corpus is comfortably above this. Tuned to catch an empty file or a paste
# that stopped at the headline, not to judge genuinely terse notices.
MIN_LENGTH = 200

# Same pattern and same "first occurrence is the subject company" rule as
# src/prefill.py — later occurrences belong to corporate officers. Verified
# against all 28 exploratory annotations: the first match is the annotated
# `uid` in every one.
UID_RE = re.compile(r"CHE-\d{3}\.\d{3}\.\d{3}")


@dataclass
class Expected:
    """One row of the sample manifest."""

    doc_id: str
    title: str
    canton: str
    url: str


@dataclass
class Mismatch:
    doc_id: str
    expected: str
    found: str
    # doc_id whose title the pasted headline actually matches, if any.
    belongs_to: str | None = None


@dataclass
class Report:
    checked: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    short: list[tuple[str, int]] = field(default_factory=list)
    mismatched: list[Mismatch] = field(default_factory=list)
    swapped: list[Mismatch] = field(default_factory=list)
    duplicate_content: list[list[str]] = field(default_factory=list)
    repeated_uid: list[tuple[str, list[str]]] = field(default_factory=list)
    without_uid: list[str] = field(default_factory=list)
    min_length: int = MIN_LENGTH

    @property
    def ok(self) -> bool:
        return not (
            self.missing
            or self.short
            or self.mismatched
            or self.duplicate_content
            or self.repeated_uid
        )


def load_expected(manifest_path: Path) -> list[Expected]:
    """The manifest rows, in doc_id order."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows = [
        Expected(
            doc_id=record["doc_id"],
            title=record["title"],
            canton=record["canton"],
            url=record["url"],
        )
        for record in manifest["records"]
    ]
    return sorted(rows, key=lambda row: row.doc_id)


def first_line(text: str) -> str:
    """The pasted headline, without its line terminator or surrounding
    whitespace. Nothing else is normalized — see the module docstring."""
    return text.splitlines()[0].strip() if text.strip() else ""


def document_uid(text: str) -> str | None:
    """The subject company's UID, or None if the notice names none."""
    match = UID_RE.search(text)
    return match.group(0) if match else None


def check_raw(
    expected: list[Expected],
    raw_dir: Path = DATA_RAW,
    min_length: int = MIN_LENGTH,
) -> Report:
    """Check every manifest row against its pasted file."""
    report = Report(min_length=min_length)
    title_owner = {row.title.strip(): row.doc_id for row in expected}
    texts: dict[str, str] = {}

    for row in expected:
        path = raw_dir / f"{row.doc_id}.txt"
        if not path.is_file():
            report.missing.append(row.doc_id)
            continue

        text = path.read_text(encoding="utf-8")
        texts[row.doc_id] = text
        report.checked.append(row.doc_id)

        if len(text) < min_length:
            report.short.append((row.doc_id, len(text)))

        headline = first_line(text)
        if headline != row.title.strip():
            owner = title_owner.get(headline)
            mismatch = Mismatch(
                doc_id=row.doc_id,
                expected=row.title,
                found=headline,
                belongs_to=owner if owner != row.doc_id else None,
            )
            report.mismatched.append(mismatch)
            if mismatch.belongs_to is not None:
                report.swapped.append(mismatch)

    _check_uniqueness(report, texts, raw_dir)
    return report


def _check_uniqueness(report: Report, texts: dict[str, str], raw_dir: Path) -> None:
    """Fill in the duplicate-content and repeated-UID sections.

    Files in `raw_dir` that the manifest does not cover are read in as
    comparison material only: a group is reported when it contains at least
    one sampled document."""
    sampled = set(texts)
    corpus = dict(texts)
    for path in sorted(raw_dir.glob("*.txt")):
        if path.stem not in corpus:
            corpus[path.stem] = path.read_text(encoding="utf-8")

    by_content: dict[str, list[str]] = defaultdict(list)
    by_uid: dict[str, list[str]] = defaultdict(list)
    for doc_id, text in sorted(corpus.items()):
        by_content[text].append(doc_id)
        uid = document_uid(text)
        if uid is None:
            if doc_id in sampled:
                report.without_uid.append(doc_id)
        else:
            by_uid[uid].append(doc_id)

    report.duplicate_content = [
        group for group in by_content.values() if len(group) > 1 and sampled.intersection(group)
    ]
    report.repeated_uid = [
        (uid, group)
        for uid, group in sorted(by_uid.items())
        if len(group) > 1 and sampled.intersection(group)
    ]


def _print_report(report: Report, expected: list[Expected]) -> None:
    print(f"manifest rows: {len(expected)}")
    print(f"files read: {len(report.checked)}")

    print(f"\nmissing files ({len(report.missing)})")
    for doc_id in report.missing:
        print(f"  {doc_id}.txt")

    print(f"\nempty or suspiciously short, under {MIN_LENGTH} characters ({len(report.short)})")
    for doc_id, length in report.short:
        print(f"  {doc_id}.txt: {length} characters" + (" (empty)" if length == 0 else ""))

    print(f"\nheadline does not match the manifest title ({len(report.mismatched)})")
    for mismatch in report.mismatched:
        print(f"  {mismatch.doc_id}.txt")
        print(f"    expected: {mismatch.expected!r}")
        print(f"    found:    {mismatch.found!r}")

    print(f"\nheadline belongs to another sampled doc_id ({len(report.swapped)})")
    for mismatch in report.swapped:
        print(f"  {mismatch.doc_id}.txt holds the text of {mismatch.belongs_to}: {mismatch.found!r}")

    print(f"\nidentical content ({len(report.duplicate_content)})")
    for group in report.duplicate_content:
        print(f"  {', '.join(f'{doc_id}.txt' for doc_id in group)}")

    print(f"\nrepeated UID ({len(report.repeated_uid)})")
    for uid, group in report.repeated_uid:
        print(f"  {uid}: {', '.join(f'{doc_id}.txt' for doc_id in group)}")

    if report.without_uid:
        print(
            f"\n{len(report.without_uid)} sampled notice(s) name no UID "
            f"({', '.join(report.without_uid)}) — not a defect, excluded from the UID check"
        )

    print("\nOK" if report.ok else "\nFAILED")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check that each data/raw/NNNN.txt is the document the sample manifest assigns to it."
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help="Sample manifest to check against (default: data/sampling/manifest_sample.json)",
    )
    parser.add_argument(
        "--raw-dir", type=Path, default=DATA_RAW, help="Directory holding the pasted notices"
    )
    parser.add_argument(
        "--min-length",
        type=int,
        default=MIN_LENGTH,
        help=f"Flag files shorter than this many characters (default: {MIN_LENGTH})",
    )
    args = parser.parse_args()

    expected = load_expected(args.manifest)
    report = check_raw(expected, args.raw_dir, args.min_length)
    _print_report(report, expected)
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
