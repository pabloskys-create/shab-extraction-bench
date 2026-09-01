"""Sample publications from saved SHAB search-results HTML pages.

Input: one or more HTML files saved by hand from the browser ("Save As... >
Webpage, Complete") of the shab.ch Handelsregister search results, one file
per (canton, publication date), with the site UI in German. This does not
fetch anything from the network — it only reads local files that were
already downloaded manually.

The page is an AngularJS single-page app whose DOM is fully interpolated in
the saved HTML (no JavaScript needs to run to see the data). Each
publication is one:

    <div class="list-entry list-entry-tenant" id="<uuid>" ...>

Fields extracted per publication (validated by hand against
data/sampling/listing_2026-07-07.html — 1050/1050 entries matched with no
exceptions; see the conversation this script was written in for the
inspection notes):
    id                  the internal UUID (div id, matches the URL)
    publication_number  Meldungsnummer, e.g. "HR02-1006700048"
    date                DD.MM.YYYY as it appears in the source
    source              gazette edition label, e.g. "SHAB - Handelsregister-
                         eintragungen" or "SHAB, Amtsblatt ZG - ..."
    title               full link text (headline). This mixes the act type
                         and the company name in free text with no reliable
                         separator (e.g. "Vorläufige Konkursanzeige ..." vs
                         "Mutation ..." — the first token is not always the
                         whole type). Splitting it further would mean
                         guessing, so it is kept raw and whole.
    url                 full https://www.shab.ch/... detail link

The population is the *union* of the listings, each record labelled with the
canton and date of the listing it came from and its position within that
listing. Building it is deliberately unforgiving — an unnoticed gap in the
frame silently biases every number the benchmark reports — so it aborts
rather than continuing when:

  * a filename is not `listing_<canton>_<YYYY-MM-DD>.html`;
  * the site's own "<n> Treffer" counter is missing. That counter is only
    rendered in German; a page saved with the UI in another language also
    has its titles and rubrics translated, which breaks act-type extraction
    downstream, so a missing counter is treated as a wrong-language capture;
  * the number of parsed records differs from that counter (an
    infinite-scroll page that was not fully scrolled before saving).

Publications already annotated in data/exploratory/ are then removed, so no
document is annotated twice. The listing markup carries neither the UID nor
the Tagesregister number, so the exclusion key is `publication_number`, read
from data/sampling/annotated_exclusions.json; the headline recorded there is
matched independently as a cross-check and a disagreement is fatal.

`main()` writes two audit manifests next to the first input HTML:
`manifest_full.json` (every eligible record, 1-indexed by position in the
union) and `manifest_sample.json` (a seeded `random.sample` subset, sorted
by position, each entry given the `doc_id` it would receive if added to the
corpus). Both carry the same metadata block, including the table of source
listings the population was built from.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

DEFAULT_N = 25
DEFAULT_SEED = 42
DEFAULT_START_ID = 29
DEFAULT_EXCLUSIONS = (
    Path(__file__).resolve().parents[1] / "data" / "sampling" / "annotated_exclusions.json"
)

# The site's own results counter, e.g. "<strong>1169</strong> &nbsp;Treffer".
# Deliberately German-only: see the module docstring on wrong-language
# captures. Do not relax this to also accept "Results" — the surrounding
# markup contains "publication-search-results" in every language, so a loose
# match would accept exactly the pages this check exists to reject.
TREFFER_RE = re.compile(r"<strong>(\d+)</strong>\s*&nbsp;Treffer")

# "07.07.2026 - HR02-1006700048 - SHAB - Handelsregistereintragungen"
META_RE = re.compile(r"^(\d{2}\.\d{2}\.\d{4}) - (\S+) - (.+)$")

# "listing_be_2026-08-10.html"
FILENAME_RE = re.compile(r"^listing_([a-z]{2})_(\d{4}-\d{2}-\d{2})\.html$")


class FrameError(Exception):
    """The sampling frame cannot be built as specified.

    Always fatal: a listing that is incomplete, mislabelled or in the wrong
    language leaves the population unknown, and a sample from an unknown
    population is not a sample.
    """


class _PublicationListParser(HTMLParser):
    """Extracts one raw record per `<div class="list-entry list-entry-tenant">`.

    Do not adapt these selectors to a different page shape without
    re-inspecting it (see CLAUDE.md: never guess selectors).
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.records: list[dict] = []
        self._current: dict | None = None
        self._capture_meta = False
        self._capture_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag == "div" and attrs_dict.get("class") == "list-entry list-entry-tenant":
            self._finish_current()
            self._current = {
                "id": attrs_dict.get("id"),
                "_meta": "",
                "title": "",
                "url": None,
                "publication_number": None,
            }
            return

        if self._current is None:
            return

        if tag == "div" and attrs_dict.get("class") == "list-col" and not self._current["_meta"]:
            self._capture_meta = True
            return

        href = attrs_dict.get("href")
        if tag == "a" and href and "/search/publications/detail/" in href and self._current["url"] is None:
            self._current["url"] = href
            self._capture_title = True
            return

        if tag == "favorite-publication" and attrs_dict.get("publication-number"):
            self._current["publication_number"] = attrs_dict["publication-number"]

    def handle_endtag(self, tag: str) -> None:
        if tag == "div":
            self._capture_meta = False
        elif tag == "a":
            self._capture_title = False

    def handle_data(self, data: str) -> None:
        if self._capture_meta:
            self._current["_meta"] += data
        if self._capture_title:
            self._current["title"] += data

    def close(self) -> None:
        self._finish_current()
        super().close()

    def _finish_current(self) -> None:
        if self._current is not None:
            self.records.append(self._current)
        self._current = None


def parse_html(html_text: str) -> list[dict]:
    """Parse a saved SHAB search-results page into a list of records, in
    the order they appear in the document."""
    parser = _PublicationListParser()
    parser.feed(html_text)
    parser.close()

    records = []
    for raw in parser.records:
        meta_match = META_RE.match(raw["_meta"].strip())
        records.append(
            {
                "id": raw["id"],
                "publication_number": raw["publication_number"]
                or (meta_match.group(2) if meta_match else None),
                "date": meta_match.group(1) if meta_match else None,
                "source": meta_match.group(3).strip() if meta_match else None,
                "title": raw["title"].strip() or None,
                "url": raw["url"],
            }
        )
    return records


def site_reported_total(html_text: str) -> int | None:
    """The result count the site itself displays (e.g. "1169 Treffer"), or
    None if not found — which also means the page was not saved with the UI
    in German. See TREFFER_RE."""
    match = TREFFER_RE.search(html_text)
    return int(match.group(1)) if match else None


def parse_listing_filename(name: str) -> tuple[str, str]:
    """(canton, date) from `listing_<canton>_<YYYY-MM-DD>.html`.

    Neither is recoverable from the page itself — the saved HTML records
    neither the search filter that produced it nor a canton on every record
    — so the filename is the only carrier and has to be well-formed.
    """
    match = FILENAME_RE.match(name)
    if match is None:
        raise FrameError(
            f"{name}: filename must be listing_<canton>_<YYYY-MM-DD>.html (the canton "
            "and date of a listing are not recoverable from the page itself)"
        )
    return match.group(1).upper(), match.group(2)


def load_listing(html_path: Path) -> dict:
    """Parse one listing and verify it against its own "Treffer" counter.

    Returns {source_html, canton, date, population, site_reported_total,
    records}; raises FrameError if the capture cannot be trusted.
    """
    canton, listing_date = parse_listing_filename(html_path.name)
    html_text = html_path.read_text(encoding="utf-8")
    records = parse_html(html_text)
    total = site_reported_total(html_text)

    if total is None:
        raise FrameError(
            f"{html_path.name}: no '<n> Treffer' counter found. Either the counter is "
            "missing or the page was saved with the site UI in another language, which "
            "also translates the titles and rubrics. Re-save it with the UI in German."
        )
    if total != len(records):
        raise FrameError(
            f"{html_path.name}: parsed {len(records)} records but the site reports {total} "
            "Treffer. The capture is incomplete (infinite scroll not fully scrolled); an "
            "incomplete listing invalidates the frame. Re-save it fully scrolled."
        )

    return {
        "source_html": html_path.name,
        "canton": canton,
        "date": listing_date,
        "population": len(records),
        "site_reported_total": total,
        "records": records,
    }


def build_population(listings: list[dict]) -> tuple[list[dict], list[dict]]:
    """Flatten the listings into one population, labelling each record with
    its origin, and drop records repeating a `publication_number` already
    seen. Returns (population, duplicates_dropped)."""
    population: list[dict] = []
    duplicates: list[dict] = []
    seen: dict[str, dict] = {}

    for listing in listings:
        for offset, record in enumerate(listing["records"]):
            labelled = {
                "source_html": listing["source_html"],
                "canton": listing["canton"],
                "listing_date": listing["date"],
                "listing_position": offset + 1,
                **record,
            }
            first = seen.get(record["publication_number"])
            if first is not None:
                duplicates.append({**labelled, "duplicate_of": first["source_html"]})
                continue
            seen[record["publication_number"]] = labelled
            population.append(labelled)

    return population, duplicates


def load_exclusions(path: Path) -> list[dict]:
    """The already-annotated publications, from annotated_exclusions.json."""
    return json.loads(path.read_text(encoding="utf-8"))["records"]


def _normalize_title(title: str | None) -> str:
    return " ".join((title or "").split())


def apply_exclusions(
    population: list[dict], exclusions: list[dict]
) -> tuple[list[dict], list[dict]]:
    """Remove the already-annotated publications from the population.

    The key is `publication_number`. The headline recorded alongside it is
    matched independently and the two must agree exactly: a disagreement
    means the exclusion list has drifted from the listings, and continuing
    would either re-annotate a document or silently drop a different one.
    """
    excluded_numbers = {e["publication_number"] for e in exclusions if e["publication_number"]}
    excluded_titles = {_normalize_title(e["title"]) for e in exclusions}

    by_number = {r["publication_number"] for r in population if r["publication_number"] in excluded_numbers}
    by_title = {
        r["publication_number"] for r in population if _normalize_title(r["title"]) in excluded_titles
    }

    if by_number != by_title:
        raise FrameError(
            "the two exclusion keys disagree: publication_number matched "
            f"{sorted(by_number - by_title)} where the headline did not, and the headline "
            f"matched {sorted(by_title - by_number)} where publication_number did not. "
            "Reconcile annotated_exclusions.json with data/exploratory/ before sampling."
        )

    remaining = [r for r in population if r["publication_number"] not in excluded_numbers]
    excluded = [r for r in population if r["publication_number"] in excluded_numbers]
    return remaining, excluded


def select_sample_positions(population_size: int, n: int, seed: int) -> list[int]:
    """A seeded simple random sample of n distinct 1-indexed positions out
    of `population_size`, returned sorted ascending."""
    random.seed(seed)
    return sorted(random.sample(range(1, population_size + 1), n))


def build_metadata(
    listings: list[dict],
    population_size: int,
    duplicates_dropped: int,
    excluded_already_annotated: int,
    eligible_size: int,
    sample_size: int,
    seed: int,
) -> dict:
    return {
        "source_listings": [
            {
                "source_html": listing["source_html"],
                "canton": listing["canton"],
                "date": listing["date"],
                "population": listing["population"],
                "site_reported_total": listing["site_reported_total"],
            }
            for listing in listings
        ],
        "population_size": population_size,
        "duplicates_dropped": duplicates_dropped,
        "excluded_already_annotated": excluded_already_annotated,
        "eligible_population_size": eligible_size,
        "sample_size": sample_size,
        "seed": seed,
        "run_date": datetime.now(tz=timezone.utc).date().isoformat(),
    }


def load_listings(html_paths: list[Path]) -> list[dict]:
    """Every listing, ordered by (date, canton) rather than by the order
    given on the command line, so that the sample depends only on the set of
    listings and the seed."""
    return sorted(
        (load_listing(path) for path in html_paths),
        key=lambda listing: (listing["date"], listing["canton"]),
    )


def build_manifests(
    html_paths: list[Path],
    n: int,
    seed: int,
    start_id: int,
    exclusions_path: Path = DEFAULT_EXCLUSIONS,
) -> tuple[dict, dict]:
    """Read every listing in `html_paths` and build the (full, sample)
    manifest dicts over their union."""
    listings = load_listings(html_paths)
    population, duplicates = build_population(listings)
    eligible, excluded = apply_exclusions(population, load_exclusions(exclusions_path))

    metadata = build_metadata(
        listings=listings,
        population_size=len(population) + len(duplicates),
        duplicates_dropped=len(duplicates),
        excluded_already_annotated=len(excluded),
        eligible_size=len(eligible),
        sample_size=n,
        seed=seed,
    )

    full_manifest = {
        "metadata": metadata,
        "records": [{"position": i + 1, **record} for i, record in enumerate(eligible)],
    }

    positions = select_sample_positions(len(eligible), n, seed)
    sample_manifest = {
        "metadata": metadata,
        "records": [
            {"position": position, "doc_id": f"{start_id + offset:04d}", **eligible[position - 1]}
            for offset, position in enumerate(positions)
        ],
    }

    return full_manifest, sample_manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sample publications from saved SHAB search-results HTML pages."
    )
    parser.add_argument(
        "html_paths",
        type=Path,
        nargs="+",
        help="Paths to the saved HTML files, named listing_<canton>_<YYYY-MM-DD>.html",
    )
    parser.add_argument(
        "--n", type=int, default=DEFAULT_N, help=f"Sample size (default: {DEFAULT_N})"
    )
    parser.add_argument(
        "--seed", type=int, default=DEFAULT_SEED, help=f"Random seed (default: {DEFAULT_SEED})"
    )
    parser.add_argument(
        "--start-id",
        type=int,
        default=DEFAULT_START_ID,
        help=f"First doc_id to assign to the sample, in position order (default: {DEFAULT_START_ID:04d})",
    )
    parser.add_argument(
        "--exclusions",
        type=Path,
        default=DEFAULT_EXCLUSIONS,
        help="JSON listing the already-annotated publications to exclude",
    )
    args = parser.parse_args()

    try:
        listings = load_listings(args.html_paths)
    except FrameError as error:
        sys.exit(f"error: {error}")

    for listing in listings:
        print(
            f"{listing['source_html']}: {listing['population']} records "
            f"({listing['canton']}, {listing['date']}) — matches the site's "
            f"{listing['site_reported_total']} Treffer"
        )

    try:
        full_manifest, sample_manifest = build_manifests(
            args.html_paths, args.n, args.seed, args.start_id, args.exclusions
        )
    except (FrameError, ValueError) as error:
        sys.exit(f"error: {error}")

    metadata = full_manifest["metadata"]
    print(f"population (union of {len(listings)} listings): {metadata['population_size']}")
    print(f"duplicates dropped (same publication_number): {metadata['duplicates_dropped']}")
    print(f"excluded as already annotated: {metadata['excluded_already_annotated']}")
    print(f"eligible population: {metadata['eligible_population_size']}")
    print(f"sampled: {metadata['sample_size']} (seed {metadata['seed']})")

    out_dir = args.html_paths[0].parent
    (out_dir / "manifest_full.json").write_text(
        json.dumps(full_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "manifest_sample.json").write_text(
        json.dumps(sample_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
