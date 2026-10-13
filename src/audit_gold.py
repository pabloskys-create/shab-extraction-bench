"""List gold cells that several strong models contradict in the same way.

An analysis aid for re-checking the gold standard by hand; it changes
neither the scoring nor the harness. Reads the gold of a split (read-only),
the source notices and `runs/<run_id>/parsed/` of several runs, and writes
two CSVs under `results/gold_audit/<split>/`:

    candidates.csv     every cell where at least --min-agree models agree on
                       the same value and that value differs from the gold
    random_sample.csv  --random-sample cells, drawn with a fixed seed, where
                       every model agrees with the gold — for estimating the
                       gold's error rate where nothing flags a problem

A "cell" is one comparable value of one document:

* a scalar or set field (every field evaluate.py scores at document level);
* one `extras` key, for every registered key in the gold or in any model's
  output (field `extras`, the key in `item`);
* one attribute of a gold person that the model's output also contains,
  matched by name exactly as evaluate.py matches persons (field
  `persons_added` etc., the gold name in `item`, the attribute in
  `attribute`). A missing or invented person is not a cell — only an
  attribute disagreement on a person both sides list.

Values are compared with evaluate.py's normalized comparison
(values_equal, set equality, the extras matchers); agreement between models
uses the same comparison. A model abstains from a cell — and gets an empty
column — when it has no parsed output for the document or, for a person
attribute, did not list that person. A cell in the gold record's
`uncertain` is skipped, as in scoring.

Both CSVs carry empty `verdict` (gold_error | model_error | ambiguous) and
`note` columns to be filled in by hand. An existing CSV is therefore never
overwritten unless --force is passed. Nothing is ever written under data/.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src import evaluate, parse_responses

REPO_ROOT = evaluate.REPO_ROOT
DATA_DIR = REPO_ROOT / "data"
RAW_DIRS = {
    "dev": DATA_DIR / "raw",
    "holdout": DATA_DIR / "holdout" / "raw",
}
DEFAULT_OUT_DIR = evaluate.DEFAULT_RESULTS_DIR / "gold_audit"

# The five strongest models of the dev runs. A run_id is "<split>-<model>".
DEFAULT_MODELS = (
    "gpt-6-luna",
    "gpt-6.1-sol",
    "claude-haiku-5.5",
    "claude-sonnet-5.5",
    "gemini-3.5-flash-lite",
)
DEFAULT_MIN_AGREE = 3
DEFAULT_RANDOM_SAMPLE = 30
DEFAULT_SEED = 20261013

SNIPPET_RADIUS = 200
# A long value (a zweck) is searched for by its first characters only: the
# gold may be truncated with "…", and one hit is enough to place it.
SEARCH_PREFIX_CHARS = 80
_ISO_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_WHITESPACE_RE = re.compile(r"\s+")

CANDIDATES_CSV = "candidates.csv"
RANDOM_SAMPLE_CSV = "random_sample.csv"

Same = Callable[[Any, Any], bool]


class AuditError(Exception):
    """The runs cannot be compared as asked (missing run, mixed splits,
    different document sets, missing gold or parsed file)."""


# --- comparison, all delegated to evaluate.py ---


def same_scalar(a: Any, b: Any) -> bool:
    return evaluate.values_equal(a, b, normalized=True)


def same_set(a: Any, b: Any) -> bool:
    return evaluate._as_set(a, normalized=True) == evaluate._as_set(b, normalized=True)


def same_extras_value(key: str) -> Same:
    """An absent key and a null value are the same; otherwise the key's
    matcher from evaluate.py (prefix for zweck, containment for
    loeschung_reason, values_equal for the rest)."""

    def same(a: Any, b: Any) -> bool:
        if a is None or b is None:
            return a is None and b is None
        return evaluate.extras_value_match(key, a, b, normalized=True)

    return same


# --- cells ---


@dataclass
class Cell:
    doc_id: str
    field: str
    gold: Any
    same: Same
    item: str = ""
    attribute: str = ""
    # run_id -> the model's value; a run that abstains is absent.
    votes: dict[str, Any] = field(default_factory=dict)


def document_cells(gold: dict, parsed_by_run: dict[str, dict], extras_registry: set[str]) -> list[Cell]:
    """Every comparable cell of one document, with each run's vote."""
    doc_id = gold["doc_id"]
    uncertain = set(gold.get("uncertain") or [])
    outputs = {run_id: p["data"] for run_id, p in parsed_by_run.items() if p["parse_status"] == "ok"}
    cells: list[Cell] = []

    for name in evaluate.FIELD_LEVEL_FIELDS:
        if name in uncertain:
            continue
        same = same_set if name in evaluate.SET_FIELDS else same_scalar
        cells.append(Cell(doc_id, name, gold.get(name), same, votes={r: d.get(name) for r, d in outputs.items()}))

    if "extras" not in uncertain:
        gold_extras = gold.get("extras") or {}
        pred_extras = {r: d.get("extras") or {} for r, d in outputs.items()}
        keys = list(gold_extras)
        for extras in pred_extras.values():
            keys += [k for k in extras if k in extras_registry and k not in keys]
        for key in keys:
            votes = {r: extras.get(key) for r, extras in pred_extras.items()}
            cells.append(Cell(doc_id, "extras", gold_extras.get(key), same_extras_value(key), item=key, votes=votes))

    for list_field, attributes in evaluate.PERSON_LIST_FIELDS:
        if list_field in uncertain:
            continue
        name_key = evaluate.PERSON_NAME_KEYS[list_field]
        gold_people = gold.get(list_field) or []
        # For each run: id(gold person) -> its matched predicted person.
        matched: dict[str, dict[int, dict]] = {}
        for run_id, data in outputs.items():
            pairs, _missed, _spurious = evaluate.match_persons(gold_people, data.get(list_field) or [], name_key)
            matched[run_id] = {id(g): p for g, p in pairs}
        for gold_person in gold_people:
            label = evaluate._person_label(gold_person, name_key)
            for attribute in attributes:
                if attribute == name_key:
                    continue
                votes = {
                    run_id: by_gold[id(gold_person)].get(attribute)
                    for run_id, by_gold in matched.items()
                    if id(gold_person) in by_gold
                }
                cells.append(
                    Cell(
                        doc_id,
                        list_field,
                        gold_person.get(attribute),
                        same_scalar,
                        item=label,
                        attribute=attribute,
                        votes=votes,
                    )
                )
    return cells


def clusters(cell: Cell) -> list[tuple[Any, list[str]]]:
    """The votes grouped by equal value, largest group first (ties keep
    the order of first appearance). Each group is (its first value, the
    run_ids in it)."""
    groups: list[tuple[Any, list[str]]] = []
    for run_id, value in cell.votes.items():
        for representative, members in groups:
            if cell.same(representative, value):
                members.append(run_id)
                break
        else:
            groups.append((value, [run_id]))
    return sorted(groups, key=lambda g: -len(g[1]))


def consensus_against_gold(cell: Cell, min_agree: int) -> tuple[Any, int] | None:
    """(value, n_agree) of the largest group of at least min_agree models
    that disagrees with the gold, or None."""
    for value, members in clusters(cell):
        if len(members) < min_agree:
            return None
        if not cell.same(cell.gold, value):
            return value, len(members)
    return None


def all_agree_with_gold(cell: Cell, run_ids: list[str]) -> bool:
    return len(cell.votes) == len(run_ids) and all(cell.same(cell.gold, v) for v in cell.votes.values())


# --- snippet ---


def search_terms(value: Any) -> list[str]:
    """Strings under which a JSON value may appear in a notice: an ISO date
    also as DD.MM.YYYY, a number also with Swiss thousands separators."""
    if value is None or isinstance(value, bool):
        return []
    if isinstance(value, list):
        return [term for v in value for term in search_terms(v)]
    if isinstance(value, (int, float)):
        terms = []
        if float(value).is_integer():
            terms.append(f"{int(value):,}".replace(",", "'"))
            terms.append(str(int(value)))
        else:
            terms.append(f"{value:,.2f}".replace(",", "'"))
            terms.append(str(value))
        return terms
    if isinstance(value, str):
        text = evaluate._strip_trailing_ellipsis(value.strip())
        terms = []
        date = _ISO_DATE_RE.fullmatch(text)
        if date:
            year, month, day = date.groups()
            terms.append(f"{day}.{month}.{year}")
        if text:
            terms.append(text[:SEARCH_PREFIX_CHARS])
        return terms
    return []


def snippet(text: str, *values: Any) -> str:
    """±SNIPPET_RADIUS characters around the first occurrence (ignoring
    case) of the first value found, whitespace collapsed; "" if none is."""
    lowered = text.lower()
    for value in values:
        for term in search_terms(value):
            start = lowered.find(term.lower())
            if start >= 0:
                window = text[max(0, start - SNIPPET_RADIUS) : start + len(term) + SNIPPET_RADIUS]
                return _WHITESPACE_RE.sub(" ", window).strip()
    return ""


# --- loading ---


def load_runs(runs_dir: Path, run_ids: list[str], split: str) -> list[str]:
    """The shared doc_ids of the runs, after checking each run exists, is
    of `split`, and covers the same documents as the others."""
    doc_ids: list[str] | None = None
    for run_id in run_ids:
        path = runs_dir / run_id / "run.json"
        if not path.is_file():
            raise AuditError(f"no run.json at {path}")
        config = json.loads(path.read_text(encoding="utf-8"))
        if config["split"] != split:
            raise AuditError(f"run {run_id} is of split {config['split']!r}, not {split!r}")
        if doc_ids is None:
            doc_ids = config["doc_ids"]
        elif sorted(config["doc_ids"]) != sorted(doc_ids):
            raise AuditError(f"run {run_id} covers different documents than {run_ids[0]}")
    return list(doc_ids or [])


def audit(
    *,
    split: str,
    run_ids: list[str],
    runs_dir: Path,
    gold_dir: Path,
    raw_dir: Path,
    min_agree: int,
    sample_size: int,
    seed: int,
    extras_registry: set[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    """(candidate rows, random-sample rows). Reads only."""
    if extras_registry is None:
        extras_registry = parse_responses._load_extras_registry()
    doc_ids = load_runs(runs_dir, run_ids, split)

    candidates: list[dict] = []
    agreed: list[tuple[Cell, str]] = []
    for doc_id in doc_ids:
        gold_path = gold_dir / f"{doc_id}.json"
        if not gold_path.is_file():
            raise AuditError(f"no gold file {gold_path}")
        gold = json.loads(gold_path.read_text(encoding="utf-8"))
        parsed_by_run = {}
        for run_id in run_ids:
            parsed_path = runs_dir / run_id / "parsed" / f"{doc_id}.json"
            if not parsed_path.is_file():
                raise AuditError(f"no parsed file {parsed_path} — run parse_responses.py on {run_id} first")
            parsed_by_run[run_id] = json.loads(parsed_path.read_text(encoding="utf-8"))
        raw_path = raw_dir / f"{doc_id}.txt"
        text = raw_path.read_text(encoding="utf-8") if raw_path.is_file() else ""

        for cell in document_cells(gold, parsed_by_run, extras_registry):
            found = consensus_against_gold(cell, min_agree)
            if found is not None:
                value, n_agree = found
                candidates.append(_csv_row(cell, run_ids, value, n_agree, snippet(text, cell.gold, value)))
            elif all_agree_with_gold(cell, run_ids):
                agreed.append((cell, text))

    rng = random.Random(seed)
    drawn = sorted(rng.sample(range(len(agreed)), min(sample_size, len(agreed))))
    sample = [
        _csv_row(cell, run_ids, cell.gold, len(cell.votes), snippet(text, cell.gold))
        for cell, text in (agreed[i] for i in drawn)
    ]
    return candidates, sample


# --- output ---


def csv_columns(run_ids: list[str]) -> list[str]:
    return [
        "doc_id",
        "field",
        "item",
        "attribute",
        "gold_value",
        "consensus_value",
        "n_agree",
        "n_voting",
        *run_ids,
        "snippet",
        "verdict",
        "note",
    ]


def _json_cell(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _csv_row(cell: Cell, run_ids: list[str], consensus: Any, n_agree: int, context: str) -> dict:
    return {
        "doc_id": cell.doc_id,
        "field": cell.field,
        "item": cell.item,
        "attribute": cell.attribute,
        "gold_value": _json_cell(cell.gold),
        "consensus_value": _json_cell(consensus),
        "n_agree": n_agree,
        "n_voting": len(cell.votes),
        # An abstaining run gets an empty cell; a null vote is "null".
        **{run_id: _json_cell(cell.votes[run_id]) if run_id in cell.votes else "" for run_id in run_ids},
        "snippet": context,
        "verdict": "",
        "note": "",
    }


def write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def _is_inside(path: Path, directory: Path) -> bool:
    return path.resolve().is_relative_to(directory.resolve())


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="List gold cells that several models contradict in the same way.")
    parser.add_argument("--split", required=True, choices=sorted(evaluate.GOLD_DIRS))
    parser.add_argument(
        "--run-ids",
        nargs="+",
        default=None,
        help="Runs to compare (default: <split>-<model> for the five strong models)",
    )
    parser.add_argument("--min-agree", type=int, default=DEFAULT_MIN_AGREE)
    parser.add_argument("--random-sample", type=int, default=DEFAULT_RANDOM_SAMPLE)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--runs-dir", type=Path, default=evaluate.DEFAULT_RUNS_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="CSVs go to <out-dir>/<split>/")
    parser.add_argument("--gold-dir", type=Path, default=None, help="Override the split's gold directory (tests only)")
    parser.add_argument("--raw-dir", type=Path, default=None, help="Override the split's raw directory (tests only)")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing CSVs (default: refuse — they may hold verdicts)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    run_ids = args.run_ids or [f"{args.split}-{model}" for model in DEFAULT_MODELS]
    out_dir = args.out_dir / args.split
    if _is_inside(out_dir, DATA_DIR):
        print(f"error: refusing to write under {DATA_DIR}", file=sys.stderr)
        return 2
    paths = [out_dir / CANDIDATES_CSV, out_dir / RANDOM_SAMPLE_CSV]
    existing = [p for p in paths if p.exists()]
    # An existing CSV most likely holds verdicts written by hand;
    # overwriting it silently destroys that work.
    if existing and not args.force:
        print(
            f"error: {', '.join(map(str, existing))} already exist(s) and may hold verdicts; "
            "nothing was written. Pass --force to overwrite.",
            file=sys.stderr,
        )
        return 2

    try:
        candidates, sample = audit(
            split=args.split,
            run_ids=run_ids,
            runs_dir=args.runs_dir,
            gold_dir=args.gold_dir or evaluate.gold_dir_for_split(args.split),
            raw_dir=args.raw_dir or RAW_DIRS[args.split],
            min_agree=args.min_agree,
            sample_size=args.random_sample,
            seed=args.seed,
        )
    except (AuditError, evaluate.EvaluationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    columns = csv_columns(run_ids)
    write_csv(paths[0], columns, candidates)
    write_csv(paths[1], columns, sample)
    print(f"{len(candidates)} candidates (>= {args.min_agree} of {len(run_ids)} models agree against the gold)")
    print(f"{len(sample)} random cells where all models agree with the gold")
    print(f"wrote {paths[0]} and {paths[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
