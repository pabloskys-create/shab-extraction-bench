# SCHEMA.md — v0.2 (exploratory, not frozen)

Status: **exploratory**. Freeze to v1.0 after ~25 randomly sampled documents.

Scope: German-language Handelsregister publications only (`Neueintragung`,
`Mutation`, `Löschung`). French (FOSC) and Italian (FUSC) excluded — see
"Scope decisions" below.

---

## Core fields — scored in the benchmark

| Field | Type | Notes |
|---|---|---|
| `schema_version` | string | e.g. `"0.2"` |
| `doc_id` | string | matches filename, e.g. `"0001"` |
| `idioma` | string | always `"de"` in this corpus |
| `tipo_acto` | enum | `neueintragung` \| `mutation` \| `loeschung` |
| `subtipos` | list[string] | derived labels, see below |
| `empresa_nombre_completo` | string | includes legal-status suffix |
| `empresa_nombre_base` | string | **includes legal form** (`... AG`), excludes status suffix |
| `sufijo_estado` | string\|null | `in Liquidation`, `in Liq.`, else `null` |
| `nombres_alternativos` | list[string] | other-language registered names |
| `uid` | string | `CHE-XXX.XXX.XXX` |
| `forma_juridica` | enum | `AG` \| `GmbH` \| `Einzelunternehmen` \| `Genossenschaft` \| `Stiftung` \| `Verein` \| `Kollektivgesellschaft` \| `Kommanditgesellschaft` \| `Zweigniederlassung` |
| `sede_localidad` | string | **town only**, never the street |
| `sede_canton` | string | 2-letter code |
| `direccion_co` | string\|null | the `c/o` party |
| `direccion_calle` | string\|null | |
| `direccion_cp` | string\|null | keep as string (leading zeros) |
| `direccion_localidad` | string\|null | |
| `fecha_acto` | date\|null | **only if explicit** — see rule below |
| `tagesregister_nr` | string | |
| `tagesregister_fecha` | date | |
| `publicacion_anterior_shab_nr` | int\|null | |
| `publicacion_anterior_fecha` | date\|null | |
| `publicacion_anterior_publ_id` | string\|null | |
| `autoridad` | string | the `Kontaktstelle` |
| `canton_anterior` | string\|null | only on inter-cantonal moves |
| `canton_nuevo` | string\|null | only on inter-cantonal moves |
| `capital_nuevo_chf` | number\|null | |
| `capital_anterior_chf` | number\|null | |
| `domicilio_nuevo` | string\|null | **only if the address changes** |
| `domicilio_anterior` | string\|null | **only if the address changes** |
| `personas_entrantes` | list[Person] | |
| `personas_salientes` | list[Person] | |
| `personas_mutantes` | list[PersonChange] | same person, changed attributes |
| `extras` | object | see key registry below |
| `incierto` | list[string] | field names the annotator was unsure about |
| `notas` | string | free prose |
| `_verified` | bool | `false` until a human has eyeballed prefilled fields |

### Person

```json
{ "nombre": null, "nacionalidad": null, "heimatort": null,
  "domicilio": null, "cargo": null, "firma": null }
```

### PersonChange

```json
{ "nombre_nuevo": null, "nombre_anterior": null, "heimatort": null,
  "nacionalidad_anterior": null, "cargo": null, "firma": null }
```

---

## Annotation decisions

**`fecha_acto`** — the date of the legal act being published. Use only when the
source states it explicitly (`Statutenänderung: 14.08.2026`, `Löschungsdatum: ...`).
If absent → `null`. Never substitute `tagesregister_fecha`. Never use the date
inside the parenthetical reference to the *previous* publication.

**The parenthetical trap** — `(SHAB Nr. 223 vom 18.11.2025, Publ. 1006488017)`
refers to the **previous** publication, not this one. It maps to
`publicacion_anterior_*`, never to `fecha_acto`.

**Names** — `empresa_nombre_base` keeps the legal form (`Noorik Biopharmaceuticals AG`)
because it is part of the official registered name. `forma_juridica` is stored
separately anyway. `empresa_nombre_completo` additionally keeps the status suffix.

**The page headline is not data.** `Mutation Foo AG, Basel` is portal navigation
chrome. Only `Mutation` is used (→ `tipo_acto`).

**`domicilio_*` vs `direccion_*`** — `direccion_*` is the address as printed.
`domicilio_nuevo` / `domicilio_anterior` are populated **only when the act
changes the address** (source shows `Bisher` / `bisher`).

**Nationality convention** — `von <Ort>` marks a Swiss citizen (Heimatort);
`<Land> Staatsangehörige(r)` marks a foreigner. A change from the latter to the
former indicates naturalisation.

**Missing vs empty** — absent scalar → `null`; absent list → `[]`. Never `""`.

**`sede_localidad` / `sede_canton`** — the seat as stated in the body
(`… , in <Town>, CHE-…`), i.e. the seat *before* the act. On relocations
this differs from the post-act seat, which is captured by
`canton_anterior`/`canton_nuevo` and `domicilio_*`.

**The Kontaktstelle trap (inter-cantonal moves)** — when the body reads
`bisher in <Ort>`, that phrasing marks a relocation: the seat named next to
the UID is the seat *before* the act, but `Kontaktstelle` (→ `autoridad`)
always names the authority for the seat *after* it — on an inter-cantonal
move, the new canton's registry office. Since `sede_canton` must be the
pre-act canton, it must never be derived from `autoridad` in this case.
`prefill.py` already implements this: on a `bisher in <Ort>` match it nulls
out both `sede_localidad` and `sede_canton` rather than deriving them from
the wrong token, leaving them for the annotator to fill by hand. Found at
0021 (Aeschi SO → Lyss BE); this entry documents that existing behaviour.

**Gendered forms** — `nacionalidad` is normalised to the base adjective
(`französische` → `französisch`, `brasilianischer` → `brasilianisch`),
since it is a category rather than a transcribed value.

`cargo`, `nombre` and all other person attributes are transcribed
verbatim from the source, gendered forms included
(`Präsidentin des Stiftungsrates`, `Geschäftsführerin`). These are
register data, and normalising them would produce values that do not
appear in the source text — breaking crosscheck and unfairly penalising
models that transcribe correctly.

Academic and professional titles stay inside `nombre`
(`Böckli, Peter Prof. Dr.`).

**`incierto`** — top-level field names only. Nested keys (e.g. a person's
`heimatort`) are flagged by naming their containing field
(`personas_mutantes`) and describing the specifics in `notas`.
---

## `subtipos` — controlled vocabulary

`statutenaenderung`, `kapitalerhoehung`, `kapitalherabsetzung`,
`bedingte_kapitalerhoehung`, `kapitalband_aufhebung`, `organaenderung`,
`sitzverlegung`, `kantonswechsel`, `firmenaenderung`, `zweckaenderung`,
`rechtsformaenderung`, `liquidationseroeffnung`, `liquidation_beendet`,
`fusion`, `revisionsstelle`

`rechtsformaenderung` — the company's legal form itself changes (e.g.
`Rechtsform Hauptsitz neu: Aktiengesellschaft [bisher: Gesellschaft mit
beschränkter Haftung]`). Distinct from `firmenaenderung` (name change) and
from `statutenaenderung` (which nearly always co-occurs but is broader).

Add new values here before using them.

---

## `extras` — key registry

Keys already in use. **Check this list before inventing a new key.**

- `decision_junta_fecha` — date of the shareholder resolution underlying the act
- `acciones_nuevas` / `acciones_anteriores` — share counts
- `valor_nominal_chf` — nominal value per share
- `clases_acciones` — description of share classes and privileges
- `confirmacion_revisor_fecha` — auditor confirmation date
- `antes_del_sperrjahr` — bool, deletion before the statutory blocking year
- `motivo_loeschung` — stated reason for the deletion (e.g. `Geschäftsaufgabe`)
- `firma_nueva` — new company name on a `Firma neu:` change
- `zweck` — company purpose, truncated to first sentence or ~200 chars
- `hauptsitz` — parent company's head office (branch registrations only)
- `liberierung_nuevo_chf` / `liberierung_anterior_chf` — paid-in capital
- `vinkulierung` — bool, share transferability restricted per articles
- `revision` — audit regime, e.g. `opting-out`
- `tipo_kapitalerhoehung` — e.g. `Ordentliche Kapitalerhöhung innerhalb Kapitalband`
- `weitere_adressen_nueva` / `weitere_adressen_anterior` — secondary addresses

**Promotion rule:** an `extras` key appearing in ≥5% of documents is promoted to
a core field in the next schema version, and previously annotated documents are
back-filled for that field only.

---

## Scope decisions

**German only.** Annotation quality depends on the annotator verifying meaning
directly. French and Italian notices would have to be annotated through machine
translation, which cannot be verified and would silently corrupt the ground truth.
Excluded documents are retained in `data/excluded/` with a reason.
Cross-language degradation is listed as future work.

**Exclusion budget.** Up to ~10% of sampled documents may be excluded when the
annotator cannot stand behind the annotation. All exclusions are logged with a
reason and reported in the README.

**Random sampling.** Documents are sampled at random from a publication day, not
hand-picked, so the corpus reflects the real distribution of act types.

---

## Corpus sampling

- Frame: SHAB Handelsregister publications, German, Bern, 2026-08-10
- Population: 85 — verified equal to the site's reported hit count,
  so the frame is fully enumerated (see `data/sampling/manifest_full.json`:
  `population_size` == `site_reported_total` == `len(records)` == 85).
- Method: simple random sample, seed 42, n=25, no replacement
- Note: the SHAB listing uses virtual DOM scrolling and recycles nodes.
  Saved listings of ~1050 and ~220 results were both incomplete
  (1050 of 1169; 210 of 220). The frame was narrowed until the saved
  page provably contained the entire population.
- Out-of-scope documents are excluded and NOT replaced.

Scope: Handelsregister publications only (Neueintragung, Mutation,
Löschung). Other SHAB rubrics — Schuldenruf, Testamentseröffnung,
Kraftloserklärung, Gesuch and similar — are out of scope and are
excluded at the filtering stage, not at annotation time.

---

## v1.0 draft — not yet in effect

Schema changes proposed from exploratory annotation but not yet applied to
`data/gold/`, `src/validate.py`'s `FIELD_SPECS`, or `src/prefill.py`. Do not
annotate against these fields until the schema is actually bumped to v1.0.

**`PersonChange` — redesigned.** Replaces the current shape (a single
generic `nombre_nuevo`/`nombre_anterior` pair plus flat `heimatort`,
`nacionalidad_anterior`, `cargo`, `firma`) with one `_nuevo`/`_anterior` pair
per attribute:

```json
{ "nombre_nuevo": null, "nombre_anterior": null,
  "domicilio_nuevo": null, "domicilio_anterior": null,
  "cargo_nuevo": null, "cargo_anterior": null,
  "firma_nueva": null, "firma_anterior": null,
  "nacionalidad_nueva": null, "nacionalidad_anterior": null,
  "heimatort_nuevo": null, "heimatort_anterior": null }
```

Rule: an attribute that does not change is filled only in its `_nuevo` (or
`_nueva`) half; the matching `_anterior` stays `null`. This closes the
`PersonChange.cargo_anterior`, `PersonChange.domicilio` and
`PersonChange.firma_anterior` gaps logged repeatedly in `annotation_log.md`
(0003, 0004, 0006, 0015, 0017-0019, 0023).

**`Person` — two new keys.**

- `uid` — for a legal person acting as an officer or partner (e.g. OBT AG in
  0028, or the company itself as a partner in 0004).
- `stammanteile` — GmbH participation share, seen in 0004, 0025, 0027.

**New top-level fields: `empresa_nombre_nuevo` / `empresa_nombre_anterior`.**
A change pair, same style as `domicilio_nuevo`/`domicilio_anterior`, for a
company name change. Trigger: `Firma neu:` (seen in 0019, 0022).

---

## Changelog

- **Unreleased** — registered nine `extras` keys already in use:
  `zweck`, `hauptsitz`, `liberierung_nuevo_chf`, `liberierung_anterior_chf`,
  `vinkulierung`, `revision`, `tipo_kapitalerhoehung`,
  `weitere_adressen_nueva`, `weitere_adressen_anterior`. See the "v1.0 draft"
  section for schema changes still pending (`PersonChange` redesign, etc.) —
  the freeze itself has not happened yet.
- **v0.2** — added `idioma`, `nombres_alternativos`, `direccion_*`, `canton_nuevo`,
  `canton_anterior`, `tagesregister_fecha`, `publicacion_anterior_publ_id`,
  `personas_mutantes`, `incierto`, `_verified`. Fixed `fecha_acto` rule.
  Derived from 4 exploratory documents.
- **v0.1** — initial sketch.