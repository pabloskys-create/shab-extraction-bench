"""Tests for src/sample.py against reduced HTML fixtures.

tests/fixtures/listing_snippet.html is a hand-trimmed excerpt of the real
saved page: 12 `list-entry` records with the exact same markup shape, and a
"15 Treffer" counter that does not match them.

The five `listing_<canton>_<date>.html` fixtures are built from those same
12 records and cover the frame checks:
    aa (07.07)  6 records, counter agrees, every
                headline an act type               — valid
    bb (08.07)  5 records, counter agrees, the last
                one repeating aa's HR02-0000001    — valid, exercises dedup
    cc (09.07)  6 records but a counter of 7       — partial capture
    dd (10.07)  6 records, English "Results"       — wrong-language capture
    ee (11.07)  aa plus the snippet's Schuldenruf
                and Vorläufige Konkursanzeige      — rubric filter not applied
"""

from pathlib import Path

import pytest

from src.sample import (
    ACT_TYPE_FIRST_WORDS,
    FrameError,
    apply_exclusions,
    build_manifests,
    build_population,
    check_rubric,
    foreign_rubric_counts,
    load_exclusions,
    load_listing,
    load_listings,
    parse_html,
    parse_listing_filename,
    select_sample_positions,
    site_reported_total,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures"
FIXTURE_HTML = FIXTURES / "listing_snippet.html"
FIXTURE_POPULATION = 12

LISTING_AA = FIXTURES / "listing_aa_2026-07-07.html"
LISTING_BB = FIXTURES / "listing_bb_2026-07-08.html"
LISTING_CC = FIXTURES / "listing_cc_2026-07-09.html"
LISTING_DD = FIXTURES / "listing_dd_2026-07-10.html"
LISTING_EE = FIXTURES / "listing_ee_2026-07-11.html"
EXCLUSIONS = FIXTURES / "annotated_exclusions.json"

# aa (6) + bb (5), minus bb's repeat of HR02-0000001, minus the two records
# named in the exclusions fixture.
UNION_SIZE = 11
ELIGIBLE_SIZE = 8


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


def test_site_reported_total_is_none_for_a_non_german_counter():
    assert site_reported_total(LISTING_DD.read_text(encoding="utf-8")) is None


# --- listing filenames ---


def test_parse_listing_filename_returns_uppercase_canton_and_date():
    assert parse_listing_filename("listing_be_2026-08-10.html") == ("BE", "2026-08-10")


@pytest.mark.parametrize(
    "name",
    [
        "listing_snippet.html",
        "listing_be.html",
        "listing_be_10.08.2026.html",
        "listing_bern_2026-08-10.html",
        "2026-08-10_be.html",
    ],
)
def test_parse_listing_filename_rejects_malformed_names(name):
    with pytest.raises(FrameError):
        parse_listing_filename(name)


# --- per-listing verification ---


def test_load_listing_labels_the_listing_from_its_filename():
    listing = load_listing(LISTING_AA)
    assert listing["canton"] == "AA"
    assert listing["date"] == "2026-07-07"
    assert listing["population"] == 6
    assert listing["site_reported_total"] == 6


def test_load_listing_aborts_when_records_do_not_match_the_counter():
    with pytest.raises(FrameError, match="incomplete"):
        load_listing(LISTING_CC)


def test_load_listing_aborts_on_a_wrong_language_capture():
    with pytest.raises(FrameError, match="Treffer"):
        load_listing(LISTING_DD)


def test_load_listing_aborts_when_the_rubric_filter_was_not_applied():
    with pytest.raises(FrameError, match="Handelsregister rubric filter"):
        load_listing(LISTING_EE)


def test_the_rubric_error_names_every_offending_first_word_with_its_count():
    with pytest.raises(FrameError) as error:
        load_listing(LISTING_EE)
    message = str(error.value)
    assert "2 of 8 headlines" in message
    assert "Schuldenruf (1)" in message
    assert "Vorläufige (1)" in message


def test_foreign_rubric_counts_is_empty_for_a_filtered_listing():
    records = parse_html(LISTING_AA.read_text(encoding="utf-8"))
    assert foreign_rubric_counts(records) == {}


def test_foreign_rubric_counts_orders_by_frequency():
    records = [
        {"title": "Mutation Alpha AG, Bern"},
        {"title": "Vorläufige Konkursanzeige Beta AG, Bern"},
        {"title": "Vorläufige Konkursanzeige Gamma AG, Bern"},
        {"title": "Schuldenruf Delta AG, Bern"},
    ]
    assert list(foreign_rubric_counts(records)) == ["Vorläufige", "Schuldenruf"]


def test_foreign_rubric_counts_accepts_every_act_type():
    records = [{"title": f"{word} Alpha AG, Bern"} for word in ACT_TYPE_FIRST_WORDS]
    assert foreign_rubric_counts(records) == {}


def test_check_rubric_passes_a_clean_listing():
    check_rubric("listing_aa_2026-07-07.html", parse_html(LISTING_AA.read_text(encoding="utf-8")))


def test_load_listings_orders_by_date_and_canton_not_by_argument_order():
    forwards = load_listings([LISTING_AA, LISTING_BB])
    backwards = load_listings([LISTING_BB, LISTING_AA])
    assert [x["source_html"] for x in forwards] == [x["source_html"] for x in backwards]
    assert [x["date"] for x in forwards] == ["2026-07-07", "2026-07-08"]


# --- union, labelling and deduplication ---


def test_build_population_labels_each_record_with_its_origin():
    population, _ = build_population(load_listings([LISTING_AA, LISTING_BB]))
    first_of_bb = next(r for r in population if r["canton"] == "BB")
    assert first_of_bb["source_html"] == "listing_bb_2026-07-08.html"
    assert first_of_bb["listing_date"] == "2026-07-08"
    assert first_of_bb["listing_position"] == 1


def test_build_population_positions_restart_within_each_listing():
    population, _ = build_population(load_listings([LISTING_AA, LISTING_BB]))
    for canton, expected in (("AA", 6), ("BB", 4)):
        positions = [r["listing_position"] for r in population if r["canton"] == canton]
        assert positions == list(range(1, expected + 1))


def test_build_population_drops_repeated_publication_numbers():
    population, duplicates = build_population(load_listings([LISTING_AA, LISTING_BB]))
    assert len(population) == UNION_SIZE - 1
    assert [d["publication_number"] for d in duplicates] == ["HR02-0000001"]
    assert duplicates[0]["duplicate_of"] == "listing_aa_2026-07-07.html"


def test_build_population_keeps_the_first_occurrence_of_a_duplicate():
    population, _ = build_population(load_listings([LISTING_AA, LISTING_BB]))
    kept = next(r for r in population if r["publication_number"] == "HR02-0000001")
    assert kept["canton"] == "AA"


# --- exclusion of already-annotated publications ---


def test_apply_exclusions_removes_only_the_annotated_records():
    population, _ = build_population(load_listings([LISTING_AA, LISTING_BB]))
    remaining, excluded = apply_exclusions(population, load_exclusions(EXCLUSIONS))
    assert {r["publication_number"] for r in excluded} == {"HR02-0000002", "HR02-0000010"}
    assert len(remaining) == len(population) - 2


def test_apply_exclusions_ignores_annotations_absent_from_the_frame():
    # The fixture's doc 0001 has no publication_number: it was annotated
    # before the frame existed and must not change any count.
    population, _ = build_population(load_listings([LISTING_AA, LISTING_BB]))
    _, excluded = apply_exclusions(population, load_exclusions(EXCLUSIONS))
    assert len(excluded) == 2


def test_apply_exclusions_aborts_when_number_and_headline_disagree():
    population, _ = build_population(load_listings([LISTING_AA, LISTING_BB]))
    drifted = [{"doc_id": "0002", "publication_number": "HR02-0000002", "title": "Something else"}]
    with pytest.raises(FrameError, match="disagree"):
        apply_exclusions(population, drifted)


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


def _manifests(n=4, seed=42, start_id=29):
    return build_manifests([LISTING_AA, LISTING_BB], n, seed, start_id, EXCLUSIONS)


def test_build_manifests_full_holds_the_eligible_population_only():
    full, _ = _manifests()
    assert len(full["records"]) == ELIGIBLE_SIZE
    assert [r["position"] for r in full["records"]] == list(range(1, ELIGIBLE_SIZE + 1))
    numbers = {r["publication_number"] for r in full["records"]}
    assert "HR02-0000002" not in numbers
    assert "HR02-0000010" not in numbers


def test_build_manifests_sample_is_drawn_from_the_eligible_population():
    full, sample = _manifests()
    eligible = {r["publication_number"] for r in full["records"]}
    assert {r["publication_number"] for r in sample["records"]} <= eligible


def test_build_manifests_sample_is_sorted_by_position_and_ordered_doc_ids():
    _, sample = _manifests()
    positions = [r["position"] for r in sample["records"]]
    doc_ids = [r["doc_id"] for r in sample["records"]]
    assert positions == sorted(positions)
    assert doc_ids == [f"{29 + i:04d}" for i in range(len(doc_ids))]


def test_build_manifests_metadata_matches_in_both_files():
    full, sample = _manifests()
    assert full["metadata"] == sample["metadata"]


def test_build_manifests_metadata_records_the_source_listing_table():
    full, _ = _manifests()
    assert full["metadata"]["source_listings"] == [
        {
            "source_html": "listing_aa_2026-07-07.html",
            "canton": "AA",
            "date": "2026-07-07",
            "population": 6,
            "site_reported_total": 6,
        },
        {
            "source_html": "listing_bb_2026-07-08.html",
            "canton": "BB",
            "date": "2026-07-08",
            "population": 5,
            "site_reported_total": 5,
        },
    ]


def test_build_manifests_metadata_reconciles_every_count():
    full, _ = _manifests()
    metadata = full["metadata"]
    assert metadata["population_size"] == UNION_SIZE
    assert metadata["duplicates_dropped"] == 1
    assert metadata["excluded_already_annotated"] == 2
    assert metadata["eligible_population_size"] == ELIGIBLE_SIZE
    assert (
        metadata["population_size"]
        - metadata["duplicates_dropped"]
        - metadata["excluded_already_annotated"]
        == metadata["eligible_population_size"]
    )
    assert metadata["sample_size"] == 4
    assert metadata["seed"] == 42


def test_build_manifests_sample_records_carry_their_origin():
    _, sample = _manifests()
    for record in sample["records"]:
        assert set(record.keys()) == {
            "position",
            "doc_id",
            "source_html",
            "canton",
            "listing_date",
            "listing_position",
            "id",
            "publication_number",
            "date",
            "source",
            "title",
            "url",
        }


def test_build_manifests_aborts_on_an_untrustworthy_listing():
    with pytest.raises(FrameError):
        build_manifests([LISTING_AA, LISTING_CC], n=2, seed=42, start_id=29, exclusions_path=EXCLUSIONS)
