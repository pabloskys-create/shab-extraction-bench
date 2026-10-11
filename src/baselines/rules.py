"""Rule baseline: prefill.py's deterministic regex extraction, as a run.

Only the keys of the prompt's output contract are kept; whatever prefill.py
does not fill stays at its schema default (null, [] or {}). No LLM, no
inference beyond what prefill.py itself does (CLAUDE.md rule 4).
"""

from __future__ import annotations

from typing import Any

from src.parse_responses import CORE_KEYS
from src.prefill import prefill_text


def rules_prediction(text: str, doc_id: str) -> dict[str, Any]:
    record = prefill_text(text, doc_id=doc_id)
    return {key: record[key] for key in CORE_KEYS}
