"""Tests for src/sample.py against a reduced HTML fixture.

tests/fixtures/listing_snippet.html is a hand-trimmed excerpt of the real
saved page (data/sampling/listing_2026-07-07.html): 12 `list-entry` records
with the exact same markup shape, and a "15 Treffer" counter so the
population/site-total mismatch path is exercised too.
"""

from pathlib import Path

import pytest

from src.sample import (
    build_manifests,
    parse_html,
    select_sample_positions,
    site_reported_total,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_HTML = REPO_ROOT / "tests" / "fixtures" / "listing_snippet.html"
FIXTURE_POPULATION = 12


# --- parsing ---


def test_parse_html_finds_all_records():
    records = parse_html(FIXTURE_HTML.read_text(encoding="utf-8"))
    assert len(records) == FIXTURE_POPULATION


def test_parse_html_extracts_expected_fields_for_first_record():
    records = parse_html(FIXTURE_HTML.read_text(encoding="utf-8"))
    first = records[0]
    assert first == {
        "id": "fixture-uuid-0001",
        "publication_number": "HR02-0000001",
        "date": "07.07.2026",
        "source": "SHAB - Handelsregistereintragungen",
        "title": "Mutation Test Company One AG, Zürich",
        "url": "https://www.shab.ch/#!/search/publications/detail/fixture-uuid-0001",
    }


def test_parse_html_keeps_cantonal_source_variants_distinct():
    records = parse_html(FIXTURE_HTML.read_text(encoding="utf-8"))
    sources = {r["source"] for r in records}
    assert "SHAB, Amtsblatt ZG - Handelsregistereintragungen" in sources
    assert "SHAB, Amtsblatt SZ - Handelsregistereintragungen" in sources


def test_parse_html_does_not_split_title_into_act_type_and_company():
    # Deliberate: the source markup gives no reliable separator between the
    # act type and the company name (see src/sample.py module docstring).
    records = parse_html(FIXTURE_HTML.read_text(encoding="utf-8"))
    titles = {r["title"] for r in records}
    assert "Vorläufige Konkursanzeige Test Company Seven GmbH, Schwyz" in titles


def test_site_reported_total_reads_treffer_counter():
    assert site_reported_total(FIXTURE_HTML.read_text(encoding="utf-8")) == 15


# --- sampling ---


def test_same_seed_produces_same_sample():
    first = select_sample_positions(FIXTURE_POPULATION, n=5, seed=42)
    second = select_sample_positions(FIXTURE_POPULATION, n=5, seed=42)
    assert first == second


def test_different_seeds_produce_different_samples():
    first = select_sample_positions(FIXTURE_POPULATION, n=5, seed=42)
    second = select_sample_positions(FIXTURE_POPULATION, n=5, seed=123)
    assert first != second


def test_sample_size_matches_requested_n():
    positions = select_sample_positions(FIXTURE_POPULATION, n=7, seed=42)
    assert len(positions) == 7


def test_sample_has_no_repeated_positions():
    positions = select_sample_positions(FIXTURE_POPULATION, n=FIXTURE_POPULATION, seed=42)
    assert len(set(positions)) == len(positions)


def test_sample_positions_are_sorted_ascending():
    positions = select_sample_positions(FIXTURE_POPULATION, n=8, seed=7)
    assert positions == sorted(positions)


def test_sample_requesting_full_population_returns_every_position():
    positions = select_sample_positions(FIXTURE_POPULATION, n=FIXTURE_POPULATION, seed=42)
    assert positions == list(range(1, FIXTURE_POPULATION + 1))


def test_sample_size_larger_than_population_raises():
    with pytest.raises(ValueError):
        select_sample_positions(FIXTURE_POPULATION, n=FIXTURE_POPULATION + 1, seed=42)


# --- end-to-end manifest building ---


def test_build_manifests_full_has_one_record_per_position():
    full, _ = build_manifests(FIXTURE_HTML, n=5, seed=42, start_id=4)
    positions = [r["position"] for r in full["records"]]
    assert positions == list(range(1, FIXTURE_POPULATION + 1))


def test_build_manifests_sample_is_sorted_by_position_and_ordered_doc_ids():
    _, sample = build_manifests(FIXTURE_HTML, n=5, seed=42, start_id=4)
    positions = [r["position"] for r in sample["records"]]
    doc_ids = [r["doc_id"] for r in sample["records"]]
    assert positions == sorted(positions)
    assert doc_ids == [f"{4 + i:04d}" for i in range(len(doc_ids))]


def test_build_manifests_metadata_matches_in_both_files():
    full, sample = build_manifests(FIXTURE_HTML, n=5, seed=42, start_id=4)
    assert full["metadata"] == sample["metadata"]


def test_build_manifests_metadata_flags_population_mismatch():
    full, _ = build_manifests(FIXTURE_HTML, n=5, seed=42, start_id=4)
    metadata = full["metadata"]
    assert metadata["source_html"] == "listing_snippet.html"
    assert metadata["population_size"] == FIXTURE_POPULATION
    assert metadata["site_reported_total"] == 15
    assert metadata["sample_size"] == 5
    assert metadata["seed"] == 42
    assert "population_note" in metadata


def test_build_manifests_sample_records_are_full_publication_records():
    _, sample = build_manifests(FIXTURE_HTML, n=3, seed=42, start_id=4)
    for record in sample["records"]:
        assert set(record.keys()) == {
            "position",
            "doc_id",
            "id",
            "publication_number",
            "date",
            "source",
            "title",
            "url",
        }
