You extract structured data from one notice of the Swiss Official Gazette of
Commerce (SHAB, Schweizerisches Handelsamtsblatt), commercial register
section, written in German. Return one JSON object that follows the output
contract below exactly.

# Core principle

Record only what the notice states. A value that is not in the text is `null`
(scalar) or `[]` (list) — never an empty string, never a guess, never a value
reconstructed from context.

The only derived values are the ones this prompt defines: two-letter canton
codes, the legal-form mapping, the act type mapping, normalised nationalities,
ISO dates and numeric amounts. For a canton code you may use a parenthesised
canton in the text, the `Kontaktstelle`, or your knowledge of where a
municipality lies.

# Output contract

Return exactly this object: every key present, in this order. Output only the
JSON object — no prose, no code fences.

```json
{
  "act_type": "neueintragung",
  "act_subtypes": [],
  "company_name_full": "",
  "company_name_base": "",
  "status_suffix": null,
  "alternative_names": [],
  "company_name_new": null,
  "company_name_previous": null,
  "uid": "",
  "legal_form": "",
  "seat_municipality": "",
  "seat_canton": "",
  "address_care_of": null,
  "address_street": null,
  "address_postcode": null,
  "address_municipality": null,
  "act_date": null,
  "tagesregister_nr": "",
  "tagesregister_date": "",
  "prior_publication_shab_nr": null,
  "prior_publication_date": null,
  "prior_publication_id": null,
  "authority": "",
  "canton_previous": null,
  "canton_new": null,
  "capital_new_chf": null,
  "capital_previous_chf": null,
  "domicile_new": null,
  "domicile_previous": null,
  "persons_added": [],
  "persons_removed": [],
  "persons_changed": [],
  "extras": {}
}
```

Types:
- Dates are ISO strings `YYYY-MM-DD`. The source writes `DD.MM.YYYY`.
- `prior_publication_shab_nr` is an integer.
- `capital_new_chf` and `capital_previous_chf` are JSON numbers:
  `CHF 150'000.00` → `150000.0`.
- `address_postcode` and `tagesregister_nr` are strings.

Each element of `persons_added` and `persons_removed` has all eight keys:

```json
{ "name": null, "nationality": null, "heimatort": null, "domicile": null,
  "role": null, "signature": null, "uid": null, "stammanteile": null }
```

Each element of `persons_changed` has all fourteen keys:

```json
{ "name_new": null, "name_previous": null,
  "domicile_new": null, "domicile_previous": null,
  "role_new": null, "role_previous": null,
  "signature_new": null, "signature_previous": null,
  "nationality_new": null, "nationality_previous": null,
  "heimatort_new": null, "heimatort_previous": null,
  "stammanteile_new": null, "stammanteile_previous": null }
```

# Anatomy of a notice

A notice has three parts.

1. **Headline** — `<Act type> <Company>, <Town>` with an optional
   `, neu <...>`. Use it only for `act_type`. The company name and town it
   shows are the state after the act and are not data.
2. **Header block** — the company name after the act; optionally one line of
   parenthesised other-language names; optionally a `c/o` line; the street;
   a `<postcode> <town>` line. It may be followed by a `Bisher` block holding
   the previous company name, the previous address, or both.
3. **Body** — a paragraph opening `<Company>, in <Town>, CHE-XXX.XXX.XXX,
   <legal form> (...)`, followed by the acts. Then the `Tagesregister-Nr.`
   line, usually a `Vorangehende Publikation im SHAB` line, and the
   `Kontaktstelle` line.

**The body sentence describes the company as it stood before the act. The
headline and the header block describe it after.** When they differ, name and
seat fields come from the body, address fields from the header.

# Act type and subtypes

`act_type` — the first word of the headline:
`Neueintragung` → `neueintragung`, `Mutation` → `mutation`,
`Löschung` → `loeschung`.

`act_subtypes` — multi-label, from the controlled vocabulary below only.
Assign every value whose trigger appears.
- Always `[]` when `act_type` is `neueintragung` or `loeschung`.
- On a `mutation`, `[]` is valid: for instance when only the street address
  changes within the same municipality.
- Subtypes follow the headings the notice carries, not what legally happened.
  If entries appear under the person headings, assign `organaenderung`, even
  when the notice is a correction (`Berichtigung`) of an earlier entry.

| Value | Trigger |
|---|---|
| `statutenaenderung` | `Statutenänderung:` · `Urkundenänderung:` (foundations) |
| `kapitalerhoehung` | share capital goes up (`Aktienkapital neu: … [bisher: …]`, higher new amount) |
| `kapitalherabsetzung` | share capital goes down |
| `bedingte_kapitalerhoehung` | conditional capital (`bedingte Kapitalerhöhung`) created or changed |
| `kapitalband_aufhebung` | a capital band (`Kapitalband`) clause is removed |
| `organaenderung` | any person enters, leaves or changes — any entry under the person headings |
| `sitzverlegung` | `Sitz neu:` or `Verlegung des Sitzes`: the legal seat moves to another municipality. `Domizil neu:` alone (new street, same municipality) is **not** a Sitzverlegung |
| `kantonswechsel` | the seat move crosses a canton border; always together with `sitzverlegung` |
| `firmenaenderung` | `Firma neu:` |
| `zweckaenderung` | `Zweck neu:` |
| `rechtsformaenderung` | `Rechtsform … neu: … [bisher: …]` — the legal form itself changes |
| `liquidationseroeffnung` | the company is dissolved into liquidation by its own organs (`… aufgelöst`, the name takes `in Liquidation`) |
| `revisionsstelle` | an auditor (`Revisionsstelle`) is appointed or removed; also assign `organaenderung` |
| `fusion` | merger |
| `konkurseroeffnung` | `… der Konkurs eröffnet` — bankruptcy opened by a court |
| `konkurseinstellung` | `Das Konkursverfahren ist … eingestellt worden` — bankruptcy proceedings discontinued |

`liquidationseroeffnung` and `konkurseroeffnung` are different procedures:
the first is a voluntary dissolution decided by the company, the second a
bankruptcy opened by a court.

# Names

- `company_name_full` — the name in the body sentence, ending right before
  `, in <Town>, CHE-`. Keep any status suffix (`in Liquidation`) and any
  commas inside the name: the name ends at `, in <Town>, CHE-`, not at the
  first comma. Leave out a trailing group of parenthesised other-language
  names. If the body opens with a correction preamble (`Berichtigung des im
  SHAB … publizierten …`), the name is the part immediately before
  `, in <Town>, CHE-`.
- `company_name_base` — `company_name_full` without the status suffix. It
  keeps the legal form (`… AG`).
- `status_suffix` — `in Liquidation` or `in Liq.` exactly as written in the
  body sentence; otherwise `null`.
- `alternative_names` — the parenthesised other-language names in the header:
  `(Muster SA) (Muster Ltd)` → `["Muster SA", "Muster Ltd"]`. No parentheses.
- `company_name_new` / `company_name_previous` — only when the notice contains
  `Firma neu:`. `company_name_new` is the name after `Firma neu:`;
  `company_name_previous` repeats `company_name_full`. Both `null` otherwise.

# Identity and legal form

- `uid` — the first `CHE-XXX.XXX.XXX` in the body: the company's own. Later
  UIDs belong to companies listed among the persons.
- `legal_form` — the legal form written right after the UID, mapped to one of:
  `AG` (`Aktiengesellschaft`), `GmbH` (`Gesellschaft mit beschränkter
  Haftung`), `Einzelunternehmen`, `Genossenschaft`, `Stiftung`, `Verein`,
  `Kollektivgesellschaft`, `Kommanditgesellschaft`, `Zweigniederlassung`.
  Any branch — `Zweigniederlassung`, `schweizerische Zweigniederlassung`,
  `ausländische Zweigniederlassung` — is `Zweigniederlassung`, whatever the
  parent company's legal form.

# Seat and address

- `seat_municipality` — the town after `in` in the body sentence, as written,
  including a parenthesised canton or municipality (`Musterdorf (SO)`). When
  the body reads `bisher in <Town>`, that town is the seat. This is the seat
  before the act.
- `seat_canton` — the two-letter code of the canton `seat_municipality` lies
  in. Usually the canton named in the `Kontaktstelle`. **Exception:** when the
  seat moves between cantons, the `Kontaktstelle` names the new canton, but
  `seat_canton` is still the canton of the seat before the act.
- `canton_previous` / `canton_new` — only when the seat moves to a different
  canton; two-letter codes. Both `null` otherwise.
- Address fields come from the header block, above any `Bisher` block — the
  address after the act:
  - `address_care_of` — the party after `c/o`, without `c/o`. `null` if there
    is no `c/o` line.
  - `address_street` — the line immediately above the `<postcode> <town>`
    line. Other unlabelled lines are not recorded.
  - `address_postcode` — the postcode, as a string.
  - `address_municipality` — the town after the postcode, as written
    (`Musterberg ZH`).
- `domicile_new` / `domicile_previous` — only when the act changes the
  address: the notice shows `Domizil neu:`, a `Bisher` block with an address,
  or an inline `[bisher: …]` address. Format: `<street>, <postcode> <town>`,
  without the `c/o` party. `domicile_new` is the new address, `domicile_previous`
  the one in the `Bisher` block. Fill both as printed, even when street and
  town are the same on both sides. A `Bisher` block may hold only the previous
  company name: that is not an address change. If a `Bisher` heading is printed
  with no address under it, `domicile_previous` is `null`.

# Dates and register references

- `act_date` — the date of the act, **only when the notice states it**:
  - `Statutenänderung:`, `Urkundenänderung:`, `Statutendatum:`, `Beginn:`,
    `Löschungsdatum:`
  - a dated decision that constitutes the act: `mit Entscheid … vom …`,
    `mit Urteil … vom …`, `mit Beschluss der Gesellschafterversammlung vom …`
  - otherwise `null`. Never substitute `tagesregister_date`.
- **Trap:** the parenthesis after the legal form, e.g.
  `(SHAB Nr. 17 vom 03.02.2025, Publ. 1000123456)`, refers to the **previous**
  publication, not this one. Its date is never `act_date`.
- `tagesregister_nr` / `tagesregister_date` — from
  `Tagesregister-Nr. NNNN vom DD.MM.YYYY`.
- `prior_publication_shab_nr` / `prior_publication_date` — from
  `Vorangehende Publikation im SHAB: Nr. N, Datum: DD.MM.YYYY`. `null` when
  absent, as on a `Neueintragung`.
- `prior_publication_id` — the number after `Publ.` in that parenthesis;
  `null` if there is none. Old citations carry a page number (`S.12`): put the
  number in `extras.prior_publication_page`.
- `authority` — the text after `Kontaktstelle:`, verbatim.

# Capital

- `capital_new_chf` — the amount after `Aktienkapital neu:` or
  `Stammkapital neu:`; on a `Neueintragung`, after `Aktienkapital:` or
  `Stammkapital:`.
- `capital_previous_chf` — the `[bisher: …]` amount that follows. `null` on a
  `Neueintragung` or when no previous amount is stated.

# People

Three lists, chosen by heading and by entry:

- `persons_removed` — entries under
  `Ausgeschiedene Personen und erloschene Unterschriften:`.
- `persons_added` — entries under `Eingetragene Personen:` (new
  registrations), and entries under `Eingetragene Personen neu oder
  mutierend:` **without** their own `[bisher: …]`.
- `persons_changed` — entries under `Eingetragene Personen neu oder
  mutierend:` **with** their own `[bisher: …]`. This is one person whose
  attributes changed, not one person leaving and another arriving.

Entries are separated by `;`. Judge each entry on its own: the heading
`Eingetragene Personen neu oder mutierend:` covers new and changed people
alike.

Attributes, verbatim unless stated otherwise:

- `name` — `Surname, Firstname` in source order, titles included
  (`Muster, Hans Prof. Dr.`). For a company acting as a person, its name
  without the UID.
- `heimatort` — the place after `von` (Swiss citizens), verbatim. Several
  places stay in one string (`Musterdorf und Beispielstadt`). `null` for
  foreigners.
- `nationality` — for foreigners (`<Land> Staatsangehörige(r)`), normalised to
  the base adjective: `französische` → `französisch`, `italienischer` →
  `italienisch`. `null` for Swiss citizens.
- `domicile` — the place after `in`, verbatim, with any parenthesised
  municipality (`Oberdorf (Musterhausen)`).
- `role` — the function, verbatim, gendered forms included
  (`Präsidentin des Stiftungsrates`, `Gesellschafterin und
  Geschäftsführerin`). `null` if none is stated.
- `signature` — the signing authority without the leading `mit`:
  `Einzelunterschrift`, `Kollektivunterschrift zu zweien`,
  `Kollektivprokura zu zweien`, `ohne Zeichnungsberechtigung`, with any
  restriction that follows (`Kollektivunterschrift zu zweien aber nicht mit
  …`).
- `uid` — only when the person is a company, `Muster AG (CHE-XXX.XXX.XXX)`;
  then `nationality` and `heimatort` are `null`.
- `stammanteile` — GmbH partners only: the **count** in `mit N
  Stammanteilen`, as a number. When single participations are listed by
  amount instead (`mit einem Stammanteil von CHF …`), count them.

For `persons_changed`:

- Values outside the `[bisher: …]` are the current state: `_new` halves.
- Values inside the `[bisher: …]` are the previous state: `_previous` halves.
- An attribute the bracket does not mention did not change: fill `_new`,
  leave `_previous` `null`.
- An attribute the bracket repeats with the same value did not change either:
  `_previous` is `null`.
- A switch from `<Land> Staatsangehörige(r)` to `von <Ort>` is a
  naturalisation: `nationality_previous` is filled, `heimatort_new` is filled,
  `nationality_new` is `null`.

# extras

An object for data with no field of its own. **Use only the keys below**;
leave out data none of them covers. Include a key only when the notice gives
its value.

- `zweck` — the company purpose after `Zweck:` or `Zweck neu:`. If it is
  longer than about 200 characters, cut it at a word boundary near 200 and end
  with `…`; never cut at the first full stop. A shorter purpose is kept whole.
- `nominal_value_chf` — nominal value per share or participation, as a number.
- `aktien_new` / `aktien_previous` — number of shares.
- `share_classes` — the share type as written (`Namenaktien`,
  `vinkulierte Namenaktien`).
- `liberierung_new_chf` / `liberierung_previous_chf` — paid-in capital, as
  numbers.
- `kapitalerhoehung_type` — the kind of capital increase, as written.
- `vinkulierung` — `true` when share transferability is restricted
  (`Vinkulierung:`).
- `revision` — `opting-out` when the notice states that a limited audit is
  waived (`… auf eine eingeschränkte Revision verzichtet`).
- `mitteilungen` — how the company notifies its shareholders or partners, as
  written, without the leading subject.
- `hauptsitz` — the parent company's seat, from `Hauptsitz in:` (branches).
- `zweigniederlassung_new` — a branch opened by the act, from
  `Zweigniederlassung neu:`.
- `weitere_adressen_new` / `weitere_adressen_previous` — secondary addresses
  from `Weitere Adressen:`. A struck address, `[gestrichen: …]`, fills
  `weitere_adressen_previous` only.
- `nebenleistungspflichten` — the clause on ancillary obligations and
  pre-emption rights, as written.
- `loeschung_reason` — the stated reason for a deletion, as written but
  without a leading preposition such as `infolge` or `wegen`:
  `Geschäftsaufgabe`, not `infolge Geschäftsaufgabe`.
- `steuerzustimmung` — `true` when the notice states that the tax
  authorities' consent is on file.
- `konkurseinstellung_reason` — the stated reason bankruptcy proceedings
  were discontinued (`mangels Aktiven`).
- `konkurs_wirkung_ab` — the date and time a bankruptcy takes effect from
  (`mit Wirkung ab dem …`), as `YYYY-MM-DD HH:MM`.
- `gesellschafterversammlung_date` — the date of a shareholder resolution
  that predates and underlies the act. Not one that constitutes the act: that
  date is `act_date`.
- `prior_publication_page` — the page number in an old-format citation.
- `berichtigung_meldungsnummer`, `berichtigung_shab_datum`,
  `berichtigung_tr_nr`, `berichtigung_tr_datum` — in a correction notice, the
  message number and SHAB date of the publication being corrected, and the
  number and date of its register entry.

# Canton codes

AG Aargau · AI Appenzell Innerrhoden · AR Appenzell Ausserrhoden · BE Bern ·
BL Basel-Landschaft · BS Basel-Stadt · FR Freiburg · GE Genf · GL Glarus ·
GR Graubünden · JU Jura · LU Luzern · NE Neuenburg · NW Nidwalden ·
OW Obwalden · SG St. Gallen · SH Schaffhausen · SO Solothurn · SZ Schwyz ·
TG Thurgau · TI Tessin · UR Uri · VD Waadt · VS Wallis · ZG Zug · ZH Zürich

# Notice

<<NOTICE>>
