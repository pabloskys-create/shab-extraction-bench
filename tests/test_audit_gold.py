"""Tests for src/audit_gold.py. Every gold record, run and notice here is
built by hand in tmp_path; nothing is read from the real gold or runs."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from src import audit_gold, parse_responses
from src.audit_gold import Cell

REGISTRY = parse_responses._load_extras_registry()
RUNS = ["dev-a", "dev-b", "dev-c", "dev-d", "dev-e"]
NOTICE = (
    "Foo AG, in Bern, CHE-123.456.789, Aktiengesellschaft (SHAB Nr. 12 vom 03.02.2025). "
    "Kapital neu: CHF 150'000.00. Ausgeschiedene Personen: Muster, Hans, deutscher "
    "Staatsangehöriger, in Thun, Mitglied, mit Einzelunterschrift."
)


def _gold(**overrides) -> dict:
    record = dict.fromkeys(parse_responses.CORE_KEYS)
    record.update(
        {
            "doc_id": "0001",
            "act_type": "mutation",
            "act_subtypes": ["kapitalerhoehung"],
            "company_name_full": "Foo AG",
            "alternative_names": [],
            "seat_municipality": "Bern",
            "capital_new_chf": 150000,
            "persons_added": [],
            "persons_removed": [],
            "persons_changed": [],
            "extras": {},
            "uncertain": [],
        }
    )
    record.update(overrides)
    return record


def _person(**overrides) -> dict:
    person = dict.fromkeys(parse_responses.PERSON_KEYS_ORDER)
    person.update(overrides)
    return person


def _data(gold: dict, **overrides) -> dict:
    data = {k: gold[k] for k in parse_responses.CORE_KEYS}
    data.update(overrides)
    return data


def _ok(data: dict) -> dict:
    return {"doc_id": data.get("doc_id", "0001"), "parse_status": "ok", "data": data}


FAILED = {"doc_id": "0001", "parse_status": "failed", "data": None}


def _cell(cells: list[Cell], field: str, item: str = "", attribute: str = "") -> Cell:
    [cell] = [c for c in cells if c.field == field and c.item == item and c.attribute == attribute]
    return cell


# --- cells and votes ---


def test_scalar_votes_use_normalized_comparison():
    gold = _gold()
    parsed = {r: _ok(_data(gold, seat_municipality="  BERN ")) for r in RUNS}
    cell = _cell(audit_gold.document_cells(gold, parsed, REGISTRY), "seat_municipality")
    assert audit_gold.consensus_against_gold(cell, 3) is None
    assert audit_gold.all_agree_with_gold(cell, RUNS)


def test_number_and_clean_number_string_agree_as_in_evaluate():
    gold = _gold()
    parsed = {r: _ok(_data(gold, capital_new_chf="150000.00")) for r in RUNS}
    cell = _cell(audit_gold.document_cells(gold, parsed, REGISTRY), "capital_new_chf")
    assert audit_gold.consensus_against_gold(cell, 3) is None


def test_three_models_agreeing_against_gold_is_a_candidate():
    gold = _gold()
    parsed = {
        "dev-a": _ok(_data(gold, seat_municipality="Thun")),
        "dev-b": _ok(_data(gold, seat_municipality="thun")),
        "dev-c": _ok(_data(gold, seat_municipality="Thun ")),
        "dev-d": _ok(_data(gold)),
        "dev-e": _ok(_data(gold, seat_municipality="Biel")),
    }
    cell = _cell(audit_gold.document_cells(gold, parsed, REGISTRY), "seat_municipality")
    value, n_agree = audit_gold.consensus_against_gold(cell, 3)
    assert value == "Thun" and n_agree == 3
    assert not audit_gold.all_agree_with_gold(cell, RUNS)


def test_two_agreeing_is_not_enough_at_min_agree_three():
    gold = _gold()
    parsed = {r: _ok(_data(gold)) for r in RUNS}
    parsed["dev-a"] = _ok(_data(gold, seat_municipality="Thun"))
    parsed["dev-b"] = _ok(_data(gold, seat_municipality="Thun"))
    cell = _cell(audit_gold.document_cells(gold, parsed, REGISTRY), "seat_municipality")
    assert audit_gold.consensus_against_gold(cell, 3) is None
    assert audit_gold.consensus_against_gold(cell, 2) == ("Thun", 2)


def test_consensus_null_against_gold_value_is_a_candidate():
    gold = _gold()
    parsed = {r: _ok(_data(gold, capital_new_chf=None)) for r in RUNS}
    cell = _cell(audit_gold.document_cells(gold, parsed, REGISTRY), "capital_new_chf")
    assert audit_gold.consensus_against_gold(cell, 3) == (None, 5)


def test_failed_parse_abstains():
    gold = _gold()
    parsed = {r: _ok(_data(gold)) for r in RUNS}
    parsed["dev-e"] = FAILED
    cell = _cell(audit_gold.document_cells(gold, parsed, REGISTRY), "seat_municipality")
    assert "dev-e" not in cell.votes
    # Agreement with the gold needs every run to vote.
    assert not audit_gold.all_agree_with_gold(cell, RUNS)


def test_uncertain_field_is_skipped():
    gold = _gold(uncertain=["seat_municipality", "extras"], extras={"zweck": "Handel"})
    parsed = {r: _ok(_data(gold, seat_municipality="Thun")) for r in RUNS}
    cells = audit_gold.document_cells(gold, parsed, REGISTRY)
    assert not [c for c in cells if c.field in ("seat_municipality", "extras")]


def test_act_subtypes_compared_as_sets():
    gold = _gold(act_subtypes=["kapitalerhoehung", "organaenderung"])
    parsed = {r: _ok(_data(gold, act_subtypes=["organaenderung", "kapitalerhoehung"])) for r in RUNS}
    parsed["dev-a"] = _ok(_data(gold, act_subtypes=["organaenderung"]))
    parsed["dev-b"] = _ok(_data(gold, act_subtypes=["organaenderung"]))
    parsed["dev-c"] = _ok(_data(gold, act_subtypes=["Organaenderung"]))
    cell = _cell(audit_gold.document_cells(gold, parsed, REGISTRY), "act_subtypes")
    assert audit_gold.consensus_against_gold(cell, 3) == (["organaenderung"], 3)


def test_empty_list_and_null_set_agree():
    gold = _gold(alternative_names=[])
    parsed = {r: _ok(_data(gold, alternative_names=None)) for r in RUNS}
    cell = _cell(audit_gold.document_cells(gold, parsed, REGISTRY), "alternative_names")
    assert audit_gold.consensus_against_gold(cell, 3) is None


# --- extras ---


def test_extras_key_missing_from_gold_is_a_candidate():
    gold = _gold()
    parsed = {r: _ok(_data(gold, extras={"zweck": "Handel mit Waren."})) for r in RUNS}
    cell = _cell(audit_gold.document_cells(gold, parsed, REGISTRY), "extras", item="zweck")
    assert cell.gold is None
    assert audit_gold.consensus_against_gold(cell, 3) == ("Handel mit Waren.", 5)


def test_extras_unregistered_key_is_not_a_cell():
    gold = _gold()
    parsed = {r: _ok(_data(gold, extras={"not_a_registered_key": "x"})) for r in RUNS}
    cells = audit_gold.document_cells(gold, parsed, REGISTRY)
    assert not [c for c in cells if c.field == "extras"]


def test_extras_zweck_uses_prefix_matcher():
    zweck = "Die Gesellschaft bezweckt " + "den Handel mit Waren aller Art " * 5
    gold = _gold(extras={"zweck": zweck + "…"})
    parsed = {r: _ok(_data(gold, extras={"zweck": zweck + "und Dienstleistungen."})) for r in RUNS}
    cell = _cell(audit_gold.document_cells(gold, parsed, REGISTRY), "extras", item="zweck")
    assert audit_gold.consensus_against_gold(cell, 3) is None
    assert audit_gold.all_agree_with_gold(cell, RUNS)


# --- persons ---


def test_person_attribute_disagreement_on_matched_person():
    gold = _gold(persons_removed=[_person(name="Muster, Hans", domicile="Bern", nationality="deutscher")])
    wrong_gold_domicile = [_person(name="muster, hans", domicile="Thun", nationality="deutscher")]
    parsed = {r: _ok(_data(gold, persons_removed=wrong_gold_domicile)) for r in RUNS}
    cells = audit_gold.document_cells(gold, parsed, REGISTRY)
    domicile = _cell(cells, "persons_removed", item="Muster, Hans", attribute="domicile")
    assert audit_gold.consensus_against_gold(domicile, 3) == ("Thun", 5)
    nationality = _cell(cells, "persons_removed", item="Muster, Hans", attribute="nationality")
    assert audit_gold.consensus_against_gold(nationality, 3) is None
    # The name itself is the matching key, never an attribute cell.
    assert not [c for c in cells if c.attribute == "name"]


def test_unmatched_person_abstains_and_extra_person_is_no_cell():
    gold = _gold(persons_removed=[_person(name="Muster, Hans", domicile="Bern")])
    parsed = {r: _ok(_data(gold, persons_removed=[_person(name="Other, Eva", domicile="Thun")])) for r in RUNS}
    parsed["dev-a"] = _ok(_data(gold))
    cells = audit_gold.document_cells(gold, parsed, REGISTRY)
    domicile = _cell(cells, "persons_removed", item="Muster, Hans", attribute="domicile")
    assert set(domicile.votes) == {"dev-a"}
    assert not [c for c in cells if c.item == "Other, Eva"]


def test_persons_changed_matched_on_name_new():
    keys = dict(parse_responses.PERSON_LIST_FIELDS)["persons_changed"]
    changed = dict.fromkeys(keys)
    changed.update({"name_new": "Muster, Hans", "role_new": "Präsident"})
    gold = _gold(persons_changed=[changed])
    pred = {**changed, "role_new": "Mitglied"}
    parsed = {r: _ok(_data(gold, persons_changed=[pred])) for r in RUNS}
    cell = _cell(audit_gold.document_cells(gold, parsed, REGISTRY), "persons_changed", "Muster, Hans", "role_new")
    assert audit_gold.consensus_against_gold(cell, 3) == ("Mitglied", 5)


# --- snippet ---


def test_snippet_finds_iso_date_as_swiss_date():
    found = audit_gold.snippet(NOTICE, "2025-02-03")
    assert "03.02.2025" in found


def test_snippet_finds_number_with_thousands_separator():
    assert "150'000.00" in audit_gold.snippet(NOTICE, 150000)


def test_snippet_falls_back_to_consensus_value_and_is_case_insensitive():
    found = audit_gold.snippet(NOTICE, "Zürich", "THUN")
    assert "in Thun" in found


def test_snippet_window_is_bounded():
    text = "x" * 1000 + "Thun" + "y" * 1000
    found = audit_gold.snippet(text, "Thun")
    assert len(found) == 200 + 4 + 200


def test_snippet_empty_when_not_found():
    assert audit_gold.snippet(NOTICE, "Zürich", None) == ""


# --- end to end ---


def _write_json(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")


def _setup(tmp_path: Path, n_docs: int = 3) -> dict[str, Path]:
    gold_dir, raw_dir, runs_dir = tmp_path / "gold", tmp_path / "raw", tmp_path / "runs"
    doc_ids = [f"{i:04d}" for i in range(1, n_docs + 1)]
    for doc_id in doc_ids:
        gold = _gold(doc_id=doc_id)
        _write_json(gold_dir / f"{doc_id}.json", gold)
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / f"{doc_id}.txt").write_text(NOTICE, encoding="utf-8")
        for run_id in RUNS:
            _write_json(runs_dir / run_id / "run.json", {"run_id": run_id, "split": "dev", "doc_ids": doc_ids})
            # Doc 0001: every run says Thun, the gold says Bern.
            data = _data(gold, seat_municipality="Thun") if doc_id == "0001" else _data(gold)
            _write_json(runs_dir / run_id / "parsed" / f"{doc_id}.json", {**_ok(data), "doc_id": doc_id})
    return {"gold": gold_dir, "raw": raw_dir, "runs": runs_dir, "out": tmp_path / "out"}


def _main(dirs: dict[str, Path], *extra: str) -> int:
    return audit_gold.main(
        [
            "--split", "dev",
            "--run-ids", *RUNS,
            "--runs-dir", str(dirs["runs"]),
            "--gold-dir", str(dirs["gold"]),
            "--raw-dir", str(dirs["raw"]),
            "--out-dir", str(dirs["out"]),
            *extra,
        ]
    )  # fmt: skip


def _read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_main_writes_both_csvs(tmp_path):
    dirs = _setup(tmp_path)
    assert _main(dirs, "--random-sample", "5") == 0
    candidates = _read_csv(dirs["out"] / "dev" / "candidates.csv")
    assert [(r["doc_id"], r["field"]) for r in candidates] == [("0001", "seat_municipality")]
    [row] = candidates
    assert row["gold_value"] == '"Bern"'
    assert row["consensus_value"] == '"Thun"'
    assert row["n_agree"] == "5"
    assert all(row[r] == '"Thun"' for r in RUNS)
    assert "in Bern" in row["snippet"]
    assert row["verdict"] == "" and row["note"] == ""
    assert list(row) == audit_gold.csv_columns(RUNS)

    sample = _read_csv(dirs["out"] / "dev" / "random_sample.csv")
    assert len(sample) == 5
    assert all(r["gold_value"] == r["consensus_value"] and r["n_agree"] == "5" for r in sample)


def test_random_sample_is_deterministic(tmp_path):
    first = _setup(tmp_path / "one")
    second = _setup(tmp_path / "two")
    assert _main(first, "--random-sample", "7") == 0
    assert _main(second, "--random-sample", "7") == 0
    assert _read_csv(first["out"] / "dev" / "random_sample.csv") == _read_csv(
        second["out"] / "dev" / "random_sample.csv"
    )


def test_existing_csv_is_never_overwritten(tmp_path, capsys):
    dirs = _setup(tmp_path)
    out = dirs["out"] / "dev"
    out.mkdir(parents=True)
    (out / "candidates.csv").write_text("my verdicts", encoding="utf-8")
    assert _main(dirs) == 2
    assert (out / "candidates.csv").read_text(encoding="utf-8") == "my verdicts"
    assert not (out / "random_sample.csv").exists()
    assert "already exist" in capsys.readouterr().err

    assert _main(dirs, "--force") == 0
    assert (out / "candidates.csv").read_text(encoding="utf-8") != "my verdicts"


def test_refuses_to_write_under_data(tmp_path, capsys):
    dirs = _setup(tmp_path)
    dirs["out"] = audit_gold.DATA_DIR / "gold_audit_should_not_exist"
    assert _main(dirs) == 2
    assert not dirs["out"].exists()


def test_mismatched_doc_sets_are_an_error(tmp_path, capsys):
    dirs = _setup(tmp_path)
    _write_json(dirs["runs"] / "dev-e" / "run.json", {"run_id": "dev-e", "split": "dev", "doc_ids": ["0001"]})
    assert _main(dirs) == 2
    assert "different documents" in capsys.readouterr().err


def test_wrong_split_is_an_error(tmp_path, capsys):
    dirs = _setup(tmp_path)
    _write_json(dirs["runs"] / "dev-e" / "run.json", {"run_id": "dev-e", "split": "holdout", "doc_ids": []})
    assert _main(dirs) == 2
    assert "split" in capsys.readouterr().err


def test_missing_parsed_file_is_an_error(tmp_path, capsys):
    dirs = _setup(tmp_path)
    (dirs["runs"] / "dev-c" / "parsed" / "0002.json").unlink()
    assert _main(dirs) == 2
    assert "parse_responses.py" in capsys.readouterr().err


def test_default_run_ids_follow_split(tmp_path, monkeypatch):
    seen = {}

    def fake_audit(**kwargs):
        seen.update(kwargs)
        return [], []

    monkeypatch.setattr(audit_gold, "audit", fake_audit)
    assert audit_gold.main(["--split", "holdout", "--out-dir", str(tmp_path)]) == 0
    assert seen["run_ids"] == [f"holdout-{m}" for m in audit_gold.DEFAULT_MODELS]


@pytest.mark.parametrize("value", [True, None, {"a": 1}])
def test_search_terms_ignore_non_text_values(value):
    assert audit_gold.search_terms(value) == []
