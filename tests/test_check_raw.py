"""Tests for src/check_raw.py.

Everything runs against a synthetic raw directory built in tmp_path — the
real data/raw/ is immutable (CLAUDE.md rule 2) and, more usefully, a
checker for paste mistakes has to be tested on files that actually contain
paste mistakes.
"""

import json
from pathlib import Path

import pytest

from src.check_raw import (
    Expected,
    check_raw,
    document_uid,
    first_line,
    load_expected,
)

BODY = (
    "\nMuster AG, in Bern, {uid}, Aktiengesellschaft (SHAB Nr. 12 vom 03.03.2025, "
    "Publ. 1000000001). Ausgeschiedene Personen und erloschene Unterschriften: "
    "Muster, Hans, von Bern, in Bern, Mitglied, mit Einzelunterschrift.\n"
    "Tagesregister-Nr. 100 vom 05.08.2026\n"
    "Kontaktstelle: Handelsregisteramt des Kantons Bern\n"
)

TITLES = {
    "0029": "Mutation Alpha AG, Bern",
    "0030": "Neueintragung Beta GmbH, Luzern",
    "0031": "Löschung Gamma AG, Zürich",
}


def _notice(doc_id: str, uid: str = "CHE-100.000.001", title: str | None = None) -> str:
    return (title if title is not None else TITLES[doc_id]) + BODY.format(uid=uid)


def _corpus(tmp_path: Path, files: dict[str, str]) -> Path:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    for doc_id, text in files.items():
        (raw_dir / f"{doc_id}.txt").write_text(text, encoding="utf-8")
    return raw_dir


def _expected(*doc_ids: str) -> list[Expected]:
    return [
        Expected(doc_id=doc_id, title=TITLES[doc_id], canton="BE", url=f"https://example/{doc_id}")
        for doc_id in doc_ids
    ]


@pytest.fixture
def clean(tmp_path):
    """Three well-formed pastes, one distinct UID each."""
    files = {
        doc_id: _notice(doc_id, uid=f"CHE-100.000.00{offset + 1}")
        for offset, doc_id in enumerate(TITLES)
    }
    return _corpus(tmp_path, files), _expected(*TITLES)


# --- helpers ---


def test_first_line_strips_the_line_terminator_and_surrounding_space():
    assert first_line("  Mutation Alpha AG, Bern  \nrest\n") == "Mutation Alpha AG, Bern"


def test_first_line_of_an_empty_file_is_empty():
    assert first_line("") == ""
    assert first_line("   \n\n") == ""


def test_document_uid_takes_the_first_occurrence():
    # Later UIDs belong to corporate officers, not to the subject company.
    text = "Alpha AG, CHE-100.000.001, ... Verwaltungsrat Beta AG, CHE-200.000.002"
    assert document_uid(text) == "CHE-100.000.001"


def test_document_uid_is_none_when_the_notice_names_no_register_entry():
    assert document_uid("Gesuch um Erteilung einer Arbeitszeitbewilligung") is None


def test_load_expected_reads_and_sorts_manifest_rows(tmp_path):
    manifest = tmp_path / "manifest_sample.json"
    manifest.write_text(
        json.dumps(
            {
                "metadata": {},
                "records": [
                    {"doc_id": "0031", "title": TITLES["0031"], "canton": "ZH", "url": "u31"},
                    {"doc_id": "0029", "title": TITLES["0029"], "canton": "BE", "url": "u29"},
                ],
            }
        ),
        encoding="utf-8",
    )
    rows = load_expected(manifest)
    assert [row.doc_id for row in rows] == ["0029", "0031"]
    assert rows[0].title == TITLES["0029"]
    assert rows[1].canton == "ZH"


# --- the clean case ---


def test_a_correct_corpus_passes_every_check(clean):
    raw_dir, expected = clean
    report = check_raw(expected, raw_dir)
    assert report.ok
    assert report.checked == ["0029", "0030", "0031"]
    assert report.missing == []
    assert report.short == []
    assert report.mismatched == []
    assert report.duplicate_content == []
    assert report.repeated_uid == []


def test_files_not_named_by_the_manifest_are_not_reported_as_checked(clean):
    raw_dir, expected = clean
    report = check_raw(expected[:2], raw_dir)
    assert report.checked == ["0029", "0030"]
    assert report.ok


# --- missing and short ---


def test_a_missing_file_is_reported(tmp_path):
    raw_dir = _corpus(tmp_path, {"0029": _notice("0029")})
    report = check_raw(_expected("0029", "0030"), raw_dir)
    assert report.missing == ["0030"]
    assert not report.ok


def test_a_missing_file_is_not_also_reported_as_a_mismatch(tmp_path):
    raw_dir = _corpus(tmp_path, {"0029": _notice("0029")})
    report = check_raw(_expected("0029", "0030"), raw_dir)
    assert [m.doc_id for m in report.mismatched] == []


def test_an_empty_file_is_reported_as_short(tmp_path):
    raw_dir = _corpus(tmp_path, {"0029": ""})
    report = check_raw(_expected("0029"), raw_dir)
    assert report.short == [("0029", 0)]
    assert not report.ok


def test_a_truncated_paste_is_reported_as_short(tmp_path):
    raw_dir = _corpus(tmp_path, {"0029": TITLES["0029"] + "\n"})
    report = check_raw(_expected("0029"), raw_dir)
    assert [doc_id for doc_id, _ in report.short] == ["0029"]


def test_the_short_threshold_is_configurable(tmp_path):
    raw_dir = _corpus(tmp_path, {"0029": TITLES["0029"] + "\n"})
    report = check_raw(_expected("0029"), raw_dir, min_length=5)
    assert report.short == []
    assert report.min_length == 5


# --- headline mismatch ---


def test_a_wrong_headline_is_reported_with_both_strings(tmp_path):
    raw_dir = _corpus(tmp_path, {"0029": _notice("0029", title="Mutation Someone Else AG, Thun")})
    report = check_raw(_expected("0029"), raw_dir)
    assert len(report.mismatched) == 1
    mismatch = report.mismatched[0]
    assert mismatch.expected == TITLES["0029"]
    assert mismatch.found == "Mutation Someone Else AG, Thun"
    assert mismatch.belongs_to is None
    assert not report.ok


def test_a_headline_differing_only_in_surrounding_space_still_matches(tmp_path):
    raw_dir = _corpus(tmp_path, {"0029": "  " + _notice("0029")})
    report = check_raw(_expected("0029"), raw_dir)
    assert report.mismatched == []


def test_a_headline_differing_inside_the_line_does_not_match(tmp_path):
    # Only surrounding whitespace is ignored: a headline that differs
    # internally came from a different page.
    squeezed = TITLES["0029"].replace(", Bern", ",Bern")
    raw_dir = _corpus(tmp_path, {"0029": _notice("0029", title=squeezed)})
    report = check_raw(_expected("0029"), raw_dir)
    assert [m.doc_id for m in report.mismatched] == ["0029"]


# --- swapped pastes ---


def test_two_swapped_pastes_are_reported_as_swapped_and_name_each_other(tmp_path):
    raw_dir = _corpus(
        tmp_path,
        {
            "0029": _notice("0029", uid="CHE-100.000.002", title=TITLES["0030"]),
            "0030": _notice("0030", uid="CHE-100.000.001", title=TITLES["0029"]),
        },
    )
    report = check_raw(_expected("0029", "0030"), raw_dir)
    assert {(m.doc_id, m.belongs_to) for m in report.swapped} == {("0029", "0030"), ("0030", "0029")}
    # A swap is a kind of mismatch, and is listed in both sections.
    assert len(report.mismatched) == 2
    assert not report.ok


def test_a_headline_from_outside_the_sample_is_a_mismatch_but_not_a_swap(tmp_path):
    raw_dir = _corpus(tmp_path, {"0029": _notice("0029", title="Mutation Outsider AG, Sion")})
    report = check_raw(_expected("0029"), raw_dir)
    assert report.swapped == []
    assert len(report.mismatched) == 1


# --- duplicates ---


def test_two_identical_pastes_are_reported_together(tmp_path):
    raw_dir = _corpus(tmp_path, {"0029": _notice("0029"), "0030": _notice("0029")})
    report = check_raw(_expected("0029", "0030"), raw_dir)
    assert report.duplicate_content == [["0029", "0030"]]
    assert not report.ok


def test_a_paste_duplicating_an_already_annotated_document_is_reported(tmp_path):
    # 0001 is not in the manifest: it is comparison material, and the group
    # is still reported because a sampled document is in it.
    raw_dir = _corpus(tmp_path, {"0001": _notice("0029"), "0029": _notice("0029")})
    report = check_raw(_expected("0029"), raw_dir)
    assert report.duplicate_content == [["0001", "0029"]]


def test_duplicates_among_unsampled_files_alone_are_not_reported(tmp_path):
    raw_dir = _corpus(
        tmp_path,
        {
            "0001": _notice("0030", uid="CHE-100.000.002"),
            "0002": _notice("0030", uid="CHE-100.000.002"),
            "0029": _notice("0029", uid="CHE-100.000.001"),
        },
    )
    report = check_raw(_expected("0029"), raw_dir)
    assert report.duplicate_content == []
    assert report.repeated_uid == []
    assert report.ok


# --- UID uniqueness ---


def test_two_documents_sharing_a_uid_are_reported(tmp_path):
    raw_dir = _corpus(
        tmp_path,
        {
            "0029": _notice("0029", uid="CHE-100.000.001"),
            "0030": _notice("0030", uid="CHE-100.000.001"),
        },
    )
    report = check_raw(_expected("0029", "0030"), raw_dir)
    assert report.repeated_uid == [("CHE-100.000.001", ["0029", "0030"])]
    assert not report.ok


def test_a_uid_shared_with_an_already_annotated_document_is_reported(tmp_path):
    raw_dir = _corpus(
        tmp_path,
        {
            "0001": _notice("0030", uid="CHE-100.000.001"),
            "0029": _notice("0029", uid="CHE-100.000.001"),
        },
    )
    report = check_raw(_expected("0029"), raw_dir)
    assert report.repeated_uid == [("CHE-100.000.001", ["0001", "0029"])]


def test_a_notice_naming_no_uid_is_counted_not_flagged(tmp_path):
    raw_dir = _corpus(
        tmp_path,
        {
            "0029": TITLES["0029"] + "\nGesuch um Erteilung einer Arbeitszeitbewilligung. " * 5,
            "0030": _notice("0030"),
        },
    )
    report = check_raw(_expected("0029", "0030"), raw_dir)
    assert report.without_uid == ["0029"]
    assert report.repeated_uid == []
    assert report.ok


def test_several_notices_without_a_uid_do_not_collide_with_each_other(tmp_path):
    filler = "\nGesuch um Erteilung einer Arbeitszeitbewilligung. " * 5
    raw_dir = _corpus(
        tmp_path,
        {"0029": TITLES["0029"] + filler, "0030": TITLES["0030"] + filler},
    )
    report = check_raw(_expected("0029", "0030"), raw_dir)
    assert report.without_uid == ["0029", "0030"]
    assert report.ok


# --- reporting contract ---


def test_report_is_not_ok_if_any_single_check_fails(clean, tmp_path):
    raw_dir, expected = clean
    (raw_dir / "0031.txt").unlink()
    report = check_raw(expected, raw_dir)
    assert not report.ok
