"""Tests for src/crosscheck.py.

Most tests call `crosscheck_record` directly with small inline text/record
fixtures — no files needed. `test_crosscheck_doc_reads_both_files` is the
one integration test that exercises the actual file-reading path, against
tests/fixtures/crosscheck_sample.{txt,json}.
"""

import json
from pathlib import Path

import pytest

from src import crosscheck
from src.crosscheck import CrosscheckResult, crosscheck_doc, crosscheck_record

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures"


def _field_names(checks) -> list[str]:
    return [c.field for c in checks]


def _result_field_names(result: CrosscheckResult) -> set[str]:
    return (
        set(_field_names(result.unique))
        | set(_field_names(result.ambiguous))
        | set(_field_names(result.missing))
    )


# --- string fields ---


def test_string_value_found_once_is_unique():
    result = crosscheck_record("The UID is CHE-123.456.789 here.", {"uid": "CHE-123.456.789"})
    assert _field_names(result.unique) == ["uid"]
    assert result.unique[0].count == 1
    assert result.ambiguous == []
    assert result.missing == []


def test_string_value_found_several_times_is_ambiguous():
    result = crosscheck_record("Basel Basel Basel", {"seat_municipality": "Basel"})
    assert _field_names(result.ambiguous) == ["seat_municipality"]
    assert result.ambiguous[0].count == 3


def test_string_value_absent_is_missing():
    # e.g. a value copied in from a different document — the tool's stated purpose.
    result = crosscheck_record("nothing relevant here", {"authority": "Handelsregisteramt X"})
    assert _field_names(result.missing) == ["authority"]
    assert result.missing[0].count == 0


# --- date fields: ISO in the JSON, DD.MM.YYYY in the source ---


def test_date_field_matches_via_swiss_format():
    result = crosscheck_record(
        "Tagesregister-Nr. 123 vom 18.08.2026", {"tagesregister_date": "2026-08-18"}
    )
    assert _field_names(result.unique) == ["tagesregister_date"]
    check = result.unique[0]
    assert check.count == 1
    assert check.searched == ["2026-08-18", "18.08.2026"]


def test_date_field_missing_entirely():
    result = crosscheck_record("no dates here", {"act_date": "2026-01-01"})
    assert _field_names(result.missing) == ["act_date"]


# --- int fields: Swiss apostrophe grouping ---


def test_int_field_matches_via_swiss_grouping():
    result = crosscheck_record(
        "SHAB Nr. 1'234 was cited.", {"prior_publication_shab_nr": 1234}
    )
    assert _field_names(result.unique) == ["prior_publication_shab_nr"]
    check = result.unique[0]
    assert check.count == 1
    assert check.searched == ["1234", "1'234"]


def test_small_int_does_not_double_count_identical_candidates():
    # str(223) and the Swiss-grouped form are the same string ("223", no
    # grouping below 1000) — must be deduplicated, not counted twice per hit.
    text = "223 first, 223 second, 223 third."
    result = crosscheck_record(text, {"prior_publication_shab_nr": 223})
    check = result.ambiguous[0]
    assert check.searched == ["223"]
    assert check.count == 3


# --- number fields: Swiss apostrophe grouping + 2 decimals ---


def test_number_field_matches_via_swiss_money_format():
    result = crosscheck_record(
        "Aktienkapital neu: CHF 189'123.50.", {"capital_new_chf": 189123.5}
    )
    assert _field_names(result.unique) == ["capital_new_chf"]
    check = result.unique[0]
    assert check.count == 1
    assert "189'123.50" in check.searched


def test_number_field_missing():
    result = crosscheck_record("no money mentioned", {"capital_previous_chf": 50000.0})
    assert _field_names(result.missing) == ["capital_previous_chf"]


# --- fields that must never appear in any list ---


def test_null_fields_are_skipped_entirely():
    result = crosscheck_record("some text", {"notes": None})
    assert "notes" not in _result_field_names(result)


def test_bookkeeping_fields_are_always_skipped():
    # doc_id/schema_version would always land in "missing", and language's
    # 2-character value ("de") spuriously substring-matches inside ordinary
    # German words — see src/crosscheck.py _SKIPPED_FIELDS.
    text = "Gesellschafterin und Geschäftsführerin, in Bern"  # full of "de"
    record = {"doc_id": "0001", "schema_version": "0.2", "language": "de"}
    result = crosscheck_record(text, record)
    assert _result_field_names(result) == set()


def test_annotator_prose_fields_are_always_skipped():
    # notes is the annotator's own commentary and uncertain holds field
    # names, so neither is ever a literal quote from the source text.
    record = {"notes": "Sole board member; cannot sign alone.", "uncertain": ["seat_canton"]}
    result = crosscheck_record("unrelated text", record)
    assert _result_field_names(result) == set()


def test_composite_kind_fields_are_skipped():
    record = {
        "alternative_names": ["Foo Bar SA"],
        "extras": {"x": True},
        "_verified": True,
        "persons_added": [{"name": "X"}],
        "act_subtypes": ["fusion"],
        "uncertain": ["notes"],
    }
    result = crosscheck_record("Foo Bar SA fusion X notes", record)
    assert _result_field_names(result) == set()


# --- doc-level integration (real file reads) ---


def test_crosscheck_doc_reads_both_files(monkeypatch):
    monkeypatch.setattr(crosscheck, "DATA_RAW", FIXTURES)
    monkeypatch.setattr(crosscheck, "DATA_EXPLORATORY", FIXTURES)

    result = crosscheck_doc("crosscheck_sample")

    assert _field_names(result.unique) == [
        "uid",
        "tagesregister_nr",
        "tagesregister_date",
        "prior_publication_id",
        "authority",
        "capital_new_chf",
    ]
    assert _field_names(result.ambiguous) == [
        "legal_form",
        "prior_publication_shab_nr",
        "prior_publication_date",
    ]
    # seat_municipality ("Bern") is deliberately wrong in the fixture — the text
    # says Zürich — this is exactly the contamination case the tool targets.
    assert "seat_municipality" in _field_names(result.missing)
    assert "capital_previous_chf" in _field_names(result.missing)
    # bookkeeping fields never appear even though the fixture sets them
    assert _result_field_names(result).isdisjoint({"doc_id", "schema_version", "language"})


def test_crosscheck_doc_raises_for_missing_doc(monkeypatch):
    monkeypatch.setattr(crosscheck, "DATA_RAW", FIXTURES)
    monkeypatch.setattr(crosscheck, "DATA_EXPLORATORY", FIXTURES)
    try:
        crosscheck_doc("no-such-doc")
        assert False, "expected FileNotFoundError"
    except FileNotFoundError:
        pass


# --- multi-line address blocks in the header ---


def _text(*lines: str) -> str:
    """A source text built from its lines, as the raw files store it."""
    return "\n".join(lines)


HEADER = _text(
    "Mutation Liyame Gastro GmbH, Herzogenbuchsee, neu Burgdorf",
    "Liyame Gastro GmbH",
    "Gyrischachenstrasse 38",
    "3400 Burgdorf",
    "Bisher",
    "Zürichstrasse 11",
    "3360 Herzogenbuchsee",
)


def test_join_address_lines_joins_street_to_postal_code_line():
    joined = crosscheck._join_address_lines(HEADER).splitlines()
    assert "Gyrischachenstrasse 38, 3400 Burgdorf" in joined
    assert "Zürichstrasse 11, 3360 Herzogenbuchsee" in joined


def test_join_address_lines_keeps_everything_else_intact():
    joined = crosscheck._join_address_lines(HEADER).splitlines()
    assert joined[0] == "Mutation Liyame Gastro GmbH, Herzogenbuchsee, neu Burgdorf"
    assert "Liyame Gastro GmbH" in joined
    assert "Bisher" in joined


def test_join_address_lines_attaches_a_co_line_to_its_own_block_only():
    # Regression: the "c/o" rule must not keep swallowing the rest of the
    # notice — it attaches the street that follows it and stops there.
    text = _text(
        "denkarbeit GmbH",
        "c/o Roland Hunziker",
        "Hinterwies 31",
        "9042 Speicher",
        "Bisher",
        "Achenbergstrasse 9",
        "5000 Aarau",
    )

    joined = crosscheck._join_address_lines(text).splitlines()

    assert joined == [
        "denkarbeit GmbH",
        "c/o Roland Hunziker, Hinterwies 31, 9042 Speicher",
        "Bisher",
        "Achenbergstrasse 9, 5000 Aarau",
    ]


def test_join_address_lines_does_not_join_across_a_blank_line():
    text = "c/o Roland Hunziker\n   \nHinterwies 31"
    assert crosscheck._join_address_lines(text).splitlines() == [
        "c/o Roland Hunziker",
        "   ",
        "Hinterwies 31",
    ]


def test_previous_domicile_from_the_bisher_block_is_found():
    # The whole point: the annotation writes the address as one string,
    # the header prints it over two lines.
    result = crosscheck_record(
        HEADER, {"domicile_previous": "Zürichstrasse 11, 3360 Herzogenbuchsee"}
    )
    assert _field_names(result.unique) == ["domicile_previous"]


def test_a_domicile_absent_from_the_text_is_still_missing():
    # Joining must not make everything match: a fabricated address stays a
    # finding.
    result = crosscheck_record(
        HEADER, {"domicile_previous": "Bahnhofstrasse 1, 8001 Zürich"}
    )
    assert _field_names(result.missing) == ["domicile_previous"]


def test_a_line_that_was_found_before_joining_is_still_found():
    result = crosscheck_record(HEADER, {"seat_municipality": "Herzogenbuchsee"})
    assert result.ambiguous[0].field == "seat_municipality"


# --- batch mode over verified exploratory records ---


def _write_pair(tmp_path, doc_id, text, record):
    (tmp_path / f"{doc_id}.txt").write_text(text, encoding="utf-8")
    (tmp_path / f"{doc_id}.json").write_text(json.dumps(record), encoding="utf-8")


def _use_tmp_data(monkeypatch, tmp_path):
    monkeypatch.setattr(crosscheck, "DATA_RAW", tmp_path)
    monkeypatch.setattr(crosscheck, "DATA_EXPLORATORY", tmp_path)


def test_batch_skips_unverified_records(monkeypatch, tmp_path):
    _use_tmp_data(monkeypatch, tmp_path)
    _write_pair(tmp_path, "0001", "in Bern", {"_verified": True, "seat_municipality": "Bern"})
    _write_pair(tmp_path, "0002", "in Bern", {"_verified": False, "seat_municipality": "Zug"})
    # no flag at all counts as unverified
    _write_pair(tmp_path, "0003", "in Bern", {"seat_municipality": "Zug"})

    entries = crosscheck.crosscheck_verified()

    assert [e.doc_id for e in entries] == ["0001"]


def test_batch_reports_missing_fields_and_is_ordered(monkeypatch, tmp_path):
    _use_tmp_data(monkeypatch, tmp_path)
    _write_pair(tmp_path, "0002", "in Bern", {"_verified": True, "seat_municipality": "Zug"})
    _write_pair(tmp_path, "0001", "in Bern", {"_verified": True, "seat_municipality": "Bern"})

    entries = crosscheck.crosscheck_verified()

    assert [e.doc_id for e in entries] == ["0001", "0002"]
    assert entries[0].result.missing == []
    assert _field_names(entries[1].result.missing) == ["seat_municipality"]


def test_batch_records_error_for_missing_raw_text(monkeypatch, tmp_path):
    _use_tmp_data(monkeypatch, tmp_path)
    (tmp_path / "0001.json").write_text(json.dumps({"_verified": True}), encoding="utf-8")

    (entry,) = crosscheck.crosscheck_verified()

    assert entry.result is None
    assert "no source text" in entry.error


def test_batch_records_error_for_invalid_json(monkeypatch, tmp_path):
    _use_tmp_data(monkeypatch, tmp_path)
    (tmp_path / "0001.json").write_text("{not json", encoding="utf-8")

    (entry,) = crosscheck.crosscheck_verified()

    assert entry.result is None
    assert "invalid JSON" in entry.error


def test_batch_exit_code_is_zero_when_nothing_is_missing(monkeypatch, tmp_path, capsys):
    _use_tmp_data(monkeypatch, tmp_path)
    _write_pair(tmp_path, "0001", "in Bern", {"_verified": True, "seat_municipality": "Bern"})

    assert crosscheck._run_batch() == 0
    assert "1 verified document checked, 0 with findings." in capsys.readouterr().out


def test_batch_exit_code_is_nonzero_when_a_field_is_missing(monkeypatch, tmp_path, capsys):
    _use_tmp_data(monkeypatch, tmp_path)
    _write_pair(tmp_path, "0001", "in Bern", {"_verified": True, "seat_municipality": "Zug"})

    assert crosscheck._run_batch() == 1
    out = capsys.readouterr().out
    assert "0001 — NOT found in the text (1)" in out
    assert "seat_municipality" in out


def test_batch_ignores_a_document_with_only_normalized_fields_missing(
    monkeypatch, tmp_path, capsys
):
    # Every correct annotation has these missing, so on their own they are
    # not a reason to flag the document (or to fail the run).
    _use_tmp_data(monkeypatch, tmp_path)
    _write_pair(
        tmp_path,
        "0001",
        "Aktiengesellschaft in Bern",
        {"_verified": True, "seat_canton": "BE", "act_type": "mutation"},
    )

    assert crosscheck._run_batch() == 0
    assert "0001 — NOT found" not in capsys.readouterr().out


def test_batch_lists_normalized_fields_alongside_a_real_finding(monkeypatch, tmp_path, capsys):
    _use_tmp_data(monkeypatch, tmp_path)
    _write_pair(
        tmp_path,
        "0001",
        "in Bern",
        {"_verified": True, "seat_canton": "BE", "seat_municipality": "Zug"},
    )

    assert crosscheck._run_batch() == 1
    out = capsys.readouterr().out
    assert "seat_canton: 'BE' — 0 times [normalized, expected]" in out
    assert "seat_municipality" in out


def test_batch_output_omits_found_fields(monkeypatch, tmp_path, capsys):
    _use_tmp_data(monkeypatch, tmp_path)
    _write_pair(
        tmp_path,
        "0001",
        "in Bern, CHE-123.456.789",
        {"_verified": True, "seat_municipality": "Zug", "uid": "CHE-123.456.789"},
    )

    crosscheck._run_batch()

    out = capsys.readouterr().out
    assert "seat_municipality" in out
    assert "uid" not in out


@pytest.mark.parametrize(
    "field, value",
    [
        ("legal_form", "AG"),
        ("seat_canton", "BE"),
        ("canton_previous", "SO"),
        ("canton_new", "BE"),
    ],
)
def test_batch_marks_normalized_fields_as_expected(monkeypatch, tmp_path, capsys, field, value):
    # Canton codes are derived from the municipality, never written out in
    # the notice, so they are reported but not counted as a real finding.
    _use_tmp_data(monkeypatch, tmp_path)
    _write_pair(
        tmp_path,
        "0001",
        "Aktiengesellschaft in Bern",
        # seat_municipality is the real finding that makes the document
        # print at all; the normalized field rides along, marked.
        {"_verified": True, field: value, "seat_municipality": "Zug"},
    )

    crosscheck._run_batch()

    out = capsys.readouterr().out
    assert f"{field}: {value!r} — 0 times [normalized, expected]" in out
