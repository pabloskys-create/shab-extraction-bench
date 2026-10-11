"""Tests for src/evaluate.py. Every gold record and prediction here is
built by hand in the test; nothing is read from the real gold."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

import pytest

from src import evaluate, parse_responses
from src.evaluate import (
    CORRECT,
    CORRECT_NULL,
    EXCLUDED,
    HALLUCINATION,
    MISSED,
    NO_OUTPUT,
    OMISSION,
    SPURIOUS,
    UNREGISTERED,
    WRONG,
)

REGISTRY = parse_responses._load_extras_registry()
LONG_ZWECK = (
    "Die Gesellschaft bezweckt den Betrieb eines Handels mit Waren aller Art, insbesondere mit "
    "Lebensmitteln und Getränken, sowie die Erbringung von Dienstleistungen in diesem Bereich."
)


def _gold(**overrides) -> dict:
    record = dict.fromkeys(parse_responses.CORE_KEYS)
    record.update(
        {
            "doc_id": "0001",
            "act_type": "mutation",
            "act_subtypes": [],
            "company_name_full": "Foo AG",
            "alternative_names": [],
            "persons_added": [],
            "persons_removed": [],
            "persons_changed": [],
            "extras": {},
            "uncertain": [],
        }
    )
    record.update(overrides)
    return record


def _prediction_from(gold: dict, **overrides) -> dict:
    data = {k: gold[k] for k in parse_responses.CORE_KEYS}
    data.update(overrides)
    return data


def _parsed(data: dict | None, status: str = "ok") -> dict:
    return {"doc_id": "0001", "parse_status": status, "data": data}


def _score(gold: dict, parsed: dict):
    return evaluate.score_document(gold, parsed, REGISTRY)


def _row(rows: list[dict], field: str, item: str = "", attribute: str = "") -> dict:
    [row] = [r for r in rows if r["field"] == field and r["item"] == item and r["attribute"] == attribute]
    return row


def _person(**overrides) -> dict:
    person = dict.fromkeys(parse_responses.PERSON_KEYS_ORDER)
    person.update(overrides)
    return person


# --- field lists come from the schema, not a hand copy ---


def test_every_contract_key_is_scored_somewhere():
    covered = (
        set(evaluate.FIELD_LEVEL_FIELDS) | {f for f, _ in evaluate.PERSON_LIST_FIELDS} | {"extras"}
    )
    assert covered == set(parse_responses.CORE_KEYS)
    assert set(evaluate.SET_FIELDS) == {"act_subtypes", "alternative_names"}


# --- the five outcomes, plus no_output ---


@pytest.mark.parametrize(
    ("gold_value", "pred_value", "expected"),
    [
        ("Bern", "Bern", CORRECT),
        (None, None, CORRECT_NULL),
        ("Bern", None, OMISSION),
        (None, "Bern", HALLUCINATION),
        ("Bern", "Zürich", WRONG),
    ],
)
def test_each_of_the_five_outcomes(gold_value, pred_value, expected):
    gold = _gold(seat_municipality=gold_value)
    rows, tally = _score(gold, _parsed(_prediction_from(gold, seat_municipality=pred_value)))
    row = _row(rows, "seat_municipality")
    assert row["outcome_exact"] == row["outcome_normalized"] == expected
    assert tally[("field", "seat_municipality", "exact", expected)] == 1


@pytest.mark.parametrize("status", ["failed", "no_response"])
def test_unparsed_document_fails_every_field_as_no_output(status):
    gold = _gold(
        act_subtypes=["organaenderung"],
        persons_added=[_person(name="Hans Muster")],
        extras={"revision": "opting-out"},
    )
    _, tally = _score(gold, _parsed(None, status=status))
    for field in evaluate.FIELD_LEVEL_FIELDS:
        assert tally[("field", field, "normalized", NO_OUTPUT)] == 1
    assert tally[("persons", "persons_added", "fn")] == 1
    assert tally[("extras_key", "fn")] == 1
    assert tally[("subtype", "organaenderung", "fn")] == 1
    accuracy = evaluate.field_accuracy(tally, "normalized")
    assert accuracy == (0, len(evaluate.FIELD_LEVEL_FIELDS))


def test_no_output_is_not_counted_as_omission():
    gold = _gold()
    _, tally = _score(gold, _parsed(None, status="failed"))
    assert tally[("field", "company_name_full", "exact", OMISSION)] == 0


# --- uncertain ---


def test_field_in_uncertain_is_excluded_for_that_document_only():
    gold = _gold(seat_municipality="Bern", uncertain=["seat_municipality"])
    rows, tally = _score(gold, _parsed(_prediction_from(gold, seat_municipality="Zürich")))
    assert _row(rows, "seat_municipality")["outcome_exact"] == EXCLUDED
    assert not any(k[0] == "field" and k[1] == "seat_municipality" for k in tally)
    assert tally[("excluded", "seat_municipality")] == 1

    other = _gold(seat_municipality="Bern")
    _, other_tally = _score(other, _parsed(_prediction_from(other, seat_municipality="Zürich")))
    assert other_tally[("field", "seat_municipality", "exact", WRONG)] == 1


def test_person_list_in_uncertain_excludes_the_whole_list():
    gold = _gold(persons_added=[_person(name="Hans Muster")], uncertain=["persons_added"])
    _, tally = _score(gold, _parsed(_prediction_from(gold, persons_added=[])))
    assert not any(k[0] in ("persons", "person_attr") and k[1] == "persons_added" for k in tally)


def test_extras_in_uncertain_is_excluded():
    gold = _gold(extras={"revision": "opting-out"}, uncertain=["extras"])
    _, tally = _score(gold, _parsed(_prediction_from(gold, extras={})))
    assert not any(k[0].startswith("extras") for k in tally)


# --- normalization ---


@pytest.mark.parametrize(
    ("gold_value", "pred_value"),
    [
        (100000, 100000.0),
        ("Foo  AG ", "Foo AG"),
        ("FOO ag", "Foo AG"),
        ("Zürich", "Zürich"),  # decomposed vs composed ü
    ],
)
def test_normalized_but_not_exact_matches(gold_value, pred_value):
    assert evaluate.values_equal(gold_value, pred_value, normalized=True)
    assert not evaluate.values_equal(gold_value, pred_value, normalized=False)


def test_normalization_does_not_drop_name_particles():
    assert not evaluate.values_equal("von Schangnau", "Schangnau", normalized=True)


def test_bool_never_equals_a_number():
    assert not evaluate.values_equal(True, 1, normalized=True)


@pytest.mark.parametrize("text", ["100000", "100000.00", " 100000 "])
def test_clean_numeric_string_equals_the_number_only_when_normalized(text):
    assert evaluate.values_equal(100000, text, normalized=True)
    assert evaluate.values_equal(text, 100000.0, normalized=True)
    assert not evaluate.values_equal(100000, text, normalized=False)


@pytest.mark.parametrize("text", ["100'000", "CHF 100000", "100000.", "1.000.000", "-5", "", "100001"])
def test_formatted_or_different_numeric_string_is_not_the_number(text):
    assert not evaluate.values_equal(100000, text, normalized=True)


def test_two_numeric_strings_still_compare_as_text():
    assert not evaluate.values_equal("007", "7", normalized=True)


def test_bool_never_equals_a_numeric_string():
    assert not evaluate.values_equal(True, "1", normalized=True)


# --- sets ---


def test_set_field_ignores_order_and_treats_empty_as_null():
    assert evaluate.classify_set(["a", "b"], ["b", "a"], normalized=False) == CORRECT
    assert evaluate.classify_set([], [], normalized=False) == CORRECT_NULL
    assert evaluate.classify_set([], ["a"], normalized=False) == HALLUCINATION
    assert evaluate.classify_set(["a"], [], normalized=False) == OMISSION
    assert evaluate.classify_set(["a"], ["a", "b"], normalized=False) == WRONG


def test_act_subtypes_micro_and_macro_by_hand():
    # doc 1: gold {organaenderung, sitzverlegung}, pred {organaenderung, fusion}
    # doc 2: gold {organaenderung},                pred {organaenderung}
    # organaenderung tp 2; sitzverlegung fn 1; fusion fp 1
    # micro: P = 2/3, R = 2/3, F1 = 2/3
    # macro F1: organaenderung 1.0, sitzverlegung 0.0, fusion 0.0 -> 1/3
    tally: Counter = Counter()
    for gold_labels, pred_labels in [
        (["organaenderung", "sitzverlegung"], ["organaenderung", "fusion"]),
        (["organaenderung"], ["organaenderung"]),
    ]:
        gold = _gold(act_subtypes=gold_labels)
        _, doc_tally = _score(gold, _parsed(_prediction_from(gold, act_subtypes=pred_labels)))
        tally.update(doc_tally)
    summary = evaluate.build_summary(tally, [tally], [], run_info={"run_id": "t"}, iterations=10, seed=1)
    micro = summary["act_subtypes"]["micro"]
    assert (micro["tp"], micro["fp"], micro["fn"]) == (2, 1, 1)
    assert micro["f1"] == pytest.approx(2 / 3)
    assert summary["act_subtypes"]["macro"]["f1"] == pytest.approx(1 / 3)
    assert summary["fields"]["act_subtypes"]["exact"][CORRECT] == 1
    assert summary["fields"]["act_subtypes"]["exact"][WRONG] == 1


# --- persons ---


def test_person_with_a_non_matching_name_is_one_fp_and_one_fn():
    gold = _gold(persons_added=[_person(name="Hans Müller", role="Mitglied")])
    pred = _prediction_from(gold, persons_added=[_person(name="Müller, Hans", role="Mitglied")])
    rows, tally = _score(gold, _parsed(pred))
    assert tally[("persons", "persons_added", "tp")] == 0
    assert tally[("persons", "persons_added", "fp")] == 1
    assert tally[("persons", "persons_added", "fn")] == 1
    assert not any(k[0] == "person_attr" for k in tally)
    assert _row(rows, "persons_added", item="Hans Müller")["outcome_exact"] == MISSED
    assert _row(rows, "persons_added", item="Müller, Hans")["outcome_exact"] == SPURIOUS


def test_person_names_match_after_normalization_and_attributes_get_both_modes():
    gold = _gold(persons_added=[_person(name="Hans Müller", role="Mitglied", domicile="Bern")])
    pred = _prediction_from(gold, persons_added=[_person(name="hans  müller", role="mitglied", domicile="Basel")])
    rows, tally = _score(gold, _parsed(pred))
    assert tally[("persons", "persons_added", "tp")] == 1
    role = _row(rows, "persons_added", item="Hans Müller", attribute="role")
    assert (role["outcome_exact"], role["outcome_normalized"]) == (WRONG, CORRECT)
    assert _row(rows, "persons_added", item="Hans Müller", attribute="domicile")["outcome_normalized"] == WRONG
    assert _row(rows, "persons_added", item="Hans Müller", attribute="uid")["outcome_normalized"] == CORRECT_NULL


def test_namesakes_pair_one_to_one_in_order():
    gold_people = [_person(name="Anna Meier", role="A"), _person(name="Anna Meier", role="B")]
    pred_people = [_person(name="Anna Meier", role="A")]
    pairs, missed, spurious = evaluate.match_persons(gold_people, pred_people, "name")
    assert [(g["role"], p["role"]) for g, p in pairs] == [("A", "A")]
    assert [g["role"] for g in missed] == ["B"]
    assert spurious == []


def test_persons_changed_matches_on_name_new():
    changed = dict.fromkeys(parse_responses.PERSON_CHANGE_KEYS_ORDER)
    gold_person = {**changed, "name_new": "Eva Keller", "name_previous": "Eva Brunner"}
    pred_person = {**changed, "name_new": "Eva Keller", "name_previous": None}
    gold = _gold(persons_changed=[gold_person])
    rows, tally = _score(gold, _parsed(_prediction_from(gold, persons_changed=[pred_person])))
    assert tally[("persons", "persons_changed", "tp")] == 1
    assert _row(rows, "persons_changed", item="Eva Keller", attribute="name_previous")["outcome_exact"] == OMISSION


# --- extras ---


def test_zweck_prefix_matches_across_ellipsis_styles():
    gold = LONG_ZWECK[:150] + "…"
    pred = LONG_ZWECK[:120] + "..."
    assert evaluate.zweck_match(gold, pred, normalized=False)


def test_zweck_with_a_different_prefix_is_wrong():
    pred = "Der Verein" + LONG_ZWECK[10:150] + "…"
    assert not evaluate.zweck_match(LONG_ZWECK[:150] + "…", pred, normalized=True)


def test_zweck_too_short_a_prefix_is_wrong():
    assert not evaluate.zweck_match(LONG_ZWECK, "Die", normalized=True)
    assert not evaluate.zweck_match(LONG_ZWECK, LONG_ZWECK[:99] + "…", normalized=True)
    assert evaluate.zweck_match(LONG_ZWECK, LONG_ZWECK[:100] + "…", normalized=True)


def test_short_gold_zweck_must_be_matched_in_full():
    short = "Handel mit Waren."
    assert evaluate.zweck_match(short, short, normalized=False)
    assert not evaluate.zweck_match(short, "Handel mit", normalized=False)


def test_loeschung_reason_matches_by_containment_either_way():
    assert evaluate.loeschung_reason_match("Geschäftsaufgabe", "Infolge Geschäftsaufgabe", normalized=False)
    assert evaluate.loeschung_reason_match("infolge Geschäftsaufgabe", "GESCHÄFTSAUFGABE", normalized=True)
    assert not evaluate.loeschung_reason_match("Geschäftsaufgabe", "Fusion", normalized=True)


def test_extras_keys_and_values():
    gold = _gold(extras={"revision": "opting-out", "zweck": LONG_ZWECK, "vinkulierung": True})
    pred = _prediction_from(
        gold,
        extras={"revision": "Opting-Out", "zweck": LONG_ZWECK[:120] + "…", "aktien_new": 100, "made_up": "x"},
    )
    rows, tally = _score(gold, _parsed(pred))
    assert tally[("extras_key", "tp")] == 2
    assert tally[("extras_key", "fn")] == 1
    assert tally[("extras_key", "fp")] == 2
    assert tally[("extras_key_unregistered",)] == 1
    revision = _row(rows, "extras", item="revision")
    assert (revision["outcome_exact"], revision["outcome_normalized"]) == (WRONG, CORRECT)
    assert _row(rows, "extras", item="zweck")["outcome_exact"] == CORRECT
    assert _row(rows, "extras", item="vinkulierung")["outcome_exact"] == MISSED
    assert _row(rows, "extras", item="aktien_new")["outcome_exact"] == SPURIOUS
    assert _row(rows, "extras", item="made_up")["outcome_exact"] == UNREGISTERED


# --- bootstrap ---


def _tally_with(correct: int, total: int) -> Counter:
    tally: Counter = Counter()
    tally[("field", "uid", "normalized", CORRECT)] = correct
    tally[("field", "uid", "normalized", WRONG)] = total - correct
    return tally


def test_bootstrap_is_reproducible_with_the_same_seed():
    per_doc = [_tally_with(c, 3) for c in (0, 1, 2, 3, 3, 1)]
    metrics = {"acc": lambda t: evaluate.field_accuracy(t, "normalized")}
    first = evaluate.bootstrap_ci(per_doc, metrics, iterations=500, seed=7)
    second = evaluate.bootstrap_ci(per_doc, metrics, iterations=500, seed=7)
    assert first == second
    assert first["acc"]["ci_low"] < first["acc"]["ci_high"]


def test_bootstrap_of_identical_documents_collapses_to_a_point():
    per_doc = [_tally_with(2, 4)] * 10
    metrics = {"acc": lambda t: evaluate.field_accuracy(t, "normalized")}
    ci = evaluate.bootstrap_ci(per_doc, metrics, iterations=200, seed=1)["acc"]
    assert ci["ci_low"] == ci["ci_high"] == pytest.approx(0.5)


# --- end to end ---


def _write_run(tmp_path: Path, golds: list[dict], parsed: list[dict]) -> tuple[Path, Path]:
    run_dir = tmp_path / "runs" / "t"
    gold_dir = tmp_path / "gold"
    (run_dir / "parsed").mkdir(parents=True)
    gold_dir.mkdir()
    doc_ids = [g["doc_id"] for g in golds]
    (run_dir / "run.json").write_text(json.dumps({"run_id": "t", "split": "dev", "doc_ids": doc_ids}), encoding="utf-8")
    for gold, record in zip(golds, parsed, strict=True):
        (gold_dir / f"{gold['doc_id']}.json").write_text(json.dumps(gold), encoding="utf-8")
        full = {**parse_responses._empty_analysis(), "repairs": {}, **record, "doc_id": gold["doc_id"]}
        (run_dir / "parsed" / f"{gold['doc_id']}.json").write_text(json.dumps(full), encoding="utf-8")
    return run_dir, gold_dir


def test_main_writes_summary_and_long_table(tmp_path):
    good = _gold(doc_id="0001")
    bad = _gold(doc_id="0002")
    _, gold_dir = _write_run(tmp_path, [good, bad], [_parsed(_prediction_from(good)), _parsed(None, "failed")])
    results = tmp_path / "results"
    code = evaluate.main(
        ["--run-id", "t", "--runs-dir", str(tmp_path / "runs"), "--results-dir", str(results),
         "--gold-dir", str(gold_dir), "--iterations", "50"]
    )
    assert code == 0
    summary = json.loads((results / "t" / "summary.json").read_text(encoding="utf-8"))
    assert summary["headline"]["core_accuracy_normalized"]["value"] == pytest.approx(0.5)
    assert summary["headline"]["parse_failure_rate"]["value"] == pytest.approx(0.5)
    assert summary["format"]["status_counts"]["failed"] == 1
    with (results / "t" / "fields.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert {r["doc_id"] for r in rows} == {"0001", "0002"}
    assert {r["outcome_exact"] for r in rows if r["doc_id"] == "0002"} == {NO_OUTPUT}


def test_missing_parsed_file_aborts(tmp_path):
    gold = _gold()
    run_dir, gold_dir = _write_run(tmp_path, [gold], [_parsed(_prediction_from(gold))])
    (run_dir / "parsed" / "0001.json").unlink()
    with pytest.raises(evaluate.EvaluationError, match="parse_responses"):
        evaluate.evaluate_run(run_dir, gold_dir=gold_dir, iterations=10)


def test_missing_gold_file_aborts(tmp_path):
    gold = _gold()
    run_dir, gold_dir = _write_run(tmp_path, [gold], [_parsed(_prediction_from(gold))])
    (gold_dir / "0001.json").unlink()
    with pytest.raises(evaluate.EvaluationError, match="no gold file"):
        evaluate.evaluate_run(run_dir, gold_dir=gold_dir, iterations=10)


# --- field groups, value accuracy and null accuracy ---


def test_field_groups_partition_the_field_level_fields_exactly():
    header, comprehension = evaluate.HEADER_FIELDS, evaluate.COMPREHENSION_FIELDS
    assert len(header) == 19
    assert len(set(header)) == len(header)
    assert len(set(comprehension)) == len(comprehension)
    assert not set(header) & set(comprehension)
    assert set(header) | set(comprehension) == set(evaluate.FIELD_LEVEL_FIELDS)


def _tally_for(pairs: list[tuple], field: str = "capital_new_chf") -> Counter:
    tally: Counter = Counter()
    for gold_value, pred_value in pairs:
        gold = _gold(**{field: gold_value})
        _, doc_tally = _score(gold, _parsed(_prediction_from(gold, **{field: pred_value})))
        tally.update(doc_tally)
    return tally


def test_value_and_null_accuracy_by_hand():
    # gold has a value: correct, omission, wrong   -> value accuracy 1/3
    # gold is null:     correct_null, hallucination -> null accuracy 1/2
    tally = _tally_for([(100, 100), (100, None), (100, 200), (None, None), (None, 5)])
    fields = ("capital_new_chf",)
    assert evaluate.value_accuracy(tally, "normalized", fields) == (1, 3)
    assert evaluate.null_accuracy(tally, "normalized", fields) == (1, 2)
    assert evaluate.field_accuracy(tally, "normalized", fields) == (2, 5)


def test_no_output_counts_as_a_failure_in_both_denominators():
    gold = _gold(capital_new_chf=100, act_date=None, act_subtypes=["fusion"], canton_new=None)
    _, tally = _score(gold, _parsed(None, status="failed"))
    assert evaluate.value_accuracy(tally, "normalized", ("capital_new_chf",)) == (0, 1)
    assert evaluate.null_accuracy(tally, "normalized", ("capital_new_chf",)) == (0, 0)
    assert evaluate.null_accuracy(tally, "normalized", ("act_date",)) == (0, 1)
    assert evaluate.value_accuracy(tally, "normalized", ("act_subtypes",)) == (0, 1)
    comprehension = evaluate.COMPREHENSION_FIELDS
    value_num, value_den = evaluate.value_accuracy(tally, "normalized", comprehension)
    null_num, null_den = evaluate.null_accuracy(tally, "normalized", comprehension)
    assert (value_num, null_num) == (0, 0)
    assert value_den + null_den == len(comprehension)


def test_empty_gold_set_counts_as_null():
    tally = _tally_for([([], []), ([], ["fusion"])], field="act_subtypes")
    assert evaluate.null_accuracy(tally, "normalized", ("act_subtypes",)) == (1, 2)
    assert evaluate.value_accuracy(tally, "normalized", ("act_subtypes",)) == (0, 0)


def test_summary_reports_groups_and_per_field_value_and_null_accuracy_with_intervals():
    tally = _tally_for([(100, 100), (100, None), (None, None)])
    summary = evaluate.build_summary(tally, [tally], [], run_info={"run_id": "t"}, iterations=10, seed=1)
    group = summary["groups"]["comprehension"]["normalized"]
    assert group["value_accuracy"] == pytest.approx(1 / 2)
    assert group["null_accuracy"] == pytest.approx(
        _div_counts(evaluate.null_accuracy(tally, "normalized", evaluate.COMPREHENSION_FIELDS))
    )
    field = summary["fields"]["capital_new_chf"]
    assert field["group"] == "comprehension"
    assert field["normalized"]["value_accuracy"] == pytest.approx(1 / 2)
    assert field["normalized"]["null_accuracy"] == pytest.approx(1.0)
    for name in (
        "group_accuracy_normalized:header",
        "group_value_accuracy_normalized:comprehension",
        "group_null_accuracy_normalized:comprehension",
        "value_accuracy_normalized:capital_new_chf",
        "null_accuracy_normalized:capital_new_chf",
    ):
        assert {"value", "ci_low", "ci_high"} <= set(summary["headline"][name])


def _div_counts(ratio: tuple[int, int]) -> float:
    return ratio[0] / ratio[1]


def test_screen_output_follows_the_decided_order():
    tally = _tally_for([(100, 100)])
    summary = evaluate.build_summary(tally, [tally], [], run_info={"run_id": "t"}, iterations=10, seed=1)
    lines = evaluate.format_headline(summary["headline"]).splitlines()
    assert [line.split()[0] for line in lines] == [
        "group_value_accuracy_normalized:comprehension",
        "group_null_accuracy_normalized:comprehension",
        "persons_found_f1:persons_added",
        "persons_found_f1:persons_removed",
        "persons_found_f1:persons_changed",
        "act_subtypes_micro_f1",
        "extras_key_f1",
        "group_accuracy_normalized:header",
        "core_accuracy_normalized",
    ]
