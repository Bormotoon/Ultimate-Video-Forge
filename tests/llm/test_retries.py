from urllib.error import HTTPError

import pytest

import studio.llm.retries as retries
from studio.llm.retries import JsonRequestBudget


def test_invalid_json_refreshes_cache_and_shared_budget_bounds_attempts():
    calls = []

    class Provider:
        def complete(self, prompt, **kwargs):
            calls.append(kwargs)
            if not kwargs.get("refresh"):
                return "broken"
            return '{"ok": true}'

    budget = JsonRequestBudget(retries=3, remaining=1)
    assert budget.request(Provider(), "one") == {"ok": True}
    with pytest.raises(ValueError):
        budget.request(Provider(), "two")
    assert calls == [{}, {"refresh": True}, {}]
    assert budget.retries_used == 1 and budget.attempts == 3


def test_exhausted_transport_errors_and_validation_are_bounded():
    class Provider:
        def complete(self, prompt, **kwargs):
            raise OSError("offline")

    budget = JsonRequestBudget(2, 5)
    with pytest.raises(OSError):
        budget.request(Provider(), "test")
    assert budget.attempts == 3 and budget.remaining == 3


def test_process_control_exceptions_are_not_retried():
    class Provider:
        def complete(self, prompt):
            raise KeyboardInterrupt()

    budget = JsonRequestBudget()
    with pytest.raises(KeyboardInterrupt):
        budget.request(Provider(), "test")
    assert budget.retries_used == 0


def test_permanent_http_error_does_not_spend_budget():
    class Provider:
        def complete(self, prompt):
            raise HTTPError("http://local", 401, "unauthorized", {}, None)

    budget = JsonRequestBudget()
    with pytest.raises(HTTPError):
        budget.request(Provider(), "test")
    assert budget.attempts == 1 and budget.remaining == 5


def test_transient_backoff_honors_retry_after_and_caps_delays(monkeypatch):
    sleeps = []
    monkeypatch.setattr(retries.time, "sleep", sleeps.append)

    class Provider:
        def complete(self, prompt, **kwargs):
            raise HTTPError("http://local", 503, "busy", {"Retry-After": "12"}, None)

    budget = JsonRequestBudget(3, 3, backoff_s=10)
    with pytest.raises(HTTPError):
        budget.request(Provider(), "test")
    assert sleeps == [12, 20, 30]
    assert budget.backoff_total_s == 62
    assert budget.remaining == 0
