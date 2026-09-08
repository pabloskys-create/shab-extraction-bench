# Annotation log

Format: `doc_id | minutes | new fields | notes`

## Exploratory batch (0001-0003, warm-up)

0001 | 7 | 0 | Sole board member with joint two-signature requirement — functional contradiction
0002 | 6 | 0 | Inter-cantonal move AG→AR; seat_municipality ambiguity (pre- vs post-act seat) resolved in SCHEMA.md
0003 | 10 | 1 (PersonChange.domicile) | Naturalisation + surname change; first non-null act_date

## Sampled batch (0004-0028, Bern 2026-08-10, seed 42)

0004 | 14 | 2 (PersonChange.role_previous, Person.stammanteile) | GmbH partners; company enters as its own partner with its own UID
0005 | 5 | 0 | Alt names (SA/Ltd.); opting-out of eingeschränkte Revision declared for a future fiscal year — no dedicated field
0006 | 11 | 1 (heimatort with two values) | Five people in one notice; uncertain cannot address nested keys
0007 | 4 | 0 | Clean. Person with signing power but no role
0008 | 10 | 2 (weitere_adressen_nueva/anterior) | Secondary address change only; no schema field
0009 | 7 | 0 | Misread Ausgeschiedene as incoming — corrected
0010 | 5 | 0 | Address change within same municipality, not a Sitzverlegung
0011 | 2 | 0 | Löschung, template. First motivo_loeschung (Geschäftsaufgabe)
0012 | 15 | 0 | KLG; prefill bug on Neueintragung (name + legal_form null)
0013 | 10 | 0 | Same prefill bug. First Zweck + share structure
0014 | 4 | 0 | Einzelunternehmen, Neueintragung; single owner, clean
0015 | 9 | 0 | Stiftung; surname + domicile change (Minder → Frauchiger) — repeat of PersonChange.domicile gap (0003)
0016 | 12 | 0 | Zweigniederlassung of Muttenz parent; legal_form ambiguity (branch vs. parent) flagged uncertain; four-way parenthesised name (Sàrl/Sagl/Ltd liab Co)
0017 | 3 | 0 | Domicile-only change, no persons — clean
0018 | 6 | 0 | Domicile change (Neuendorf → Roggwil) tied to sole owner's move; PersonChange.domicile gap repeat
0019 | 15 | 0 | Firmenänderung; two Reber brothers gain sole signature from no registered role — PersonChange gaps (role_previous, domicile) recur together, and signature changes with no previous field either; source data error in the Bisher block ("ober Bärhegen null")
0020 | 3 | 0 | Löschung; second motivo_loeschung value (Nichtaufnahme des Geschäftsbetriebes)
0021 | 9 | 0 | Intercantonal move (Aeschi SO → Lyss BE); "bisher in <Ort>" phrasing exposed a prefill.py seat_canton bug (authority names the new canton, not the pre-act seat) — fixed later this session
0022 | 11 | 0 | Zweigniederlassung renamed; parent (Kloten) separately renamed and changes legal form GmbH→AG — schema can't attribute parent-level changes to the branch record, kept in extras (repeat of 0016). Source of the "Rechtsform Hauptsitz neu: … [bisher: …]" phrasing used to add rechtsformaenderung to act_subtypes this session
0023 | 9 | 0 | role_previous gap recurs (3rd time) — Reichle enters Verwaltungsrat with no prior role on record
0024 | 4 | 0 | Domicile-only change; old-format prior-publication citation includes a page number ("S.5") — clean otherwise
0025 | 6 | 0 | Neueintragung GmbH; Stammanteile and Mitteilungen details recorded in notes, no dedicated fields
0026 | 4 | 0 | Einzelunternehmen, Neueintragung; single owner, clean
0027 | 8 | 0 | Field swap: act_subtypes values pasted into alternative_names
0028 | 7 | 0 | Two legal persons as auditors (Person.uid again)

## Exploratory phase verdict (28 documents)

New fields in the last five documents (0024-0028): **0**. Everything that
appeared was a repeat of an already-known gap (Person.stammanteile,
Person.uid for legal persons). Schema has converged → freeze v1.0.

Average annotation time: ~7.5 min/doc after the first five.
Exclusions from the sampled frame: 0 of 25 (0%).
One additional document (0000, French FOSC) was excluded during the
warm-up phase, before the sampling frame was defined.

The corpus was later fixed at 120 documents, not the 200 originally
targeted, so 92 remained to annotate after this verdict. Per-document times
were not logged for them (0034 is the only entry below that carries one), so
the actual cost of that stretch is not recoverable from this log — the old
projection for "the remaining 172 documents" has been removed rather than
rescaled to a number nothing here supports.

## Sampled batch (0029-0043, Bern 2026-08-10, first batch after the freeze)

0034 | 11 | 1 (konkurseinstellung) | Bankruptcy discontinued for lack of assets — no vocabulary value existed

## Corpus complete (120 documents)

Counts below were recomputed from `data/exploratory/` and `SCHEMA.md`, not
carried over from earlier entries. All 120 records declare `schema_version`
`1.0` and `_verified: true`. The gold standard lives in `data/exploratory/`;
`data/gold/` is still empty.

**New schema fields after the v1.0 freeze (daf99cd): 0.** 39 core fields at
the freeze and 39 now — the Spanish→English rename (3a8d0b4) changed every
name and added none. `Person` still has 8 keys, `PersonChange` 14. The 92
documents annotated after the freeze produced no field the schema lacked.

**`act_subtypes` values first seen after the freeze — 3 of 16:**

- `konkurseinstellung` — 0034, 0037, 0101 (×3). New vocabulary value, added
  in ae28b36; the first case, 0034, is what forced it.
- `konkurseroeffnung` — 0036, 0118 (×2). Same commit, same reason.
- `liquidationseroeffnung` — 0041, 0096 (×2). Already in the frozen
  vocabulary; the corpus simply had no instance until 0041.

So the vocabulary grew by exactly two values in 92 documents, both from the
same gap: bankruptcy. `kapitalerhoehung` and `rechtsformaenderung` went the
other way — seen only in the exploratory 28 and never again.

**`extras` keys first seen after the freeze — 10 of the 25 in use:**

| Key | n | Documents | Registered in SCHEMA.md |
|---|---|---|---|
| `konkurseinstellung_reason` | 3 | 0034, 0037, 0101 | yes |
| `konkurs_wirkung_ab` | 2 | 0036, 0118 | **no** |
| `prior_publication_page` | 2 | 0033, 0041 | **no** |
| `steuerzustimmung` | 1 | 0068 | yes |
| `berichtigung_meldungsnummer` | 1 | 0095 | **no** |
| `berichtigung_shab_datum` | 1 | 0095 | **no** |
| `berichtigung_tr_datum` | 1 | 0095 | **no** |
| `berichtigung_tr_nr` | 1 | 0095 | **no** |
| `zweigniederlassung_new` | 1 | 0104 | **no** |
| `nebenleistungspflichten` | 1 | 0112 | **no** |

Eight of the ten were not in the `extras` key registry — it was updated for
the bankruptcy work and for 0068, and drifted afterwards. All eight have since
been registered. None of the ten is near the 5% promotion threshold (≥6 of
120); the highest is 3.

Four registered keys appeared in no document at all. Three were dropped from
the registry as exploratory leftovers: `confirmacion_revisor_fecha`,
`antes_del_sperrjahr`, and `firma_nueva`, which the core field
`company_name_new` had already replaced. The fourth,
`gesellschafterversammlung_date`, was kept: `ANNOTATION_GUIDE.md` actively
prescribes it for a resolution that predates the act, so it is a rule waiting
for its first case rather than a leftover.

**Harness, run on the complete corpus:**

- `python -m src.validate data/exploratory --gold` → 120 of 120 `OK`, exit 0.
- `python src/crosscheck.py` → `120 verified documents checked, 0 with findings.`

**Exclusions from the 92-document sample: 0.** The only excluded document
remains 0000 (French FOSC), dropped before the sampling frame existed.

## Method notes

- **AG collision.** "AG" abbreviates both the legal form (Aktiengesellschaft)
  and the canton (Aargau) — check whether models confuse the two when the
  company is domiciled in Aargau. Sample AG-canton documents deliberately
  for the benchmark report.
- **Virtual scrolling.** The SHAB listing recycles DOM nodes; saved pages
  never held the full population. Measured: 1050/1169, then 210/220.
  Frame narrowed until 85/85 matched exactly.
- **Rubric mixing.** The initial listing contained 10 non-Handelsregister
  publications (Schuldenruf, Testamentseröffnung, Kraftloserklärung).
  Filtered at source rather than excluded at annotation time.
- **Rubric mixing, second occurrence.** The same filter was missing again when
  the Zurich and Lucerne listings were saved, this time unnoticed until after
  92 documents had been pasted. ZH held 40 foreign publications in 233
  (17% of the captured listing), LU 13 in 87 (15%); Bern was clean. It
  surfaced only indirectly: `check_raw.py` reported four sampled notices
  carrying no UID, which is normal for Konkurse or Arbeitszeitbewilligungen
  and impossible for a Handelsregister entry. `sample.py` now aborts when a
  headline starts with anything but Neueintragung, Mutation or Löschung —
  a test that partitions the 405 records exactly as the rubric label does.
  Cost: the corrected frame dropped from 380 to 327 eligible, the resample
  kept only 45 of the 92 pastes, and 47 had to be redone. Lesson: the two
  checks that existed (record count against the site's own counter, page
  language) both passed on a listing that was wrong, because neither looked
  at what the records *were*. A frame check has to test the content, not
  just the size and shape of the capture.
- **Browser translation.** One document was captured through automatic
  Spanish translation and had to be recaptured. Source text must be German.
- **Divergent environments.** The agent ran tests in the global Python while
  I ran the script in the venv, producing an apparent regression that did
  not exist. Root cause: reading output mid-write, plus stale `__pycache__`.
- **A plausible hypothesis is not a verified one.** Three rounds were spent
  on a circular-test theory that two diagnostic commands disproved
  immediately. Applies directly to failure analysis in the benchmark:
  inspect real failures before writing an explanation.
- **Prefill anchored to the wrong token.** legal_form and the company
  name were extracted from the "(SHAB Nr. …, Publ. …)" parenthesis, which
  does not exist on Neueintragung (it reads "(Neueintragung)"). Silently
  null on ~12% of the corpus until 0012.
- **Seat vs authority conflict.** prefill derives seat_canton from the
  Kontaktstelle, but on inter-cantonal moves the authority is the NEW
  canton while SCHEMA.md defines sede_* as the pre-act seat. Found at 0021.
  (`sede_*` was renamed to `seat_*` in the post-v1.0 field rename.)
- **`src/migrate_v1.py` deleted.** It migrated v0.2 → v1.0, so its input keys
  were the old Spanish ones; the field rename rewrote its lookup table and it
  could no longer read the format it existed to read. The corpus is already
  migrated and the schema is frozen, so it was removed rather than repaired.
- **Field swap survived validation.** At 0027, act_subtypes values ended up in
  alternative_names and validate.py passed: both are lists of strings and
  alternative_names had no controlled vocabulary. Fixed by forbidding
  act_subtypes values there.
- **A verification pass is only as good as its boundaries.** The
  Spanish→English field rename was verified clean and the suite passed,
  but word-boundary matching never sees a field name embedded inside a
  longer identifier. Twelve test function names and every wildcard
  reference in prose were still Spanish afterwards, and tests do not
  check their own names. Found only by re-sweeping without boundaries
  for an unrelated reason.
- **`extras` key language homogenised.** The registry had grown three
  languages, and six keys mixed German and Spanish inside one identifier
  (`motivo_konkurseinstellung`, `tipo_kapitalerhoehung`,
  `weitere_adressen_nueva`, `liberierung_nuevo_chf`, `zweigniederlassung_nueva`,
  `valor_nominal_chf`). Rule adopted: a German noun for the registry concept,
  English for everything else, never Spanish. Twelve keys renamed across 29
  documents (55 occurrences), plus `decision_junta_fecha` →
  `gesellschafterversammlung_date`, which no document used. Done by parsing and
  re-dumping the JSON, not by text substitution: `weitere_adressen_nueva` and
  `weitere_adressen_anterior` share a prefix, as do the `liberierung_*` pair,
  and a textual pass would have hit them twice. The map, oldest name first:

  | Before | After |
  |---|---|
  | `acciones_nuevas` / `acciones_anteriores` | `aktien_new` / `aktien_previous` |
  | `valor_nominal_chf` | `nominal_value_chf` |
  | `clases_acciones` | `share_classes` |
  | `motivo_loeschung` | `loeschung_reason` |
  | `motivo_konkurseinstellung` | `konkurseinstellung_reason` |
  | `liberierung_nuevo_chf` / `liberierung_anterior_chf` | `liberierung_new_chf` / `liberierung_previous_chf` |
  | `weitere_adressen_nueva` / `weitere_adressen_anterior` | `weitere_adressen_new` / `weitere_adressen_previous` |
  | `tipo_kapitalerhoehung` | `kapitalerhoehung_type` |
  | `zweigniederlassung_nueva` | `zweigniederlassung_new` |
  | `decision_junta_fecha` | `gesellschafterversammlung_date` |

  The per-document entries above keep the name the key carried on the day the
  document was annotated (0008, 0011, 0020, and the "Schema candidates"
  section), same as the Spanish→English field rename. Read them through this
  table.
- **A test that skips a check looks the same as one that passes it.**
  The 0016 test had a comment declining to assert legal_form. It sat
  green among 170 tests, so when 0107 failed the same way, "why does
  0016 work?" seemed like the obvious question. It had never worked.

## Schema candidates for v1.0 — resolved

All eight entered schema v1.0 and are closed. The first five became fields
on `Person` / `PersonChange`; the last three were answered without new
fields, as noted per line.

- `PersonChange.role_previous` — seen in 0004, 0006, 0019, 0023 (×4)
- `PersonChange.domicile` — seen in 0003, 0015, 0017, 0018, 0019
- `PersonChange.signature_previous` — seen in 0019
- `Person.stammanteile` — seen in 0004
- `Person.uid` — seen in 0004, 0028
- `heimatort` with multiple values — seen in 0006. No new field: several
  Heimatorte are transcribed into the single `heimatort` string as the
  source writes them.
- `weitere_adressen_*` — seen in 0008. Registered as the `extras` keys
  `weitere_adressen_new` / `weitere_adressen_previous`, not as core
  fields; still under the 5% promotion threshold at 120 documents.
- parent vs branch attribution — schema can't attribute parent-level
  changes to a branch (Zweigniederlassung) record — seen in 0016, 0022.
  Resolved as a scope decision, not a field: the record describes the
  branch, and parent-level facts go to `extras.hauptsitz`.

## Pending after v1.0

empresa_nombre_completo  → company_name_full
empresa_nombre_base      → company_name_base
sufijo_estado            → status_suffix
nombres_alternativos     → alternative_names
forma_juridica           → legal_form
sede_localidad/canton    → seat_municipality / seat_canton
direccion_*              → address_*
fecha_acto               → act_date
publicacion_anterior_*   → prior_publication_*
autoridad                → authority
canton_anterior/nuevo    → canton_previous / canton_new
capital_*                → capital_new_chf / capital_previous_chf
domicilio_*              → domicile_new / domicile_previous
personas_entrantes       → persons_added
personas_salientes       → persons_removed
personas_mutantes        → persons_changed
tipo_acto / subtipos     → act_type / act_subtypes
incierto / notas         → uncertain / notes

Done: applied across the repository. `extras` keys were left untouched at
the time and renamed separately later — see the rename note under
Method notes. The `act_subtypes` vocabulary stays German by design.