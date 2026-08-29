"""Deterministic prefill for SHAB gold-standard JSON records.

Extracts only the fields that can be pulled out of the source text with
regex / string parsing — no interpretation, no LLM calls. Every field this
module does not explicitly extract is left `null` (or `[]` for lists), and
the whole record is marked `"_verified": false` so a human annotator knows
it still needs to be checked.

Fields extracted here:
    uid, forma_juridica, tagesregister_nr, tagesregister_fecha,
    publicacion_anterior_shab_nr, publicacion_anterior_fecha,
    publicacion_anterior_publ_id, autoridad, sede_canton (derived from
    autoridad — or, when the body reads "bisher in <Ort>", from the postal
    code in the header's "Bisher" sub-block via a small PLZ-prefix table,
    since the authority then names the post-move canton, not the pre-act
    one SCHEMA.md wants; see BISHER_IN_RE, PLZ_PREFIX_TO_CANTON),
    sede_localidad (from "in <Ort>," right before the UID, or from the
    "bisher in <Ort>" phrase itself on a relocation), tipo_acto,
    empresa_nombre_completo, empresa_nombre_base,
    direccion_co, direccion_calle, direccion_cp, direccion_localidad,
    sufijo_estado, nombres_alternativos, idioma (constant "de" — see
    SCHEMA.md "Scope decisions": this module only ever sees
    German-language notices)

`doc_id` (from the filename) and `schema_version` (the version this module
targets) are also filled in — they are mechanical bookkeeping, not
extracted interpretation, and a record without them isn't usable.

See SCHEMA.md for field definitions. See CLAUDE.md rule 4: no LLM calls
in this file, ever.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

SCHEMA_VERSION = "1.0"

# --- German legal form -> SCHEMA.md `forma_juridica` enum ---
FORM_MAP = {
    "Aktiengesellschaft": "AG",
    "Gesellschaft mit beschränkter Haftung": "GmbH",
    "Einzelunternehmen": "Einzelunternehmen",
    "Genossenschaft": "Genossenschaft",
    "Stiftung": "Stiftung",
    "Verein": "Verein",
    "Kollektivgesellschaft": "Kollektivgesellschaft",
    "Kommanditgesellschaft": "Kommanditgesellschaft",
    "Zweigniederlassung": "Zweigniederlassung",
}

# --- first word of the headline -> SCHEMA.md `tipo_acto` enum ---
TIPO_ACTO_MAP = {
    "mutation": "mutation",
    "neueintragung": "neueintragung",
    "löschung": "loeschung",
}

# --- canton name (as it appears in `Kontaktstelle`) -> 2-letter code ---
CANTON_NAMES = {
    "Aargau": "AG",
    "Appenzell Ausserrhoden": "AR",
    "Appenzell Innerrhoden": "AI",
    "Basel-Landschaft": "BL",
    "Basel-Stadt": "BS",
    "Bern": "BE",
    "Freiburg": "FR",
    "Genf": "GE",
    "Glarus": "GL",
    "Graubünden": "GR",
    "Jura": "JU",
    "Luzern": "LU",
    "Neuenburg": "NE",
    "Nidwalden": "NW",
    "Obwalden": "OW",
    "Schaffhausen": "SH",
    "Schwyz": "SZ",
    "Solothurn": "SO",
    "St. Gallen": "SG",
    "Tessin": "TI",
    "Thurgau": "TG",
    "Uri": "UR",
    "Wallis": "VS",
    "Waadt": "VD",
    "Zug": "ZG",
    "Zürich": "ZH",
}

# Regional office names that don't literally contain the canton name.
# Extend as new `Kontaktstelle` phrasings show up in the corpus.
CANTON_ALIASES = {
    "Oberwallis": "VS",
    "Unterwallis": "VS",
}

UID_RE = re.compile(r"CHE-\d{3}\.\d{3}\.\d{3}")
# The parenthetical after the legal form is "(SHAB Nr. ... vom ..., Publ. ...)"
# when a prior publication exists, or plain "(Neueintragung)" on a first-time
# registration — either can close the sentence. On a Neueintragung the body
# also repeats the postal address between the UID and the legal form (e.g.
# "..., CHE-295.332.571, Heidbühl 475, 3537 Eggiwil, Aktiengesellschaft
# (Neueintragung)"), so the legal form is the *last* comma-separated segment
# before the parenthesis, not the first one after the UID.
FORMA_JURIDICA_RE = re.compile(
    r"CHE-\d{3}\.\d{3}\.\d{3},\s*([^(]+?)\s*\((?:SHAB|Neueintragung)"
)
# The registered seat, e.g. "..., in Aarau, CHE-450.093.916, ..." — this is
# the legal seat, which may differ from the postal address (direccion_*),
# e.g. after a Sitzverlegung the two point at different towns.
SEDE_LOCALIDAD_RE = re.compile(r",\s*in\s+([^,]+),\s*CHE-\d{3}\.\d{3}\.\d{3}")
# "<Firma>, bisher in <Ort>, CHE-..." marks a relocation: the seat named here
# is the one *before* the act, but `Kontaktstelle` always names the
# authority for the seat *after* it (on an intercantonal move, the new
# canton's registry) — see prefill_text, which never trusts autoridad for
# sede_canton when this matches. The captured group IS the pre-act
# sede_localidad, though; sede_canton comes separately from the postal code
# in the header's "Bisher" sub-block (see _bisher_header_cp below).
BISHER_IN_RE = re.compile(r"\bbisher in\s+([^,]+)")

# Swiss postal codes are geographic but not aligned to canton borders (a
# given 3-digit prefix can straddle two cantons), so this table is built
# incrementally against "bisher in <Ort>" cases actually seen in the corpus
# — same approach as CANTON_ALIASES above — rather than guessed for the
# whole country. A wrong canton is worse than none, so an unmapped prefix
# resolves to None (left for the annotator), never a guess.
PLZ_PREFIX_TO_CANTON = {
    "455": "SO",  # Bucheggberg, e.g. 4556 Aeschi (SO) -- data/raw/0021.txt
    "462": "SO",  # Gäu, e.g. 4623 Neuendorf -- data/raw/0018.txt
}
TAGESREGISTER_RE = re.compile(r"Tagesregister-Nr\.\s*(\S+)\s*vom\s*(\d{2}\.\d{2}\.\d{4})")
PRIOR_PUB_RE = re.compile(
    r"Vorangehende Publikation im SHAB:\s*Nr\.\s*(\d+),\s*Datum:\s*(\d{2}\.\d{2}\.\d{4})"
)
PUBL_ID_RE = re.compile(r"Publ\.\s*(\d+)\)")
KONTAKTSTELLE_RE = re.compile(r"Kontaktstelle:\s*(.+)")
STATE_SUFFIX_RE = re.compile(r"in Liquidation|in Liq\.")

ALT_NAME_LINE_RE = re.compile(r"^(?:\([^()]+\)\s*)+$")
# Same alt-name groups as ALT_NAME_LINE_RE, but trailing on the name line
# itself instead of getting their own line (e.g. "Foo GmbH (Foo Sàrl) (Foo
# Sagl)" all on one line — see data/raw/0016.txt).
TRAILING_ALT_NAMES_RE = re.compile(r"\s*(?:\([^()]+\)\s*)+$")
ALT_NAME_GROUP_RE = re.compile(r"\(([^()]+)\)")
CO_LINE_RE = re.compile(r"^c/o\s+(.+)$", re.IGNORECASE)
PLZ_ORT_RE = re.compile(r"^(\d{4})\s+(.+)$")
STREET_RE = re.compile(r"^.+\s\d+[A-Za-z]?$")


def _to_iso(date_str: str) -> str:
    """Convert `DD.MM.YYYY` (source format) to ISO `YYYY-MM-DD`."""
    day, month, year = date_str.split(".")
    return f"{year}-{month}-{day}"


def _blocks(text: str) -> list[list[str]]:
    """Split text into blocks of consecutive non-blank lines."""
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.strip() == "":
            if current:
                blocks.append(current)
                current = []
        else:
            current.append(line)
    if current:
        blocks.append(current)
    return blocks


def _derive_canton(autoridad: str | None) -> str | None:
    if not autoridad:
        return None
    for alias, code in CANTON_ALIASES.items():
        if alias in autoridad:
            return code
    for name, code in CANTON_NAMES.items():
        if name in autoridad:
            return code
    return None


def _bisher_header_cp(lines: list[str]) -> str | None:
    """Return the postal code from the header's "Bisher" sub-block, if any.

    On a relocation the header repeats the pre-act address after a literal
    "Bisher" line (street, then "<CP> <Ort>"). `_parse_header_block` stops
    parsing there because that block is the *old* address, not
    `direccion_*` — this reads only the piece prefill_text needs from it:
    the postal code, to derive the pre-act sede_canton (see
    PLZ_PREFIX_TO_CANTON) on a "bisher in <Ort>" relocation.
    """
    try:
        bisher_index = next(i for i, line in enumerate(lines) if line.strip().lower() == "bisher")
    except StopIteration:
        return None
    for line in lines[bisher_index + 1 :]:
        match = PLZ_ORT_RE.match(line.strip())
        if match:
            return match.group(1)
    return None


def _canton_from_plz(plz: str | None) -> str | None:
    """Look up a canton from a postal code's prefix.

    Only the 3-digit prefixes in PLZ_PREFIX_TO_CANTON are trusted; anything
    else returns None rather than guess at a boundary this module hasn't
    verified.
    """
    if not plz or len(plz) < 3:
        return None
    return PLZ_PREFIX_TO_CANTON.get(plz[:3])


def _parse_header_block(lines: list[str]) -> dict:
    """Parse the headline block: tipo_acto, sufijo_estado, alt names, address.

    `lines[0]` is the portal headline (e.g. "Mutation Foo AG, Basel") — it is
    portal chrome per SCHEMA.md and only its first word (the act type) is used.
    Parsing of the address stops at a "Bisher" line: everything after it is
    the *previous* address (`domicilio_anterior` territory), which this
    module does not fill in.
    """
    result = {
        "tipo_acto": None,
        "sufijo_estado": None,
        "nombres_alternativos": [],
        "empresa_nombre_completo": None,
        "empresa_nombre_base": None,
        "direccion_co": None,
        "direccion_calle": None,
        "direccion_cp": None,
        "direccion_localidad": None,
    }

    if not lines:
        return result

    first_word = re.match(r"^(\S+)", lines[0])
    if first_word:
        result["tipo_acto"] = TIPO_ACTO_MAP.get(first_word.group(1).lower())

    header_text = "\n".join(lines)
    state_match = STATE_SUFFIX_RE.search(header_text)
    if state_match:
        result["sufijo_estado"] = state_match.group(0)

    body_lines = lines[1:]

    # `body_lines[0]`, if present, is the portal's repeated full company name
    # (every sampled document repeats it right after the headline). This is
    # the one header line this module locates positionally rather than by
    # pattern, so guard against it actually being an address/alt-name line in
    # case some document skips the repeat.
    if body_lines:
        candidate = body_lines[0].strip()
        looks_like_something_else = (
            CO_LINE_RE.match(candidate)
            or PLZ_ORT_RE.match(candidate)
            or ALT_NAME_LINE_RE.fullmatch(candidate)
            or STREET_RE.match(candidate)
        )
        if candidate and not looks_like_something_else:
            # Alternative-language names sometimes trail on this same line as
            # "(...)" groups instead of getting their own line below.
            trailing_match = TRAILING_ALT_NAMES_RE.search(candidate)
            if trailing_match:
                result["nombres_alternativos"].extend(
                    ALT_NAME_GROUP_RE.findall(trailing_match.group(0))
                )
                candidate = candidate[: trailing_match.start()].rstrip()
            result["empresa_nombre_completo"] = candidate
            result["empresa_nombre_base"] = STATE_SUFFIX_RE.sub("", candidate).strip()
            body_lines = body_lines[1:]

    for line in body_lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.lower() == "bisher":
            break
        if ALT_NAME_LINE_RE.fullmatch(stripped):
            result["nombres_alternativos"].extend(ALT_NAME_GROUP_RE.findall(stripped))
            continue
        co_match = CO_LINE_RE.match(stripped)
        if co_match:
            result["direccion_co"] = co_match.group(1).strip()
            continue
        plz_match = PLZ_ORT_RE.match(stripped)
        if plz_match:
            result["direccion_cp"] = plz_match.group(1)
            result["direccion_localidad"] = plz_match.group(2).strip()
            continue
        if result["direccion_calle"] is None and STREET_RE.match(stripped):
            result["direccion_calle"] = stripped
            continue
        # Anything else here is an unhandled line — not extracted.

    return result


def _empty_record() -> dict:
    """The full SCHEMA.md v1.0 shape, every field null/empty/false.

    `empresa_nombre_nuevo` / `empresa_nombre_anterior` are always null here:
    filling them requires reading a `Firma neu:` change against the rest of
    the notice, which is judgement, not regex extraction (see CLAUDE.md
    rule 4). Same reasoning as `personas_mutantes` below staying `[]` —
    prefill.py never emits a `Person` or `PersonChange` object; it isn't
    that their v1.0 shape (with `uid`/`stammanteile`, and PersonChange's
    fourteen `_nuevo`/`_anterior` keys) is unsupported, there's simply
    nothing here that constructs one.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "doc_id": None,
        "idioma": None,
        "tipo_acto": None,
        "subtipos": [],
        "empresa_nombre_completo": None,
        "empresa_nombre_base": None,
        "sufijo_estado": None,
        "nombres_alternativos": [],
        "empresa_nombre_nuevo": None,
        "empresa_nombre_anterior": None,
        "uid": None,
        "forma_juridica": None,
        "sede_localidad": None,
        "sede_canton": None,
        "direccion_co": None,
        "direccion_calle": None,
        "direccion_cp": None,
        "direccion_localidad": None,
        "fecha_acto": None,
        "tagesregister_nr": None,
        "tagesregister_fecha": None,
        "publicacion_anterior_shab_nr": None,
        "publicacion_anterior_fecha": None,
        "publicacion_anterior_publ_id": None,
        "autoridad": None,
        "canton_anterior": None,
        "canton_nuevo": None,
        "capital_nuevo_chf": None,
        "capital_anterior_chf": None,
        "domicilio_nuevo": None,
        "domicilio_anterior": None,
        "personas_entrantes": [],
        "personas_salientes": [],
        "personas_mutantes": [],
        "extras": {},
        "incierto": [],
        "notas": None,
        "_verified": False,
    }


def prefill_text(text: str, doc_id: str | None = None) -> dict:
    """Build a prefilled record from raw SHAB notice text.

    Only the fields listed in the module docstring are populated; everything
    else is left at its schema default. `_verified` is always `False`.
    """
    record = _empty_record()
    record["doc_id"] = doc_id
    # Corpus-level constant, not a per-document extraction: see SCHEMA.md
    # "Scope decisions" — this module only ever runs on German notices.
    record["idioma"] = "de"

    blocks = _blocks(text)
    header = blocks[0] if blocks else []
    record.update(_parse_header_block(header))

    uid_match = UID_RE.search(text)
    if uid_match:
        record["uid"] = uid_match.group(0)

    sede_localidad_match = SEDE_LOCALIDAD_RE.search(text)
    if sede_localidad_match:
        record["sede_localidad"] = sede_localidad_match.group(1).strip()

    forma_match = FORMA_JURIDICA_RE.search(text)
    if forma_match:
        # On a Neueintragung the captured group also contains the repeated
        # address (see FORMA_JURIDICA_RE comment); the legal form is always
        # the last comma-separated segment, address or not.
        last_segment = forma_match.group(1).split(",")[-1].strip()
        record["forma_juridica"] = FORM_MAP.get(last_segment)

    tagesregister_match = TAGESREGISTER_RE.search(text)
    if tagesregister_match:
        record["tagesregister_nr"] = tagesregister_match.group(1)
        record["tagesregister_fecha"] = _to_iso(tagesregister_match.group(2))

    prior_pub_match = PRIOR_PUB_RE.search(text)
    if prior_pub_match:
        record["publicacion_anterior_shab_nr"] = int(prior_pub_match.group(1))
        record["publicacion_anterior_fecha"] = _to_iso(prior_pub_match.group(2))

    publ_id_match = PUBL_ID_RE.search(text)
    if publ_id_match:
        record["publicacion_anterior_publ_id"] = publ_id_match.group(1)

    kontaktstelle_match = KONTAKTSTELLE_RE.search(text)
    if kontaktstelle_match:
        autoridad = kontaktstelle_match.group(1).strip()
        record["autoridad"] = autoridad
        record["sede_canton"] = _derive_canton(autoridad)

    # "bisher in <Ort>" means the Kontaktstelle authority (and any locality
    # parsed near the UID) reflects the post-move seat, not the pre-act one
    # SCHEMA.md wants — see BISHER_IN_RE above. The pre-act seat is
    # recoverable from elsewhere in the document instead: <Ort> itself for
    # sede_localidad, and the postal code in the header's "Bisher" block
    # (via PLZ_PREFIX_TO_CANTON) for sede_canton.
    bisher_in_match = BISHER_IN_RE.search(text)
    if bisher_in_match:
        record["sede_localidad"] = bisher_in_match.group(1).strip()
        record["sede_canton"] = _canton_from_plz(_bisher_header_cp(header))

    return record


def prefill_file(path: str | Path) -> dict:
    """Read a raw SHAB `.txt` file (UTF-8) and prefill a record from it."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    return prefill_text(text, doc_id=path.stem)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deterministic (regex-only) prefill of a SHAB gold-standard record."
    )
    parser.add_argument("raw_path", type=Path, help="Path to a data/raw/NNNN.txt file")
    parser.add_argument(
        "-o", "--output", type=Path, default=None, help="Write JSON here instead of stdout"
    )
    args = parser.parse_args()

    record = prefill_file(args.raw_path)
    output_json = json.dumps(record, ensure_ascii=False, indent=2)

    if args.output:
        args.output.write_text(output_json + "\n", encoding="utf-8")
    else:
        print(output_json)


if __name__ == "__main__":
    main()
