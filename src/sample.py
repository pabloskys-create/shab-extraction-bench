"""Sample publications from a saved SHAB search-results HTML page.

Input: an HTML file saved by hand from the browser ("Save As... > Webpage,
Complete") of the shab.ch Handelsregister search results for one day
(German-language filter). This does not fetch anything from the network —
it only reads a local file that was already downloaded manually.

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

`main()` also writes two audit manifests to the same directory as the input
HTML: `manifest_full.json` (every record found, 1-indexed by position) and
`manifest_sample.json` (a seeded `random.sample` subset, sorted by position,
each entry given the `doc_id` it would receive if added to the corpus).
"""

from __future__ import annotations

import argparse
import json
import random
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

DEFAULT_N = 25
DEFAULT_SEED = 42
DEFAULT_START_ID = 4

# The site's own results counter, e.g. "<strong>1169</strong> &nbsp;Treffer".
# Used only to detect and record a partial capture (infinite-scroll page that
# wasn't fully scrolled before saving) — never to fabricate missing records.
TREFFER_RE = re.compile(r"<strong>(\d+)</strong>\s*&nbsp;Treffer")

# "07.07.2026 - HR02-1006700048 - SHAB - Handelsregistereintragungen"
META_RE = re.compile(r"^(\d{2}\.\d{2}\.\d{4}) - (\S+) - (.+)$")


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
    None if not found. Used to flag a partial infinite-scroll capture."""
    match = TREFFER_RE.search(html_text)
    return int(match.group(1)) if match else None


def select_sample_positions(population_size: int, n: int, seed: int) -> list[int]:
    """A seeded simple random sample of n distinct 1-indexed positions out
    of `population_size`, returned sorted ascending."""
    random.seed(seed)
    return sorted(random.sample(range(1, population_size + 1), n))


def build_metadata(
    source_html_name: str,
    population_size: int,
    sample_size: int,
    seed: int,
    site_total: int | None,
) -> dict:
    metadata = {
        "source_html": source_html_name,
        "population_size": population_size,
        "site_reported_total": site_total,
        "sample_size": sample_size,
        "seed": seed,
        "run_date": datetime.now(tz=timezone.utc).date().isoformat(),
    }
    if site_total is not None and site_total != population_size:
        metadata["population_note"] = (
            f"HTML capturado con infinite-scroll incompleto: contiene {population_size} "
            f"de {site_total} publicaciones reportadas por el sitio para este día."
        )
    return metadata


def build_manifests(html_path: Path, n: int, seed: int, start_id: int) -> tuple[dict, dict]:
    """Read `html_path` and build the (full, sample) manifest dicts."""
    html_text = html_path.read_text(encoding="utf-8")
    records = parse_html(html_text)
    metadata = build_metadata(
        html_path.name, len(records), n, seed, site_reported_total(html_text)
    )

    full_manifest = {
        "metadata": metadata,
        "records": [{"position": i + 1, **record} for i, record in enumerate(records)],
    }

    positions = select_sample_positions(len(records), n, seed)
    sample_manifest = {
        "metadata": metadata,
        "records": [
            {"position": position, "doc_id": f"{start_id + offset:04d}", **records[position - 1]}
            for offset, position in enumerate(positions)
        ],
    }

    return full_manifest, sample_manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sample publications from a saved SHAB search-results HTML page."
    )
    parser.add_argument("html_path", type=Path, help="Path to the saved HTML file")
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
    args = parser.parse_args()

    full_manifest, sample_manifest = build_manifests(
        args.html_path, args.n, args.seed, args.start_id
    )
    print(f"{len(full_manifest['records'])} registros encontrados")

    out_dir = args.html_path.parent
    (out_dir / "manifest_full.json").write_text(
        json.dumps(full_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "manifest_sample.json").write_text(
        json.dumps(sample_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
