"""Validate a gold-standard SHAB record against SCHEMA.md.

Checks a record's shape (`FIELD_SPECS` below is a hand-kept mirror of the
"Core fields" table in SCHEMA.md — see `test_field_specs_match_schema_field_names`
in tests/test_validate.py for the drift guard) and a handful of cross-field
coherence rules that no static type check can express.

This module never writes anything. Per CLAUDE.md rule 1, gold-standard JSON
is hand-annotated ground truth and is read-only for tooling.

Design note: SCHEMA.md marks most-but-not-all optional scalars `string|null`
in its Type column. `notas` is documented as plain `string`, but SCHEMA.md's
own "Missing vs empty" rule ("absent scalar -> null, never \"\"") leaves no
way to represent "no notes" under a required-non-null reading, so it is
treated as nullable here. Flag in SCHEMA.md if that should be tightened.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

# --- controlled vocabularies (mirrors SCHEMA.md; keep in sync by hand,
# like src/prefill.py's FORM_MAP / TIPO_ACTO_MAP) ---

TIPO_ACTO_VALUES = ["neueintragung", "mutation", "loeschung"]

FORMA_JURIDICA_VALUES = [
    "AG",
    "GmbH",
    "Einzelunternehmen",
    "Genossenschaft",
    "Stiftung",
    "Verein",
    "Kollektivgesellschaft",
    "Kommanditgesellschaft",
    "Zweigniederlassung",
]

SUBTIPOS_VALUES = [
    "statutenaenderung",
    "kapitalerhoehung",
    "kapitalherabsetzung",
    "bedingte_kapitalerhoehung",
    "kapitalband_aufhebung",
    "organaenderung",
    "sitzverlegung",
    "kantonswechsel",
    "firmenaenderung",
    "zweckaenderung",
    "rechtsformaenderung",
    "liquidationseroeffnung",
    "liquidation_beendet",
    "fusion",
    "revisionsstelle",
]

PERSON_KEYS = frozenset(
    {"nombre", "nacionalidad", "heimatort", "domicilio", "cargo", "firma", "uid", "stammanteile"}
)
# stammanteile is a count (int|null), not a transcribed string like the rest
# of Person's fields -- see PERSON_INT_KEYS below.
PERSON_INT_KEYS = frozenset({"stammanteile"})

PERSON_CHANGE_KEYS = frozenset(
    {
        "nombre_nuevo",
        "nombre_anterior",
        "domicilio_nuevo",
        "domicilio_anterior",
        "cargo_nuevo",
        "cargo_anterior",
        "firma_nueva",
        "firma_anterior",
        "nacionalidad_nueva",
        "nacionalidad_anterior",
        "heimatort_nuevo",
        "heimatort_anterior",
        "stammanteile_nuevo",
        "stammanteile_anterior",
    }
)
PERSON_CHANGE_INT_KEYS = frozenset({"stammanteile_nuevo", "stammanteile_anterior"})

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# SCHEMA.md's header reads "# SCHEMA.md — v1.0 (frozen)".
SCHEMA_MD_PATH = Path(__file__).resolve().parent.parent / "SCHEMA.md"
SCHEMA_VERSION_RE = re.compile(r"^#\s*SCHEMA\.md\s*—\s*v(\d+\.\d+)", re.MULTILINE)


def _schema_declared_version() -> str | None:
    """Return the version declared in SCHEMA.md's header (e.g. "1.0"), or
    None if SCHEMA.md is missing or its header doesn't match the expected
    "# SCHEMA.md — vX.Y ..." shape.
    """
    try:
        text = SCHEMA_MD_PATH.read_text(encoding="utf-8")
    except OSError:
        return None
    match = SCHEMA_VERSION_RE.search(text)
    return match.group(1) if match else None

# --- record shape: field name -> {kind, nullable, enum} ---
#
# kind is one of: str, date, int, number, bool, dict,
# list_str, list_person, list_person_change.
FIELD_SPECS: dict[str, dict[str, Any]] = {
    "schema_version": {"kind": "str", "nullable": False},
    "doc_id": {"kind": "str", "nullable": False},
    "idioma": {"kind": "str", "nullable": False},
    "tipo_acto": {"kind": "str", "nullable": False, "enum": TIPO_ACTO_VALUES},
    "subtipos": {"kind": "list_str", "nullable": False, "enum": SUBTIPOS_VALUES},
    "empresa_nombre_completo": {"kind": "str", "nullable": False},
    "empresa_nombre_base": {"kind": "str", "nullable": False},
    "sufijo_estado": {"kind": "str", "nullable": True},
    "nombres_alternativos": {"kind": "list_str", "nullable": False},
    "empresa_nombre_nuevo": {"kind": "str", "nullable": True},
    "empresa_nombre_anterior": {"kind": "str", "nullable": True},
    "uid": {"kind": "str", "nullable": False},
    "forma_juridica": {"kind": "str", "nullable": False, "enum": FORMA_JURIDICA_VALUES},
    "sede_localidad": {"kind": "str", "nullable": False},
    "sede_canton": {"kind": "str", "nullable": False},
    "direccion_co": {"kind": "str", "nullable": True},
    "direccion_calle": {"kind": "str", "nullable": True},
    "direccion_cp": {"kind": "str", "nullable": True},
    "direccion_localidad": {"kind": "str", "nullable": True},
    "fecha_acto": {"kind": "date", "nullable": True},
    "tagesregister_nr": {"kind": "str", "nullable": False},
    "tagesregister_fecha": {"kind": "date", "nullable": False},
    "publicacion_anterior_shab_nr": {"kind": "int", "nullable": True},
    "publicacion_anterior_fecha": {"kind": "date", "nullable": True},
    "publicacion_anterior_publ_id": {"kind": "str", "nullable": True},
    "autoridad": {"kind": "str", "nullable": False},
    "canton_anterior": {"kind": "str", "nullable": True},
    "canton_nuevo": {"kind": "str", "nullable": True},
    "capital_nuevo_chf": {"kind": "number", "nullable": True},
    "capital_anterior_chf": {"kind": "number", "nullable": True},
    "domicilio_nuevo": {"kind": "str", "nullable": True},
    "domicilio_anterior": {"kind": "str", "nullable": True},
    "personas_entrantes": {"kind": "list_person", "nullable": False},
    "personas_salientes": {"kind": "list_person", "nullable": False},
    "personas_mutantes": {"kind": "list_person_change", "nullable": False},
    "extras": {"kind": "dict", "nullable": False},
    "incierto": {"kind": "list_str", "nullable": False},
    "notas": {"kind": "str", "nullable": True},
    "_verified": {"kind": "bool", "nullable": False},
}


@dataclass
class ValidationError:
    field: str
    reason: str

    def __str__(self) -> str:
        return f"{self.field}: {self.reason}"


def _parse_iso_date(value: Any) -> date | None:
    """Return the parsed date, or None if `value` isn't a valid ISO date string."""
    if not isinstance(value, str) or not DATE_RE.match(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


# --- per-kind type checks ---


def _check_str(value: Any, field: str, nullable: bool, enum: list[str] | None) -> list[ValidationError]:
    if value is None:
        if nullable:
            return []
        return [ValidationError(field, "is null but the field is required")]
    if not isinstance(value, str):
        return [ValidationError(field, f"expected a string, got {type(value).__name__}")]
    if value == "":
        return [ValidationError(field, 'is an empty string; use null for a missing value, never ""')]
    if enum is not None and value not in enum:
        return [ValidationError(field, f"{value!r} is not in the controlled vocabulary {enum}")]
    return []


def _check_date(value: Any, field: str, nullable: bool) -> list[ValidationError]:
    if value is None:
        if nullable:
            return []
        return [ValidationError(field, "is null but the field is required")]
    if not isinstance(value, str):
        return [ValidationError(field, f"expected an ISO date string, got {type(value).__name__}")]
    if value == "":
        return [ValidationError(field, 'is an empty string; use null for a missing value, never ""')]
    if _parse_iso_date(value) is None:
        return [ValidationError(field, f"{value!r} is not a valid ISO YYYY-MM-DD date")]
    return []


def _check_int(value: Any, field: str, nullable: bool) -> list[ValidationError]:
    if value is None:
        if nullable:
            return []
        return [ValidationError(field, "is null but the field is required")]
    if isinstance(value, bool) or not isinstance(value, int):
        return [ValidationError(field, f"expected an integer, got {type(value).__name__}")]
    return []


def _check_number(value: Any, field: str, nullable: bool) -> list[ValidationError]:
    if value is None:
        if nullable:
            return []
        return [ValidationError(field, "is null but the field is required")]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return [
            ValidationError(
                field,
                f"expected a number, got {type(value).__name__} — "
                "Swiss-format strings like \"189'123.50\" must be converted to a JSON number",
            )
        ]
    return []


def _check_bool(value: Any, field: str) -> list[ValidationError]:
    if not isinstance(value, bool):
        return [ValidationError(field, f"expected a boolean, got {type(value).__name__}")]
    return []


def _check_dict(value: Any, field: str) -> list[ValidationError]:
    if not isinstance(value, dict):
        return [ValidationError(field, f"expected an object, got {type(value).__name__}")]
    return []


def _check_list_str(value: Any, field: str, enum: list[str] | None) -> list[ValidationError]:
    if not isinstance(value, list):
        return [ValidationError(field, f"expected a list, got {type(value).__name__}")]
    errors: list[ValidationError] = []
    for i, item in enumerate(value):
        item_field = f"{field}[{i}]"
        if not isinstance(item, str) or item == "":
            errors.append(ValidationError(item_field, "must be a non-empty string"))
        elif enum is not None and item not in enum:
            errors.append(ValidationError(item_field, f"{item!r} is not in the controlled vocabulary {enum}"))
    return errors


def _check_list_of_objects(
    value: Any,
    field: str,
    allowed_keys: frozenset[str],
    int_keys: frozenset[str] = frozenset(),
) -> list[ValidationError]:
    """Validate a list of Person/PersonChange-shaped objects.

    Every key in `allowed_keys` is string-or-null by default (transcribed
    text); keys listed in `int_keys` (e.g. `stammanteile`, a count) are
    integer-or-null instead.
    """
    if not isinstance(value, list):
        return [ValidationError(field, f"expected a list, got {type(value).__name__}")]
    errors: list[ValidationError] = []
    for i, item in enumerate(value):
        item_field = f"{field}[{i}]"
        if not isinstance(item, dict):
            errors.append(ValidationError(item_field, f"expected an object, got {type(item).__name__}"))
            continue
        extra_keys = set(item) - allowed_keys
        missing_keys = allowed_keys - set(item)
        if extra_keys:
            errors.append(ValidationError(item_field, f"unexpected keys {sorted(extra_keys)}"))
        if missing_keys:
            errors.append(ValidationError(item_field, f"missing keys {sorted(missing_keys)}"))
        for key, val in item.items():
            if key not in allowed_keys:
                continue
            sub_field = f"{item_field}.{key}"
            if key in int_keys:
                if val is not None and (isinstance(val, bool) or not isinstance(val, int)):
                    errors.append(
                        ValidationError(sub_field, f"expected an integer or null, got {type(val).__name__}")
                    )
                continue
            if val == "":
                errors.append(ValidationError(sub_field, 'is an empty string; use null for a missing value, never ""'))
            elif val is not None and not isinstance(val, str):
                errors.append(ValidationError(sub_field, f"expected a string or null, got {type(val).__name__}"))
    return errors


_KIND_CHECKS = {
    "str": lambda value, field, spec: _check_str(value, field, spec["nullable"], spec.get("enum")),
    "date": lambda value, field, spec: _check_date(value, field, spec["nullable"]),
    "int": lambda value, field, spec: _check_int(value, field, spec["nullable"]),
    "number": lambda value, field, spec: _check_number(value, field, spec["nullable"]),
    "bool": lambda value, field, _spec: _check_bool(value, field),
    "dict": lambda value, field, _spec: _check_dict(value, field),
    "list_str": lambda value, field, spec: _check_list_str(value, field, spec.get("enum")),
    "list_person": lambda value, field, _spec: _check_list_of_objects(
        value, field, PERSON_KEYS, PERSON_INT_KEYS
    ),
    "list_person_change": lambda value, field, _spec: _check_list_of_objects(
        value, field, PERSON_CHANGE_KEYS, PERSON_CHANGE_INT_KEYS
    ),
}


def _check_coherence(record: dict) -> list[ValidationError]:
    errors: list[ValidationError] = []

    tagesregister_date = _parse_iso_date(record.get("tagesregister_fecha"))

    prior_pub_date = _parse_iso_date(record.get("publicacion_anterior_fecha"))
    if (
        prior_pub_date is not None
        and tagesregister_date is not None
        and not prior_pub_date < tagesregister_date
    ):
        errors.append(
            ValidationError("publicacion_anterior_fecha", "must be before tagesregister_fecha")
        )

    fecha_acto_date = _parse_iso_date(record.get("fecha_acto"))
    if (
        fecha_acto_date is not None
        and tagesregister_date is not None
        and not fecha_acto_date <= tagesregister_date
    ):
        errors.append(
            ValidationError("fecha_acto", "must be before or equal to tagesregister_fecha")
        )

    if record.get("tipo_acto") == "loeschung":
        entrantes = record.get("personas_entrantes")
        if isinstance(entrantes, list) and len(entrantes) > 0:
            errors.append(
                ValidationError("personas_entrantes", 'must be empty when tipo_acto is "loeschung"')
            )

    canton_anterior = record.get("canton_anterior")
    canton_nuevo = record.get("canton_nuevo")
    if canton_anterior is not None and canton_nuevo is None:
        errors.append(ValidationError("canton_nuevo", "must not be null when canton_anterior is set"))
    if canton_nuevo is not None and canton_anterior is None:
        errors.append(ValidationError("canton_anterior", "must not be null when canton_nuevo is set"))

    domicilio_nuevo = record.get("domicilio_nuevo")
    domicilio_anterior = record.get("domicilio_anterior")
    if domicilio_nuevo is not None and domicilio_anterior is None:
        errors.append(
            ValidationError("domicilio_anterior", "must not be null when domicilio_nuevo is set")
        )
    if domicilio_anterior is not None and domicilio_nuevo is None:
        errors.append(
            ValidationError("domicilio_nuevo", "must not be null when domicilio_anterior is set")
        )

    incierto = record.get("incierto")
    if isinstance(incierto, list):
        for i, name in enumerate(incierto):
            if isinstance(name, str) and name not in FIELD_SPECS:
                errors.append(
                    ValidationError(f"incierto[{i}]", f"{name!r} is not a field defined in SCHEMA.md")
                )

    # Both are list[string], so a subtipos value pasted into
    # nombres_alternativos by mistake (or vice versa) passes the plain type
    # check silently — see tests/test_validate.py for the regression this
    # guards (data/exploratory/0027.json had subtipos values glued in here).
    nombres_alternativos = record.get("nombres_alternativos")
    if isinstance(nombres_alternativos, list):
        for i, name in enumerate(nombres_alternativos):
            if isinstance(name, str) and name in SUBTIPOS_VALUES:
                errors.append(
                    ValidationError(
                        f"nombres_alternativos[{i}]",
                        f"{name!r} is a subtipos value, not an alternative company name "
                        "— looks like subtipos leaked into nombres_alternativos",
                    )
                )

    return errors


def validate_record(record: Any, *, require_verified: bool = False) -> list[ValidationError]:
    """Validate a decoded gold-standard record. Returns [] when it is valid.

    `require_verified` enforces `_verified == true` — pass it (the CLI's
    `--gold` flag) only when validating the final gold set. Without it,
    unreviewed `prefill.py` output (`_verified: false`) is accepted.
    """
    if not isinstance(record, dict):
        return [ValidationError("<root>", f"expected a JSON object, got {type(record).__name__}")]

    errors: list[ValidationError] = []

    extra_keys = set(record) - set(FIELD_SPECS)
    missing_keys = set(FIELD_SPECS) - set(record)
    for key in sorted(extra_keys):
        errors.append(ValidationError(key, "field is not defined in SCHEMA.md"))
    for key in sorted(missing_keys):
        errors.append(ValidationError(key, "field is required by SCHEMA.md but missing"))

    for field, spec in FIELD_SPECS.items():
        if field not in record:
            continue
        errors.extend(_KIND_CHECKS[spec["kind"]](record[field], field, spec))

    errors.extend(_check_coherence(record))

    schema_version = record.get("schema_version")
    if isinstance(schema_version, str):
        declared = _schema_declared_version()
        if declared is not None and schema_version != declared:
            errors.append(
                ValidationError(
                    "schema_version",
                    f"record declares {schema_version!r} but SCHEMA.md's header declares {declared!r}",
                )
            )

    if require_verified and record.get("_verified") is not True:
        errors.append(
            ValidationError("_verified", "must be true to validate as gold data (--gold)")
        )

    return errors


def validate_file(path: str | Path, *, require_verified: bool = False) -> list[ValidationError]:
    """Read and validate a single gold-standard JSON file.

    Also checks that `doc_id` matches the filename (without extension) —
    the guard against copying an existing record and forgetting to change
    its identifier.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [ValidationError("<file>", f"could not read {path}: {exc}")]
    try:
        record = json.loads(text)
    except json.JSONDecodeError as exc:
        return [ValidationError("<file>", f"invalid JSON: {exc}")]

    errors = validate_record(record, require_verified=require_verified)

    if isinstance(record, dict) and record.get("doc_id") != path.stem:
        errors.append(
            ValidationError(
                "doc_id",
                f"{record.get('doc_id')!r} does not match the filename {path.stem!r}",
            )
        )

    return errors


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate SHAB gold-standard record(s) against SCHEMA.md."
    )
    parser.add_argument("path", type=Path, help="A gold JSON file, or a directory of them")
    parser.add_argument(
        "--gold",
        action="store_true",
        help="Require _verified == true (use for the final gold set, not raw prefill output)",
    )
    args = parser.parse_args()

    if args.path.is_dir():
        files = sorted(args.path.glob("*.json"))
    else:
        files = [args.path]

    if not files:
        print(f"No JSON files found at {args.path}", file=sys.stderr)
        sys.exit(1)

    had_errors = False
    for file_path in files:
        errors = validate_file(file_path, require_verified=args.gold)
        if errors:
            had_errors = True
            print(f"{file_path}: {len(errors)} error(s)")
            for error in errors:
                print(f"  - {error}")
        else:
            print(f"{file_path}: OK")

    sys.exit(1 if had_errors else 0)


if __name__ == "__main__":
    main()
