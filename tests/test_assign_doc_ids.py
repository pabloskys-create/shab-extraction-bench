"""Tests for src/assign_doc_ids.py."""

import json
from pathlib import Path

import pytest

from src.assign_doc_ids import (
    AssignmentError,
    assign,
    headline,
    read_pasted_headlines,
    reassign_manifest,
)

TITLES = {
    "HR02-0000001": "Mutation Alpha AG, Bern",
    "HR02-0000002": "Neueintragung Beta GmbH, Luzern",
    "HR02-0000003": "Löschung Gamma AG, Zürich",
    "HR02-0000004": "Mutation Delta AG, Thun",
}


def _records(*numbers: str, start: int = 29) -> list[dict]:
    """Manifest records in frame-position order, doc_ids running in step."""
    return [
        {
            "position": offset + 1,
            "doc_id": f"{start + offset:04d}",
            "publication_number": number,
            "title": TITLES[number],
        }
        for offset, number in enumerate(numbers)
    ]


def _pasted(**by_doc_id: str) -> dict[str, str]:
    """{doc_id: headline}, given {doc_id: publication_number}."""
    return {doc_id: TITLES[number] for doc_id, number in by_doc_id.items()}


# --- headline reading ---


def test_headline_takes_the_first_line_without_surrounding_space():
    assert headline("  Mutation Alpha AG, Bern \nbody\n") == "Mutation Alpha AG, Bern"


def test_headline_collapses_internal_whitespace():
    # A paste can pick up a run of spaces from the page; the listing title
    # never has one, so comparing normalized forms is what makes them equal.
    assert headline("Mutation  Alpha   AG,\tBern\nbody") == "Mutation Alpha AG, Bern"


def test_headline_of_an_empty_file_is_empty():
    assert headline("") == ""


def test_read_pasted_headlines_ignores_files_outside_the_pool(tmp_path):
    (tmp_path / "0001.txt").write_text("Mutation Exploratory AG, Sion\nbody", encoding="utf-8")
    (tmp_path / "0029.txt").write_text(TITLES["HR02-0000001"] + "\nbody", encoding="utf-8")
    pasted = read_pasted_headlines(tmp_path, pool={"0029", "0030"})
    assert pasted == {"0029": TITLES["HR02-0000001"]}


# --- assignment ---


def test_a_pasted_publication_keeps_the_doc_id_of_its_file():
    records = _records("HR02-0000001", "HR02-0000002", "HR02-0000003")
    # Its text sits in 0031, not in the 0029 the manifest proposes.
    result = assign(records, _pasted(**{"0031": "HR02-0000001"}))
    assert result.kept == {"HR02-0000001": "0031"}


def test_unpasted_publications_take_the_free_numbers_in_frame_order():
    records = _records("HR02-0000001", "HR02-0000002", "HR02-0000003")
    result = assign(records, _pasted(**{"0031": "HR02-0000001"}))
    assert result.to_paste == {"HR02-0000002": "0029", "HR02-0000003": "0030"}


def test_every_publication_receives_exactly_one_doc_id():
    records = _records("HR02-0000001", "HR02-0000002", "HR02-0000003", "HR02-0000004")
    result = assign(records, _pasted(**{"0032": "HR02-0000002", "0029": "HR02-0000004"}))
    assigned = {**result.kept, **result.to_paste}
    assert set(assigned) == set(TITLES)
    assert sorted(assigned.values()) == ["0029", "0030", "0031", "0032"]


def test_the_doc_id_pool_is_unchanged_by_re_assignment():
    records = _records("HR02-0000001", "HR02-0000002", "HR02-0000003")
    result = assign(records, _pasted(**{"0031": "HR02-0000001"}))
    assigned = {**result.kept, **result.to_paste}
    assert sorted(assigned.values()) == [r["doc_id"] for r in records]


def test_a_file_holding_no_sampled_publication_is_reported_not_assigned():
    records = _records("HR02-0000001", "HR02-0000002")
    pasted = {"0029": TITLES["HR02-0000001"], "0030": "Kraftloserklärung Something, Uster"}
    result = assign(records, pasted)
    assert result.unmatched_files == ["0030"]
    assert result.kept == {"HR02-0000001": "0029"}
    # 0030 is free again, so the unpasted publication is given it.
    assert result.to_paste == {"HR02-0000002": "0030"}


def test_assignment_is_idempotent():
    records = _records("HR02-0000001", "HR02-0000002", "HR02-0000003")
    pasted = _pasted(**{"0031": "HR02-0000001"})
    once = assign(records, pasted)
    fitted = [
        {**record, "doc_id": {**once.kept, **once.to_paste}[record["publication_number"]]}
        for record in records
    ]
    twice = assign(fitted, pasted)
    assert {**twice.kept, **twice.to_paste} == {**once.kept, **once.to_paste}


def test_nothing_moves_when_the_manifest_already_fits():
    records = _records("HR02-0000001", "HR02-0000002")
    pasted = _pasted(**{"0029": "HR02-0000001", "0030": "HR02-0000002"})
    result = assign(records, pasted)
    assert result.kept == {"HR02-0000001": "0029", "HR02-0000002": "0030"}
    assert result.to_paste == {}


def test_the_same_publication_pasted_twice_is_an_error():
    records = _records("HR02-0000001", "HR02-0000002")
    pasted = _pasted(**{"0029": "HR02-0000001", "0030": "HR02-0000001"})
    with pytest.raises(AssignmentError, match="pasted twice"):
        assign(records, pasted)


def test_two_records_sharing_a_headline_is_an_error():
    records = _records("HR02-0000001", "HR02-0000002")
    records[1]["title"] = records[0]["title"]
    with pytest.raises(AssignmentError, match="share the headline"):
        assign(records, {})


def test_a_manifest_repeating_a_doc_id_is_an_error():
    records = _records("HR02-0000001", "HR02-0000002")
    records[1]["doc_id"] = records[0]["doc_id"]
    with pytest.raises(AssignmentError, match="same doc_id"):
        assign(records, {})


# --- manifest rewriting ---


def test_reassign_manifest_keeps_position_order_and_every_other_field():
    manifest = {
        "metadata": {"seed": 42},
        "records": _records("HR02-0000001", "HR02-0000002", "HR02-0000003"),
    }
    reassigned, _ = reassign_manifest(manifest, _pasted(**{"0031": "HR02-0000001"}))
    assert [r["position"] for r in reassigned["records"]] == [1, 2, 3]
    assert [r["publication_number"] for r in reassigned["records"]] == [
        "HR02-0000001",
        "HR02-0000002",
        "HR02-0000003",
    ]
    assert [r["title"] for r in reassigned["records"]] == [
        TITLES[n] for n in ("HR02-0000001", "HR02-0000002", "HR02-0000003")
    ]


def test_reassign_manifest_lets_doc_id_run_out_of_step_with_position():
    manifest = {"metadata": {}, "records": _records("HR02-0000001", "HR02-0000002")}
    reassigned, _ = reassign_manifest(manifest, _pasted(**{"0030": "HR02-0000001"}))
    assert [r["doc_id"] for r in reassigned["records"]] == ["0030", "0029"]


def test_reassign_manifest_records_what_it_did_in_the_metadata():
    manifest = {"metadata": {"seed": 42}, "records": _records("HR02-0000001", "HR02-0000002")}
    reassigned, _ = reassign_manifest(manifest, _pasted(**{"0030": "HR02-0000001"}))
    assert reassigned["metadata"]["seed"] == 42
    note = reassigned["metadata"]["doc_id_assignment"]
    assert "assign_doc_ids.py" in note
    assert "1 publications keep" in note


def test_reassign_manifest_does_not_mutate_its_input():
    manifest = {"metadata": {}, "records": _records("HR02-0000001", "HR02-0000002")}
    before = json.dumps(manifest, sort_keys=True)
    reassign_manifest(manifest, _pasted(**{"0030": "HR02-0000001"}))
    assert json.dumps(manifest, sort_keys=True) == before


def test_reassign_manifest_reads_real_files_end_to_end(tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "0031.txt").write_text(TITLES["HR02-0000001"] + "\nbody\n", encoding="utf-8")
    manifest = {
        "metadata": {},
        "records": _records("HR02-0000001", "HR02-0000002", "HR02-0000003"),
    }
    pool = {r["doc_id"] for r in manifest["records"]}
    reassigned, assignment = reassign_manifest(manifest, read_pasted_headlines(raw_dir, pool))
    assert assignment.kept == {"HR02-0000001": "0031"}
    assert reassigned["records"][0]["doc_id"] == "0031"
    assert Path(raw_dir / "0031.txt").exists(), "re-assignment must not touch any file"
