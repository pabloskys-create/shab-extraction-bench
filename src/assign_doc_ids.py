"""Re-assign the doc_ids of a sample manifest to fit the notices already pasted.

Resampling on a corrected frame draws largely the same publications in a
different order, so most documents keep their place in the corpus but are
handed a different doc_id. Renaming the pasted files to follow would mean a
permutation over dozens of files whose targets are still occupied — every
step of which can lose a document.

A doc_id is an arbitrary label. So instead of moving the files to fit the
manifest, this moves the labels to fit the files: a publication that is
already pasted somewhere keeps the doc_id of the file it occupies, and the
publications still to be pasted take the numbers left over. No file is
touched, and the manifest ends up describing the corpus as it actually sits
on disk.

Which publication a pasted file holds is decided by its first line — the
headline SHAB prints above the notice, which `src/sample.py` records as the
listing `title`. Headlines are unique across the frame (verified over the
405 records of the three listings in data/sampling/: no title occurs twice,
within a listing or across them), so the match is unambiguous. A pasted file
whose headline is in no manifest record belongs to the previous sample and
is left for a human to retire; it is reported, never deleted.

The doc_id pool is exactly the set of doc_ids the input manifest already
uses, so re-assignment neither grows nor shrinks the corpus — it permutes
labels within the sample. Records still to be pasted receive the free
numbers in ascending order, following frame position, which is the same rule
`src/sample.py` uses for a fresh sample.

The result is deliberately NOT produced by `src/sample.py`: that script must
stay a pure function of (listings, exclusions, seed), because the frame's
defensibility rests on being reproducible from the saved pages alone.
Fitting labels to whatever happens to be on disk is a separate, explicit
step, and it records itself in the manifest metadata under
`doc_id_assignment`.

Re-running `src/sample.py` overwrites the manifest and undoes this. Re-run
this afterwards; it is idempotent.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

DATA_RAW = REPO_ROOT / "data" / "raw"
DEFAULT_MANIFEST = REPO_ROOT / "data" / "sampling" / "manifest_sample.json"


class AssignmentError(Exception):
    """The manifest cannot be fitted to the files on disk."""


@dataclass
class Assignment:
    """The outcome of fitting one manifest to one raw directory."""

    kept: dict[str, str] = field(default_factory=dict)
    """publication_number -> doc_id it keeps, because its text is already there."""

    to_paste: dict[str, str] = field(default_factory=dict)
    """publication_number -> doc_id it is given, still to be pasted."""

    unmatched_files: list[str] = field(default_factory=list)
    """doc_ids in the pool whose pasted text is in no manifest record."""


def _normalize(title: str | None) -> str:
    return " ".join((title or "").split())


def headline(text: str) -> str:
    """The first line of a pasted notice, without surrounding whitespace."""
    return _normalize(text.splitlines()[0]) if text.strip() else ""


def read_pasted_headlines(raw_dir: Path, pool: set[str]) -> dict[str, str]:
    """{doc_id: headline} for the files in `raw_dir` whose doc_id is in the
    pool. Files outside the pool — the exploratory corpus — are ignored."""
    pasted = {}
    for path in sorted(raw_dir.glob("*.txt")):
        if path.stem in pool:
            pasted[path.stem] = headline(path.read_text(encoding="utf-8"))
    return pasted


def assign(records: list[dict], pasted: dict[str, str]) -> Assignment:
    """Fit the manifest `records` to the already-pasted files.

    `records` are in frame-position order; `pasted` maps doc_id to the
    headline of the file sitting there.
    """
    pool = [record["doc_id"] for record in records]
    if len(set(pool)) != len(pool):
        raise AssignmentError("the manifest assigns the same doc_id to more than one record")

    by_headline: dict[str, dict] = {}
    for record in records:
        key = _normalize(record["title"])
        if key in by_headline:
            raise AssignmentError(
                f"two sampled publications share the headline {key!r}, so a pasted file "
                "cannot be attributed to either. Match them by hand."
            )
        by_headline[key] = record

    result = Assignment()
    taken: set[str] = set()
    for doc_id, head in sorted(pasted.items()):
        record = by_headline.get(head)
        if record is None:
            result.unmatched_files.append(doc_id)
            continue
        number = record["publication_number"]
        if number in result.kept:
            raise AssignmentError(
                f"{number} is pasted twice, as {result.kept[number]} and {doc_id}. "
                "Retire one before re-assigning."
            )
        result.kept[number] = doc_id
        taken.add(doc_id)

    free = [doc_id for doc_id in sorted(pool) if doc_id not in taken]
    for record in records:
        number = record["publication_number"]
        if number not in result.kept:
            result.to_paste[number] = free.pop(0)
    assert not free, "pool and record count disagree"
    return result


def reassign_manifest(manifest: dict, pasted: dict[str, str]) -> tuple[dict, Assignment]:
    """A copy of `manifest` with doc_ids fitted to the pasted files."""
    records = manifest["records"]
    assignment = assign(records, pasted)
    doc_ids = {**assignment.kept, **assignment.to_paste}

    reassigned = {
        "metadata": {
            **manifest["metadata"],
            "doc_id_assignment": (
                "Fitted to the notices already pasted in data/raw/ by src/assign_doc_ids.py: "
                f"{len(assignment.kept)} publications keep the doc_id of the file that already "
                f"holds their text, and the remaining {len(assignment.to_paste)} take the free "
                "numbers in frame order. doc_id therefore does not run in step with position. "
                "Re-running src/sample.py resets this."
            ),
        },
        "records": [
            {**record, "doc_id": doc_ids[record["publication_number"]]} for record in records
        ],
    }
    return reassigned, assignment


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fit a sample manifest's doc_ids to the notices already pasted in data/raw/."
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--raw-dir", type=Path, default=DATA_RAW)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Rewrite the manifest in place. Without it, only the summary is printed.",
    )
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    pool = {record["doc_id"] for record in manifest["records"]}
    pasted = read_pasted_headlines(args.raw_dir, pool)

    try:
        reassigned, assignment = reassign_manifest(manifest, pasted)
    except AssignmentError as error:
        sys.exit(f"error: {error}")

    print(f"manifest records: {len(manifest['records'])}")
    print(f"pasted files in the doc_id pool: {len(pasted)}")
    print(f"keeping the doc_id they already occupy: {len(assignment.kept)}")
    print(f"still to be pasted, given a free doc_id: {len(assignment.to_paste)}")

    moved = [
        (record["doc_id"], reassigned["records"][i]["doc_id"])
        for i, record in enumerate(manifest["records"])
        if record["doc_id"] != reassigned["records"][i]["doc_id"]
    ]
    print(f"doc_ids changed by the re-assignment: {len(moved)}")

    if assignment.unmatched_files:
        print(
            f"\npasted files belonging to no sampled publication "
            f"({len(assignment.unmatched_files)}) — retire these by hand:"
        )
        print("  " + " ".join(f"{doc_id}.txt" for doc_id in assignment.unmatched_files))

    if args.write:
        args.manifest.write_text(
            json.dumps(reassigned, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"\nwritten: {args.manifest}")
    else:
        print("\ndry run; pass --write to rewrite the manifest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
