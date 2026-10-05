"""Collect raw model responses for the SHAB extraction benchmark.

For each selected document: load `prompts/extraction_v1.md`, substitute
`<<NOTICE>>` with `str.replace()` (never `.format()` — the prompt's output
contract is full of literal JSON `{`/`}`), call the model, and persist the
raw response. This module never parses or scores a response; that is a
separate script's job (see CLAUDE.md's harness requirements for the
longer-term shape of the eval pipeline — this piece is deliberately scoped
to collection only).

Two backends, one code path, selected by `base_url`: OpenRouter
(https://openrouter.ai/api/v1) and a local Ollama
(http://localhost:11434/v1), both via the `openai` package's
OpenAI-compatible client (checked against the installed v3.24 API; see
CLAUDE.md on not assuming a vendored SDK's shape from memory). Fixed
parameters: temperature 0, one pass per document.

TODO(ollama): Ollama silently truncates the context window instead of
erroring when a prompt exceeds it. This prompt runs to roughly 5000 tokens
with the full schema and vocabulary; nothing here sets Ollama's `num_ctx`
(an Ollama-specific option, passed via `extra_body={"options": {...}}`), so
a real Ollama run may silently score against a truncated prompt. Not solved
here — flagging it rather than guessing a context size.

See CLAUDE.md on `.env`: this module loads `OPENROUTER_API_KEY` from it with
a minimal stdlib parser (no new dependency) and never opens, reads aloud, or
echoes the file for any other purpose.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import openai

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROMPT_PATH = REPO_ROOT / "prompts" / "extraction_v1.md"
DEFAULT_ENV_FILE = REPO_ROOT / ".env"
DEFAULT_DATA_ROOT = REPO_ROOT / "data"
RUNS_DIR = REPO_ROOT / "runs"

NOTICE_PLACEHOLDER = "<<NOTICE>>"

# --- fixed parameters (not CLI flags) ---
TEMPERATURE = 0
PASSES = 1
# Not implemented yet; recorded on every run so a later structured-output
# mode can be told apart from today's plain-text runs by this field alone.
STRUCTURED_OUTPUT = False

BACKEND_BASE_URLS = {
    "openrouter": "https://openrouter.ai/api/v1",
    "ollama": "http://localhost:11434/v1",
}

BACKEND_DEFAULT_TIMEOUTS = {
    "openrouter": 180.0,
    "ollama": 900.0,
}

# The env var each backend's key comes from. A backend absent here needs no
# key (Ollama's OpenAI-compat endpoint doesn't check one; any non-empty
# string satisfies the SDK's constructor).
BACKEND_ENV_KEYS = {
    "openrouter": "OPENROUTER_API_KEY",
}

TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}

# run.json fields that must match exactly to resume into an existing run_id.
# Deliberately excludes max_retries/timeout_s/doc_ids/git_*: those can change
# between invocations of the same run without invalidating prior responses.
RUN_CONFIG_RESUME_FIELDS = ("split", "backend", "model_requested", "openrouter_provider", "prompt_sha256", "params")


class ConfigError(Exception):
    """A startup problem that must abort before anything is written (exit 2)."""


# --- .env ---


def load_env_file(path: Path) -> dict[str, str]:
    """Minimal `KEY=VALUE` .env parser, stdlib only.

    Blank lines and lines starting with '#' are skipped. A value wrapped in
    matching single or double quotes has them stripped. Returns {} if the
    file doesn't exist. Never logs or echoes what it reads.
    """
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def resolve_api_key(backend: str, env_file: Path) -> str:
    """The API key for `backend`, from the real environment first, then
    `env_file`. Raises ConfigError if a backend that needs one has none."""
    env_key = BACKEND_ENV_KEYS.get(backend)
    if env_key is None:
        return "ollama"
    key = os.environ.get(env_key) or load_env_file(env_file).get(env_key)
    if not key:
        raise ConfigError(f"{env_key} not found in the environment or in {env_file}")
    return key


# --- hashing / git ---


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _run_git(args: list[str], repo_root: Path) -> str:
    """Run a git subcommand and return its stdout, or raise ConfigError with
    a clear message — never an uncaught subprocess exception — if git is
    missing or the command fails."""
    try:
        result = subprocess.run(
            ["git", *args], cwd=repo_root, capture_output=True, text=True, check=True
        )
    except FileNotFoundError as exc:
        raise ConfigError("git executable not found — required to record git_commit/git_dirty") from exc
    except subprocess.CalledProcessError as exc:
        raise ConfigError(f"git {' '.join(args)} failed: {exc.stderr.strip()}") from exc
    return result.stdout


def git_commit_hash(repo_root: Path = REPO_ROOT) -> str:
    return _run_git(["rev-parse", "HEAD"], repo_root).strip()


def git_is_dirty(repo_root: Path = REPO_ROOT) -> bool:
    """True if `git status --porcelain` reports any uncommitted change."""
    return bool(_run_git(["status", "--porcelain"], repo_root).strip())


# --- prompt ---


def build_prompt(template: str, notice_text: str) -> str:
    """Substitute `<<NOTICE>>` with str.replace — never .format(): the
    prompt's JSON output contract is full of literal `{`/`}`."""
    return template.replace(NOTICE_PLACEHOLDER, notice_text)


# --- document discovery ---


def discover_doc_ids(split: str, *, data_root: Path = DEFAULT_DATA_ROOT) -> list[str]:
    """doc_ids available for `split`.

    dev: the intersection of data/raw/*.txt and data/exploratory/*.json —
    only documents with both source text and a gold annotation are eligible.
    holdout: data/holdout/raw/*.txt (gold may still be mid-annotation; see
    CLAUDE.md rule 8 on when holdout runs are actually allowed).
    """
    if split == "dev":
        raw_ids = {p.stem for p in (data_root / "raw").glob("*.txt")}
        gold_ids = {p.stem for p in (data_root / "exploratory").glob("*.json")}
        return sorted(raw_ids & gold_ids)
    if split == "holdout":
        return sorted(p.stem for p in (data_root / "holdout" / "raw").glob("*.txt"))
    raise ValueError(f"unknown split {split!r}")


def doc_text_path(split: str, doc_id: str, *, data_root: Path = DEFAULT_DATA_ROOT) -> Path:
    if split == "dev":
        return data_root / "raw" / f"{doc_id}.txt"
    return data_root / "holdout" / "raw" / f"{doc_id}.txt"


def select_doc_ids(all_ids: list[str], docs_arg: str | None, limit_arg: int | None) -> list[str]:
    """Apply --docs (an explicit, order-preserving subset) and then --limit
    (a cap on however many ids are left) to the discovered `all_ids`."""
    if docs_arg:
        wanted = [d.strip() for d in docs_arg.split(",") if d.strip()]
        unknown = [d for d in wanted if d not in all_ids]
        if unknown:
            raise ConfigError(f"--docs names doc_id(s) not in this split: {', '.join(unknown)}")
        ids = wanted
    else:
        ids = list(all_ids)
    if limit_arg is not None:
        ids = ids[:limit_arg]
    return ids


# --- client ---


def make_client(backend: str, api_key: str, timeout: float) -> openai.OpenAI:
    # max_retries=0: this module's own call_with_retries owns backoff, so
    # the SDK's built-in retry is disabled to avoid stacking two retry loops.
    return openai.OpenAI(api_key=api_key, base_url=BACKEND_BASE_URLS[backend], timeout=timeout, max_retries=0)


def build_extra_body(backend: str, openrouter_provider: str | None) -> dict[str, Any] | None:
    """OpenRouter's provider-pin request body, for open-weight models that
    must not silently fall back to a different provider. None for every
    other case (including Ollama, which has no such concept)."""
    if backend != "openrouter" or not openrouter_provider:
        return None
    return {"provider": {"only": [openrouter_provider], "allow_fallbacks": False}}


# --- error classification / retry ---


def classify_error(exc: Exception) -> Literal["transient", "fatal"]:
    if isinstance(exc, openai.APIConnectionError):  # covers APITimeoutError, its subclass
        return "transient"
    if isinstance(exc, openai.APIStatusError):
        return "transient" if exc.status_code in TRANSIENT_STATUS_CODES else "fatal"
    return "fatal"


@dataclass
class CallOutcome:
    response: Any | None
    error: dict[str, Any] | None
    attempts: int
    # Wall time of the attempt that actually resolved the call (succeeded,
    # or exhausted retries / hit a fatal error) — excludes every backoff
    # sleep in between, those aren't part of "how long the model took".
    latency_ms: float


def call_with_retries(
    call_fn: Callable[[], Any],
    *,
    max_retries: int,
    backoff_base: float = 1.0,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> CallOutcome:
    """Call `call_fn()` until it succeeds, a fatal error occurs, or
    `max_retries` transient retries are exhausted.

    Backoff is `backoff_base * 2**attempt` seconds (attempt 0 for the first
    retry), i.e. increasing wait between attempts.
    """
    attempt = 0
    while True:
        started = time.monotonic()
        try:
            response = call_fn()
            latency_ms = (time.monotonic() - started) * 1000
            return CallOutcome(response=response, error=None, attempts=attempt + 1, latency_ms=latency_ms)
        except openai.OpenAIError as exc:
            latency_ms = (time.monotonic() - started) * 1000
            kind = classify_error(exc)
            if kind == "fatal" or attempt >= max_retries:
                error_info = {"type": type(exc).__name__, "message": str(exc), "attempts": attempt + 1}
                return CallOutcome(
                    response=None, error=error_info, attempts=attempt + 1, latency_ms=latency_ms
                )
            sleep_fn(backoff_base * (2**attempt))
            attempt += 1


# --- response -> record ---


def extract_result_fields(response: Any) -> dict[str, Any]:
    """Pull the fields this benchmark records out of a ChatCompletion.

    Reads via `.model_dump()` rather than typed attributes so that
    OpenRouter-only extras (`provider`, possibly `usage.cost`) survive even
    though they aren't in the SDK's own ChatCompletion/CompletionUsage
    fields — both those models declare `extra = "allow"`.
    """
    dumped = response.model_dump()
    choices = dumped.get("choices") or [{}]
    choice = choices[0]
    usage = dumped.get("usage") or {}
    message = choice.get("message") or {}
    return {
        "model_served": dumped.get("model"),
        "provider_served": dumped.get("provider"),
        "usage": {
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
        },
        "cost_usd": usage.get("cost"),
        "finish_reason": choice.get("finish_reason"),
        "response_text": message.get("content"),
    }


@dataclass
class RunParams:
    temperature: int
    max_tokens: int
    passes: int
    structured_output: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "passes": self.passes,
            "structured_output": self.structured_output,
        }


def process_document(
    *,
    doc_id: str,
    split: str,
    notice_text: str,
    prompt_template: str,
    client: Any,
    model: str,
    backend: str,
    extra_body: dict[str, Any] | None,
    params: RunParams,
    max_retries: int,
    prompt_sha256: str,
    git_commit: str,
    git_dirty: bool,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Call the model for one document and build its raw/<doc_id>.json record."""
    prompt_text = build_prompt(prompt_template, notice_text)

    def call_fn() -> Any:
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt_text}],
            "temperature": params.temperature,
            "max_tokens": params.max_tokens,
        }
        if extra_body is not None:
            kwargs["extra_body"] = extra_body
        return client.chat.completions.create(**kwargs)

    outcome = call_with_retries(call_fn, max_retries=max_retries, sleep_fn=sleep_fn)

    record: dict[str, Any] = {
        "doc_id": doc_id,
        "split": split,
        "model_requested": model,
        "backend": backend,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "latency_ms": outcome.latency_ms,
        "params": params.as_dict(),
        "prompt_sha256": prompt_sha256,
        "git_commit": git_commit,
        "git_dirty": git_dirty,
        "attempts": outcome.attempts,
    }

    if outcome.response is not None:
        fields = extract_result_fields(outcome.response)
        record.update(status="ok", error=None, **fields)
    else:
        record.update(
            status="error",
            error=outcome.error,
            model_served=None,
            provider_served=None,
            usage=None,
            cost_usd=None,
            finish_reason=None,
            response_text=None,
        )
    return record


# --- run.json / resumability ---


def doc_output_path(run_dir: Path, doc_id: str) -> Path:
    return run_dir / "raw" / f"{doc_id}.json"


def load_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def build_run_config(
    *,
    run_id: str,
    split: str,
    backend: str,
    model: str,
    openrouter_provider: str | None,
    base_url: str,
    prompt_path: Path,
    prompt_sha256: str,
    git_commit: str,
    git_dirty: bool,
    params: RunParams,
    max_retries: int,
    timeout: float,
    doc_ids: list[str],
    started_at: str,
) -> dict[str, Any]:
    try:
        prompt_display = str(prompt_path.relative_to(REPO_ROOT))
    except ValueError:
        prompt_display = str(prompt_path)
    return {
        "run_id": run_id,
        "split": split,
        "backend": backend,
        "model_requested": model,
        "openrouter_provider": openrouter_provider,
        "base_url": base_url,
        "prompt_path": prompt_display,
        "prompt_sha256": prompt_sha256,
        "git_commit": git_commit,
        "git_dirty": git_dirty,
        "params": params.as_dict(),
        "max_retries": max_retries,
        "timeout_s": timeout,
        "doc_ids": doc_ids,
        "started_at": started_at,
        "finished_at": None,
    }


def check_resume_compatible(existing: dict[str, Any], new_config: dict[str, Any]) -> None:
    """Raise ConfigError naming the first field that differs between an
    existing run.json and the config this invocation would use."""
    for field_name in RUN_CONFIG_RESUME_FIELDS:
        if existing.get(field_name) != new_config.get(field_name):
            raise ConfigError(
                f"runs/{existing.get('run_id')}/run.json already exists with a different "
                f"{field_name!r} ({existing.get(field_name)!r} vs {new_config.get(field_name)!r}) "
                "— use a different --run-id, or match the original arguments to resume"
            )


# --- CLI ---


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect raw model responses for the SHAB extraction benchmark."
    )
    parser.add_argument("--split", choices=["dev", "holdout"], required=True)
    parser.add_argument("--backend", choices=["openrouter", "ollama"], required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--prompt", type=Path, default=DEFAULT_PROMPT_PATH)
    parser.add_argument("--docs", default=None, help="Comma-separated doc_ids, e.g. 0013,0019")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--allow-holdout", action="store_true")
    parser.add_argument("--openrouter-provider", default=None)
    parser.add_argument("--max-retries", type=int, default=5)
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--timeout", type=float, default=None)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    return parser.parse_args(argv)


def default_run_id(backend: str, model: str) -> str:
    slug = model.replace("/", "_")
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{backend}-{slug}-{stamp}"


def main(
    argv: list[str] | None = None,
    *,
    client_factory: Callable[[str, str, float], Any] = make_client,
    sleep_fn: Callable[[float], None] = time.sleep,
    data_root: Path = DEFAULT_DATA_ROOT,
    runs_dir: Path = RUNS_DIR,
) -> int:
    args = parse_args(argv)

    if args.split == "holdout" and not args.allow_holdout:
        print("error: --split holdout requires --allow-holdout", file=sys.stderr)
        return 2

    try:
        api_key = resolve_api_key(args.backend, args.env_file)
        all_ids = discover_doc_ids(args.split, data_root=data_root)
        doc_ids = select_doc_ids(all_ids, args.docs, args.limit)
        prompt_template = args.prompt.read_text(encoding="utf-8")
        git_commit = git_commit_hash()
        git_dirty = git_is_dirty()
    except (ConfigError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    prompt_sha256 = sha256_text(prompt_template)
    timeout = args.timeout if args.timeout is not None else BACKEND_DEFAULT_TIMEOUTS[args.backend]
    params = RunParams(
        temperature=TEMPERATURE, max_tokens=args.max_tokens, passes=PASSES, structured_output=STRUCTURED_OUTPUT
    )
    run_id = args.run_id or default_run_id(args.backend, args.model)
    run_dir = runs_dir / run_id
    run_json_path = run_dir / "run.json"
    base_url = BACKEND_BASE_URLS[args.backend]
    started_at = datetime.now(tz=timezone.utc).isoformat()

    new_config = build_run_config(
        run_id=run_id,
        split=args.split,
        backend=args.backend,
        model=args.model,
        openrouter_provider=args.openrouter_provider,
        base_url=base_url,
        prompt_path=args.prompt,
        prompt_sha256=prompt_sha256,
        git_commit=git_commit,
        git_dirty=git_dirty,
        params=params,
        max_retries=args.max_retries,
        timeout=timeout,
        doc_ids=doc_ids,
        started_at=started_at,
    )

    existing_config = load_json(run_json_path)
    if existing_config is not None:
        try:
            check_resume_compatible(existing_config, new_config)
        except ConfigError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        new_config["started_at"] = existing_config["started_at"]
        new_config["doc_ids"] = sorted(set(existing_config.get("doc_ids", [])) | set(doc_ids))

    write_json_atomic(run_json_path, new_config)

    client = client_factory(args.backend, api_key, timeout)
    extra_body = build_extra_body(args.backend, args.openrouter_provider)

    any_errors = False
    for doc_id in doc_ids:
        output_path = doc_output_path(run_dir, doc_id)
        existing_result = load_json(output_path)
        if existing_result is not None and existing_result.get("status") == "ok":
            print(f"{doc_id}: skipped (already ok)")
            continue

        notice_text = doc_text_path(args.split, doc_id, data_root=data_root).read_text(encoding="utf-8")
        record = process_document(
            doc_id=doc_id,
            split=args.split,
            notice_text=notice_text,
            prompt_template=prompt_template,
            client=client,
            model=args.model,
            backend=args.backend,
            extra_body=extra_body,
            params=params,
            max_retries=args.max_retries,
            prompt_sha256=prompt_sha256,
            git_commit=git_commit,
            git_dirty=git_dirty,
            sleep_fn=sleep_fn,
        )
        write_json_atomic(output_path, record)

        if record["status"] == "ok":
            print(f"{doc_id}: ok ({record['attempts']} attempt(s))")
        else:
            any_errors = True
            print(f"{doc_id}: error — {record['error']['message']}")

    new_config["finished_at"] = datetime.now(tz=timezone.utc).isoformat()
    write_json_atomic(run_json_path, new_config)

    return 1 if any_errors else 0


if __name__ == "__main__":
    sys.exit(main())
