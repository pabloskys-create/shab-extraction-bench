"""Tests for src/apply_gold_corrections.py. Every gold record and corrections
file here is synthetic, written to tmp_path; the real gold is never read."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src import apply_gold_corrections as agc


def _record(doc_id: str = "0001", **overrides) -> dict:
    record = {
        "doc_id": doc_id,
        "schema_version": "1.0",
        "domicile_new": None,
        "persons_changed": [
            {"name_new": "Muster, Hans", "name_previous": None, "nationality_new": None},
            {"name_new": "Beispiel, Anna", "name_previous": None, "nationality_new": "deutscher"},
        ],
        "extras": {"mitteilungen": "Mitteilungen an die Gesellschafter: per Brief."},
        "uncertain": [],
        "notes": "Zürich",
        "_verified": True,
    }
    record.update(overrides)
    return record


def _write_record(directory: Path, record: dict, newline: str = "\r\n", trailing: bool = False) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    text = json.dumps(record, ensure_ascii=False, indent=2) + ("\n" if trailing else "")
    path = directory / f"{record['doc_id']}.json"
    path.write_bytes(text.replace("\n", newline).encode("utf-8"))
    return path


def _correction(path: str, expected_old, new, doc_id: str = "0001", split: str = "dev") -> dict:
    return {"split": split, "doc_id": doc_id, "path": path, "expected_old": expected_old, "new": new, "reason": "r"}


@pytest.fixture
def dirs(tmp_path: Path) -> dict[str, Path]:
    return {"dev": tmp_path / "dev", "holdout": tmp_path / "holdout"}


def _run(tmp_path: Path, dirs: dict[str, Path], corrections: list[dict], *flags: str) -> int:
    file = tmp_path / "corrections.json"
    file.write_text(json.dumps(corrections, ensure_ascii=False), encoding="utf-8")
    return agc.main(["--file", str(file), "--dev-dir", str(dirs["dev"]), "--holdout-dir", str(dirs["holdout"]), *flags])


# --- paths ---


def test_parse_path_with_selector_containing_comma_and_dot():
    segments = agc.parse_path("persons_changed[name=Muster, Hans Prof. Dr.].nationality_new")
    assert segments == [
        agc.Segment("persons_changed", "name", "Muster, Hans Prof. Dr."),
        agc.Segment("nationality_new"),
    ]


@pytest.mark.parametrize("path", ["", "extras.", ".extras", "persons_changed[name=X]", "a[name=X]b", "a b"])
def test_parse_path_rejects_malformed(path):
    with pytest.raises(agc.CorrectionError):
        agc.parse_path(path)


def test_read_value_by_name_matches_name_new():
    record = _record()
    assert agc.read_value(record, "persons_changed[name=Beispiel, Anna].nationality_new") == "deutscher"


def test_missing_extras_key_reads_as_null():
    assert agc.read_value(_record(), "extras.revision") is None


def test_selector_matching_no_element_is_an_error():
    with pytest.raises(agc.CorrectionError, match="matches 0 elements"):
        agc.read_value(_record(), "persons_changed[name=Nobody, X].nationality_new")


def test_selector_matching_two_elements_is_an_error():
    record = _record()
    record["persons_changed"][1]["name_new"] = "Muster, Hans"
    with pytest.raises(agc.CorrectionError, match="matches 2 elements"):
        agc.read_value(record, "persons_changed[name=Muster, Hans].nationality_new")


def test_null_under_extras_removes_the_key():
    record = _record()
    agc.write_value(record, "extras.mitteilungen", None)
    assert record["extras"] == {}


def test_null_on_a_top_level_field_is_stored():
    record = _record(domicile_new="Musterweg 1, 3000 Bern")
    agc.write_value(record, "domicile_new", None)
    assert "domicile_new" in record and record["domicile_new"] is None


def test_same_value_numbers_and_bools():
    assert agc.same_value(100, 100.0)
    assert not agc.same_value(True, 1)
    assert not agc.same_value(["a"], "a")


# --- text edits keep the file's formatting ---

HAND_FORMATTED = (
    "{\r\n"
    '  "doc_id": "0001",\r\n'
    '  "act_subtypes": ["sitzverlegung", "organaenderung"],\r\n'
    '  "domicile_new": "Rubigenstrasse 66",\r\n'
    '  "persons_changed": [\r\n'
    '    {"name_new": "Frey, Heiko", "nationality_new": "deutscher", "role_new": null}\r\n'
    "  ],\r\n"
    '  "extras": {\r\n'
    '    "zweck": "Handel",\r\n'
    '    "mitteilungen": "Mitteilungen an die Gesellschafter: per Brief."\r\n'
    "  },\r\n"
    '  "notes": "Zürich",\r\n'
    '  "_verified": true\r\n'
    "}"
)


def _edit(text: str, path: str, new) -> str:
    """Apply one correction in memory and check the text still parses to the record."""
    gold = agc.GoldFile(Path("x.json"), text, json.loads(text))
    agc.apply_to(gold, agc.Correction("dev", "0001", path, agc.read_value(gold.record, path), new, "r"))
    assert agc.same_value(json.loads(gold.text), gold.record)
    return gold.text


def test_replacing_a_scalar_changes_only_that_value():
    out = _edit(HAND_FORMATTED, "domicile_new", "Rubigenstrasse 66, 3123 Belp")
    assert out == HAND_FORMATTED.replace('"Rubigenstrasse 66"', '"Rubigenstrasse 66, 3123 Belp"')


def test_replacing_a_list_keeps_it_inline():
    out = _edit(HAND_FORMATTED, "act_subtypes", [])
    assert out == HAND_FORMATTED.replace('["sitzverlegung", "organaenderung"]', "[]")


def test_editing_a_person_on_one_line_keeps_the_line():
    out = _edit(HAND_FORMATTED, "persons_changed[name=Frey, Heiko].nationality_new", "deutsch")
    assert out == HAND_FORMATTED.replace('"deutscher"', '"deutsch"')


def test_inserting_into_a_multiline_object_follows_its_layout():
    out = _edit(HAND_FORMATTED, "extras.revision", "opting-out")
    assert out == HAND_FORMATTED.replace(
        'per Brief."\r\n  },', 'per Brief.",\r\n    "revision": "opting-out"\r\n  },'
    )


def test_inserting_into_an_empty_object_writes_it_inline():
    text = '{\n  "doc_id": "0001",\n  "extras": {},\n  "_verified": true\n}\n'
    out = _edit(text, "extras.prior_publication_page", 0)
    assert out == text.replace("{}", '{"prior_publication_page": 0}')


def test_inserting_into_a_one_line_object():
    text = '{\n  "doc_id": "0001",\n  "extras": {"zweck": "Handel"},\n  "_verified": true\n}'
    out = _edit(text, "extras.revision", "opting-out")
    assert out == text.replace('{"zweck": "Handel"}', '{"zweck": "Handel", "revision": "opting-out"}')


def test_inserting_into_a_multiline_object_with_one_member():
    text = '{\n  "doc_id": "0001",\n  "extras": {\n    "zweck": "Handel"\n  }\n}'
    out = _edit(text, "extras.revision", "opting-out")
    assert out == text.replace('"Handel"\n', '"Handel",\n    "revision": "opting-out"\n')


def test_deleting_the_last_extras_key():
    out = _edit(HAND_FORMATTED, "extras.mitteilungen", None)
    assert out == HAND_FORMATTED.replace(',\r\n    "mitteilungen": "Mitteilungen an die Gesellschafter: per Brief."', "")


def test_deleting_a_middle_extras_key():
    out = _edit(HAND_FORMATTED, "extras.zweck", None)
    assert out == HAND_FORMATTED.replace('"zweck": "Handel",\r\n    ', "")


def test_deleting_the_only_extras_key():
    text = '{\n  "doc_id": "0001",\n  "extras": {"loeschung_reason": "x"},\n  "_verified": true\n}'
    assert _edit(text, "extras.loeschung_reason", None) == text.replace('{"loeschung_reason": "x"}', "{}")


def test_apply_on_disk_changes_only_the_value(tmp_path, dirs):
    dirs["dev"].mkdir()
    path = dirs["dev"] / "0001.json"
    path.write_bytes(HAND_FORMATTED.encode("utf-8"))
    corrections = [
        _correction("domicile_new", "Rubigenstrasse 66", "Rubigenstrasse 66, 3123 Belp"),
        _correction("persons_changed[name=Frey, Heiko].nationality_new", "deutscher", "deutsch"),
    ]
    assert _run(tmp_path, dirs, corrections, "--apply") == 0
    expected = HAND_FORMATTED.replace('"Rubigenstrasse 66"', '"Rubigenstrasse 66, 3123 Belp"').replace(
        '"deutscher"', '"deutsch"'
    )
    assert path.read_bytes() == expected.encode("utf-8")


# --- corrections file ---


def test_load_rejects_missing_key(tmp_path):
    file = tmp_path / "c.json"
    entry = _correction("domicile_new", None, "x")
    del entry["reason"]
    file.write_text(json.dumps([entry]), encoding="utf-8")
    with pytest.raises(agc.CorrectionError, match="expected exactly the keys"):
        agc.load_corrections(file, {"dev", "holdout"})


def test_load_rejects_duplicates(tmp_path):
    file = tmp_path / "c.json"
    file.write_text(json.dumps([_correction("domicile_new", None, "x")] * 2), encoding="utf-8")
    with pytest.raises(agc.CorrectionError, match="duplicate"):
        agc.load_corrections(file, {"dev", "holdout"})


def test_load_rejects_unknown_split(tmp_path):
    file = tmp_path / "c.json"
    file.write_text(json.dumps([_correction("domicile_new", None, "x", split="test")]), encoding="utf-8")
    with pytest.raises(agc.CorrectionError, match="unknown split"):
        agc.load_corrections(file, {"dev", "holdout"})


# --- end to end ---


def test_dry_run_is_the_default_and_writes_nothing(tmp_path, dirs, capsys):
    path = _write_record(dirs["dev"], _record())
    before = path.read_bytes()
    code = _run(tmp_path, dirs, [_correction("extras.revision", None, "opting-out")])
    assert code == 0
    assert path.read_bytes() == before
    out = capsys.readouterr().out
    assert "dev 0001" in out
    assert 'apply       extras.revision: null -> "opting-out"' in out
    assert "dry run" in out


def test_apply_writes_and_keeps_order_verified_and_line_endings(tmp_path, dirs):
    path = _write_record(dirs["dev"], _record(), newline="\r\n", trailing=False)
    corrections = [
        _correction("extras.revision", None, "opting-out"),
        _correction("extras.mitteilungen", "Mitteilungen an die Gesellschafter: per Brief.", "per Brief."),
        _correction("persons_changed[name=Beispiel, Anna].nationality_new", "deutscher", "deutsch"),
        _correction("domicile_new", None, "Musterweg 1, 3000 Bern"),
    ]
    assert _run(tmp_path, dirs, corrections, "--apply") == 0

    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
    assert not raw.endswith(b"\n")
    assert "Zürich".encode() in raw  # ensure_ascii=False

    record = json.loads(raw.decode("utf-8"))
    assert list(record) == list(_record())
    assert record["_verified"] is True
    assert record["extras"] == {"mitteilungen": "per Brief.", "revision": "opting-out"}
    assert record["persons_changed"][1]["nationality_new"] == "deutsch"
    assert record["domicile_new"] == "Musterweg 1, 3000 Bern"


def test_apply_keeps_lf_and_trailing_newline(tmp_path, dirs):
    path = _write_record(dirs["holdout"], _record("0121"), newline="\n", trailing=True)
    assert _run(tmp_path, dirs, [_correction("domicile_new", None, "X", doc_id="0121", split="holdout")], "--apply") == 0
    raw = path.read_bytes()
    assert b"\r\n" not in raw and raw.endswith(b"}\n")


def test_already_ok_is_skipped_and_file_untouched(tmp_path, dirs, capsys):
    path = _write_record(dirs["dev"], _record(extras={"revision": "opting-out"}))
    before = path.read_bytes()
    assert _run(tmp_path, dirs, [_correction("extras.revision", None, "opting-out")], "--apply") == 0
    assert path.read_bytes() == before
    out = capsys.readouterr().out
    assert "already ok" in out
    assert "0 gold records written" in out


def test_conflict_aborts_and_writes_nothing(tmp_path, dirs, capsys):
    first = _write_record(dirs["dev"], _record("0001"))
    second = _write_record(dirs["dev"], _record("0002", domicile_new="Somewhere else"))
    before = (first.read_bytes(), second.read_bytes())
    corrections = [
        _correction("extras.revision", None, "opting-out", doc_id="0001"),
        _correction("domicile_new", None, "Musterweg 1, 3000 Bern", doc_id="0002"),
    ]
    assert _run(tmp_path, dirs, corrections, "--apply") == 1
    assert (first.read_bytes(), second.read_bytes()) == before
    captured = capsys.readouterr()
    assert 'conflict    domicile_new: found "Somewhere else", expected null' in captured.out
    assert "nothing was written" in captured.err


def test_unresolvable_path_aborts_and_writes_nothing(tmp_path, dirs, capsys):
    path = _write_record(dirs["dev"], _record())
    before = path.read_bytes()
    corrections = [
        _correction("extras.revision", None, "opting-out"),
        _correction("persons_changed[name=Nobody, X].nationality_new", None, "deutsch"),
    ]
    assert _run(tmp_path, dirs, corrections, "--apply") == 2
    assert path.read_bytes() == before
    assert "nothing was written" in capsys.readouterr().err


def test_missing_record_aborts(tmp_path, dirs, capsys):
    dirs["dev"].mkdir()
    assert _run(tmp_path, dirs, [_correction("domicile_new", None, "x", doc_id="0999")], "--apply") == 2
    assert "no gold record" in capsys.readouterr().err
