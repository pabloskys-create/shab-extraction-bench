"""Tests for src/prefill.py against the three German-language documents in
data/raw/ (0001–0003). 0000.txt is a French FOSC notice and is out of scope
per SCHEMA.md ("Scope decisions" — German only), so it is not used here.
"""

import re
from pathlib import Path

import pytest

from src.prefill import _derive_canton, _to_iso, prefill_file, prefill_text

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


@pytest.mark.parametrize("doc_id", ["0001", "0002", "0003"])
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
    assert record["empresa_nombre_completo"] is None
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
