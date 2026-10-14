"""Apply a reviewed list of gold corrections, checking every old value first.

The corrections are decided by hand (the human annotator); this tool only
carries them out, so that each change to a gold record is listed in one
reviewable file with its reason instead of being typed into the JSON.

The corrections file is a JSON list of objects:

    {"split": "dev", "doc_id": "0005", "path": "extras.revision",
     "expected_old": null, "new": "opting-out", "reason": "..."}

`path` is a dotted key path. A list element is picked by one of its keys:
`persons_changed[name=Eftodii, Valeriu].nationality_new`. `name` also
matches `name_new`, since persons_changed elements carry no `name` key.
The selector must match exactly one element.

For each correction the current value decides what happens:

    current == new           "already ok", skipped
    current == expected_old  applied
    anything else            conflict

A single conflict (or an unresolvable path) aborts the whole run before
anything is written. A missing key reads as null. Under `extras`, where a
key is present only when the notice gives its value, writing null removes
the key instead of storing null.

Dry run is the default; --apply writes. The gold files are hand-formatted
(lists and objects often on one line), so a record is never re-serialised:
only the text of the changed value is replaced, a new key is inserted after
the last one in the style of its neighbours, and everything else — key
order, `_verified`, line endings, indentation, the trailing newline — stays
byte for byte. Values are written with ensure_ascii=False and the file as
UTF-8 without BOM. Before writing, the edited text is parsed again and must
equal the record with the corrections applied.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from json.decoder import scanstring
from pathlib import Path
from typing import Any

from src import evaluate

CORRECTION_KEYS = ("split", "doc_id", "path", "expected_old", "new", "reason")
_DOC_ID_RE = re.compile(r"\d{4}")
_SEGMENT_RE = re.compile(r"([A-Za-z_]\w*)(?:\[(\w+)=([^\]]+)\])?")
_NUMBER_RE = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?")
_WHITESPACE = " \t\r\n"
_MISSING = object()


class CorrectionError(Exception):
    """The corrections file, a record or a path in it cannot be used."""


@dataclass(frozen=True)
class Correction:
    split: str
    doc_id: str
    path: str
    expected_old: Any
    new: Any
    reason: str


@dataclass(frozen=True)
class Segment:
    key: str
    match_key: str | None = None
    match_value: str | None = None


@dataclass
class Outcome:
    correction: Correction
    status: str  # "apply", "already ok" or "conflict"
    current: Any


# --- corrections file ---


def load_corrections(path: Path, splits: set[str]) -> list[Correction]:
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CorrectionError(f"cannot read {path}: {exc}") from exc
    if not isinstance(entries, list):
        raise CorrectionError(f"{path}: expected a JSON list")

    corrections = []
    seen = set()
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict) or set(entry) != set(CORRECTION_KEYS):
            raise CorrectionError(f"entry {i}: expected exactly the keys {', '.join(CORRECTION_KEYS)}")
        correction = Correction(**entry)
        if correction.split not in splits:
            raise CorrectionError(f"entry {i}: unknown split {correction.split!r}")
        if not isinstance(correction.doc_id, str) or not _DOC_ID_RE.fullmatch(correction.doc_id):
            raise CorrectionError(f"entry {i}: doc_id must be four digits, got {correction.doc_id!r}")
        if not isinstance(correction.reason, str) or not correction.reason.strip():
            raise CorrectionError(f"entry {i}: reason is empty")
        parse_path(correction.path)
        key = (correction.split, correction.doc_id, correction.path)
        if key in seen:
            raise CorrectionError(f"entry {i}: duplicate correction for {' '.join(key)}")
        seen.add(key)
        corrections.append(correction)
    return corrections


def parse_path(path: str) -> list[Segment]:
    if not isinstance(path, str) or not path:
        raise CorrectionError(f"invalid path {path!r}")
    segments = []
    pos = 0
    while True:
        match = _SEGMENT_RE.match(path, pos)
        if not match:
            raise CorrectionError(f"invalid path {path!r} at position {pos}")
        segments.append(Segment(*match.groups()))
        pos = match.end()
        if pos == len(path):
            break
        if path[pos] != ".":
            raise CorrectionError(f"invalid path {path!r} at position {pos}")
        pos += 1
    if segments[-1].match_key is not None:
        raise CorrectionError(f"path {path!r} must end in a plain key, not a list selector")
    return segments


# --- path resolution on the parsed record ---


def _matches(element: Any, key: str, value: str) -> bool:
    if not isinstance(element, dict):
        return False
    if key in element:
        return element[key] == value
    return key == "name" and element.get("name_new") == value


def _locate(record: dict, segments: list[Segment], path: str) -> list[str | int]:
    """The concrete keys and list indices leading to the dict that holds the
    last segment's key."""
    steps: list[str | int] = []
    node: Any = record
    for segment in segments[:-1]:
        if not isinstance(node, dict) or segment.key not in node:
            raise CorrectionError(f"{path}: no key {segment.key!r}")
        node = node[segment.key]
        steps.append(segment.key)
        if segment.match_key is not None:
            if not isinstance(node, list):
                raise CorrectionError(f"{path}: {segment.key!r} is not a list")
            hits = [i for i, e in enumerate(node) if _matches(e, segment.match_key, segment.match_value)]
            if len(hits) != 1:
                raise CorrectionError(
                    f"{path}: [{segment.match_key}={segment.match_value}] matches {len(hits)} elements, expected 1"
                )
            node = node[hits[0]]
            steps.append(hits[0])
    if not isinstance(node, dict):
        raise CorrectionError(f"{path}: parent of {segments[-1].key!r} is not an object")
    return steps


def _walk(node: Any, steps: list[str | int]) -> Any:
    for step in steps:
        node = node[step]
    return node


def _null_means_absent(segments: list[Segment]) -> bool:
    return len(segments) == 2 and segments[0].key == "extras" and segments[0].match_key is None


def read_value(record: dict, path: str) -> Any:
    segments = parse_path(path)
    parent = _walk(record, _locate(record, segments, path))
    value = parent.get(segments[-1].key, _MISSING)
    return None if value is _MISSING else value


def write_value(record: dict, path: str, value: Any) -> None:
    segments = parse_path(path)
    parent = _walk(record, _locate(record, segments, path))
    key = segments[-1].key
    if value is None and _null_means_absent(segments):
        parent.pop(key, None)
    else:
        parent[key] = value


def same_value(a: Any, b: Any) -> bool:
    """JSON equality: 100 equals 100.0, but true does not equal 1."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same_value(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same_value(x, y) for x, y in zip(a, b))
    return a == b


# --- text spans, for edits that keep the file's own formatting ---


@dataclass
class Span:
    """Where a JSON value sits in the text. Objects keep their members (key
    start offset and value span), arrays their items."""

    start: int
    end: int
    members: dict[str, tuple[int, Span]] = field(default_factory=dict)
    items: list[Span] = field(default_factory=list)


def scan(text: str) -> Span:
    span, pos = _scan_value(text, _skip(text, 0))
    if _skip(text, pos) != len(text):
        raise CorrectionError(f"trailing data at offset {pos}")
    return span


def _skip(text: str, pos: int) -> int:
    while pos < len(text) and text[pos] in _WHITESPACE:
        pos += 1
    return pos


def _scan_value(text: str, pos: int) -> tuple[Span, int]:
    char = text[pos : pos + 1]
    if char == "{":
        span = Span(pos, -1)
        pos = _skip(text, pos + 1)
        while text[pos] != "}":
            key_start = pos
            key, pos = scanstring(text, pos + 1)
            pos = _skip(text, pos)
            if text[pos] != ":":
                raise CorrectionError(f"expected ':' at offset {pos}")
            value, pos = _scan_value(text, _skip(text, pos + 1))
            span.members[key] = (key_start, value)
            pos = _skip(text, pos)
            if text[pos] == ",":
                pos = _skip(text, pos + 1)
        span.end = pos + 1
        return span, pos + 1
    if char == "[":
        span = Span(pos, -1)
        pos = _skip(text, pos + 1)
        while text[pos] != "]":
            item, pos = _scan_value(text, pos)
            span.items.append(item)
            pos = _skip(text, pos)
            if text[pos] == ",":
                pos = _skip(text, pos + 1)
        span.end = pos + 1
        return span, pos + 1
    if char == '"':
        _, end = scanstring(text, pos + 1)
        return Span(pos, end), end
    for literal in ("true", "false", "null"):
        if text.startswith(literal, pos):
            return Span(pos, pos + len(literal)), pos + len(literal)
    match = _NUMBER_RE.match(text, pos)
    if not match:
        raise CorrectionError(f"unexpected character at offset {pos}")
    return Span(pos, match.end()), match.end()


def _span_at(root: Span, steps: list[str | int]) -> Span:
    span = root
    for step in steps:
        span = span.items[step] if isinstance(step, int) else span.members[step][1]
    return span


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def edit_text(text: str, steps: list[str | int], key: str, value: Any, delete: bool) -> str:
    """Set (or delete) `key` in the object at `steps`, touching only its text."""
    parent = _span_at(scan(text), steps)
    members = list(parent.members.items())
    names = [name for name, _ in members]

    if key in parent.members:
        key_start, span = parent.members[key]
        if not delete:
            return text[: span.start] + _dump(value) + text[span.end :]
        index = names.index(key)
        if len(members) == 1:
            return text[: parent.start] + "{}" + text[parent.end :]
        if index == len(members) - 1:
            previous_end = members[index - 1][1][1].end
            return text[:previous_end] + text[span.end :]
        next_key_start = members[index + 1][1][0]
        return text[:key_start] + text[next_key_start:]

    if delete:
        return text
    member = f"{_dump(key)}: {_dump(value)}"
    if not members:
        return text[: parent.start] + "{" + member + "}" + text[parent.end :]
    last_end = members[-1][1][1].end
    if len(members) >= 2:
        separator = text[members[-2][1][1].end : members[-1][1][0]]
    else:
        gap = text[parent.start + 1 : members[0][1][0]]
        separator = "," + gap if "\n" in gap else ", "
    return text[:last_end] + separator + member + text[last_end:]


# --- records on disk ---


@dataclass
class GoldFile:
    path: Path
    text: str
    record: dict
    changed: bool = False


def read_gold(path: Path) -> GoldFile:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raise CorrectionError(f"{path}: starts with a BOM")
    text = raw.decode("utf-8")
    return GoldFile(path, text, json.loads(text))


def apply_to(gold: GoldFile, correction: Correction) -> None:
    """Apply one correction to the record and, in the same way, to its text."""
    segments = parse_path(correction.path)
    steps = _locate(gold.record, segments, correction.path)
    delete = correction.new is None and _null_means_absent(segments)
    gold.text = edit_text(gold.text, steps, segments[-1].key, correction.new, delete)
    write_value(gold.record, correction.path, correction.new)
    gold.changed = True


def write_gold(gold: GoldFile) -> None:
    tmp = gold.path.with_name(gold.path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        handle.write(gold.text)
    os.replace(tmp, gold.path)


# --- planning ---


def plan(corrections: list[Correction], gold_dirs: dict[str, Path]) -> tuple[list[Outcome], list[GoldFile]]:
    """Check every correction against the records and apply them in memory.

    Nothing is written here. Raises CorrectionError for a missing record, an
    unresolvable path, or an edited text that no longer parses to the
    corrected record.
    """
    files: dict[Path, GoldFile] = {}
    outcomes = []
    for correction in corrections:
        path = gold_dirs[correction.split] / f"{correction.doc_id}.json"
        if path not in files:
            if not path.is_file():
                raise CorrectionError(f"{correction.split} {correction.doc_id}: no gold record at {path}")
            files[path] = read_gold(path)
        gold = files[path]
        if gold.record.get("doc_id") != correction.doc_id:
            raise CorrectionError(f"{path}: doc_id is {gold.record.get('doc_id')!r}, expected {correction.doc_id!r}")

        current = read_value(gold.record, correction.path)
        if same_value(current, correction.new):
            status = "already ok"
        elif same_value(current, correction.expected_old):
            status = "apply"
            apply_to(gold, correction)
        else:
            status = "conflict"
        outcomes.append(Outcome(correction, status, current))

    for gold in files.values():
        if gold.changed and not same_value(json.loads(gold.text), gold.record):
            raise CorrectionError(f"{gold.path}: edited text does not parse to the corrected record")
    return outcomes, [gold for gold in files.values() if gold.changed]


# --- output ---


def print_summary(outcomes: list[Outcome]) -> None:
    by_notice: dict[tuple[str, str], list[Outcome]] = {}
    for outcome in outcomes:
        by_notice.setdefault((outcome.correction.split, outcome.correction.doc_id), []).append(outcome)
    for (split, doc_id), group in sorted(by_notice.items()):
        print(f"{split} {doc_id}")
        for outcome in group:
            c = outcome.correction
            if outcome.status == "apply":
                detail = f"{_dump(c.expected_old)} -> {_dump(c.new)}"
            elif outcome.status == "already ok":
                detail = _dump(c.new)
            else:
                detail = f"found {_dump(outcome.current)}, expected {_dump(c.expected_old)} (new {_dump(c.new)})"
            print(f"  {outcome.status:10}  {c.path}: {detail}")
    counts = {s: sum(o.status == s for o in outcomes) for s in ("apply", "already ok", "conflict")}
    print(
        f"{len(outcomes)} corrections in {len(by_notice)} notices: "
        f"{counts['apply']} to apply, {counts['already ok']} already ok, {counts['conflict']} conflicts"
    )


# --- CLI ---


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply a reviewed list of gold corrections.")
    parser.add_argument("--file", type=Path, required=True, help="JSON list of corrections")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Check and report only (the default)")
    mode.add_argument("--apply", action="store_true", help="Write the corrections")
    parser.add_argument("--dev-dir", type=Path, default=None, help="Override the dev gold directory (tests only)")
    parser.add_argument(
        "--holdout-dir", type=Path, default=None, help="Override the holdout gold directory (tests only)"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    gold_dirs = dict(evaluate.GOLD_DIRS)
    if args.dev_dir:
        gold_dirs["dev"] = args.dev_dir
    if args.holdout_dir:
        gold_dirs["holdout"] = args.holdout_dir

    try:
        corrections = load_corrections(args.file, set(gold_dirs))
        outcomes, changed = plan(corrections, gold_dirs)
    except CorrectionError as exc:
        print(f"error: {exc}; nothing was written", file=sys.stderr)
        return 2

    print_summary(outcomes)
    if any(o.status == "conflict" for o in outcomes):
        print("aborted: conflicts found; nothing was written", file=sys.stderr)
        return 1
    if not args.apply:
        print(f"dry run: nothing was written ({len(changed)} gold records would change); pass --apply to write")
        return 0

    for gold in sorted(changed, key=lambda g: g.path):
        write_gold(gold)
    print(f"{len(changed)} gold records written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
