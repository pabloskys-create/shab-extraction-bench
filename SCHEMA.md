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

---

## `subtipos` — controlled vocabulary

`statutenaenderung`, `kapitalerhoehung`, `kapitalherabsetzung`,
`bedingte_kapitalerhoehung`, `kapitalband_aufhebung`, `organaenderung`,
`sitzverlegung`, `kantonswechsel`, `firmenaenderung`, `zweckaenderung`,
`liquidationseroeffnung`, `liquidation_beendet`, `fusion`, `revisionsstelle`

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

## Changelog

- **v0.2** — added `idioma`, `nombres_alternativos`, `direccion_*`, `canton_nuevo`,
  `canton_anterior`, `tagesregister_fecha`, `publicacion_anterior_publ_id`,
  `personas_mutantes`, `incierto`, `_verified`. Fixed `fecha_acto` rule.
  Derived from 4 exploratory documents.
- **v0.1** — initial sketch.
