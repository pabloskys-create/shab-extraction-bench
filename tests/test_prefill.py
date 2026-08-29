"""Tests for src/prefill.py against the German-language documents in
data/raw/ (0001–0003, 0012–0016). 0000.txt is a French FOSC notice and is
out of scope per SCHEMA.md ("Scope decisions" — German only), so it is not
used here.

0012–0016 are regression cases for a set of bugs specific to
`Neueintragung` (first-time registration) notices and to names containing
parentheses:

- `forma_juridica` regexes anchored on the "(SHAB Nr. ... )" parenthetical,
  which only exists when there is a prior publication. A Neueintragung
  closes with "(Neueintragung)" instead.
- On a Neueintragung the body repeats the postal address between the UID
  and the legal form, so the legal form is the *last* comma-separated
  segment before the parenthesis, not the first one after the UID.
- `nombres_alternativos` was only extracted when the "(...)" groups sat on
  their own line; 0016 has them trailing on the same line as the name.
"""

import re
from pathlib import Path

import pytest

from src.prefill import _canton_from_plz, _derive_canton, _to_iso, prefill_file, prefill_text

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_RAW = REPO_ROOT / "data" / "raw"


def _schema_core_field_names() -> set[str]:
    """Field names from the "Core fields" table in SCHEMA.md.

    Parses the markdown table directly so this test tracks the schema
    document itself, rather than a second hand-copied list that could
    drift out of sync with it.
    """
    schema_text = (REPO_ROOT / "SCHEMA.md").read_text(encoding="utf-8")
    core_table = schema_text.split("## Core fields")[1].split("\n## ")[0]
    return set(re.findall(r"^\|\s*`([a-zA-Z_]+)`\s*\|", core_table, re.MULTILINE))


# --- record shape must match SCHEMA.md exactly ---


@pytest.mark.parametrize(
    "doc_id", ["0001", "0002", "0003", "0012", "0013", "0014", "0015", "0016"]
)
def test_record_keys_match_schema_exactly(doc_id):
    record = prefill_file(DATA_RAW / f"{doc_id}.txt")
    expected = _schema_core_field_names()
    assert set(record.keys()) == expected


# --- small pure-function unit tests ---


def test_to_iso_converts_swiss_date_to_iso():
    assert _to_iso("18.08.2026") == "2026-08-18"


@pytest.mark.parametrize(
    "autoridad, expected",
    [
        ("Handelsregisteramt Oberwallis", "VS"),
        ("Handelsregisteramt des Kantons Aargau", "AG"),
        ("Handelsregisteramt des Kantons Basel-Stadt", "BS"),
        (None, None),
        ("Handelsregisteramt eines unbekannten Ortes", None),
    ],
)
def test_derive_canton(autoridad, expected):
    assert _derive_canton(autoridad) == expected


# --- _verified / defaults, on any input ---


def test_unrequested_fields_stay_null_and_record_is_unverified():
    record = prefill_text("Mutation Foo AG, Bern\nFoo AG\n", doc_id="9999")
    assert record["_verified"] is False
    assert record["fecha_acto"] is None
    assert record["capital_nuevo_chf"] is None
    assert record["personas_entrantes"] == []
    assert record["extras"] == {}
    assert record["schema_version"] == "0.2"
    assert record["doc_id"] == "9999"


# --- 0001.txt: Zermatt Kollektiv AG in Liquidation ---


def test_0001_zermatt_kollektiv():
    record = prefill_file(DATA_RAW / "0001.txt")

    assert record["doc_id"] == "0001"
    assert record["idioma"] == "de"
    assert record["uid"] == "CHE-390.336.674"
    assert record["forma_juridica"] == "AG"
    assert record["sede_localidad"] == "Zermatt"
    assert record["tipo_acto"] == "mutation"
    assert record["sufijo_estado"] == "in Liquidation"
    assert record["nombres_alternativos"] == []

    # completo keeps the status suffix, base drops it
    assert record["empresa_nombre_completo"] == "The Zermatt Kollektiv AG in Liquidation"
    assert record["empresa_nombre_base"] == "The Zermatt Kollektiv AG"

    assert record["direccion_co"] is None
    assert record["direccion_calle"] == "Spissstrasse 67"
    assert record["direccion_cp"] == "3920"
    assert record["direccion_localidad"] == "Zermatt"

    assert record["tagesregister_nr"] == "1526"
    assert record["tagesregister_fecha"] == "2026-08-18"

    assert record["publicacion_anterior_shab_nr"] == 223
    assert record["publicacion_anterior_fecha"] == "2025-11-18"
    assert record["publicacion_anterior_publ_id"] == "1006488017"

    assert record["autoridad"] == "Handelsregisteramt Oberwallis"
    assert record["sede_canton"] == "VS"

    assert record["_verified"] is False


# --- 0002.txt: denkarbeit GmbH, intercantonal move, has "Bisher" ---


def test_0002_denkarbeit_gmbh():
    record = prefill_file(DATA_RAW / "0002.txt")

    assert record["doc_id"] == "0002"
    assert record["idioma"] == "de"
    assert record["uid"] == "CHE-450.093.916"
    assert record["forma_juridica"] == "GmbH"
    # legal seat (Aarau), not the postal locality after the move (Speicher)
    assert record["sede_localidad"] == "Aarau"
    assert record["tipo_acto"] == "mutation"
    assert record["sufijo_estado"] is None
    assert record["nombres_alternativos"] == []

    assert record["empresa_nombre_completo"] == "denkarbeit GmbH"
    assert record["empresa_nombre_base"] == "denkarbeit GmbH"

    # current (new) address only — "Bisher" block must not leak in.
    assert record["direccion_co"] == "Roland Hunziker"
    assert record["direccion_calle"] == "Hinterwies 31"
    assert record["direccion_cp"] == "9042"
    assert record["direccion_localidad"] == "Speicher"

    assert record["tagesregister_nr"] == "11765"
    assert record["tagesregister_fecha"] == "2026-08-18"

    assert record["publicacion_anterior_shab_nr"] == 167
    assert record["publicacion_anterior_fecha"] == "2024-08-29"
    assert record["publicacion_anterior_publ_id"] == "1006117375"

    assert record["autoridad"] == "Handelsregisteramt des Kantons Aargau"
    assert record["sede_canton"] == "AG"

    # fields this module never fills in, even though "Bisher" implies them
    assert record["domicilio_nuevo"] is None
    assert record["domicilio_anterior"] is None
    assert record["canton_anterior"] is None
    assert record["canton_nuevo"] is None


# --- 0003.txt: Noorik Biopharmaceuticals AG, has alt names ---


def test_0003_noorik_biopharmaceuticals():
    record = prefill_file(DATA_RAW / "0003.txt")

    assert record["doc_id"] == "0003"
    assert record["idioma"] == "de"
    assert record["uid"] == "CHE-115.986.883"
    assert record["forma_juridica"] == "AG"
    assert record["sede_localidad"] == "Basel"
    assert record["tipo_acto"] == "mutation"
    assert record["sufijo_estado"] is None
    assert record["nombres_alternativos"] == [
        "Noorik Biopharmaceuticals SA",
        "Noorik Biopharmaceuticals Ltd",
    ]

    # alt names have their own line below the name — must not end up glued
    # onto empresa_nombre_completo.
    assert record["empresa_nombre_completo"] == "Noorik Biopharmaceuticals AG"
    assert record["empresa_nombre_base"] == "Noorik Biopharmaceuticals AG"

    assert record["direccion_co"] is None
    assert record["direccion_calle"] == "Lange Gasse 15"
    assert record["direccion_cp"] == "4052"
    assert record["direccion_localidad"] == "Basel"

    assert record["tagesregister_nr"] == "5762"
    assert record["tagesregister_fecha"] == "2026-08-18"

    assert record["publicacion_anterior_shab_nr"] == 129
    assert record["publicacion_anterior_fecha"] == "2024-07-05"
    assert record["publicacion_anterior_publ_id"] == "1006077090"

    assert record["autoridad"] == "Handelsregisteramt des Kantons Basel-Stadt"
    assert record["sede_canton"] == "BS"

    # not deterministically extracted here, must stay null despite being
    # present in the source text (capital change, incoming person, ...)
    assert record["capital_nuevo_chf"] is None
    assert record["capital_anterior_chf"] is None
    assert record["fecha_acto"] is None
    assert record["personas_entrantes"] == []


def test_reads_source_files_as_utf8(tmp_path):
    # Non-ASCII content (e.g. "Gesellschaft mit beschränkter Haftung") must
    # round-trip correctly — this would mojibake under the wrong encoding.
    sample = tmp_path / "0042.txt"
    sample.write_text(
        "Mutation Müller Käseladen GmbH, Zürich\n"
        "Müller Käseladen GmbH\n"
        "Bahnhofstrasse 1\n"
        "8001 Zürich\n"
        "\n"
        "Müller Käseladen GmbH, in Zürich, CHE-100.000.000, "
        "Gesellschaft mit beschränkter Haftung (SHAB Nr. 1 vom 01.01.2020, Publ. 1).\n"
        "\n"
        "Tagesregister-Nr. 1 vom 01.01.2026\n"
        "\n"
        "Vorangehende Publikation im SHAB: Nr. 1, Datum: 01.01.2020\n"
        "\n"
        "Kontaktstelle: Handelsregisteramt des Kantons Zürich\n",
        encoding="utf-8",
    )

    record = prefill_file(sample)

    assert record["direccion_localidad"] == "Zürich"
    assert record["forma_juridica"] == "GmbH"
    assert record["sede_canton"] == "ZH"


# --- 0012.txt: Chez India KLG, Neueintragung, no prior publication ---


def test_0012_chez_india_neueintragung():
    record = prefill_file(DATA_RAW / "0012.txt")

    assert record["doc_id"] == "0012"
    assert record["tipo_acto"] == "neueintragung"
    assert record["uid"] == "CHE-358.426.607"

    # "(Neueintragung)" closes the sentence instead of "(SHAB Nr. ...)" —
    # forma_juridica must still resolve.
    assert record["forma_juridica"] == "Kollektivgesellschaft"
    assert record["empresa_nombre_completo"] == "Chez India KLG"
    assert record["empresa_nombre_base"] == "Chez India KLG"

    assert record["sede_localidad"] == "Biel/Bienne"
    assert record["publicacion_anterior_shab_nr"] is None
    assert record["publicacion_anterior_fecha"] is None
    assert record["publicacion_anterior_publ_id"] is None


# --- 0013.txt: STuBI Fleisch AG, Neueintragung, address repeated before the
#     legal form ("..., CHE-295.332.571, Heidbühl 475, 3537 Eggiwil,
#     Aktiengesellschaft (Neueintragung)") ---


def test_0013_stubi_fleisch_neueintragung():
    record = prefill_file(DATA_RAW / "0013.txt")

    assert record["doc_id"] == "0013"
    assert record["tipo_acto"] == "neueintragung"
    assert record["uid"] == "CHE-295.332.571"

    # forma_juridica is the *last* comma-separated segment before the
    # parenthesis (Aktiengesellschaft), not the first one after the UID
    # (the repeated street address).
    assert record["forma_juridica"] == "AG"
    assert record["empresa_nombre_completo"] == "STuBI Fleisch AG"
    assert record["empresa_nombre_base"] == "STuBI Fleisch AG"

    assert record["direccion_calle"] == "Heidbühl 475"
    assert record["direccion_cp"] == "3537"
    assert record["direccion_localidad"] == "Eggiwil"


# --- 0014.txt: Studio MO Gfeller Architektur, Neueintragung, Einzelunternehmen ---


def test_0014_studio_gfeller_neueintragung():
    record = prefill_file(DATA_RAW / "0014.txt")

    assert record["doc_id"] == "0014"
    assert record["tipo_acto"] == "neueintragung"
    assert record["uid"] == "CHE-247.993.988"

    assert record["forma_juridica"] == "Einzelunternehmen"
    assert record["empresa_nombre_completo"] == "Studio MO Gfeller Architektur"
    assert record["empresa_nombre_base"] == "Studio MO Gfeller Architektur"


# --- 0015.txt: Elim Stiftung für Eltern und Kind, Mutation with prior
#     publication — regression check that the "(SHAB Nr. ...)" case still
#     works after the "(Neueintragung)" alternation was added ---


def test_0015_elim_stiftung_mutation():
    record = prefill_file(DATA_RAW / "0015.txt")

    assert record["doc_id"] == "0015"
    assert record["tipo_acto"] == "mutation"
    assert record["uid"] == "CHE-110.634.974"

    assert record["forma_juridica"] == "Stiftung"
    assert record["empresa_nombre_completo"] == "Elim Stiftung für Eltern und Kind"
    assert record["empresa_nombre_base"] == "Elim Stiftung für Eltern und Kind"

    assert record["publicacion_anterior_shab_nr"] == 11
    assert record["publicacion_anterior_fecha"] == "2026-01-19"
    assert record["publicacion_anterior_publ_id"] == "1006542126"


# --- 0016.txt: Helios Solar Energie GmbH, alt names trail on the *same*
#     line as the name instead of getting their own line below it ---


def test_0016_helios_solar_alt_names_same_line():
    record = prefill_file(DATA_RAW / "0016.txt")

    assert record["doc_id"] == "0016"
    assert record["uid"] == "CHE-300.591.146"

    assert record["nombres_alternativos"] == [
        "Helios Solar Energie Sàrl",
        "Helios Solar Energie Sagl",
        "Helios Solar Energie Ltd liab Co",
    ]
    # the "(...)" alt names must be stripped off, not left glued onto the name
    assert record["empresa_nombre_completo"] == "Helios Solar Energie GmbH"
    assert record["empresa_nombre_base"] == "Helios Solar Energie GmbH"

    # forma_juridica is deliberately not asserted here: the body describes
    # this record as a "schweizerische Zweigniederlassung" of a company
    # named "... GmbH" — which of the two belongs in forma_juridica is a
    # domain judgement call outside the scope of this regression test.


# --- 0018.txt / 0021.txt: relocations where the body reads "bisher in
#     <Ort>" — the pre-act seat must come from <Ort> and the header's
#     "Bisher" postal code, never from Kontaktstelle ---


def test_0018_equimode_bisher_in_derives_sede_from_bisher_cp():
    record = prefill_file(DATA_RAW / "0018.txt")

    assert record["doc_id"] == "0018"
    assert record["uid"] == "CHE-257.978.269"

    # Kontaktstelle is "Handelsregisteramt des Kantons Bern" — the NEW
    # canton (Roggwil BE), not the pre-act seat (Neuendorf, in Solothurn).
    # sede_localidad comes from "bisher in <Ort>" itself; sede_canton from
    # the postal code in the header's "Bisher" block (4623 -> SO).
    assert record["autoridad"] == "Handelsregisteramt des Kantons Bern"
    assert record["sede_localidad"] == "Neuendorf"
    assert record["sede_canton"] == "SO"


def test_0021_linder_immobilien_bisher_in_derives_sede_from_bisher_cp():
    record = prefill_file(DATA_RAW / "0021.txt")

    assert record["doc_id"] == "0021"
    assert record["uid"] == "CHE-376.960.112"

    # Kontaktstelle is "Handelsregisteramt des Kantons Bern" — the NEW
    # canton (Lyss), not the pre-act seat (Aeschi SO). sede_localidad comes
    # from "bisher in <Ort>" itself (parenthesised canton hint and all, same
    # style as an already-annotated sede_localidad like "Brienz (BE)");
    # sede_canton from the header's "Bisher" postal code (4556 -> SO), never
    # from autoridad, which would silently produce the wrong canton here.
    assert record["autoridad"] == "Handelsregisteramt des Kantons Bern"
    assert record["sede_localidad"] == "Aeschi (SO)"
    assert record["sede_canton"] == "SO"


def test_canton_from_plz_unmapped_prefix_returns_none_not_a_guess():
    # A prefix this module hasn't verified must never resolve to a canton —
    # see PLZ_PREFIX_TO_CANTON's comment: a wrong canton is worse than none.
    assert _canton_from_plz("8001") is None
    assert _canton_from_plz(None) is None
    assert _canton_from_plz("12") is None  # too short to be a real PLZ prefix
