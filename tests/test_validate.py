"""Tests for src/validate.py.

Uses hand-built fixtures in tests/fixtures/ — never data/gold/, which is
hand-annotated ground truth and off-limits to tooling (CLAUDE.md rule 1).

Most tests here are cases that MUST fail validation, one per rule listed in
the task: each asserts the specific field/reason the rule is supposed to
catch, not just "some error happened". A handful of valid-fixture tests
guard against false positives.
"""

import copy
import json
import re
import sys
from pathlib import Path

import pytest

from src.validate import (
    FIELD_SPECS,
    ValidationError,
    main,
    validate_file,
    validate_record,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def _errors_on(errors: list[ValidationError], field: str) -> list[ValidationError]:
    return [e for e in errors if e.field == field]


# --- valid fixtures must pass cleanly ---


@pytest.mark.parametrize(
    "fixture_name",
    ["valid_mutation.json", "valid_loeschung.json", "valid_intercantonal.json"],
)
def test_valid_fixtures_have_no_errors(fixture_name):
    record = _load_fixture(fixture_name)
    assert validate_record(record) == []


def test_field_specs_match_schema_field_names():
    """Drift guard: FIELD_SPECS is a hand-kept mirror of SCHEMA.md's table,
    same pattern as test_prefill.py's test_record_keys_match_schema_exactly.
    """
    schema_text = (REPO_ROOT / "SCHEMA.md").read_text(encoding="utf-8")
    core_table = schema_text.split("## Core fields")[1].split("\n## ")[0]
    schema_fields = set(re.findall(r"^\|\s*`([a-zA-Z_]+)`\s*\|", core_table, re.MULTILINE))
    assert set(FIELD_SPECS.keys()) == schema_fields


# --- structural: keys match SCHEMA.md exactly ---


def test_extra_key_is_rejected():
    record = _load_fixture("valid_mutation.json")
    record["not_a_schema_field"] = "surprise"
    errors = validate_record(record)
    assert _errors_on(errors, "not_a_schema_field")


def test_missing_key_is_rejected():
    record = _load_fixture("valid_mutation.json")
    del record["tagesregister_nr"]
    errors = validate_record(record)
    assert _errors_on(errors, "tagesregister_nr")


# --- structural: types ---


def test_date_field_must_be_iso_string_not_source_format():
    record = _load_fixture("valid_mutation.json")
    record["tagesregister_fecha"] = "18.08.2026"  # DD.MM.YYYY source format
    errors = validate_record(record)
    assert _errors_on(errors, "tagesregister_fecha")


def test_capital_must_be_a_number_not_a_string():
    record = _load_fixture("valid_mutation.json")
    record["capital_nuevo_chf"] = "189'123.50"  # Swiss source format
    errors = validate_record(record)
    assert _errors_on(errors, "capital_nuevo_chf")


def test_list_field_must_be_a_list():
    record = _load_fixture("valid_mutation.json")
    record["subtipos"] = "liquidationseroeffnung"
    errors = validate_record(record)
    assert _errors_on(errors, "subtipos")


def test_extras_must_be_an_object():
    record = _load_fixture("valid_mutation.json")
    record["extras"] = []
    errors = validate_record(record)
    assert _errors_on(errors, "extras")


# --- structural: controlled vocabularies ---


def test_forma_juridica_must_be_in_controlled_vocabulary():
    record = _load_fixture("valid_mutation.json")
    record["forma_juridica"] = "SA"  # not a SCHEMA.md value
    errors = validate_record(record)
    assert _errors_on(errors, "forma_juridica")


def test_tipo_acto_must_be_in_controlled_vocabulary():
    record = _load_fixture("valid_mutation.json")
    record["tipo_acto"] = "eintragung"  # not a SCHEMA.md value
    errors = validate_record(record)
    assert _errors_on(errors, "tipo_acto")


def test_subtipos_values_must_be_in_controlled_vocabulary():
    record = _load_fixture("valid_mutation.json")
    record["subtipos"] = ["not_a_real_subtipo"]
    errors = validate_record(record)
    assert _errors_on(errors, "subtipos[0]")


# --- structural: absent scalar must be null, never "" ---


def test_absent_scalar_must_be_null_not_empty_string():
    record = _load_fixture("valid_mutation.json")
    record["direccion_calle"] = ""
    errors = validate_record(record)
    assert _errors_on(errors, "direccion_calle")


# --- coherence ---


def test_publicacion_anterior_fecha_must_precede_tagesregister_fecha():
    record = _load_fixture("valid_mutation.json")
    record["publicacion_anterior_fecha"] = record["tagesregister_fecha"]  # same day
    errors = validate_record(record)
    assert _errors_on(errors, "publicacion_anterior_fecha")


def test_fecha_acto_must_not_be_after_tagesregister_fecha():
    record = _load_fixture("valid_mutation.json")
    record["fecha_acto"] = "2026-09-01"  # after tagesregister_fecha (2026-08-18)
    errors = validate_record(record)
    assert _errors_on(errors, "fecha_acto")


def test_loeschung_forbids_personas_entrantes():
    record = _load_fixture("valid_loeschung.json")
    record["personas_entrantes"] = [
        {
            "nombre": "Neu Person",
            "nacionalidad": None,
            "heimatort": None,
            "domicilio": None,
            "cargo": None,
            "firma": None,
        }
    ]
    errors = validate_record(record)
    assert _errors_on(errors, "personas_entrantes")


def test_canton_anterior_requires_canton_nuevo():
    record = _load_fixture("valid_intercantonal.json")
    record["canton_nuevo"] = None
    errors = validate_record(record)
    assert _errors_on(errors, "canton_nuevo")


def test_canton_nuevo_requires_canton_anterior():
    record = _load_fixture("valid_intercantonal.json")
    record["canton_anterior"] = None
    errors = validate_record(record)
    assert _errors_on(errors, "canton_anterior")


def test_domicilio_nuevo_requires_domicilio_anterior():
    record = _load_fixture("valid_intercantonal.json")
    record["domicilio_anterior"] = None
    errors = validate_record(record)
    assert _errors_on(errors, "domicilio_anterior")


def test_domicilio_anterior_requires_domicilio_nuevo():
    record = _load_fixture("valid_intercantonal.json")
    record["domicilio_nuevo"] = None
    errors = validate_record(record)
    assert _errors_on(errors, "domicilio_nuevo")


def test_incierto_field_name_must_exist_in_schema():
    record = _load_fixture("valid_mutation.json")
    record["incierto"] = ["campo_inexistente"]
    errors = validate_record(record)
    assert _errors_on(errors, "incierto[0]")


# --- doc_id must match the filename ---


def test_doc_id_must_match_filename(tmp_path):
    record = _load_fixture("valid_mutation.json")
    # doc_id ("valid_mutation") deliberately left as-is while the file is
    # saved under a different name — the copy-paste-and-forget-to-rename case.
    mismatched = tmp_path / "0042.json"
    mismatched.write_text(json.dumps(record), encoding="utf-8")

    errors = validate_file(mismatched)
    assert _errors_on(errors, "doc_id")


def test_doc_id_matching_filename_has_no_doc_id_error(tmp_path):
    record = _load_fixture("valid_mutation.json")
    matching = tmp_path / "valid_mutation.json"
    matching.write_text(json.dumps(record), encoding="utf-8")

    errors = validate_file(matching)
    assert _errors_on(errors, "doc_id") == []


# --- --gold requires _verified == true ---


def test_gold_flag_requires_verified_true():
    record = _load_fixture("valid_mutation.json")
    record["_verified"] = False
    errors = validate_record(record, require_verified=True)
    assert _errors_on(errors, "_verified")


def test_without_gold_flag_unverified_record_is_allowed():
    record = _load_fixture("valid_mutation.json")
    record["_verified"] = False
    errors = validate_record(record)  # require_verified defaults to False
    assert _errors_on(errors, "_verified") == []


def test_cli_gold_flag_rejects_unverified_file(tmp_path, monkeypatch):
    record = _load_fixture("valid_mutation.json")
    record["_verified"] = False
    unverified = tmp_path / "valid_mutation.json"
    unverified.write_text(json.dumps(record), encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["validate.py", "--gold", str(unverified)])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code != 0


# --- file / non-dict input handling ---


def test_validate_record_rejects_non_dict_input():
    errors = validate_record(["not", "a", "record"])
    assert _errors_on(errors, "<root>")


def test_validate_file_reports_invalid_json(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")
    errors = validate_file(bad)
    assert errors


def test_validate_file_on_valid_fixture_has_no_errors():
    errors = validate_file(FIXTURES_DIR / "valid_mutation.json")
    assert errors == []


# --- CLI ---


def test_cli_exits_zero_for_a_valid_file(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["validate.py", str(FIXTURES_DIR / "valid_mutation.json")])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 0


def test_cli_exits_nonzero_for_an_invalid_file(tmp_path, monkeypatch):
    record = _load_fixture("valid_mutation.json")
    record["tipo_acto"] = "invalid_type"
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps(record), encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["validate.py", str(broken)])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code != 0


def test_cli_accepts_a_directory_of_files(tmp_path, monkeypatch):
    for name in ["valid_mutation.json", "valid_loeschung.json"]:
        (tmp_path / name).write_text(
            (FIXTURES_DIR / name).read_text(encoding="utf-8"), encoding="utf-8"
        )

    monkeypatch.setattr(sys, "argv", ["validate.py", str(tmp_path)])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 0


def test_deep_copy_of_fixture_is_independent():
    # Sanity check on the test helper itself: mutating a loaded fixture
    # dict must not corrupt what other tests load afterwards.
    a = _load_fixture("valid_mutation.json")
    b = copy.deepcopy(a)
    b["doc_id"] = "changed"
    assert a["doc_id"] == "valid_mutation"
