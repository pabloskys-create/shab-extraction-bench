"""Tests for src/baselines/. Gold records are built by hand in tmp_path."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src import parse_responses
from src.baselines import build_run, rules, trivial


def _record(doc_id: str, **overrides) -> dict:
    record = {k: trivial.empty_value(k) for k in parse_responses.CORE_KEYS}
    record.update({"doc_id": doc_id, "uncertain": [], "_verified": True})
    record.update(overrides)
    return record


# --- trivial ---


def test_trivial_predicts_the_most_frequent_value():
    records = [
        _record("1", act_type="mutation", seat_canton="BE"),
        _record("2", act_type="mutation", seat_canton="ZH"),
        _record("3", act_type="loeschung", seat_canton="BE"),
    ]
    modes = trivial.most_frequent_values(records)
    assert modes["act_type"] == "mutation"
    assert modes["seat_canton"] == "BE"
    assert set(modes) == set(parse_responses.CORE_KEYS)


def test_trivial_predicts_null_when_every_value_occurs_once():
    records = [_record(str(i), uid=f"CHE-000.000.00{i}") for i in range(3)]
    assert trivial.most_frequent_values(records)["uid"] is None


def test_trivial_falls_back_to_empty_list_for_list_fields():
    records = [_record("1", alternative_names=["A"]), _record("2", alternative_names=["B"])]
    assert trivial.most_frequent_values(records)["alternative_names"] == []


def test_trivial_breaks_ties_independently_of_file_order():
    records = [_record("1", seat_canton="ZH"), _record("2", seat_canton="BE")] * 2
    assert trivial.most_frequent_values(records)["seat_canton"] == "BE"
    assert trivial.most_frequent_values(list(reversed(records)))["seat_canton"] == "BE"


# --- rules ---


def test_rules_prediction_has_exactly_the_contract_keys_and_null_where_prefill_is_silent():
    prediction = rules.rules_prediction("nothing a regex could find", "0001")
    assert list(prediction) == list(parse_responses.CORE_KEYS)
    assert prediction["company_name_new"] is None
    assert prediction["persons_changed"] == []


# --- build_run ---


def _data_root(tmp_path: Path, *, holdout_gold_verified: bool = True, holdout_gold_count: int = 2) -> Path:
    root = tmp_path / "data"
    (root / "raw").mkdir(parents=True)
    (root / "exploratory").mkdir()
    (root / "holdout" / "raw").mkdir(parents=True)
    (root / "holdout" / "gold").mkdir()
    # Dev: "mutation" is the mode. Holdout: "loeschung" would be.
    for i, act_type in enumerate(["mutation", "mutation", "loeschung"], start=1):
        doc_id = f"{i:04d}"
        (root / "raw" / f"{doc_id}.txt").write_text("text", encoding="utf-8")
        (root / "exploratory" / f"{doc_id}.json").write_text(
            json.dumps(_record(doc_id, act_type=act_type)), encoding="utf-8"
        )
    for i in range(2):
        doc_id = f"{121 + i:04d}"
        (root / "holdout" / "raw" / f"{doc_id}.txt").write_text("text", encoding="utf-8")
        if i < holdout_gold_count:
            gold = _record(doc_id, act_type="loeschung", _verified=holdout_gold_verified)
            (root / "holdout" / "gold" / f"{doc_id}.json").write_text(json.dumps(gold), encoding="utf-8")
    return root


def _clean_git(_path: Path) -> str:
    return ""


def test_trivial_holdout_baseline_uses_only_development_gold(tmp_path):
    root = _data_root(tmp_path)
    run_dir = build_run.build_run(
        "trivial", "holdout", data_root=root, runs_dir=tmp_path / "runs", git_status=_clean_git
    )
    for doc_id in ("0121", "0122"):
        raw = json.loads((run_dir / "raw" / f"{doc_id}.json").read_text(encoding="utf-8"))
        assert json.loads(raw["response_text"])["act_type"] == "mutation"
    run_config = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert run_config["split"] == "holdout"
    assert run_config["baseline"]["modes_from"] == "dev"


def test_baseline_raw_goes_through_parse_responses_cleanly(tmp_path):
    root = _data_root(tmp_path)
    run_dir = build_run.build_run("trivial", "dev", data_root=root, runs_dir=tmp_path / "runs", git_status=_clean_git)
    raw = json.loads((run_dir / "raw" / "0001.json").read_text(encoding="utf-8"))
    parsed = parse_responses.parse_response(raw, extras_registry=set())
    assert parsed["parse_status"] == "ok"
    assert parsed["missing_keys"] == parsed["extra_keys"] == []


@pytest.mark.parametrize(
    ("kwargs", "git_output", "message"),
    [
        ({"holdout_gold_count": 1}, "", "no gold file"),
        ({"holdout_gold_verified": False}, "", "not _verified"),
        ({}, "?? data/holdout/gold/0122.json", "uncommitted"),
    ],
)
def test_holdout_guard_refuses_until_gold_is_complete_verified_and_committed(tmp_path, kwargs, git_output, message):
    root = _data_root(tmp_path, **kwargs)
    with pytest.raises(build_run.HoldoutNotReady, match=message):
        build_run.build_run(
            "rules", "holdout", data_root=root, runs_dir=tmp_path / "runs", git_status=lambda _p: git_output
        )
    assert not (tmp_path / "runs").exists()


def test_build_run_refuses_to_overwrite_an_existing_run(tmp_path):
    root = _data_root(tmp_path)
    build_run.build_run("rules", "dev", data_root=root, runs_dir=tmp_path / "runs", git_status=_clean_git)
    with pytest.raises(FileExistsError):
        build_run.build_run("rules", "dev", data_root=root, runs_dir=tmp_path / "runs", git_status=_clean_git)
