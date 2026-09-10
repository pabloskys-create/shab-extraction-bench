"""Tests for src/corpus_stats.py.

Every test builds its own small corpus — either inline `(doc_id, record)`
tuples or the JSON fixtures under tests/fixtures/corpus_stats/. Nothing
here reads data/exploratory/: the real corpus grows with every annotation
session, so asserting against it would make these tests fail on data
changes rather than on code changes.
"""

import json
from pathlib import Path

import pytest

from src.corpus_stats import (
    NULL_LABEL,
    compute,
    extras_distribution,
    list_distribution,
    load_records,
    main,
    person_list_stats,
    render_markdown,
    render_text,
    scalar_distribution,
    uncertain_distribution,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_CORPUS = REPO_ROOT / "tests" / "fixtures" / "corpus_stats"


def make_record(doc_id: str, **overrides) -> tuple[str, dict]:
    """A minimal record with every field this module touches, so a test
    only has to state the field it is about."""
    record = {
        "doc_id": doc_id,
        "act_type": "mutation",
        "act_subtypes": [],
        "legal_form": "AG",
        "seat_canton": "ZH",
        "persons_added": [],
        "persons_removed": [],
        "persons_changed": [],
        "extras": {},
        "uncertain": [],
    }
    record.update(overrides)
    return doc_id, record


def as_pairs(dist) -> list[tuple[str, int]]:
    return [(count.value, count.n) for count in dist.counts]


# --- scalar distributions ---


def test_scalar_distribution_counts_values():
    records = [
        make_record("0001", act_type="mutation"),
        make_record("0002", act_type="mutation"),
        make_record("0003", act_type="loeschung"),
    ]
    dist = scalar_distribution(records, "act_type")
    assert as_pairs(dist) == [("mutation", 2), ("loeschung", 1)]
    assert dist.total == 3


def test_scalar_distribution_reports_share_of_documents():
    records = [make_record("0001"), make_record("0002"), make_record("0003", legal_form="GmbH")]
    dist = scalar_distribution(records, "legal_form")
    shares = {count.value: round(dist.share(count), 1) for count in dist.counts}
    assert shares == {"AG": 66.7, "GmbH": 33.3}


def test_scalar_distribution_keeps_docs_per_value():
    records = [make_record("0001"), make_record("0002", seat_canton="BE")]
    dist = scalar_distribution(records, "seat_canton")
    assert {count.value: count.docs for count in dist.counts} == {
        "ZH": ["0001"],
        "BE": ["0002"],
    }


def test_scalar_distribution_labels_null_instead_of_dropping_it():
    records = [make_record("0001", legal_form=None), make_record("0002")]
    assert as_pairs(scalar_distribution(records, "legal_form")) == [
        (NULL_LABEL, 1),
        ("AG", 1),
    ]


def test_scalar_distribution_treats_missing_field_as_null():
    _, record = make_record("0001")
    del record["seat_canton"]
    assert as_pairs(scalar_distribution([("0001", record)], "seat_canton")) == [
        (NULL_LABEL, 1)
    ]


def test_counts_are_sorted_by_frequency_then_alphabetically():
    records = [
        make_record("0001", seat_canton="ZH"),
        make_record("0002", seat_canton="ZH"),
        make_record("0003", seat_canton="LU"),
        make_record("0004", seat_canton="BE"),
    ]
    assert as_pairs(scalar_distribution(records, "seat_canton")) == [
        ("ZH", 2),
        ("BE", 1),
        ("LU", 1),
    ]


# --- list, extras and uncertain distributions ---


def test_list_distribution_counts_each_subtype():
    records = [
        make_record("0001", act_subtypes=["firmenaenderung", "zweckaenderung"]),
        make_record("0002", act_subtypes=["firmenaenderung"]),
        make_record("0003", act_subtypes=[]),
    ]
    dist = list_distribution(records, "act_subtypes")
    assert as_pairs(dist) == [("firmenaenderung", 2), ("zweckaenderung", 1)]
    assert dist.total == 3


def test_list_distribution_shares_can_sum_above_100_percent():
    records = [make_record("0001", act_subtypes=["a", "b"])]
    dist = list_distribution(records, "act_subtypes")
    assert sum(dist.share(count) for count in dist.counts) == 200.0


def test_list_distribution_counts_a_document_once_per_distinct_entry():
    records = [make_record("0001", act_subtypes=["firmenaenderung", "firmenaenderung"])]
    assert as_pairs(list_distribution(records, "act_subtypes")) == [
        ("firmenaenderung", 1)
    ]


def test_extras_distribution_lists_keys_with_their_documents():
    records = [
        make_record("0001", extras={"zweck": "...", "prior_publication_page": "3707"}),
        make_record("0002", extras={"zweck": "..."}),
        make_record("0003", extras={}),
    ]
    dist = extras_distribution(records)
    assert {count.value: count.docs for count in dist.counts} == {
        "zweck": ["0001", "0002"],
        "prior_publication_page": ["0001"],
    }


def test_uncertain_distribution_counts_flagged_fields():
    records = [
        make_record("0001", uncertain=["persons_added", "domicile_new"]),
        make_record("0002", uncertain=["persons_added"]),
        make_record("0003"),
    ]
    assert as_pairs(uncertain_distribution(records)) == [
        ("persons_added", 2),
        ("domicile_new", 1),
    ]


def test_uncertain_docs_are_the_documents_with_a_non_empty_list():
    records = [
        make_record("0001", uncertain=["legal_form"]),
        make_record("0002"),
        make_record("0003", uncertain=["extras"]),
    ]
    assert compute(records).uncertain_docs == ["0001", "0003"]


# --- person lists ---


def test_person_list_stats_counts_people_and_empty_documents():
    records = [
        make_record("0001", persons_added=[{"name": "A"}, {"name": "B"}]),
        make_record("0002", persons_added=[{"name": "C"}]),
        make_record("0003"),
    ]
    stats = person_list_stats(records, "persons_added")
    assert stats.people == 3
    assert stats.empty_docs == ["0003"]
    assert stats.per_doc == 1.0
    assert round(stats.empty_share, 1) == 33.3


def test_compute_covers_every_person_list_in_the_schema():
    stats = compute([make_record("0001")])
    assert [item.field for item in stats.person_lists] == [
        "persons_added",
        "persons_removed",
        "persons_changed",
    ]
    assert all(item.people == 0 for item in stats.person_lists)
    assert all(item.empty_docs == ["0001"] for item in stats.person_lists)


# --- loading ---


def test_load_records_reads_the_directory_in_filename_order():
    records = load_records(FIXTURE_CORPUS)
    assert [doc_id for doc_id, _ in records] == ["0001", "0002", "0003"]
    assert records[0][1]["act_type"] == "mutation"


def test_load_records_falls_back_to_the_filename_when_doc_id_is_absent(tmp_path):
    (tmp_path / "0042.json").write_text(json.dumps({"act_type": "mutation"}), encoding="utf-8")
    assert [doc_id for doc_id, _ in load_records(tmp_path)] == ["0042"]


def test_load_records_raises_on_invalid_json(tmp_path):
    (tmp_path / "0001.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        load_records(tmp_path)


def test_fixture_corpus_totals():
    stats = compute(load_records(FIXTURE_CORPUS))
    assert stats.total == 3
    assert as_pairs(stats.scalars[0]) == [("mutation", 2), ("neueintragung", 1)]
    assert as_pairs(stats.extras) == [("zweck", 2), ("prior_publication_page", 1)]
    assert stats.uncertain_docs == ["0002"]


# --- rendering ---


def test_render_text_shows_counts_percentages_and_extras_documents():
    stats = compute(load_records(FIXTURE_CORPUS))
    output = render_text(stats)
    assert "3 documents" in output
    assert "mutation" in output and "66.7%" in output
    assert "zweck" in output and "0001, 0003" in output
    assert "persons_added" in output


def test_render_text_singular_for_a_one_document_corpus():
    assert render_text(compute([make_record("0001")])).startswith("1 document\n")


def test_render_markdown_emits_tables():
    stats = compute(load_records(FIXTURE_CORPUS))
    output = render_markdown(stats)
    assert "| act_type | n | % |" in output
    assert "|---|---|---|" in output
    assert "| mutation | 2 | 66.7% |" in output
    assert "| key | n | % | documents |" in output
    assert "| zweck | 2 | 66.7% | 0001, 0003 |" in output
    assert "| list | people | people/doc | empty | empty % |" in output
    assert "| persons_added | 3 | 1.00 | 1 | 33.3% |" in output


def test_render_markdown_notes_that_subtype_shares_can_exceed_100():
    output = render_markdown(compute(load_records(FIXTURE_CORPUS)))
    assert "sums above 100%" in output


def test_render_markdown_marks_an_empty_section():
    records = [make_record("0001")]
    assert "_(none)_" in render_markdown(compute(records))


# --- CLI ---


def test_main_defaults_to_text_output(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["corpus_stats.py", str(FIXTURE_CORPUS)])
    main()
    assert "--- act_type ---" in capsys.readouterr().out


def test_main_markdown_flag_switches_renderer(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["corpus_stats.py", str(FIXTURE_CORPUS), "--markdown"])
    main()
    assert "## Corpus statistics" in capsys.readouterr().out


def test_main_exits_on_a_missing_directory(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr("sys.argv", ["corpus_stats.py", str(tmp_path / "nope")])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1
    assert "not a directory" in capsys.readouterr().err


def test_main_exits_on_an_empty_directory(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr("sys.argv", ["corpus_stats.py", str(tmp_path)])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1
    assert "no *.json" in capsys.readouterr().err


def test_main_exits_on_invalid_json(monkeypatch, capsys, tmp_path):
    (tmp_path / "0001.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["corpus_stats.py", str(tmp_path)])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1
    assert "invalid JSON" in capsys.readouterr().err
