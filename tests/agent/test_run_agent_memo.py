"""Response memoization in run_agent — identical re-issued prompts must not
re-bill; malformed samples must NOT be cached (so a retry gets a fresh draw)."""
from __future__ import annotations

import pytest

from codoc.agent import base
from codoc.config import LLMConfig


@pytest.fixture(autouse=True)
def _fresh_memo():
    base._memo.clear()
    yield
    base._memo.clear()


def _cfg() -> LLMConfig:
    return LLMConfig(provider="openai", model="m", temperature=0.0)


def test_identical_prompt_hits_memo(monkeypatch):
    calls = []

    def fake_complete(prompt, cfg, *, prefix_parts=None):
        calls.append(prompt)
        return '<solution>{"ops": []}</solution>'

    monkeypatch.setattr(base, "complete", fake_complete)
    assert base.run_agent("p1", _cfg(), prefix_parts=["stable"]) == {"ops": []}
    assert base.run_agent("p1", _cfg(), prefix_parts=["stable"]) == {"ops": []}
    assert len(calls) == 1  # second call served from the memo

    base.run_agent("p2", _cfg(), prefix_parts=["stable"])
    assert len(calls) == 2  # different prompt → fresh call


def test_prefix_parts_and_model_key_the_memo(monkeypatch):
    calls = []
    monkeypatch.setattr(base, "complete",
                        lambda p, c, *, prefix_parts=None: (calls.append(1), '{"ops": []}')[1])
    base.run_agent("p", _cfg(), prefix_parts=["a"])
    base.run_agent("p", _cfg(), prefix_parts=["b"])          # different prefix
    base.run_agent("p", _cfg().model_copy(update={"model": "m2"}))  # different model
    assert len(calls) == 3


def test_malformed_response_not_cached(monkeypatch):
    responses = iter(["no json here at all", '<solution>{"ops": []}</solution>'])
    calls = []

    def fake_complete(prompt, cfg, *, prefix_parts=None):
        calls.append(1)
        return next(responses)

    monkeypatch.setattr(base, "complete", fake_complete)
    with pytest.raises(ValueError):
        base.run_agent("p", _cfg())
    # The failure was not cached — the retry re-calls and succeeds.
    assert base.run_agent("p", _cfg()) == {"ops": []}
    assert len(calls) == 2


def test_memo_bounded(monkeypatch):
    monkeypatch.setattr(base, "complete",
                        lambda p, c, *, prefix_parts=None: '{"ops": []}')
    for i in range(base._MEMO_MAX + 10):
        base.run_agent(f"p{i}", _cfg())
    assert len(base._memo) == base._MEMO_MAX
