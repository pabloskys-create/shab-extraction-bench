# CLAUDE.md

## What this project is

A benchmark for structured extraction from Swiss commercial register notices
(SHAB / Schweizerisches Handelsamtsblatt, German-language publications only).

It answers one question with evidence: **how reliably can LLMs convert free-text
Swiss legal notices into structured records, at what cost, and where exactly do
they fail — compared against a deterministic rule-based baseline?**

Three deliverables:
1. `data/gold/` — a hand-annotated gold standard (target: 200 documents)
2. `src/` — a reproducible evaluation harness
3. `results/` + `README.md` — measured findings

## 🔴 Critical rules — never violate

**1. Never create, modify, or delete anything in `data/gold/`.**
Every file there is hand-annotated ground truth produced by a human. If a model
writes ground truth, the entire benchmark is circular and worthless. You may
*read* these files. You may write code that *validates* them. You may never
write their contents.

**2. Never modify `data/raw/`.**
Source documents are immutable. If a file looks malformed, report it — do not fix it.

**3. Never annotate a document.**
If asked to "fill in" a JSON in `data/gold/`, refuse and explain why.
The only exception is `src/prefill.py`, which fills *deterministic regex-extracted
fields only* and marks them `"_verified": false`.

**4. `prefill.py` must contain no LLM calls.** Regex and string parsing only.
It is both an ergonomic tool and a benchmark baseline; an LLM inside it would
invalidate both roles.

**5. Never invent data.** If a field is absent from the source text, it is `null`.
Never infer, never fill from context, never carry a value over from another document.

## Stack

- Python 3.11+, standard library where possible
- `pytest` for tests, `ruff` for linting
- No database. JSON files on disk.
- Dependencies live in `requirements.txt`. Ask before adding one.
- The project virtualenv lives at .venv/. On Windows the interpreter is
  .venv\Scripts\python.exe. ALWAYS use it — never the system Python,
  never `py`, never a global pip install.
- Never install a package outside .venv/, not even temporarily to run tests.
  If a dependency is missing, stop and ask.

## Layout

```
data/raw/NNNN.txt        immutable source text
data/gold/NNNN.json      hand-annotated ground truth  (READ ONLY for you)
data/excluded/           documents excluded from the corpus, with reasons
data/exploratory/        first ~25 annotations, kept as provenance
src/prefill.py           deterministic field prefill (no LLM)
src/validate.py          schema + coherence validation
src/baselines/rules.py   rule-based extractor (benchmark baseline)
src/run_eval.py          the harness
results/                 generated outputs — never hand-edited
SCHEMA.md                field definitions and annotation decisions
GLOSSARY.md              German legal-registry vocabulary
annotation_log.md        running log of ambiguous cases
CHANGELOG.md             schema version history
```

## Harness requirements

- `run_eval.py` must persist every raw model response and emit a per-field
  failure listing, not just aggregate scores. Failure analysis requires
  inspecting individual cases.
- No claim about *why* a model fails goes into the README without having
  inspected at least 5 real failures for that field.

## Conventions

- All dates in output JSON: ISO `YYYY-MM-DD`. Source uses `DD.MM.YYYY`.
- All money as JSON numbers, not strings. Source uses Swiss format: `189'123.50`.
- Field absent in source → `null`. Empty list → `[]`. Never `""` for a missing value.
- Every gold JSON carries `schema_version`.
- Code, comments, commit messages and docs in **English**.

## Working style

- Small, focused commits. Conventional commits (`feat:`, `fix:`, `docs:`, `test:`).
- Write the test before or alongside the code, never after the fact.
- Prefer boring, readable code over clever code. This repo is read by humans
  evaluating the author's judgement, not just executed.
- When a design decision has a trade-off, state it in the commit message or in
  `SCHEMA.md` rather than picking silently.

## What I (the human) do, and you don't

I annotate. I decide what "correct" means. I write the interpretation of results
and the limitations section of the README.
You write tooling, tests, plumbing and analysis code.
