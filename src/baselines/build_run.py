"""Write a baseline as an ordinary run, so nothing downstream special-cases it.

    python -m src.baselines.build_run --baseline trivial --split dev

writes `runs/baseline-<kind>-<split>/run.json` and `raw/NNNN.json` in the
same shape run_eval.py writes for a model (the prediction serialised into
`response_text`). From there it goes through parse_responses.py and
evaluate.py exactly like any model run.

Holdout guard (CLAUDE.md rule 8): no output for a holdout document until
every holdout gold file exists, is `_verified` and is committed. A
baseline is not a model, but the rule is applied to it all the same.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.baselines import rules, trivial
from src.run_eval import (
    discover_doc_ids,
    doc_text_path,
    git_commit_hash,
    git_is_dirty,
    write_json_atomic,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_ROOT = REPO_ROOT / "data"
DEFAULT_RUNS_DIR = REPO_ROOT / "runs"
BASELINES = ("trivial", "rules")


class HoldoutNotReady(Exception):
    """The holdout gold is incomplete, unverified or uncommitted."""


def _git_status_porcelain(path: Path) -> str:
    result = subprocess.run(
        ["git", "status", "--porcelain", "--", str(path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def holdout_problems(
    data_root: Path, git_status: Callable[[Path], str] = _git_status_porcelain
) -> list[str]:
    """Everything that stops the holdout from being run; empty when ready."""
    raw_ids = sorted(p.stem for p in (data_root / "holdout" / "raw").glob("*.txt"))
    gold_dir = data_root / "holdout" / "gold"
    problems = []
    missing = [d for d in raw_ids if not (gold_dir / f"{d}.json").is_file()]
    if missing:
        problems.append(f"{len(missing)} holdout document(s) have no gold file: {', '.join(missing)}")
    unverified = [
        d
        for d in raw_ids
        if d not in missing
        and json.loads((gold_dir / f"{d}.json").read_text(encoding="utf-8")).get("_verified") is not True
    ]
    if unverified:
        problems.append(f"holdout gold not _verified: {', '.join(unverified)}")
    uncommitted = git_status(gold_dir).strip()
    if uncommitted:
        problems.append(f"holdout gold has uncommitted changes:\n{uncommitted}")
    return problems


def baseline_predictions(
    kind: str, split: str, doc_ids: list[str], *, data_root: Path, dev_gold_dir: Path
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """({doc_id: prediction}, baseline metadata for run.json)."""
    if kind == "trivial":
        # Always the development gold, whatever the split (see trivial.py).
        modes = trivial.most_frequent_values(trivial.load_dev_records(dev_gold_dir))
        return {d: modes for d in doc_ids}, {"kind": kind, "modes_from": "dev", "modes": modes}
    if kind == "rules":
        predictions = {
            d: rules.rules_prediction(doc_text_path(split, d, data_root=data_root).read_text(encoding="utf-8"), d)
            for d in doc_ids
        }
        return predictions, {"kind": kind, "source": "src/prefill.py"}
    raise ValueError(f"unknown baseline {kind!r}")


def build_run(
    kind: str,
    split: str,
    *,
    data_root: Path = DEFAULT_DATA_ROOT,
    runs_dir: Path = DEFAULT_RUNS_DIR,
    git_status: Callable[[Path], str] = _git_status_porcelain,
) -> Path:
    if split == "holdout":
        problems = holdout_problems(data_root, git_status)
        if problems:
            raise HoldoutNotReady("refusing to write holdout output (CLAUDE.md rule 8):\n" + "\n".join(problems))

    run_id = f"baseline-{kind}-{split}"
    run_dir = runs_dir / run_id
    if run_dir.exists():
        raise FileExistsError(f"{run_dir} already exists; delete it to rebuild the baseline")

    doc_ids = discover_doc_ids(split, data_root=data_root)
    predictions, baseline_info = baseline_predictions(
        kind, split, doc_ids, data_root=data_root, dev_gold_dir=data_root / "exploratory"
    )
    commit, dirty = git_commit_hash(), git_is_dirty()
    now = datetime.now(tz=timezone.utc).isoformat()

    for doc_id in doc_ids:
        write_json_atomic(
            run_dir / "raw" / f"{doc_id}.json",
            {
                "doc_id": doc_id,
                "split": split,
                "model_requested": f"baseline-{kind}",
                "backend": "baseline",
                "timestamp": now,
                "git_commit": commit,
                "git_dirty": dirty,
                "status": "ok",
                "error": None,
                "response_text": json.dumps(predictions[doc_id], ensure_ascii=False),
            },
        )
    write_json_atomic(
        run_dir / "run.json",
        {
            "run_id": run_id,
            "split": split,
            "backend": "baseline",
            "model_requested": f"baseline-{kind}",
            "baseline": baseline_info,
            "git_commit": commit,
            "git_dirty": dirty,
            "params": None,
            "doc_ids": doc_ids,
            "started_at": now,
            "finished_at": datetime.now(tz=timezone.utc).isoformat(),
        },
    )
    return run_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write a baseline's predictions as a run (raw/ + run.json).")
    parser.add_argument("--baseline", choices=BASELINES, required=True)
    parser.add_argument("--split", choices=["dev", "holdout"], required=True)
    parser.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR)
    args = parser.parse_args(argv)
    try:
        run_dir = build_run(args.baseline, args.split, runs_dir=args.runs_dir)
    except (HoldoutNotReady, FileExistsError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"wrote {run_dir}; next: python -m src.parse_responses --run-id {run_dir.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
