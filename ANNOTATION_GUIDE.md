# ANNOTATION_GUIDE.md

Quick reference for annotating a SHAB record. Rules live in `SCHEMA.md`;
this is the practical cheat sheet. When the two disagree, `SCHEMA.md` wins.

**The one rule above all:** if it isn't in the source text, it's `null`.
Never infer, never carry over from another document.

---

## Dates — the biggest trap

A single notice can contain four different dates. Keep them apart.

| Field | Trigger phrase in the source | If the phrase is absent |
|---|---|---|
| `act_date` | `Statutenänderung: …` · `Statutendatum: …` · `Beginn: …` · `Löschungsdatum: …` | **`null`** — never substitute another date |
| `tagesregister_date` | `Tagesregister-Nr. NNNN vom …` | always present |
| `prior_publication_date` | `Vorangehende Publikation im SHAB: Nr. N, Datum: …` | `null` |

`act_date` also takes a dated decision that constitutes the act:
`mit Entscheid … vom …` (court) · `mit Beschluss der
Gesellschafterversammlung vom …` (shareholders). Use
`extras.decision_junta_fecha` only for a resolution that predates and
underlies the act, not for the act itself.

⚠️ The date inside the parenthesis — `(SHAB Nr. 223 vom 18.11.2025, Publ. …)` —
is the **previous** publication, not this one. It duplicates
`prior_publication_*`. It is never `act_date`.

All dates as ISO `YYYY-MM-DD`. Source uses `DD.MM.YYYY`.

---

## Names

| Field | Contents | Example |
|---|---|---|
| `company_name_full` | **with** status suffix | `Zermatt Kollektiv AG in Liquidation` |
| `company_name_base` | **without** suffix, **with** legal form | `Zermatt Kollektiv AG` |
| `status_suffix` | `in Liquidation` / `in Liq.` only, else `null` | `in Liquidation` |
| `alternative_names` | parenthesised other-language names | `["… SA", "… Ltd"]` |

Watch for names containing commas: `CHALLENGE FIRST LIMITED, LONDON,
SUCCURSALE DI LUGANO`. The name ends at `, in <Town>, CHE-`, not at the
first comma.

---

## Location — four fields, two different things

**Legal seat** (from the body, `… , in <Town>, CHE-…`):
- `seat_municipality`, `seat_canton`

**Postal address** (from the header block):
- `address_care_of` (only if a `c/o` line exists), `address_street`,
  `address_postcode` (string, not number), `address_municipality`

These usually match — but not always. A relocated company can have its
legal seat in the old town and its address in the new one. Read both.

`canton_previous` / `canton_new`: **only** for moves between cantons
(look for the company being registered in one canton's register and deleted
from another's). Both `null` or both filled — never one alone.

---

## Change pairs — always both or neither

`domicile_new` / `domicile_previous` — only when the act **changes** the
address. Trigger: a `Bisher` block, or `[bisher: …]` inline. An address that
merely appears without changing goes in `address_*` only.

Same for `capital_new_chf` / `capital_previous_chf`. Trigger:
`Aktienkapital neu: … [bisher: …]`.

Capital as a JSON **number**: `189123.50`, not `"189'123.50"`.

Capital on a `Neueintragung` (`Aktienkapital: CHF X`, no `bisher` pair) goes
in `capital_new_chf`, with `capital_previous_chf` null.

`company_name_new` / `company_name_previous` — both or neither.
Trigger: `Firma neu:`. Always fill both, even though `_previous` repeats
`company_name_full`.
---

## People — three lists, pick carefully

| List | Trigger phrase |
|---|---|
| `persons_removed` | `Ausgeschiedene Personen und erloschene Unterschriften:` |
| `persons_added` | `Eingetragene Personen neu:` (genuinely new) |
| `persons_changed` | an entry under `Eingetragene Personen neu oder mutierend:` that carries its own `[bisher: …]` |

⚠️ That heading covers BOTH new and changed people. Entries are separated
by `;` — split on the semicolon and judge each one independently. An entry
without `[bisher: …]` is a new person, not a changed one.

The third is the one people get wrong. `Navarro Carpentieri, Iker, von Riehen
… [bisher: Navarro, Iker, amerikanischer Staatsangehöriger]` is **one person
whose attributes changed**, not one leaving and one arriving.

Each person is an object, never a string:

```json
{ "name": "Beswick, Graham", "nationality": "britisch",
  "heimatort": null, "domicile": "Zermatt",
  "role": "einziges Mitglied", "signature": "Kollektivunterschrift zu zweien" }
```

- Keep `name` in source order: `Surname, Firstname`.
- `von <Ort>` → Swiss citizen; that town is `heimatort`, `nationality` stays `null`.
- `<Land> Staatsangehörige(r)` → foreigner; `nationality` filled, `heimatort` `null`.
- A shift from the second form to the first means naturalisation. Note it.
- `signature` belongs to the person, not to `extras`.

---

## `act_subtypes` — read the verbs, not the nouns

Multi-label. Assign every one that applies. Controlled vocabulary only.

| Value | Trigger |
|---|---|
| `statutenaenderung` | `Statutenänderung:` · `Urkundenänderung:` (foundations) |
| `kapitalerhoehung` | capital goes up |
| `kapitalherabsetzung` | capital goes down |
| `organaenderung` | **any** person enters, leaves or changes |
| `sitzverlegung` | `Sitz neu: <Ort>` — the legal seat moves to another municipality. A `Domizil neu:` alone is an address change, NOT a Sitzverlegung |
| `kantonswechsel` | the move crosses a canton border |
| `firmenaenderung` | `neu <new company name>` |
| `zweckaenderung` | `Zweck neu:` |
| `rechtsformaenderung` | `Rechtsform … neu: … [bisher: …]` — the legal form itself changes |
| `liquidationseroeffnung` | company enters liquidation |
| `liquidation_beendet` | `Die Liquidation ist beendet` |
| `revisionsstelle` | auditor appointed or removed |
| `fusion` | merger |
| `konkurseinstellung` | `Das Konkursverfahren ist … eingestellt worden` — bankruptcy proceedings discontinued, typically for lack of assets |
| `konkurseroeffnung` | `wurde … der Konkurs eröffnet` — bankruptcy opened by court decision |

Most common mistake: forgetting `organaenderung` when someone leaves.

`act_subtypes` is always `[]` on `neueintragung` and `loeschung`. Nothing changes
on a registration or a deletion — the act type says it all.

---

## `extras`, `uncertain`, `notes` — what goes where

| Field | Use for |
|---|---|
| `extras` | real data with no field of its own — share counts, nominal value, resolution dates. Reuse existing keys, check `SCHEMA.md` first. |
| `uncertain` | field **names** you weren't sure about, e.g. `["act_subtypes"]` |
| `notes` | prose: ambiguities, oddities, why you decided something |

`extras` is an object: `{"clave": "valor"}`, never a bare string.

`extras.zweck` is truncated to the first sentence or ~200 characters, with
`…`. It is scored by prefix match, not exact equality.

`extras` vs `notes`: if it is a datum that will recur across documents with
comparable values, it goes in `extras` under a registered key. If it is your
observation about this document, it goes in `notes`.

`_verified`: `true` only after you have read the whole document.

---

## Per-document routine

1. Verify the ~17 prefilled fields against the text — **before** filling anything in
2. Fill the judgement fields
3. `_verified: true`
4. `python src/validate.py data/exploratory/NNNN.json`
5. One line in `annotation_log.md`: time, new fields, doubts

Stuck on a field? Put its name in `uncertain`, a sentence in `notes`, and move
on. Don't spend twenty minutes on one case.
