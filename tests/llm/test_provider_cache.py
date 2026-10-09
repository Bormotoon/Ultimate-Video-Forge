import io
import json
from pathlib import Path

import pytest

import studio.llm.providers as providers
from studio.llm.retries import JsonRequestBudget


def test_cache_persists_and_endpoint_identity(tmp_path: Path, monkeypatch) -> None:
    calls = []

    def request(req, **kwargs):
        calls.append(req.full_url)
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": "answer"}}]}).encode())

    monkeypatch.setattr(providers, "urlopen", request)
    provider = providers.LlamaProvider("http://localhost:8080", "local", tmp_path)
    assert provider.complete("prompt") == "answer"
    assert (
        providers.LlamaProvider("http://localhost:8080/", "local", tmp_path).complete("prompt")
        == "answer"
    )
    assert len(calls) == 1
    assert (
        providers.LlamaProvider("http://localhost:9090", "local", tmp_path).complete("prompt")
        == "answer"
    )
    assert len(calls) == 2
    assert not list(tmp_path.glob("*.tmp"))


def test_empty_response_is_not_cached(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        providers,
        "urlopen",
        lambda *a, **kw: io.BytesIO(b'{"choices":[{"message":{"content":null}}]}'),
    )
    provider = providers.LlamaProvider("http://localhost:8080", "local", tmp_path)
    with pytest.raises(ValueError, match="no text"):
        provider.complete("prompt")
    assert not list(tmp_path.iterdir())


def test_json_retry_replaces_invalid_cached_response(tmp_path: Path, monkeypatch) -> None:
    answers = iter(["broken JSON", '{"ok": true}'])
    calls = []

    def request(*args, **kwargs):
        calls.append(1)
        return io.BytesIO(
            json.dumps({"choices": [{"message": {"content": next(answers)}}]}).encode()
        )

    monkeypatch.setattr(providers, "urlopen", request)
    provider = providers.LlamaProvider("http://localhost:8080", "local", tmp_path)
    provider.complete("prompt")
    assert JsonRequestBudget().request(provider, "prompt") == {"ok": True}
    assert len(calls) == 2
    assert provider.complete("prompt") == '{"ok": true}'


def test_timeout_retries_use_configured_timeout_without_caching_failure(tmp_path, monkeypatch):
    calls = []

    def request(*args, **kwargs):
        calls.append(kwargs["timeout"])
        if len(calls) == 1:
            raise TimeoutError("timed out")
        return io.BytesIO(b'{"choices":[{"message":{"content":"{\\"ok\\":true}"}}]}')

    monkeypatch.setattr(providers, "urlopen", request)
    provider = providers.LlamaProvider("http://localhost:8080", "local", tmp_path, timeout_s=7.5)
    budget = JsonRequestBudget()
    assert budget.request(provider, "prompt") == {"ok": True}
    assert calls == [7.5, 7.5]
    assert budget.retries_used == 1
    provider.complete("prompt")
    assert len(calls) == 2
