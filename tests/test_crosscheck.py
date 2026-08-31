"""Tests for src/crosscheck.py.

Most tests call `crosscheck_record` directly with small inline text/record
fixtures — no files needed. `test_crosscheck_doc_reads_both_files` is the
one integration test that exercises the actual file-reading path, against
tests/fixtures/crosscheck_sample.{txt,json}.
"""

from pathlib import Path

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
