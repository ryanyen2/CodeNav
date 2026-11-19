"""The provider layer with the `claude` CLI faked (no model is called)."""
from __future__ import annotations

from codoc.llm import provider
from tests.helpers import fake_claude


def test_a_call_keeps_the_cost_it_reports_and_adds_nothing_when_it_reports_none(monkeypatch):
    fake_claude(monkeypatch, {"result": "a", "total_cost_usd": 0.0125}, {"result": "b"})
    cfg = provider.LLMConfig("claude", "haiku")
    mark = provider.spent()
    assert provider.complete("p", cfg) == "a" and provider.cost_since(mark) == 0.0125
    mark = provider.spent()
    assert provider.complete("p", cfg) == "b" and provider.cost_since(mark) is None
