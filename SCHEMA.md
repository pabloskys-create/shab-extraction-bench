# SCHEMA.md — v1.0 (frozen)

Status: **frozen**. Converged after 28 exploratory documents (0001-0028;
see `annotation_log.md`) and re-annotated to v1.0 in full.

Scope: German-language Handelsregister publications only (`Neueintragung`,
`Mutation`, `Löschung`). French (FOSC) and Italian (FUSC) excluded — see
"Scope decisions" below.

---

## Scoreability 
Every annotation rule must be applicable from the
source text alone, without knowing what the annotator considered
interesting. A rule that depends on judgement about relevance cannot be
scored against a model and turns the field into noise.

## Core fields — scored in the benchmark

| Field | Type | Notes |
|---|---|---|
| `schema_version` | string | e.g. `"1.0"` |
| `doc_id` | string | matches filename, e.g. `"0001"` |
| `language` | string | always `"de"` in this corpus |
| `act_type` | enum | `neueintragung` \| `mutation` \| `loeschung` |
| `act_subtypes` | list[string] | derived labels, see below |
| `company_name_full` | string | includes legal-status suffix |
| `company_name_base` | string | **includes legal form** (`... AG`), excludes status suffix |
| `status_suffix` | string\|null | `in Liquidation`, `in Liq.`, else `null` |
| `alternative_names` | list[string] | other-language registered names |
| `company_name_new` | string\|null | company name change pair, see below |
| `company_name_previous` | string\|null | company name change pair, see below |
| `uid` | string | `CHE-XXX.XXX.XXX` |
| `legal_form` | enum | `AG` \| `GmbH` \| `Einzelunternehmen` \| `Genossenschaft` \| `Stiftung` \| `Verein` \| `Kollektivgesellschaft` \| `Kommanditgesellschaft` \| `Zweigniederlassung` |
| `seat_municipality` | string | **town only**, never the street |
| `seat_canton` | string | 2-letter code |
| `address_care_of` | string\|null | the `c/o` party |
| `address_street` | string\|null | |
| `address_postcode` | string\|null | keep as string (leading zeros) |
| `address_municipality` | string\|null | |
| `act_date` | date\|null | **only if explicit** — see rule below |
| `tagesregister_nr` | string | |
| `tagesregister_date` | date | |
| `prior_publication_shab_nr` | int\|null | |
| `prior_publication_date` | date\|null | |
| `prior_publication_id` | string\|null | |
| `authority` | string | the `Kontaktstelle` |
| `canton_previous` | string\|null | only on inter-cantonal moves |
| `canton_new` | string\|null | only on inter-cantonal moves |
| `capital_new_chf` | number\|null | |
| `capital_previous_chf` | number\|null | |
| `domicile_new` | string\|null | **only if the address changes** |
| `domicile_previous` | string\|null | **only if the address changes** |
| `persons_added` | list[Person] | |
| `persons_removed` | list[Person] | |
| `persons_changed` | list[PersonChange] | same person, changed attributes |
| `extras` | object | see key registry below |
| `uncertain` | list[string] | field names the annotator was unsure about |
| `notes` | string | free prose |
| `_verified` | bool | `false` until a human has eyeballed prefilled fields |

### Person

```json
{ "name": null, "nationality": null, "heimatort": null,
  "domicile": null, "role": null, "signature": null,
  "uid": null, "stammanteile": null }
```

### PersonChange

```json
{ "name_new": null, "name_previous": null,
  "domicile_new": null, "domicile_previous": null,
  "role_new": null, "role_previous": null,
  "signature_new": null, "signature_previous": null,
  "nationality_new": null, "nationality_previous": null,
  "heimatort_new": null, "heimatort_previous": null,
  "stammanteile_new": null, "stammanteile_previous": null }
```

---

## Annotation decisions

**`act_date`** — the date of the legal act being published. Use only when the
source states it explicitly (`Statutenänderung: 14.08.2026`, `Löschungsdatum: ...`).
If absent → `null`. Never substitute `tagesregister_date`. Never use the date
inside the parenthetical reference to the *previous* publication.

**The parenthetical trap** — `(SHAB Nr. 223 vom 18.11.2025, Publ. 1006488017)`
refers to the **previous** publication, not this one. It maps to
`prior_publication_*`, never to `act_date`.

**Names** — `company_name_base` keeps the legal form (`Noorik Biopharmaceuticals AG`)
because it is part of the official registered name. `legal_form` is stored
separately anyway. `company_name_full` additionally keeps the status suffix.

**The page headline is not data.** `Mutation Foo AG, Basel` is portal navigation
chrome. Only `Mutation` is used (→ `act_type`).

**`domicile_*` vs `address_*`** — `address_*` is the address as printed.
`domicile_new` / `domicile_previous` are populated **only when the act
changes the address** (source shows `Bisher` / `bisher`). Street and
postcode-plus-town only, joined with ", ". The `c/o` party is excluded:
it has its own field (`address_care_of`).

**Nationality convention** — `von <Ort>` marks a Swiss citizen (Heimatort);
`<Land> Staatsangehörige(r)` marks a foreigner. A change from the latter to the
former indicates naturalisation.

**Missing vs empty** — absent scalar → `null`; absent list → `[]`. Never `""`.

**`seat_municipality` / `seat_canton`** — the seat as stated in the body
(`… , in <Town>, CHE-…`), i.e. the seat *before* the act. On relocations
this differs from the post-act seat, which is captured by
`canton_previous`/`canton_new` and `domicile_*`.

**The Kontaktstelle trap (inter-cantonal moves)** — when the body reads
`bisher in <Ort>`, that phrasing marks a relocation: the seat named next to
the UID is the seat *before* the act, but `Kontaktstelle` (→ `authority`)
always names the authority for the seat *after* it — on an inter-cantonal
move, the new canton's registry office. Since `seat_canton` must be the
pre-act canton, it must never be derived from `authority` in this case.
`prefill.py` implements this: on a `bisher in <Ort>` match, `seat_municipality`
comes from `<Ort>` itself, and `seat_canton` from the postal code in the
header's `Bisher` sub-block (street + `<CP> <Ort>`, repeated there for the
pre-act address) via a small, hand-verified PLZ-prefix table — not from
`authority`. An unmapped prefix resolves to `null` rather than a guess, left
for the annotator. Found at 0018 (Neuendorf, 4623 → SO) and 0021 (Aeschi SO,
4556 → SO); this entry documents that existing behaviour.

**Gendered forms** — `nationality` is normalised to the base adjective
(`französische` → `französisch`, `brasilianischer` → `brasilianisch`),
since it is a category rather than a transcribed value.

`role`, `name` and all other person attributes are transcribed
verbatim from the source, gendered forms included
(`Präsidentin des Stiftungsrates`, `Geschäftsführerin`). These are
register data, and normalising them would produce values that do not
appear in the source text — breaking crosscheck and unfairly penalising
models that transcribe correctly.

Academic and professional titles stay inside `name`
(`Böckli, Peter Prof. Dr.`).

**`Person.uid` / `Person.stammanteile`** — `uid` records a legal person
acting as an officer or partner (e.g. OBT AG in 0028, or the company itself
as a partner in 0004); `nationality`/`heimatort` stay `null` for it, since
neither applies to a company. `stammanteile` is the number of GmbH
participations the person holds (`mit N Stammanteilen`), not their nominal
value — nominal value per participation belongs to the company, not the
partner, and stays in `extras.nominal_value_chf`. Seen in 0004, 0025, 0027.

**`PersonChange` — one pair per attribute.** Each attribute that can change
(`name`, `domicile`, `role`, `signature`, `nationality`, `heimatort`,
`stammanteile`) has its own `_new` and `_previous` half. Rule: an
attribute that does not change is filled only in its `_new` half; the
matching `_previous` stays `null`. This is scored per half, not as a unit —
a model that gets the new value right but omits `_previous` (or invents
one) is wrong on exactly that field, not the whole person.

**Toponyms** — place names are transcribed exactly as the source writes
them, parenthesised canton or municipality included: `Aeschi (SO)`,
`Brienz (BE)`, `Wasen im Emmental (Sumiswald)`. The same place can appear in
two forms within one notice (e.g. the seat sentence vs. a `bisher`
clause) — each field takes the form of the phrase it was read from, never
normalised to match another field.

**`uncertain`** — top-level field names only. Nested keys (e.g. a person's
`heimatort`) are flagged by naming their containing field
(`persons_changed`) and describing the specifics in `notes`.

**`company_name_new` / `company_name_previous`** — filled as a pair
whenever the act changes the company name (`Firma neu:`). Both null
otherwise. `company_name_previous` duplicates `company_name_full`
by design: the pair must be readable without cross-referencing another
field, and a judgement-based rule ("only when it adds something") would
not be scoreable — a model cannot know when the annotator considered it
worth recording.

**`act_subtypes` follow the text, not the legal substance.** Subtypes are
assigned from the headings the notice actually carries. A `Berichtigung`
(rectification of an earlier entry) that lists people under
`Ausgeschiedene Personen und erloschene Unterschriften:` and
`Eingetragene Personen neu oder mutierend:` gets `organaenderung`, even
though no office in fact changed hands and the two entries are the same
person under a corrected name (see 0095).

Leaving it empty would require the annotator — or a model — to work out
that the earlier publication was wrong and that the two entries refer to
one person. That is reasoning about the substance, not reading, and it
breaks the scoreability rule above: a model that labels `organaenderung`
here is reading correctly and would be penalised for not thinking like a
registrar.
---

## `act_subtypes` — controlled vocabulary

`statutenaenderung`, `kapitalerhoehung`, `kapitalherabsetzung`,
`bedingte_kapitalerhoehung`, `kapitalband_aufhebung`, `organaenderung`,
`sitzverlegung`, `kantonswechsel`, `firmenaenderung`, `zweckaenderung`,
`rechtsformaenderung`, `liquidationseroeffnung`,
`fusion`, `revisionsstelle`, `konkurseinstellung`, `konkurseroeffnung`

`rechtsformaenderung` — the company's legal form itself changes (e.g.
`Rechtsform Hauptsitz neu: Aktiengesellschaft [bisher: Gesellschaft mit
beschränkter Haftung]`). Distinct from `firmenaenderung` (name change) and
from `statutenaenderung` (which nearly always co-occurs but is broader).

Add new values here before using them.

---

## `extras` — key registry

Keys already in use. **Check this list before inventing a new key.**

Naming rule: a German noun for the registry concept, English for everything
else. No Spanish. Applied across the corpus and this registry in one pass.

- `gesellschafterversammlung_date` — date of the shareholder resolution
  underlying the act (`mit Beschluss der Gesellschafterversammlung vom …`)
- `aktien_new` / `aktien_previous` — share counts
- `nominal_value_chf` — nominal value per share
- `share_classes` — description of share classes and privileges
- `loeschung_reason` — stated reason for the deletion (e.g. `Geschäftsaufgabe`)
- `zweck` — company purpose, truncated by characters to the first ~200,
  rounded out to the nearest word boundary, with `…`; never cut at the
  first full stop, since purposes routinely open with a short fragment.
  A purpose under that length is kept whole. Scored by prefix match.
- `hauptsitz` — parent company's head office (branch registrations only)
- `liberierung_new_chf` / `liberierung_previous_chf` — paid-in capital
- `vinkulierung` — bool, share transferability restricted per articles
- `revision` — audit regime, e.g. `opting-out`
- `kapitalerhoehung_type` — e.g. `Ordentliche Kapitalerhöhung innerhalb Kapitalband`
- `weitere_adressen_new` / `weitere_adressen_previous` — secondary addresses
- `mitteilungen` — how the company notifies its shareholders/partners
- `konkurseinstellung_reason` — stated reason proceedings were
  discontinued (e.g. `mangels Aktiven`)
- `konkurs_wirkung_ab` — timestamp the bankruptcy takes effect from
  (`mit Wirkung ab dem …`)
- `steuerzustimmung` — bool, tax authorities' clearance for deletion is on
  file (`Die Zustimmungen der Steuerverwaltungen liegen vor`)
- `nebenleistungspflichten` — ancillary obligations and pre-emption rights
  clause carried by the articles
- `zweigniederlassung_new` — branch office opened by the act
- `prior_publication_page` — page number in an old-format prior-publication
  citation (`S.5`)
- `berichtigung_meldungsnummer` / `berichtigung_shab_datum` /
  `berichtigung_tr_nr` / `berichtigung_tr_datum` — the publication a
  Berichtigung corrects, identified by its own notice numbers and dates

**Promotion threshold — a v2.0 decision, deliberately not taken for v1.0.**
An `extras` key appearing in ≥5% of documents (≥6 of the 120) is a candidate
for promotion to a core field. Nine keys already exceed it: `zweck` 32,
`nominal_value_chf` 14, `mitteilungen` 12, `revision` 10, and five more at
6-7 (`aktien_new`, `share_classes`, `liberierung_new_chf`,
`loeschung_reason`, `vinkulierung`). Promoting them would mean re-annotating
the whole corpus for no benefit to the benchmark: `extras` is scored as an
object, so the data is present and comparable either way. The threshold is
recorded as a signal for a future schema version, not as a rule this version
follows.

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

**Random sampling.** Documents are sampled at random from three diferent publication days, not
hand-picked, so the corpus reflects the real distribution of act types.

---

## Corpus sampling

Frame: the union of per-day, per-canton SHAB Handelsregister listings,
German language filter. Each listing was saved separately and verified
to contain its full population before sampling:

| Canton | Date | Population |
|---|---|---|
| BE | 2026-08-10 | 85 |
| ZH | 2026-08-31 | 233 |
| LU | 2026-08-25 | 87 |
| **Total** | | **405** |

Method: simple random sample from the union, seed 42, no replacement.
Documents 0001-0003 are warm-up notices annotated before any frame
existed, and belong to no listing. Documents 0004-0028 were drawn from
the Bern listing during the exploratory phase; the remaining 92 from
the union of all three, Bern included.

Resulting corpus: BE 42, LU 23, ZH 52, plus 3 warm-up. Total 120.
The cantonal mix reflects which days were captured, not each canton's
real share of SHAB output.

---

## Changelog

- **2026-09-08, unversioned** — renamed the `extras` keys from Spanish under
  one naming rule (a German noun for the registry concept, English for
  everything else): `decision_junta_fecha` → `gesellschafterversammlung_date`,
  `acciones_nuevas`/`acciones_anteriores` → `aktien_new`/`aktien_previous`,
  `valor_nominal_chf` → `nominal_value_chf`,
  `clases_acciones` → `share_classes`,
  `motivo_loeschung` → `loeschung_reason`,
  `liberierung_nuevo_chf`/`liberierung_anterior_chf` →
  `liberierung_new_chf`/`liberierung_previous_chf`,
  `tipo_kapitalerhoehung` → `kapitalerhoehung_type`,
  `weitere_adressen_nueva`/`weitere_adressen_anterior` →
  `weitere_adressen_new`/`weitere_adressen_previous`,
  `motivo_konkurseinstellung` → `konkurseinstellung_reason`.
  Dropped three keys that were registered but never used in the corpus:
  `firma_nueva`, `confirmacion_revisor_fecha`, `antes_del_sperrjahr`.
  Applied across the corpus and this registry in one pass. Key names only —
  no field semantics changed, so `schema_version` stays `1.0`.
- **2026-08-31, unversioned** — renamed the schema's own field names from
  Spanish to English (`tipo_acto` → `act_type`, `empresa_nombre_completo` →
  `company_name_full`, `sede_localidad` → `seat_municipality`,
  `personas_entrantes`/`personas_salientes`/`personas_mutantes` →
  `persons_added`/`persons_removed`/`persons_changed`, and so on, including
  the `Person` and `PersonChange` sub-objects), per the English-only
  convention in `CLAUDE.md`. Structure, types and semantics unchanged, so
  `schema_version` stays `1.0`; the v1.0 entry below keeps the old names it
  was written with. `extras` keys were left Spanish here and renamed later,
  see the entry above.
- **v1.0** — froze the schema after 28 exploratory documents (0001-0028;
  see `annotation_log.md`), all re-annotated to this version. Redesigned
  `PersonChange` to one `_new`/`_previous` pair per attribute (`name`,
  `domicile`, `role`, `signature`, `nationality`, `heimatort`,
  `stammanteile`), replacing the single generic `name_new`/`name_previous`
  pair plus flat `heimatort`/`nationality_previous`/`role`/`signature`. Added
  `Person.uid` and `Person.stammanteile`; new top-level
  `company_name_new`/`company_name_previous` pair; the toponym
  transcription rule; the Kontaktstelle/`bisher in <Ort>` seat_canton rule.
  Registered nine `extras` keys already in use: `zweck`, `hauptsitz`,
  `liberierung_nuevo_chf`, `liberierung_anterior_chf`, `vinkulierung`,
  `revision`, `tipo_kapitalerhoehung`, `weitere_adressen_nueva`,
  `weitere_adressen_anterior`.
- **v0.2** — added `language`, `alternative_names`, `address_*`, `canton_new`,
  `canton_previous`, `tagesregister_date`, `prior_publication_id`,
  `persons_changed`, `uncertain`, `_verified`. Fixed `act_date` rule.
  Derived from 4 exploratory documents.
- **v0.1** — initial sketch.