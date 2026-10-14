"""List gold records that may break a rule of the frozen prompt.

An analysis aid for re-checking the gold standard by hand; it changes
neither the scoring nor the harness. For one split it reads every gold
record (read-only) and its source notice, applies the checks below — each
a rule of prompts/extraction_v1.md turned into a string test — and writes
one row per possible violation to `results/gold_audit/<split>/rule_sweep.csv`.

The checks are deliberately literal: a row is a case to look at, not an
error. Each row carries the rule, the field, the gold value, a fragment of
the notice as evidence, a `note` saying why it was flagged, and an empty
`verdict` to be filled in by hand.

    prior_publication_page  an old citation "SHAB Nr. N vom DD.MM.YYYY, S.<n>"
                            and extras.prior_publication_page is not <n>
    domicile                the notice shows an address change ("Domizil neu:",
                            a header "Bisher" block with an address, or the
                            company's own "[bisher: <address>]") and
                            domicile_new is null — or domicile_previous is
                            null although a previous address is printed; or
                            either is filled with none of those triggers
    nationality             a person's nationality not in its base form
                            (ends in "-isch" or "deutsch" plus e, er, en or es)
    loeschung_reason        extras.loeschung_reason does not occur in the notice
    mitteilungen            extras.mitteilungen starts with "Mitteilungen"
    sitzverlegung           in act_subtypes without "Sitz neu:" / "Verlegung des
                            Sitzes" in the notice, or the other way round
    revision                the notice waives a limited audit and
                            extras.revision is not "opting-out"
    nominal_value_chf       "zu je CHF", or "zu CHF <amount>" after Aktien /
                            Stammanteil, and extras.nominal_value_chf is null
    persons_changed         a changed person is "Staatsangehörige(r)" outside
                            their "[bisher: …]" and nationality_new is null

Fields listed in a gold record's `uncertain` are skipped, as in scoring.
An existing CSV is never overwritten unless --force is passed (it may hold
verdicts). Nothing is ever written under data/.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src import evaluate

REPO_ROOT = evaluate.REPO_ROOT
DATA_DIR = REPO_ROOT / "data"
RAW_DIRS = {
    "dev": DATA_DIR / "raw",
    "holdout": DATA_DIR / "holdout" / "raw",
}
DEFAULT_OUT_DIR = evaluate.DEFAULT_RESULTS_DIR / "gold_audit"
OUTPUT_CSV = "rule_sweep.csv"
COLUMNS = ["doc_id", "rule", "field", "gold_value", "evidence", "verdict", "note"]

EVIDENCE_RADIUS = 120
_WHITESPACE_RE = re.compile(r"\s+")
_GOLD_NAME_RE = re.compile(r"\d{4}\.json")

_OLD_CITATION_RE = re.compile(r"SHAB Nr\.\s*\d+\s+vom\s+\d{2}\.\d{2}\.\d{4},\s*S\.\s*(\d+)")
_POSTCODE_LINE_RE = re.compile(r"^\d{4}\s+\S")
_DOMIZIL_NEU_RE = re.compile(r"Domizil neu:")
# An address inside a bracket: a four-digit postcode followed by a town.
_BISHER_ADDRESS_RE = re.compile(r"\[bisher:[^\]]*\b\d{4}\s+[A-ZÄÖÜ][^\]]*\]")
# The label an inline [bisher: …] hangs off. Under these it is not the
# company's own address.
_LABEL_RE = re.compile(r"(?:^|[.;]\s)([A-ZÄÖÜ][\w\- ]{2,60}?):")
_NOT_COMPANY_ADDRESS_LABELS = (
    "Weitere Adressen",
    "Eingetragene Personen",
    "Ausgeschiedene Personen",
    "Zweigniederlassung",
    "Hauptsitz",
    "Firma Hauptsitz",
)
_SITZVERLEGUNG_RE = re.compile(r"\bSitz neu:|Verlegung des Sitzes")
# "-isch" adjectives plus "deutsch", the one nationality without "-isch".
_NATIONALITY_INFLECTED_RE = re.compile(r"(?:isch|deutsch)(?:e|er|en|es)$")
_OPTING_OUT_RE = re.compile(
    r"eingeschränkte Revision verzichtet|verzichtet\b.{0,200}?auf eine eingeschränkte Revision",
    re.DOTALL,
)
_NOMINAL_RE = re.compile(r"zu je CHF|(?:[Aa]ktien|Stammanteil)[^.;\]]{0,80}?zu CHF\s*\d")
_STAATSANGEHOERIG_RE = re.compile(r"Staatsangehörig")
_BRACKET_RE = re.compile(r"\[[^\]]*\]")
_MUTATING_HEADING = "Eingetragene Personen neu oder mutierend:"
_PERSON_HEADINGS_RE = re.compile(r"Eingetragene Personen[^:]*:|Ausgeschiedene Personen[^:]*:")


@dataclass
class Finding:
    doc_id: str
    rule: str
    field: str
    gold_value: Any
    evidence: str
    note: str

    def row(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "rule": self.rule,
            "field": self.field,
            "gold_value": json.dumps(self.gold_value, ensure_ascii=False),
            "evidence": self.evidence,
            "verdict": "",
            "note": self.note,
        }


class SweepError(Exception):
    """A gold record has no source notice."""


# --- notice structure ---


@dataclass
class Notice:
    text: str
    header: list[str]  # header lines after the headline, before the first blank line
    bisher: list[str]  # lines of the header "Bisher" block, [] if none
    body: str  # everything after the header


def split_notice(text: str) -> Notice:
    lines = text.splitlines()
    header: list[str] = []
    rest_start = len(lines)
    for i, line in enumerate(lines[1:], start=1):
        if not line.strip():
            rest_start = i
            break
        header.append(line.strip())
    bisher = header[header.index("Bisher") + 1 :] if "Bisher" in header else []
    return Notice(text=text, header=header, bisher=bisher, body="\n".join(lines[rest_start:]))


def evidence(text: str, start: int, end: int) -> str:
    window = text[max(0, start - EVIDENCE_RADIUS) : end + EVIDENCE_RADIUS]
    return _WHITESPACE_RE.sub(" ", window).strip()


def evidence_for(text: str, pattern: re.Pattern[str] | str) -> str:
    """The fragment around the first match of `pattern` (a regex or a
    literal string), "" if it does not occur."""
    if isinstance(pattern, str):
        start = text.find(pattern)
        return evidence(text, start, start + len(pattern)) if start >= 0 else ""
    match = pattern.search(text)
    return evidence(text, match.start(), match.end()) if match else ""


def _collapse(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text).strip()


# --- the checks; each takes (doc_id, gold, notice) and returns findings ---


def check_prior_publication_page(doc_id: str, gold: dict, notice: Notice) -> list[Finding]:
    match = _OLD_CITATION_RE.search(notice.text)
    if not match:
        return []
    ok, page = _extras_value(gold, "prior_publication_page")
    if not ok or (page is not None and str(page).strip() == match.group(1)):
        return []
    return [
        Finding(
            doc_id,
            "prior_publication_page",
            "extras.prior_publication_page",
            page,
            evidence(notice.text, match.start(), match.end()),
            f"old citation gives S.{match.group(1)}",
        )
    ]


def inline_company_bisher_address(body: str) -> re.Match[str] | None:
    """The first "[bisher: <address>]" that belongs to the company itself:
    not under Weitere Adressen, the person headings, or a Hauptsitz label."""
    for match in _BISHER_ADDRESS_RE.finditer(body):
        labels = _LABEL_RE.findall(body[: match.start()])
        label = labels[-1] if labels else ""
        if not label.startswith(_NOT_COMPANY_ADDRESS_LABELS):
            return match
    return None


def check_domicile(doc_id: str, gold: dict, notice: Notice) -> list[Finding]:
    domizil_neu = _DOMIZIL_NEU_RE.search(notice.body)
    bisher_address = any(_POSTCODE_LINE_RE.match(line) for line in notice.bisher)
    inline = inline_company_bisher_address(notice.body)
    triggers = []
    if domizil_neu:
        triggers.append(("Domizil neu:", evidence(notice.body, domizil_neu.start(), domizil_neu.end())))
    if bisher_address:
        triggers.append(("Bisher block with an address", " | ".join(["Bisher", *notice.bisher])))
    if inline:
        triggers.append(("inline [bisher: <address>]", evidence(notice.body, inline.start(), inline.end())))

    findings = []
    uncertain = set(gold.get("uncertain") or [])
    for name in ("domicile_new", "domicile_previous"):
        if name in uncertain:
            continue
        value = gold.get(name)
        if triggers and value is None:
            # A "Domizil neu:" alone prints no previous address: a null
            # domicile_previous is then what the prompt asks for.
            if name == "domicile_previous" and not (bisher_address or inline):
                continue
            kinds = ", ".join(kind for kind, _ in triggers)
            findings.append(Finding(doc_id, "domicile", name, value, triggers[0][1], f"null despite: {kinds}"))
        elif not triggers and value is not None:
            findings.append(
                Finding(
                    doc_id,
                    "domicile",
                    name,
                    value,
                    " | ".join(notice.header),
                    "filled without Domizil neu:, a Bisher address or an inline [bisher: <address>]",
                )
            )
    return findings


def _people(gold: dict) -> list[tuple[str, str, str, Any]]:
    """(list field, person label, attribute, value) for every nationality
    attribute of every gold person."""
    out = []
    for list_field, attributes in (
        ("persons_added", ("nationality",)),
        ("persons_removed", ("nationality",)),
        ("persons_changed", ("nationality_new", "nationality_previous")),
    ):
        name_key = evaluate.PERSON_NAME_KEYS[list_field]
        for person in gold.get(list_field) or []:
            for attribute in attributes:
                out.append((list_field, evaluate._person_label(person, name_key), attribute, person.get(attribute)))
    return out


def check_nationality(doc_id: str, gold: dict, notice: Notice) -> list[Finding]:
    uncertain = set(gold.get("uncertain") or [])
    findings = []
    for list_field, label, attribute, value in _people(gold):
        if list_field in uncertain or not isinstance(value, str):
            continue
        if _NATIONALITY_INFLECTED_RE.search(value.strip()):
            findings.append(
                Finding(
                    doc_id,
                    "nationality",
                    f"{list_field}[{label}].{attribute}",
                    value,
                    evidence_for(notice.text, label.split(",")[0]),
                    "not in base form",
                )
            )
    return findings


def _extras_value(gold: dict, key: str) -> tuple[bool, Any]:
    """(whether the key may be checked, its gold value)."""
    if "extras" in set(gold.get("uncertain") or []):
        return False, None
    return True, (gold.get("extras") or {}).get(key)


def check_loeschung_reason(doc_id: str, gold: dict, notice: Notice) -> list[Finding]:
    ok, value = _extras_value(gold, "loeschung_reason")
    if not ok or not isinstance(value, str):
        return []
    if _collapse(value) in _collapse(notice.text):
        return []
    return [
        Finding(
            doc_id,
            "loeschung_reason",
            "extras.loeschung_reason",
            value,
            evidence_for(notice.body, re.compile(r"gelöscht|Löschung")),
            "value not found verbatim in the notice",
        )
    ]


def check_mitteilungen(doc_id: str, gold: dict, notice: Notice) -> list[Finding]:
    ok, value = _extras_value(gold, "mitteilungen")
    if not ok or not isinstance(value, str) or not value.strip().startswith("Mitteilungen"):
        return []
    return [
        Finding(
            doc_id,
            "mitteilungen",
            "extras.mitteilungen",
            value,
            evidence_for(notice.body, "Mitteilungen"),
            "keeps the leading subject",
        )
    ]


def check_sitzverlegung(doc_id: str, gold: dict, notice: Notice) -> list[Finding]:
    if "act_subtypes" in set(gold.get("uncertain") or []):
        return []
    subtypes = gold.get("act_subtypes") or []
    trigger = _SITZVERLEGUNG_RE.search(notice.body)
    if ("sitzverlegung" in subtypes) == bool(trigger):
        return []
    if trigger:
        return [
            Finding(
                doc_id,
                "sitzverlegung",
                "act_subtypes",
                subtypes,
                evidence(notice.body, trigger.start(), trigger.end()),
                f"trigger {trigger.group(0)!r} present, sitzverlegung missing",
            )
        ]
    return [
        Finding(
            doc_id,
            "sitzverlegung",
            "act_subtypes",
            subtypes,
            evidence_for(notice.body, re.compile(r"Sitz|Domizil")),
            "sitzverlegung without Sitz neu: or Verlegung des Sitzes",
        )
    ]


def check_revision(doc_id: str, gold: dict, notice: Notice) -> list[Finding]:
    match = _OPTING_OUT_RE.search(notice.text)
    if not match:
        return []
    ok, value = _extras_value(gold, "revision")
    if not ok or value == "opting-out":
        return []
    return [
        Finding(
            doc_id,
            "revision",
            "extras.revision",
            value,
            evidence(notice.text, match.start(), match.end()),
            "limited audit waived, revision is not opting-out",
        )
    ]


def check_nominal_value(doc_id: str, gold: dict, notice: Notice) -> list[Finding]:
    match = _NOMINAL_RE.search(notice.text)
    if not match:
        return []
    ok, value = _extras_value(gold, "nominal_value_chf")
    if not ok or value is not None:
        return []
    return [
        Finding(
            doc_id,
            "nominal_value_chf",
            "extras.nominal_value_chf",
            value,
            evidence(notice.text, match.start(), match.end()),
            "nominal value printed, nominal_value_chf is null",
        )
    ]


def split_entries(section: str) -> list[str]:
    """Split a person section at ";" outside square brackets."""
    entries, depth, current = [], 0, []
    for char in section:
        if char == "[":
            depth += 1
        elif char == "]":
            depth = max(0, depth - 1)
        if char == ";" and depth == 0:
            entries.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    entries.append("".join(current).strip())
    return [e for e in entries if e]


def mutating_entries(body: str) -> list[str]:
    """The entries under "Eingetragene Personen neu oder mutierend:", up to
    the next person heading or the end of the paragraph."""
    start = body.find(_MUTATING_HEADING)
    if start < 0:
        return []
    start += len(_MUTATING_HEADING)
    end = len(body)
    next_heading = _PERSON_HEADINGS_RE.search(body, start)
    if next_heading:
        end = next_heading.start()
    newline = body.find("\n", start)
    if 0 <= newline < end:
        end = newline
    return split_entries(body[start:end])


def check_persons_changed_nationality(doc_id: str, gold: dict, notice: Notice) -> list[Finding]:
    if "persons_changed" in set(gold.get("uncertain") or []):
        return []
    entries = mutating_entries(notice.body)
    findings = []
    for person in gold.get("persons_changed") or []:
        if person.get("nationality_new") is not None:
            continue
        names = [n for n in (person.get("name_new"), person.get("name_previous")) if n]
        entry = next((e for e in entries if any(e.startswith(n) for n in names)), None)
        if entry is None or not _STAATSANGEHOERIG_RE.search(_BRACKET_RE.sub("", entry)):
            continue
        label = evaluate._person_label(person, "name_new")
        findings.append(
            Finding(
                doc_id,
                "persons_changed",
                f"persons_changed[{label}].nationality_new",
                None,
                _collapse(entry),
                "Staatsangehörige outside [bisher: …], nationality_new is null",
            )
        )
    return findings


CHECKS = (
    check_prior_publication_page,
    check_domicile,
    check_nationality,
    check_loeschung_reason,
    check_mitteilungen,
    check_sitzverlegung,
    check_revision,
    check_nominal_value,
    check_persons_changed_nationality,
)
RULES = (
    "prior_publication_page",
    "domicile",
    "nationality",
    "loeschung_reason",
    "mitteilungen",
    "sitzverlegung",
    "revision",
    "nominal_value_chf",
    "persons_changed",
)


def sweep_document(gold: dict, text: str) -> list[Finding]:
    notice = split_notice(text)
    doc_id = gold["doc_id"]
    return [finding for check in CHECKS for finding in check(doc_id, gold, notice)]


def sweep(gold_dir: Path, raw_dir: Path) -> list[Finding]:
    """Every finding of every gold record in gold_dir. Reads only."""
    findings: list[Finding] = []
    for gold_path in sorted(gold_dir.glob("*.json")):
        if not _GOLD_NAME_RE.fullmatch(gold_path.name):
            continue
        gold = json.loads(gold_path.read_text(encoding="utf-8"))
        raw_path = raw_dir / f"{gold_path.stem}.txt"
        if not raw_path.is_file():
            raise SweepError(f"no source notice {raw_path} for {gold_path}")
        findings += sweep_document(gold, raw_path.read_text(encoding="utf-8"))
    return findings


# --- output ---


def write_csv(path: Path, findings: list[Finding]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(f.row() for f in findings)


def _is_inside(path: Path, directory: Path) -> bool:
    return path.resolve().is_relative_to(directory.resolve())


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="List gold records that may break a rule of the frozen prompt.")
    parser.add_argument("--split", required=True, choices=sorted(evaluate.GOLD_DIRS))
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="The CSV goes to <out-dir>/<split>/")
    parser.add_argument("--gold-dir", type=Path, default=None, help="Override the split's gold directory (tests only)")
    parser.add_argument("--raw-dir", type=Path, default=None, help="Override the split's raw directory (tests only)")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing CSV (default: refuse — it may hold verdicts)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    out_path = args.out_dir / args.split / OUTPUT_CSV
    if _is_inside(out_path, DATA_DIR):
        print(f"error: refusing to write under {DATA_DIR}", file=sys.stderr)
        return 2
    if out_path.exists() and not args.force:
        print(
            f"error: {out_path} already exists and may hold verdicts; nothing was written. Pass --force to overwrite.",
            file=sys.stderr,
        )
        return 2

    try:
        findings = sweep(
            args.gold_dir or evaluate.gold_dir_for_split(args.split),
            args.raw_dir or RAW_DIRS[args.split],
        )
    except SweepError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    write_csv(out_path, findings)
    for rule in RULES:
        print(f"{rule:24} {sum(f.rule == rule for f in findings)}")
    print(f"{len(findings)} rows written to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
