"""Tests for src/gold_rule_sweep.py. Every gold record and notice here is
built by hand in memory or in tmp_path; nothing is read from the real gold."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from src import gold_rule_sweep as sweep
from src.gold_rule_sweep import split_notice

BODY_PREFIX = "Foo GmbH, in Bern, CHE-123.456.789, Gesellschaft mit beschränkter Haftung (SHAB Nr. 12 vom 03.02.2025, Publ. 1000123456)."
TAIL = "\n \nTagesregister-Nr. 1 vom 01.08.2026\n \nKontaktstelle: Handelsregisteramt des Kantons Bern\n"


def _notice(body: str, header: str = "Foo GmbH\nMusterweg 1\n3000 Bern", headline: str = "Mutation Foo GmbH, Bern") -> str:
    return f"{headline}\n{header}\n \n{BODY_PREFIX} {body}{TAIL}"


def _gold(**overrides) -> dict:
    record = {
        "doc_id": "0001",
        "act_type": "mutation",
        "act_subtypes": [],
        "domicile_new": None,
        "domicile_previous": None,
        "persons_added": [],
        "persons_removed": [],
        "persons_changed": [],
        "extras": {},
        "uncertain": [],
    }
    record.update(overrides)
    return record


def _run(gold: dict, text: str) -> list[sweep.Finding]:
    return sweep.sweep_document(gold, text)


def _rules(findings: list[sweep.Finding]) -> list[tuple[str, str]]:
    return [(f.rule, f.field) for f in findings]


# --- notice structure ---


def test_split_notice_header_bisher_and_body():
    text = _notice("Domizil neu: X.", header="Foo GmbH\nNeuweg 2\n3000 Bern\nBisher\nAltweg 1\n3000 Bern")
    notice = split_notice(text)
    assert notice.header[0] == "Foo GmbH"
    assert notice.bisher == ["Altweg 1", "3000 Bern"]
    assert notice.body.lstrip().startswith("Foo GmbH, in Bern")


# --- 1. prior_publication_page ---


OLD_CITATION_BODY = "Firma neu: Bar GmbH."


def _old_citation_text() -> str:
    return _notice(OLD_CITATION_BODY).replace("Publ. 1000123456", "S.3707")


def test_page_matches_no_finding():
    gold = _gold(extras={"prior_publication_page": "3707"})
    assert _run(gold, _old_citation_text()) == []


def test_page_as_number_matches():
    gold = _gold(extras={"prior_publication_page": 3707})
    assert _run(gold, _old_citation_text()) == []


def test_page_missing_is_flagged():
    findings = _run(_gold(), _old_citation_text())
    assert _rules(findings) == [("prior_publication_page", "extras.prior_publication_page")]
    assert "S.3707" in findings[0].evidence


def test_page_wrong_is_flagged():
    findings = _run(_gold(extras={"prior_publication_page": "370"}), _old_citation_text())
    assert _rules(findings) == [("prior_publication_page", "extras.prior_publication_page")]


def test_no_old_citation_no_page_check():
    assert _run(_gold(), _notice("Firma neu: Bar GmbH.")) == []


# --- 2. domicile ---


def test_domizil_neu_with_null_new_is_flagged():
    findings = _run(_gold(), _notice("Domizil neu: Neuweg 2, 3000 Bern."))
    # No previous address is printed: a null domicile_previous is correct.
    assert _rules(findings) == [("domicile", "domicile_new")]


def test_domizil_neu_filled_new_no_finding():
    gold = _gold(domicile_new="Neuweg 2, 3000 Bern")
    assert _run(gold, _notice("Domizil neu: Neuweg 2, 3000 Bern.")) == []


def test_bisher_block_with_address_needs_both():
    header = "Foo GmbH\nNeuweg 2\n3000 Bern\nBisher\nAltweg 1\n3000 Bern"
    text = _notice("Domizil neu: Neuweg 2, 3000 Bern.", header=header)
    findings = _run(_gold(domicile_new="Neuweg 2, 3000 Bern"), text)
    assert _rules(findings) == [("domicile", "domicile_previous")]
    assert "Bisher block" in findings[0].note


def test_bisher_block_name_only_is_not_a_trigger():
    header = "Bar GmbH\nMusterweg 1\n3000 Bern\nBisher\nFoo GmbH"
    assert _run(_gold(), _notice("Firma neu: Bar GmbH.", header=header)) == []


def test_bisher_heading_without_lines_is_not_a_trigger():
    header = "Foo GmbH\nMusterweg 1\n3000 Bern\nBisher"
    assert _run(_gold(), _notice("Zweck neu: Handel.", header=header)) == []


def test_inline_company_bisher_address_is_a_trigger():
    text = _notice("Domizil: Neuweg 2, 3000 Bern [bisher: Altweg 1, 3000 Bern].")
    findings = _run(_gold(), text)
    assert _rules(findings) == [("domicile", "domicile_new"), ("domicile", "domicile_previous")]


def test_weitere_adressen_bisher_is_not_a_trigger():
    text = _notice("Weitere Adressen: Neuweg 2, 3000 Bern [bisher: Altweg 1, 3000 Bern].")
    assert _run(_gold(), text) == []


def test_domicile_filled_without_trigger_is_flagged():
    gold = _gold(domicile_new="Musterweg 1, 3000 Bern", domicile_previous="Altweg 1, 3000 Bern")
    findings = _run(gold, _notice("Zweck neu: Handel."))
    assert _rules(findings) == [("domicile", "domicile_new"), ("domicile", "domicile_previous")]


def test_domicile_uncertain_is_skipped():
    gold = _gold(uncertain=["domicile_new"])
    assert _run(gold, _notice("Domizil neu: Neuweg 2, 3000 Bern.")) == []


# --- 3. nationality ---


@pytest.mark.parametrize("value", ["deutsche", "italienischer", "französischen", "spanisches"])
def test_inflected_nationality_is_flagged(value):
    gold = _gold(persons_added=[{"name": "Muster, Hans", "nationality": value}])
    findings = _run(gold, _notice("Eingetragene Personen neu oder mutierend: Muster, Hans, in Bern."))
    assert _rules(findings) == [("nationality", "persons_added[Muster, Hans].nationality")]


@pytest.mark.parametrize("value", ["deutsch", "italienisch", None])
def test_base_nationality_no_finding(value):
    gold = _gold(persons_removed=[{"name": "Muster, Hans", "nationality": value}])
    assert _run(gold, _notice("Zweck neu: Handel.")) == []


def test_changed_person_both_nationalities_checked():
    person = {"name_new": "Muster, Hans", "nationality_new": "deutsch", "nationality_previous": "österreichischer"}
    findings = _run(_gold(persons_changed=[person]), _notice("Zweck neu: Handel."))
    assert _rules(findings) == [("nationality", "persons_changed[Muster, Hans].nationality_previous")]


# --- 4. loeschung_reason ---


def test_loeschung_reason_verbatim_no_finding():
    gold = _gold(act_type="loeschung", extras={"loeschung_reason": "Geschäftsaufgabe"})
    assert _run(gold, _notice("Die Rechtseinheit wird infolge Geschäftsaufgabe gelöscht.")) == []


def test_loeschung_reason_not_in_notice_is_flagged():
    gold = _gold(act_type="loeschung", extras={"loeschung_reason": "Aufgabe des Geschäfts"})
    findings = _run(gold, _notice("Die Rechtseinheit wird infolge Geschäftsaufgabe gelöscht."))
    assert _rules(findings) == [("loeschung_reason", "extras.loeschung_reason")]
    assert "gelöscht" in findings[0].evidence


# --- 5. mitteilungen ---


def test_mitteilungen_with_subject_is_flagged():
    gold = _gold(extras={"mitteilungen": "Mitteilungen an die Gesellschafter: per Brief."})
    findings = _run(gold, _notice("Mitteilungen neu: Mitteilungen an die Gesellschafter: per Brief."))
    assert _rules(findings) == [("mitteilungen", "extras.mitteilungen")]


def test_mitteilungen_without_subject_no_finding():
    gold = _gold(extras={"mitteilungen": "per Brief."})
    assert _run(gold, _notice("Mitteilungen neu: Mitteilungen an die Gesellschafter: per Brief.")) == []


# --- 6. sitzverlegung ---


def test_sitz_neu_without_subtype_is_flagged():
    findings = _run(_gold(domicile_new="X"), _notice("Sitz neu: Thun. Domizil neu: X."))
    assert ("sitzverlegung", "act_subtypes") in _rules(findings)


def test_verlegung_des_sitzes_with_subtype_no_finding():
    gold = _gold(act_subtypes=["sitzverlegung", "kantonswechsel"])
    text = _notice("Die Rechtseinheit wird infolge Verlegung des Sitzes nach Thun eingetragen.")
    assert _run(gold, text) == []


def test_subtype_without_trigger_is_flagged():
    gold = _gold(act_subtypes=["sitzverlegung"], domicile_new="Neuweg 2, 3000 Bern")
    findings = _run(gold, _notice("Domizil neu: Neuweg 2, 3000 Bern."))
    assert _rules(findings) == [("sitzverlegung", "act_subtypes")]


def test_hauptsitz_neu_is_not_sitz_neu():
    assert _run(_gold(), _notice("Hauptsitz neu: Thun.")) == []


# --- 7. revision ---


@pytest.mark.parametrize(
    "sentence",
    [
        "Gemäss Erklärung wird auf eine eingeschränkte Revision verzichtet.",
        "Die Gesellschaft verzichtet ab dem Geschäftsjahr, das am 01.01.2027 beginnt, auf eine eingeschränkte Revision.",
    ],
)
def test_opting_out_without_revision_is_flagged(sentence):
    findings = _run(_gold(), _notice(sentence))
    assert _rules(findings) == [("revision", "extras.revision")]


def test_opting_out_recorded_no_finding():
    gold = _gold(extras={"revision": "opting-out"})
    assert _run(gold, _notice("Es wird auf eine eingeschränkte Revision verzichtet.")) == []


# --- 8. nominal_value_chf ---


@pytest.mark.parametrize(
    "sentence",
    [
        "Aktien neu: 100 Namenaktien zu CHF 1'000.00 [bisher: 50 Namenaktien zu CHF 1'000.00].",
        "Eingetragene Personen: Muster, Hans, von Bern, in Bern, mit 20 Stammanteilen zu je CHF 100.00.",
    ],
)
def test_nominal_value_null_is_flagged(sentence):
    findings = _run(_gold(), _notice(sentence))
    assert _rules(findings) == [("nominal_value_chf", "extras.nominal_value_chf")]


def test_nominal_value_recorded_no_finding():
    gold = _gold(extras={"nominal_value_chf": 1000.0})
    assert _run(gold, _notice("Aktien: 100 Namenaktien zu CHF 1'000.00.")) == []


def test_capital_amount_alone_is_not_a_nominal_value():
    assert _run(_gold(), _notice("Aktienkapital neu: CHF 100'000.00 [bisher: CHF 50'000.00].")) == []


# --- 9. persons_changed nationality_new ---

MUTATING = (
    "Eingetragene Personen neu oder mutierend: "
    "Muster, Hans, deutscher Staatsangehöriger, in Thun, Präsident, mit Einzelunterschrift "
    "[bisher: in Bern, Mitglied]; "
    "Beispiel, Anna, von Zürich, in Bern, Mitglied [bisher: deutsche Staatsangehörige, in Thun]."
)


def _changed(name: str, nationality_new: str | None) -> dict:
    return {"name_new": name, "name_previous": None, "nationality_new": nationality_new}


def test_staatsangehoerig_outside_bisher_with_null_is_flagged():
    gold = _gold(act_subtypes=["organaenderung"], persons_changed=[_changed("Muster, Hans", None)])
    findings = _run(gold, _notice(MUTATING))
    assert _rules(findings) == [("persons_changed", "persons_changed[Muster, Hans].nationality_new")]
    assert findings[0].evidence.startswith("Muster, Hans")
    assert "Beispiel" not in findings[0].evidence


def test_staatsangehoerig_only_inside_bisher_no_finding():
    gold = _gold(act_subtypes=["organaenderung"], persons_changed=[_changed("Beispiel, Anna", None)])
    assert _run(gold, _notice(MUTATING)) == []


def test_nationality_new_filled_no_finding():
    gold = _gold(act_subtypes=["organaenderung"], persons_changed=[_changed("Muster, Hans", "deutsch")])
    assert _run(gold, _notice(MUTATING)) == []


def test_split_entries_ignores_semicolons_in_brackets():
    assert sweep.split_entries("A [bisher: x; y]; B") == ["A [bisher: x; y]", "B"]


# --- files and CLI ---


def _write_split(tmp_path: Path, gold: dict, text: str) -> tuple[Path, Path]:
    gold_dir, raw_dir = tmp_path / "gold", tmp_path / "raw"
    gold_dir.mkdir()
    raw_dir.mkdir()
    (gold_dir / "0001.json").write_text(json.dumps(gold, ensure_ascii=False), encoding="utf-8")
    (gold_dir / ".gitkeep").write_text("", encoding="utf-8")
    (raw_dir / "0001.txt").write_text(text, encoding="utf-8")
    return gold_dir, raw_dir


def test_main_writes_csv_and_refuses_to_overwrite(tmp_path, capsys):
    gold_dir, raw_dir = _write_split(tmp_path, _gold(), _notice("Es wird auf eine eingeschränkte Revision verzichtet."))
    out_dir = tmp_path / "out"
    argv = ["--split", "dev", "--gold-dir", str(gold_dir), "--raw-dir", str(raw_dir), "--out-dir", str(out_dir)]

    assert sweep.main(argv) == 0
    path = out_dir / "dev" / "rule_sweep.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert list(rows[0]) == sweep.COLUMNS
    assert [(r["doc_id"], r["rule"], r["gold_value"], r["verdict"]) for r in rows] == [("0001", "revision", "null", "")]
    assert "eingeschränkte Revision" in rows[0]["evidence"]

    path.write_text("verdicts by hand", encoding="utf-8")
    assert sweep.main(argv) == 2
    assert path.read_text(encoding="utf-8") == "verdicts by hand"
    assert "already exists" in capsys.readouterr().err

    assert sweep.main([*argv, "--force"]) == 0


def test_missing_raw_is_an_error(tmp_path):
    gold_dir, raw_dir = _write_split(tmp_path, _gold(), "")
    (raw_dir / "0001.txt").unlink()
    with pytest.raises(sweep.SweepError):
        sweep.sweep(gold_dir, raw_dir)


def test_refuses_to_write_under_data(tmp_path):
    gold_dir, raw_dir = _write_split(tmp_path, _gold(), _notice("Zweck neu: Handel."))
    argv = ["--split", "dev", "--gold-dir", str(gold_dir), "--raw-dir", str(raw_dir), "--out-dir", str(sweep.DATA_DIR)]
    assert sweep.main(argv) == 2
