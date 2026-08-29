"""Migrate data/exploratory/*.json from schema v0.2 to the v1.0 draft
described in SCHEMA.md's "v1.0 draft — not yet in effect" section.

Purely structural: renames and adds keys, never interprets, infers, or
moves content between fields. Every newly added `_anterior` (or `_nueva`)
field is left `null` for a human to fill in later — this script does not
annotate anything, per CLAUDE.md.

Transformations:
    - schema_version: "0.2" -> "1.0"
    - Person (personas_entrantes, personas_salientes) gains `uid` and
      `stammanteile`, both null.
    - New top-level fields `empresa_nombre_nuevo` / `empresa_nombre_anterior`,
      both null.
    - PersonChange (personas_mutantes): renames `cargo` -> `cargo_nuevo`,
      `firma` -> `firma_nueva`, `heimatort` -> `heimatort_nuevo`, keeping
      their values; `nombre_nuevo`, `nombre_anterior` and
      `nacionalidad_anterior` are kept as-is; adds `domicilio_nuevo`,
      `domicilio_anterior`, `cargo_anterior`, `firma_anterior`,
      `nacionalidad_nueva`, `heimatort_anterior`, `stammanteile_nuevo`,
      `stammanteile_anterior`, all null.

CLI usage — always review a diff before overwriting the real corpus:

    python src/migrate_v1.py data/exploratory /tmp/migrated-preview
    diff data/exploratory/0019.json /tmp/migrated-preview/0019.json
    # once approved:
    python src/migrate_v1.py data/exploratory data/exploratory --write
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# --- PersonChange: v0.2 key -> v1.0 draft key (values carried over as-is) ---
PERSON_CHANGE_RENAMES = {
    "cargo": "cargo_nuevo",
    "firma": "firma_nueva",
    "heimatort": "heimatort_nuevo",
}

# Full v1.0 draft PersonChange shape, in the order SCHEMA.md lists it.
PERSON_CHANGE_KEY_ORDER = [
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
]


def _migrate_person(person: dict) -> dict:
    """Person gains `uid` and `stammanteile` if it doesn't already have
    them, both null. Never overwrites a value already present -- running
    this on an already-migrated (or partially hand-annotated) record must
    not clobber what's there.
    """
    migrated = dict(person)
    migrated.setdefault("uid", None)
    migrated.setdefault("stammanteile", None)
    return migrated


def _migrate_person_change(change: dict) -> dict:
    """Rename cargo/firma/heimatort to their _nuevo form (keeping the
    value), keep nombre_nuevo/nombre_anterior/nacionalidad_anterior as-is,
    and add the eight new null fields the v0.2 shape didn't have (including
    stammanteile_nuevo/stammanteile_anterior).
    """
    renamed = {PERSON_CHANGE_RENAMES.get(key, key): value for key, value in change.items()}

    unknown = set(renamed) - set(PERSON_CHANGE_KEY_ORDER)
    if unknown:
        # A key we don't recognise means this record's PersonChange doesn't
        # match the v0.2 shape we designed this migration against — better
        # to fail loudly than silently drop or misplace it.
        raise ValueError(f"unrecognised PersonChange key(s): {sorted(unknown)}")

    return {key: renamed.get(key) for key in PERSON_CHANGE_KEY_ORDER}


def _insert_after(record: dict, anchor: str, new_fields: dict) -> dict:
    """Return a copy of `record` with `new_fields` spliced in right after
    `anchor`, preserving the original key order otherwise.
    """
    result: dict = {}
    for key, value in record.items():
        result[key] = value
        if key == anchor:
            result.update(new_fields)
    return result


def migrate_record(record: dict) -> dict:
    """Return a new dict: `record` migrated from schema v0.2 to the v1.0
    draft. Does not mutate `record`.
    """
    migrated = dict(record)

    if migrated.get("schema_version") == "0.2":
        migrated["schema_version"] = "1.0"

    migrated["personas_entrantes"] = [
        _migrate_person(p) for p in migrated.get("personas_entrantes", [])
    ]
    migrated["personas_salientes"] = [
        _migrate_person(p) for p in migrated.get("personas_salientes", [])
    ]
    migrated["personas_mutantes"] = [
        _migrate_person_change(c) for c in migrated.get("personas_mutantes", [])
    ]

    migrated = _insert_after(
        migrated,
        "nombres_alternativos",
        {"empresa_nombre_nuevo": None, "empresa_nombre_anterior": None},
    )

    return migrated


def migrate_file(path: str | Path) -> dict:
    """Read a v0.2 JSON file and return its migrated (v1.0 draft) record."""
    path = Path(path)
    record = json.loads(path.read_text(encoding="utf-8"))
    return migrate_record(record)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate data/exploratory/*.json from schema v0.2 to the v1.0 draft in SCHEMA.md."
    )
    parser.add_argument("src", type=Path, help="Directory of v0.2 JSON files (e.g. data/exploratory)")
    parser.add_argument("dst", type=Path, help="Directory to write migrated files to")
    parser.add_argument(
        "--write",
        action="store_true",
        help="Required when dst is the same directory as src, to confirm an in-place overwrite",
    )
    args = parser.parse_args()

    if not args.src.is_dir():
        print(f"{args.src} is not a directory", file=sys.stderr)
        sys.exit(1)

    if args.src.resolve() == args.dst.resolve() and not args.write:
        print(
            f"Refusing to overwrite {args.src} in place without --write. "
            "Migrate to a temporary directory first and review the diff.",
            file=sys.stderr,
        )
        sys.exit(1)

    files = sorted(args.src.glob("*.json"))
    if not files:
        print(f"No JSON files found in {args.src}", file=sys.stderr)
        sys.exit(1)

    args.dst.mkdir(parents=True, exist_ok=True)

    for path in files:
        migrated = migrate_record(json.loads(path.read_text(encoding="utf-8")))
        out_path = args.dst / path.name
        # No trailing newline: matches the existing data/exploratory/*.json
        # files exactly, so the reviewable diff only shows real changes.
        out_path.write_text(json.dumps(migrated, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{path.name} -> {out_path}")


if __name__ == "__main__":
    main()
