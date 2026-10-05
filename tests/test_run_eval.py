"""Tests for src/run_eval.py — all against a fake client, no network.

The fake client mimics only the shapes run_eval.py actually reads off a
ChatCompletion: `.model_dump()` for success, and real `openai` exception
instances (built with the installed `httpx2` package, which this SDK
version actually depends on — see run_eval.py's module docstring) for
failure, so classify_error() is exercised against the genuine SDK types.
"""

from __future__ import annotations

import json
import time
import types
from dataclasses import dataclass
from pathlib import Path

import httpx2
import openai
import pytest

from src import run_eval

REPO_ROOT = Path(__file__).resolve().parents[1]


# --- fakes ---


@dataclass
class FakeResponse:
    data: dict

    def model_dump(self) -> dict:
        return dict(self.data)


def _make_fake_completion(
    *,
    model: str = "served/model-x",
    provider: str = "Together",
    content: str = "{}",
    finish_reason: str = "stop",
    prompt_tokens: int = 100,
    completion_tokens: int = 20,
    cost: float = 0.0012,
) -> FakeResponse:
    return FakeResponse(
        {
            "model": model,
            "provider": provider,
            "choices": [{"finish_reason": finish_reason, "message": {"role": "assistant", "content": content}}],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
                "cost": cost,
            },
        }
    )


def _http_error(status_code: int, exc_cls, message: str = "boom"):
    request = httpx2.Request("POST", "https://example.test/v1/chat/completions")
    response = httpx2.Response(status_code, request=request)
    return exc_cls(message, response=response, body=None)


def _transient_error(message: str = "rate limited"):
    return _http_error(429, openai.RateLimitError, message)


def _fatal_error(message: str = "bad request"):
    return _http_error(400, openai.BadRequestError, message)


class _FakeCompletions:
    def __init__(self, queue: list):
        self._queue = list(queue)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._queue:
            raise AssertionError("create() called more times than the test queued responses for")
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeClient:
    def __init__(self, queue: list):
        self.completions = _FakeCompletions(queue)
        self.chat = types.SimpleNamespace(completions=self.completions)


def _client_factory(client):
    return lambda backend, api_key, timeout: client


# --- test fixtures ---


def _write_prompt(tmp_path: Path) -> Path:
    path = tmp_path / "prompt.md"
    path.write_text('Output contract: {"act_type": ""}\n<<NOTICE>>\n', encoding="utf-8")
    return path


def _make_dev_corpus(tmp_path: Path, docs: dict[str, str]) -> Path:
    """A tmp data_root with data/raw/*.txt + data/exploratory/*.json for
    each {doc_id: notice_text}, plus an empty holdout/raw/ dir."""
    data_root = tmp_path / "data"
    (data_root / "raw").mkdir(parents=True)
    (data_root / "exploratory").mkdir(parents=True)
    (data_root / "holdout" / "raw").mkdir(parents=True)
    for doc_id, text in docs.items():
        (data_root / "raw" / f"{doc_id}.txt").write_text(text, encoding="utf-8")
        (data_root / "exploratory" / f"{doc_id}.json").write_text("{}", encoding="utf-8")
    return data_root


def _patch_git(monkeypatch, *, commit: str = "deadbeef", dirty: bool = False) -> None:
    monkeypatch.setattr(run_eval, "git_commit_hash", lambda *a, **k: commit)
    monkeypatch.setattr(run_eval, "git_is_dirty", lambda *a, **k: dirty)


def _base_argv(run_id: str, *, split: str = "dev", extra: list[str] | None = None) -> list[str]:
    argv = ["--split", split, "--backend", "ollama", "--model", "test-model", "--run-id", run_id]
    if split == "holdout":
        argv.append("--allow-holdout")
    return argv + (extra or [])


# --- 1. prompt placeholder substitution ---


def test_build_prompt_replaces_notice_placeholder_only():
    template = 'Output contract: {"act_type": ""}\n<<NOTICE>>\n'
    prompt = run_eval.build_prompt(template, "Mutation Foo AG, Bern")
    assert prompt == 'Output contract: {"act_type": ""}\nMutation Foo AG, Bern\n'
    # Prove .format() would have been the wrong tool: the literal JSON
    # braces in the schema make it fail outright.
    with pytest.raises((KeyError, IndexError)):
        template.format()


# --- 2. resumability: skip ok, retry error ---


def test_resume_skips_ok_and_retries_error_documents(tmp_path, monkeypatch):
    _patch_git(monkeypatch)
    data_root = _make_dev_corpus(tmp_path, {"0001": "NOTICE-ONE", "0002": "NOTICE-TWO"})
    prompt_path = _write_prompt(tmp_path)
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "run1"

    (run_dir / "raw").mkdir(parents=True)
    (run_dir / "raw" / "0001.json").write_text(json.dumps({"status": "ok"}), encoding="utf-8")
    (run_dir / "raw" / "0002.json").write_text(json.dumps({"status": "error"}), encoding="utf-8")

    client = FakeClient([_make_fake_completion(content="retried-ok")])
    code = run_eval.main(
        _base_argv("run1", extra=["--prompt", str(prompt_path)]),
        client_factory=_client_factory(client),
        sleep_fn=lambda s: None,
        data_root=data_root,
        runs_dir=runs_dir,
    )

    assert code == 0
    assert len(client.completions.calls) == 1
    assert "NOTICE-TWO" in client.completions.calls[0]["messages"][0]["content"]
    result_0001 = json.loads((run_dir / "raw" / "0001.json").read_text())
    assert result_0001 == {"status": "ok"}  # untouched, not re-called
    result_0002 = json.loads((run_dir / "raw" / "0002.json").read_text())
    assert result_0002["status"] == "ok"
    assert result_0002["response_text"] == "retried-ok"


# --- 3. holdout gate ---


def test_holdout_without_allow_flag_aborts(tmp_path):
    data_root = _make_dev_corpus(tmp_path, {})
    prompt_path = _write_prompt(tmp_path)
    runs_dir = tmp_path / "runs"

    def _must_not_be_called(*a, **k):
        pytest.fail("client_factory must not be called when the holdout gate rejects the run")

    code = run_eval.main(
        ["--split", "holdout", "--backend", "ollama", "--model", "m", "--run-id", "run1", "--prompt", str(prompt_path)],
        client_factory=_must_not_be_called,
        data_root=data_root,
        runs_dir=runs_dir,
    )
    assert code == 2
    assert not runs_dir.exists()


# --- 4. fatal error logged, run continues ---


def test_fatal_error_is_logged_and_run_continues(tmp_path, monkeypatch):
    _patch_git(monkeypatch)
    data_root = _make_dev_corpus(tmp_path, {"0001": "NOTICE-ONE", "0002": "NOTICE-TWO"})
    prompt_path = _write_prompt(tmp_path)
    runs_dir = tmp_path / "runs"

    client = FakeClient([_fatal_error("no such model"), _make_fake_completion(content="second-ok")])
    code = run_eval.main(
        _base_argv("run1", extra=["--prompt", str(prompt_path)]),
        client_factory=_client_factory(client),
        sleep_fn=lambda s: None,
        data_root=data_root,
        runs_dir=runs_dir,
    )

    assert code == 1
    result_0001 = json.loads((runs_dir / "run1" / "raw" / "0001.json").read_text())
    assert result_0001["status"] == "error"
    assert result_0001["error"]["message"] == "no such model"
    assert result_0001["attempts"] == 1
    result_0002 = json.loads((runs_dir / "run1" / "raw" / "0002.json").read_text())
    assert result_0002["status"] == "ok"


# --- 5. transient retries with increasing backoff, then success ---


def test_transient_errors_retry_with_backoff_then_succeed(tmp_path, monkeypatch):
    _patch_git(monkeypatch)
    data_root = _make_dev_corpus(tmp_path, {"0001": "NOTICE-ONE"})
    prompt_path = _write_prompt(tmp_path)
    runs_dir = tmp_path / "runs"

    sleeps: list[float] = []
    client = FakeClient([_transient_error(), _transient_error(), _make_fake_completion(content="ok-at-last")])
    code = run_eval.main(
        _base_argv("run1", extra=["--prompt", str(prompt_path)]),
        client_factory=_client_factory(client),
        sleep_fn=sleeps.append,
        data_root=data_root,
        runs_dir=runs_dir,
    )

    assert code == 0
    assert len(client.completions.calls) == 3
    assert sleeps == [1.0, 2.0]  # backoff_base * 2**attempt, increasing
    result = json.loads((runs_dir / "run1" / "raw" / "0001.json").read_text())
    assert result["status"] == "ok"
    assert result["attempts"] == 3
    assert result["response_text"] == "ok-at-last"


# --- 6. .env parser (tmp file only, never the real .env) ---


def test_env_file_parser_reads_key_value_pairs(tmp_path):
    env_path = tmp_path / "fake.env"
    env_path.write_text(
        "# a comment\n\nOPENROUTER_API_KEY=abc123\nQUOTED=\"with space\"\nSINGLE='val'\n",
        encoding="utf-8",
    )
    assert run_eval.load_env_file(env_path) == {
        "OPENROUTER_API_KEY": "abc123",
        "QUOTED": "with space",
        "SINGLE": "val",
    }


def test_env_file_parser_returns_empty_for_missing_file(tmp_path):
    assert run_eval.load_env_file(tmp_path / "nope.env") == {}


# --- 7. dev split discovery ---


def test_dev_split_discovers_intersection_of_raw_and_exploratory(tmp_path):
    data_root = tmp_path / "data"
    (data_root / "raw").mkdir(parents=True)
    (data_root / "exploratory").mkdir(parents=True)
    (data_root / "raw" / "0001.txt").write_text("a", encoding="utf-8")
    (data_root / "raw" / "0002.txt").write_text("b", encoding="utf-8")
    (data_root / "exploratory" / "0001.json").write_text("{}", encoding="utf-8")
    (data_root / "exploratory" / "0003.json").write_text("{}", encoding="utf-8")  # no matching raw text

    assert run_eval.discover_doc_ids("dev", data_root=data_root) == ["0001"]


def test_holdout_split_discovers_holdout_raw_only(tmp_path):
    data_root = tmp_path / "data"
    (data_root / "holdout" / "raw").mkdir(parents=True)
    (data_root / "holdout" / "raw" / "0121.txt").write_text("a", encoding="utf-8")

    assert run_eval.discover_doc_ids("holdout", data_root=data_root) == ["0121"]


# --- 8. OpenRouter provider pin ---


def test_openrouter_provider_pin_builds_extra_body():
    assert run_eval.build_extra_body("openrouter", "together") == {
        "provider": {"only": ["together"], "allow_fallbacks": False}
    }


def test_extra_body_omitted_for_ollama_and_when_unpinned():
    assert run_eval.build_extra_body("ollama", "together") is None
    assert run_eval.build_extra_body("openrouter", None) is None


# --- 9/13. resume-compatibility gate ---


def test_resume_with_different_model_aborts(tmp_path, monkeypatch, capsys):
    _patch_git(monkeypatch)
    data_root = _make_dev_corpus(tmp_path, {"0001": "NOTICE-ONE"})
    prompt_path = _write_prompt(tmp_path)
    runs_dir = tmp_path / "runs"

    client = FakeClient([_make_fake_completion()])
    first_code = run_eval.main(
        _base_argv("run1", extra=["--model", "model-a", "--prompt", str(prompt_path)]),
        client_factory=_client_factory(client),
        sleep_fn=lambda s: None,
        data_root=data_root,
        runs_dir=runs_dir,
    )
    assert first_code == 0
    run_json_before = (runs_dir / "run1" / "run.json").read_text()

    def _must_not_be_called(*a, **k):
        pytest.fail("client_factory must not be called when the resume gate rejects the run")

    second_code = run_eval.main(
        ["--split", "dev", "--backend", "ollama", "--model", "model-b", "--run-id", "run1", "--prompt", str(prompt_path)],
        client_factory=_must_not_be_called,
        data_root=data_root,
        runs_dir=runs_dir,
    )
    assert second_code == 2
    assert "model_requested" in capsys.readouterr().err
    assert (runs_dir / "run1" / "run.json").read_text() == run_json_before  # untouched


def test_resume_with_different_split_aborts(tmp_path, monkeypatch, capsys):
    _patch_git(monkeypatch)
    data_root = _make_dev_corpus(tmp_path, {"0001": "NOTICE-ONE"})
    prompt_path = _write_prompt(tmp_path)
    runs_dir = tmp_path / "runs"

    client = FakeClient([_make_fake_completion()])
    first_code = run_eval.main(
        _base_argv("run1", split="dev", extra=["--prompt", str(prompt_path)]),
        client_factory=_client_factory(client),
        sleep_fn=lambda s: None,
        data_root=data_root,
        runs_dir=runs_dir,
    )
    assert first_code == 0

    def _must_not_be_called(*a, **k):
        pytest.fail("client_factory must not be called when the resume gate rejects the run")

    second_code = run_eval.main(
        _base_argv("run1", split="holdout", extra=["--prompt", str(prompt_path)]),
        client_factory=_must_not_be_called,
        data_root=data_root,
        runs_dir=runs_dir,
    )
    assert second_code == 2
    assert "split" in capsys.readouterr().err


# --- 10/12. per-document record contents ---


def test_raw_record_has_prompt_hash_git_commit_finish_reason_and_max_tokens(tmp_path, monkeypatch):
    _patch_git(monkeypatch, commit="deadbeef", dirty=False)
    data_root = _make_dev_corpus(tmp_path, {"0001": "NOTICE-ONE"})
    prompt_path = _write_prompt(tmp_path)
    runs_dir = tmp_path / "runs"
    expected_sha = run_eval.sha256_text(prompt_path.read_text(encoding="utf-8"))

    client = FakeClient([_make_fake_completion(finish_reason="length")])
    code = run_eval.main(
        _base_argv("run1", extra=["--prompt", str(prompt_path), "--max-tokens", "123"]),
        client_factory=_client_factory(client),
        sleep_fn=lambda s: None,
        data_root=data_root,
        runs_dir=runs_dir,
    )

    assert code == 0
    assert client.completions.calls[0]["max_tokens"] == 123
    record = json.loads((runs_dir / "run1" / "raw" / "0001.json").read_text())
    assert record["prompt_sha256"] == expected_sha
    assert record["git_commit"] == "deadbeef"
    assert record["finish_reason"] == "length"
    assert record["params"]["max_tokens"] == 123
    run_config = json.loads((runs_dir / "run1" / "run.json").read_text())
    assert run_config["params"]["max_tokens"] == 123


# --- 14. latency excludes backoff wait ---


def test_latency_excludes_backoff_wait(tmp_path, monkeypatch):
    _patch_git(monkeypatch)
    data_root = _make_dev_corpus(tmp_path, {"0001": "NOTICE-ONE"})
    prompt_path = _write_prompt(tmp_path)
    runs_dir = tmp_path / "runs"

    client = FakeClient([_transient_error(), _make_fake_completion()])
    code = run_eval.main(
        _base_argv("run1", extra=["--prompt", str(prompt_path), "--max-retries", "3"]),
        client_factory=_client_factory(client),
        sleep_fn=lambda seconds: time.sleep(0.2),  # a real, measurable wait
        data_root=data_root,
        runs_dir=runs_dir,
    )

    assert code == 0
    record = json.loads((runs_dir / "run1" / "raw" / "0001.json").read_text())
    assert record["attempts"] == 2
    # The fake call itself is ~instant; 100ms is generous headroom, and well
    # under the 200ms backoff sleep that must NOT be counted here.
    assert record["latency_ms"] < 100


# --- 15. git_dirty, and a missing git aborting cleanly ---


def test_git_dirty_recorded_in_run_and_raw_files(tmp_path, monkeypatch):
    _patch_git(monkeypatch, commit="deadbeef", dirty=True)
    data_root = _make_dev_corpus(tmp_path, {"0001": "NOTICE-ONE"})
    prompt_path = _write_prompt(tmp_path)
    runs_dir = tmp_path / "runs"

    client = FakeClient([_make_fake_completion()])
    code = run_eval.main(
        _base_argv("run1", extra=["--prompt", str(prompt_path)]),
        client_factory=_client_factory(client),
        sleep_fn=lambda s: None,
        data_root=data_root,
        runs_dir=runs_dir,
    )

    assert code == 0
    run_config = json.loads((runs_dir / "run1" / "run.json").read_text())
    assert run_config["git_commit"] == "deadbeef"
    assert run_config["git_dirty"] is True
    record = json.loads((runs_dir / "run1" / "raw" / "0001.json").read_text())
    assert record["git_commit"] == "deadbeef"
    assert record["git_dirty"] is True


def test_run_git_missing_raises_config_error(monkeypatch):
    def _raise_missing(*a, **k):
        raise FileNotFoundError("git not on PATH")

    monkeypatch.setattr(run_eval.subprocess, "run", _raise_missing)
    with pytest.raises(run_eval.ConfigError):
        run_eval.git_commit_hash()


def test_git_unavailable_aborts_cleanly_without_writing_anything(tmp_path, monkeypatch):
    def _raise_missing(*a, **k):
        raise FileNotFoundError("git not on PATH")

    monkeypatch.setattr(run_eval.subprocess, "run", _raise_missing)
    data_root = _make_dev_corpus(tmp_path, {"0001": "NOTICE-ONE"})
    prompt_path = _write_prompt(tmp_path)
    runs_dir = tmp_path / "runs"

    def _must_not_be_called(*a, **k):
        pytest.fail("client_factory must not be called when git is unavailable")

    code = run_eval.main(
        _base_argv("run1", extra=["--prompt", str(prompt_path)]),
        client_factory=_must_not_be_called,
        data_root=data_root,
        runs_dir=runs_dir,
    )
    assert code == 2
    assert not runs_dir.exists()
