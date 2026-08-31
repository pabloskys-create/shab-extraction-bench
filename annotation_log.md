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
0019 | 15 | 0 | Firmenänderung; two Reber brothers gain sole signature from no registered role — PersonChange gaps (role_previous, domicile) recur together, and signature changes with no anterior field either; source data error in the Bisher block ("ober Bärhegen null")
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
Estimated cost of the remaining 172 documents: ~21.5 hours (172 × 7.5 min ÷ 60).
Exclusions from the sampled frame: 0 of 25 (0%).
One additional document (0000, French FOSC) was excluded during the
warm-up phase, before the sampling frame was defined.

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

## Schema candidates for v1.0

- `PersonChange.role_previous` — seen in 0004, 0006, 0019, 0023 (×4)
- `PersonChange.domicile` — seen in 0003, 0015, 0017, 0018, 0019
- `PersonChange.signature_previous` — seen in 0019
- `Person.stammanteile` — seen in 0004
- `Person.uid` — seen in 0004, 0028
- `heimatort` with multiple values — seen in 0006
- `weitere_adressen_*` — seen in 0008
- atribución matriz vs sucursal — schema can't attribute parent-level
  changes to a branch (Zweigniederlassung) record — seen in 0016, 0022

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

Completado: aplicado en todo el repositorio (claves solamente; `extras` y el vocabulario de `act_subtypes` siguen en alemán).
