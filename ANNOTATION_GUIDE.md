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
| `fecha_acto` | `Statutenänderung: …` · `Statutendatum: …` · `Beginn: …` · `Löschungsdatum: …` | **`null`** — never substitute another date |
| `tagesregister_fecha` | `Tagesregister-Nr. NNNN vom …` | always present |
| `publicacion_anterior_fecha` | `Vorangehende Publikation im SHAB: Nr. N, Datum: …` | `null` |

⚠️ The date inside the parenthesis — `(SHAB Nr. 223 vom 18.11.2025, Publ. …)` —
is the **previous** publication, not this one. It duplicates
`publicacion_anterior_*`. It is never `fecha_acto`.

All dates as ISO `YYYY-MM-DD`. Source uses `DD.MM.YYYY`.

---

## Names

| Field | Contents | Example |
|---|---|---|
| `empresa_nombre_completo` | **with** status suffix | `Zermatt Kollektiv AG in Liquidation` |
| `empresa_nombre_base` | **without** suffix, **with** legal form | `Zermatt Kollektiv AG` |
| `sufijo_estado` | `in Liquidation` / `in Liq.` only, else `null` | `in Liquidation` |
| `nombres_alternativos` | parenthesised other-language names | `["… SA", "… Ltd"]` |

Watch for names containing commas: `CHALLENGE FIRST LIMITED, LONDON,
SUCCURSALE DI LUGANO`. The name ends at `, in <Town>, CHE-`, not at the
first comma.

---

## Location — four fields, two different things

**Legal seat** (from the body, `… , in <Town>, CHE-…`):
- `sede_localidad`, `sede_canton`

**Postal address** (from the header block):
- `direccion_co` (only if a `c/o` line exists), `direccion_calle`,
  `direccion_cp` (string, not number), `direccion_localidad`

These usually match — but not always. A relocated company can have its
legal seat in the old town and its address in the new one. Read both.

`canton_anterior` / `canton_nuevo`: **only** for moves between cantons
(look for the company being registered in one canton's register and deleted
from another's). Both `null` or both filled — never one alone.

---

## Change pairs — always both or neither

`domicilio_nuevo` / `domicilio_anterior` — only when the act **changes** the
address. Trigger: a `Bisher` block, or `[bisher: …]` inline. An address that
merely appears without changing goes in `direccion_*` only.

Same for `capital_nuevo_chf` / `capital_anterior_chf`. Trigger:
`Aktienkapital neu: … [bisher: …]`.

Capital as a JSON **number**: `189123.50`, not `"189'123.50"`.

Capital on a `Neueintragung` (`Aktienkapital: CHF X`, no `bisher` pair) goes
in `capital_nuevo_chf`, with `capital_anterior_chf` null.

---

## People — three lists, pick carefully

| List | Trigger phrase |
|---|---|
| `personas_salientes` | `Ausgeschiedene Personen und erloschene Unterschriften:` |
| `personas_entrantes` | `Eingetragene Personen neu:` (genuinely new) |
| `personas_mutantes` | an entry under `Eingetragene Personen neu oder mutierend:` that carries its own `[bisher: …]` |

⚠️ That heading covers BOTH new and changed people. Entries are separated
by `;` — split on the semicolon and judge each one independently. An entry
without `[bisher: …]` is a new person, not a changed one.

The third is the one people get wrong. `Navarro Carpentieri, Iker, von Riehen
… [bisher: Navarro, Iker, amerikanischer Staatsangehöriger]` is **one person
whose attributes changed**, not one leaving and one arriving.

Each person is an object, never a string:

```json
{ "nombre": "Beswick, Graham", "nacionalidad": "britisch",
  "heimatort": null, "domicilio": "Zermatt",
  "cargo": "einziges Mitglied", "firma": "Kollektivunterschrift zu zweien" }
```

- Keep `nombre` in source order: `Surname, Firstname`.
- `von <Ort>` → Swiss citizen; that town is `heimatort`, `nacionalidad` stays `null`.
- `<Land> Staatsangehörige(r)` → foreigner; `nacionalidad` filled, `heimatort` `null`.
- A shift from the second form to the first means naturalisation. Note it.
- `firma` belongs to the person, not to `extras`.

---

## `subtipos` — read the verbs, not the nouns

Multi-label. Assign every one that applies. Controlled vocabulary only.

| Value | Trigger |
|---|---|
| `statutenaenderung` | `Statutenänderung:` |
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

Most common mistake: forgetting `organaenderung` when someone leaves.

`subtipos` is always `[]` on `neueintragung` and `loeschung`. Nothing changes
on a registration or a deletion — the act type says it all.

---

## `extras`, `incierto`, `notas` — what goes where

| Field | Use for |
|---|---|
| `extras` | real data with no field of its own — share counts, nominal value, resolution dates. Reuse existing keys, check `SCHEMA.md` first. |
| `incierto` | field **names** you weren't sure about, e.g. `["subtipos"]` |
| `notas` | prose: ambiguities, oddities, why you decided something |

`extras` is an object: `{"clave": "valor"}`, never a bare string.

`extras.zweck` is truncated to the first sentence or ~200 characters, with
`…`. It is scored by prefix match, not exact equality.

`extras` vs `notas`: if it is a datum that will recur across documents with
comparable values, it goes in `extras` under a registered key. If it is your
observation about this document, it goes in `notas`.

`_verified`: `true` only after you have read the whole document.

---

## Per-document routine

1. Verify the ~17 prefilled fields against the text — **before** filling anything in
2. Fill the judgement fields
3. `_verified: true`
4. `python src/validate.py data/exploratory/NNNN.json`
5. One line in `annotation_log.md`: time, new fields, doubts

Stuck on a field? Put its name in `incierto`, a sentence in `notas`, and move
on. Don't spend twenty minutes on one case.
