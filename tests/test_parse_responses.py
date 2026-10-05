"""Tests for src/parse_responses.py — in-test fixtures only, no network.

Fixtures are built from a minimal valid 33-key core object (_valid_object)
rather than files on disk, then deliberately corrupted per test, matching
the shapes actually seen in runs/scratch/smoke1/ (gemma-3-12b: a
markdown-fenced response, and extras padded with null-valued keys).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from src import parse_responses, validate

REPO_ROOT = Path(__file__).resolve().parents[1]


def _valid_object(**overrides) -> dict:
    obj = dict.fromkeys(parse_responses.CORE_KEYS)
    obj.update(
        {
            "act_type": "mutation",
            "act_subtypes": [],
            "company_name_full": "Foo AG",
            "company_name_base": "Foo AG",
            "alternative_names": [],
            "uid": "CHE-123.456.789",
            "legal_form": "AG",
            "seat_municipality": "Bern",
            "seat_canton": "BE",
            "tagesregister_nr": "123",
            "tagesregister_date": "2026-01-01",
            "authority": "Handelsregisteramt",
            "persons_added": [],
            "persons_removed": [],
            "persons_changed": [],
            "extras": {},
        }
    )
    obj.update(overrides)
    return obj


def _raw(doc_id: str = "0001", *, status: str = "ok", response_text: str | None = None, error=None) -> dict:
    return {"doc_id": doc_id, "status": status, "response_text": response_text, "error": error}


EXTRAS_REGISTRY = parse_responses._load_extras_registry()


def _parse(response_text: str) -> dict:
    return parse_responses.parse_response(_raw(response_text=response_text), extras_registry=EXTRAS_REGISTRY)


# --- 1/2: real gemma-3-12b patterns (runs/scratch/smoke1/raw/0011.json, 0013.json) ---


def test_markdown_fence_is_stripped_and_parses_ok():
    text = "```json\n" + json.dumps(_valid_object()) + "\n```"
    record = _parse(text)
    assert record["parse_status"] == "ok"
    assert record["repairs"]["markdown_fence_stripped"] is True
    assert record["data"]["act_type"] == "mutation"


def test_extras_null_keys_are_dropped_and_logged():
    obj = _valid_object(extras={"zweck": "purpose", "hauptsitz": None, "vinkulierung": None})
    record = _parse(json.dumps(obj))
    assert record["parse_status"] == "ok"
    assert record["data"]["extras"] == {"zweck": "purpose"}
    assert sorted(record["extras_null_keys"]) == ["hauptsitz", "vinkulierung"]


# --- 3/4: text repairs ---


def test_leading_and_trailing_prose_is_stripped():
    text = "Here is the JSON:\n" + json.dumps(_valid_object()) + "\nHope that helps!"
    record = _parse(text)
    assert record["parse_status"] == "ok"
    assert record["repairs"]["extracted_from_surrounding_text"] is True


def test_trailing_comma_is_removed():
    text = json.dumps(_valid_object())
    # Inject a trailing comma right before the closing brace.
    broken = text[:-1] + ",}"
    record = _parse(broken)
    assert record["parse_status"] == "ok"
    assert record["repairs"]["trailing_commas_removed"] == 1


# --- 5: truncated JSON must fail, not be falsely "repaired" ---


def test_truncated_json_fails_without_false_repairs():
    text = json.dumps(_valid_object())
    truncated = text[: len(text) // 2]
    record = _parse(truncated)
    assert record["parse_status"] == "failed"
    assert record["data"] is None
    assert record["error"]["message"]
    assert record["repairs"] == {
        "markdown_fence_stripped": False,
        "extracted_from_surrounding_text": False,
        "trailing_commas_removed": 0,
    }


# --- 6/7: core key invented / missing ---


def test_invented_core_key_is_removed_and_logged():
    obj = _valid_object()
    obj["confidence"] = 0.97
    record = _parse(json.dumps(obj))
    assert record["parse_status"] == "ok"
    assert "confidence" not in record["data"]
    assert record["extra_keys"] == ["confidence"]


def test_missing_core_key_is_added_as_null_and_logged():
    obj = _valid_object()
    del obj["status_suffix"]
    record = _parse(json.dumps(obj))
    assert record["parse_status"] == "ok"
    assert record["data"]["status_suffix"] is None
    assert record["missing_keys"] == ["status_suffix"]


# --- 8: person object with an extra key ---


def test_person_object_extra_key_is_logged_with_index():
    person = dict.fromkeys(parse_responses.PERSON_KEYS_ORDER)
    person["confidence"] = 0.5
    obj = _valid_object(persons_added=[person])
    record = _parse(json.dumps(obj))
    assert record["parse_status"] == "ok"
    assert record["data"]["persons_added"] == [dict.fromkeys(parse_responses.PERSON_KEYS_ORDER)]
    assert record["person_list_issues"]["persons_added"] == [{"index": 0, "extra_keys": ["confidence"]}]


# --- 9: raw error status ---


def test_raw_error_status_yields_no_response():
    raw = _raw(status="error", error={"type": "BadRequestError", "message": "boom"})
    record = parse_responses.parse_response(raw, extras_registry=EXTRAS_REGISTRY)
    assert record["parse_status"] == "no_response"
    assert record["data"] is None
    assert record["error"] == {"type": "BadRequestError", "message": "boom"}


# --- 10: non-dict person-list element ---


def test_non_dict_person_list_element_is_dropped_and_logged():
    person = dict.fromkeys(parse_responses.PERSON_KEYS_ORDER)
    obj = _valid_object(persons_added=[person, "not a person"])
    record = _parse(json.dumps(obj))
    assert record["parse_status"] == "ok"
    assert record["data"]["persons_added"] == [person]
    assert record["person_list_elements_dropped"]["persons_added"] == [{"index": 1, "value_type": "str"}]


# --- 11/12: extras shape ---


def test_extras_not_an_object_is_replaced_and_logged():
    obj = _valid_object(extras="not an object")
    record = _parse(json.dumps(obj))
    assert record["parse_status"] == "ok"
    assert record["data"]["extras"] == {}
    assert record["extras_replaced"] is True


def test_unregistered_extras_key_is_kept_and_logged():
    obj = _valid_object(extras={"made_up_key": "value"})
    record = _parse(json.dumps(obj))
    assert record["parse_status"] == "ok"
    assert record["data"]["extras"] == {"made_up_key": "value"}
    assert record["unregistered_extras_keys"] == ["made_up_key"]


# --- 13: determinism ---


def test_parse_response_is_deterministic():
    raw = _raw(response_text="```json\n" + json.dumps(_valid_object()) + "\n```")
    first = parse_responses.parse_response(raw, extras_registry=EXTRAS_REGISTRY)
    second = parse_responses.parse_response(raw, extras_registry=EXTRAS_REGISTRY)
    assert first == second


# --- 14: end-to-end CLI ---


def test_cli_writes_parsed_files_and_summary(tmp_path):
    run_dir = tmp_path / "runs" / "run1"
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True)

    (raw_dir / "0001.json").write_text(
        json.dumps(_raw("0001", response_text=json.dumps(_valid_object()))), encoding="utf-8"
    )
    (raw_dir / "0002.json").write_text(
        json.dumps(_raw("0002", response_text="not json at all {")), encoding="utf-8"
    )
    (raw_dir / "0003.json").write_text(
        json.dumps(_raw("0003", status="error", error={"message": "boom"})), encoding="utf-8"
    )

    code = parse_responses.main(["--run-id", "run1", "--runs-dir", str(tmp_path / "runs")])
    assert code == 0

    parsed_dir = run_dir / "parsed"
    assert json.loads((parsed_dir / "0001.json").read_text())["parse_status"] == "ok"
    assert json.loads((parsed_dir / "0002.json").read_text())["parse_status"] == "failed"
    assert json.loads((parsed_dir / "0003.json").read_text())["parse_status"] == "no_response"

    summary = json.loads((run_dir / "parse_summary.json").read_text())
    assert summary["status_counts"] == {"ok": 1, "failed": 1, "no_response": 1}
    assert summary["total"] == 3


def test_cli_missing_raw_dir_aborts_with_exit_2(tmp_path):
    code = parse_responses.main(["--run-id", "nope", "--runs-dir", str(tmp_path / "runs")])
    assert code == 2


# --- 15/16/17: drift guards against the prompt / validate.py / SCHEMA.md ---


def test_core_keys_match_prompt_output_contract():
    prompt_text = (REPO_ROOT / "prompts" / "extraction_v1.md").read_text(encoding="utf-8")
    section = prompt_text.split("# Output contract")[1].split("\n# ")[0]
    fence_match = re.search(r"```json\s*\n(.*?)```", section, re.DOTALL)
    contract_keys = set(json.loads(fence_match.group(1)).keys())
    assert set(parse_responses.CORE_KEYS) == contract_keys


def test_person_key_order_matches_validate_frozenset():
    assert set(parse_responses.PERSON_KEYS_ORDER) == validate.PERSON_KEYS


def test_person_change_key_order_matches_validate_frozenset():
    assert set(parse_responses.PERSON_CHANGE_KEYS_ORDER) == validate.PERSON_CHANGE_KEYS


def test_extras_registry_matches_expected_schema_keys():
    expected = {
        "gesellschafterversammlung_date",
        "aktien_new",
        "aktien_previous",
        "nominal_value_chf",
        "share_classes",
        "loeschung_reason",
        "zweck",
        "hauptsitz",
        "liberierung_new_chf",
        "liberierung_previous_chf",
        "vinkulierung",
        "revision",
        "kapitalerhoehung_type",
        "weitere_adressen_new",
        "weitere_adressen_previous",
        "mitteilungen",
        "konkurseinstellung_reason",
        "konkurs_wirkung_ab",
        "steuerzustimmung",
        "nebenleistungspflichten",
        "zweigniederlassung_new",
        "prior_publication_page",
        "berichtigung_meldungsnummer",
        "berichtigung_shab_datum",
        "berichtigung_tr_nr",
        "berichtigung_tr_datum",
    }
    assert EXTRAS_REGISTRY == expected
    # And the parser must not have swallowed an example value quoted inside
    # a bullet's description as if it were a registered key name.
    assert "opting-out" not in EXTRAS_REGISTRY
    assert "mangels Aktiven" not in EXTRAS_REGISTRY


# --- 18/19/20: duplicate keys, at every nesting level ---


def test_duplicate_top_level_key_keeps_last_value_and_is_logged():
    text = json.dumps(_valid_object()).replace(
        '"act_type": "mutation"', '"act_type": "neueintragung", "act_type": "mutation"'
    )
    record = _parse(text)
    assert record["parse_status"] == "ok"
    assert record["data"]["act_type"] == "mutation"  # last value wins, like json.loads itself
    assert record["duplicate_keys"] == ["act_type"]


def test_duplicate_key_inside_person_object_is_logged():
    text = (
        '{"name": "A", "name": "B", "nationality": null, "heimatort": null, "domicile": null, '
        '"role": null, "signature": null, "uid": null, "stammanteile": null}'
    )
    obj = _valid_object()
    full_text = json.dumps(obj).replace('"persons_added": []', f'"persons_added": [{text}]')
    record = _parse(full_text)
    assert record["parse_status"] == "ok"
    assert record["data"]["persons_added"][0]["name"] == "B"
    assert record["person_list_issues"]["persons_added"] == [{"index": 0, "duplicate_keys": ["name"]}]


def test_duplicate_key_inside_extras_is_logged():
    full_text = json.dumps(_valid_object()).replace(
        '"extras": {}', '"extras": {"zweck": "first", "zweck": "second"}'
    )
    record = _parse(full_text)
    assert record["parse_status"] == "ok"
    assert record["data"]["extras"] == {"zweck": "second"}
    assert record["extras_duplicate_keys"] == ["zweck"]


# --- 21/22/23: a person-list field that isn't a list ---


def test_null_person_list_field_is_replaced_with_empty_list_and_logged():
    obj = _valid_object(persons_added=None)
    record = _parse(json.dumps(obj))
    assert record["parse_status"] == "ok"
    assert record["data"]["persons_added"] == []
    assert record["person_lists_replaced"] == {"persons_added": "NoneType"}


def test_single_dict_person_list_field_is_wrapped_in_a_list_and_logged():
    person = dict.fromkeys(parse_responses.PERSON_KEYS_ORDER)
    obj = _valid_object(persons_added=person)
    record = _parse(json.dumps(obj))
    assert record["parse_status"] == "ok"
    assert record["data"]["persons_added"] == [person]
    assert record["person_lists_replaced"] == {"persons_added": "dict"}


def test_string_person_list_field_is_replaced_with_empty_list_and_logged():
    obj = _valid_object(persons_changed="none")
    record = _parse(json.dumps(obj))
    assert record["parse_status"] == "ok"
    assert record["data"]["persons_changed"] == []
    assert record["person_lists_replaced"] == {"persons_changed": "str"}
