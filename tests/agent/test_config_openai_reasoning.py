"""The OpenAI path against reasoning models that refuse a set temperature.

codoc sent ``temperature`` on every call. Newer reasoning models (gpt-5.6-luna
among them) accept only their default and answer 400 to anything else, so every
call failed — which in the study would have meant codoc dying throughout the one
condition being measured. Verified against the live API before this was written:
``temperature=0.2`` 400s, omitting it succeeds.

The retry learns the refusal from the API rather than from a list of model names,
so the contract worth testing is that it retries once, remembers, and leaves
models that accept a temperature alone.
"""
from __future__ import annotations

import sys
import types

import pytest

from codoc import config


class _BadRequest(Exception):
    """Stands in for openai.BadRequestError, which the fake module also exports."""


def _fake_openai(refuses_temperature: bool):
    """A minimal `openai` module recording every create() it is handed."""
    calls: list[dict] = []

    class _Msg:
        content = "ok"

    class _Choice:
        message = _Msg()
        finish_reason = "stop"

    class _Response:
        choices = [_Choice()]
        usage = None

    class _Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            if refuses_temperature and "temperature" in kwargs:
                raise _BadRequest(
                    "Error code: 400 - Unsupported value: 'temperature' does not "
                    "support 0.2 with this model. Only the default (1) is supported."
                )
            return _Response()

    class _Chat:
        completions = _Completions()

    class _Client:
        def __init__(self, **_kwargs):
            self.chat = _Chat()

    mod = types.ModuleType("openai")
    mod.OpenAI = _Client
    mod.BadRequestError = _BadRequest
    return mod, calls


@pytest.fixture(autouse=True)
def _forget_learned_refusals():
    """Each test starts without what a previous one taught the process."""
    config._REFUSES_TEMPERATURE.clear()
    yield
    config._REFUSES_TEMPERATURE.clear()


def _run(monkeypatch, mod, cfg):
    monkeypatch.setitem(sys.modules, "openai", mod)
    return config._complete_openai("hello", cfg, [])


def test_a_model_that_refuses_a_temperature_is_retried_without_one(monkeypatch):
    mod, calls = _fake_openai(refuses_temperature=True)
    cfg = config.LLMConfig(provider="openai", model="gpt-5.6-luna", api_key="k")

    assert _run(monkeypatch, mod, cfg) == "ok"

    assert len(calls) == 2, "one refused call, then one without the temperature"
    assert "temperature" in calls[0]
    assert "temperature" not in calls[1]


def test_the_refusal_is_remembered_so_it_costs_one_call_not_one_per_call(monkeypatch):
    mod, calls = _fake_openai(refuses_temperature=True)
    cfg = config.LLMConfig(provider="openai", model="gpt-5.6-luna", api_key="k")

    _run(monkeypatch, mod, cfg)
    calls.clear()
    _run(monkeypatch, mod, cfg)

    assert len(calls) == 1, "the second completion should not re-learn the refusal"
    assert "temperature" not in calls[0]
    assert "gpt-5.6-luna" in config._REFUSES_TEMPERATURE


def test_a_model_that_accepts_a_temperature_still_gets_one(monkeypatch):
    mod, calls = _fake_openai(refuses_temperature=False)
    cfg = config.LLMConfig(
        provider="openai", model="gpt-5.4-mini", api_key="k", temperature=0.2
    )

    assert _run(monkeypatch, mod, cfg) == "ok"

    assert len(calls) == 1, "nothing to learn, so nothing to retry"
    assert calls[0]["temperature"] == 0.2


def test_a_400_about_anything_else_is_not_swallowed(monkeypatch):
    """Retrying every 400 without a temperature would turn a real error into a
    second identical failure and hide what the API actually said."""
    mod, _ = _fake_openai(refuses_temperature=False)

    def create(**kwargs):
        raise _BadRequest("Error code: 400 - context_length_exceeded")

    mod.OpenAI().chat.completions.create = create
    monkeypatch.setattr(
        mod, "OpenAI", lambda **_k: types.SimpleNamespace(
            chat=types.SimpleNamespace(
                completions=types.SimpleNamespace(create=create))))
    cfg = config.LLMConfig(provider="openai", model="gpt-5.6-luna", api_key="k")

    with pytest.raises(_BadRequest, match="context_length_exceeded"):
        _run(monkeypatch, mod, cfg)


def test_effort_and_verbosity_ride_along_only_when_set(monkeypatch):
    """A model that does not know these fields rejects the call, so an unset
    value must be absent rather than sent as an empty string."""
    mod, calls = _fake_openai(refuses_temperature=False)

    plain = config.LLMConfig(provider="openai", model="gpt-5.4-mini", api_key="k")
    _run(monkeypatch, mod, plain)
    assert "reasoning_effort" not in calls[0]
    assert "verbosity" not in calls[0]

    calls.clear()
    tuned = config.LLMConfig(
        provider="openai", model="gpt-5.6-luna", api_key="k",
        reasoning_effort="medium", verbosity="medium",
    )
    _run(monkeypatch, mod, tuned)
    assert calls[0]["reasoning_effort"] == "medium"
    assert calls[0]["verbosity"] == "medium"


def test_an_empty_temperature_means_send_none(monkeypatch):
    """The study sets it empty for luna, so no call is ever spent discovering
    the refusal. Unset must keep the old default, or every existing workspace
    silently changes what it asks for."""
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.delenv("CODOC_TEMPERATURE", raising=False)
    assert config.get_llm_config().temperature == 0.2

    monkeypatch.setenv("CODOC_TEMPERATURE", "")
    assert config.get_llm_config().temperature is None

    monkeypatch.setenv("CODOC_TEMPERATURE", "0.7")
    assert config.get_llm_config().temperature == 0.7

    # Nonsense falls back rather than crashing a daemon on a typo.
    monkeypatch.setenv("CODOC_TEMPERATURE", "warm")
    assert config.get_llm_config().temperature == 0.2


def test_none_means_the_field_is_absent_not_null(monkeypatch):
    """Sending temperature=None is not the same as omitting it: every client
    rejects the null, so this is the difference between working and 400."""
    mod, calls = _fake_openai(refuses_temperature=True)
    cfg = config.LLMConfig(
        provider="openai", model="gpt-5.6-luna", api_key="k", temperature=None
    )

    assert _run(monkeypatch, mod, cfg) == "ok"

    assert len(calls) == 1, "nothing to learn, because nothing was sent"
    assert "temperature" not in calls[0]


def test_the_environment_is_what_sets_them(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("CODOC_PROVIDER", "openai")
    monkeypatch.setenv("CODOC_MODEL", "gpt-5.6-luna")
    monkeypatch.setenv("CODOC_REASONING_EFFORT", "medium")
    monkeypatch.setenv("CODOC_VERBOSITY", "medium")

    cfg = config.get_llm_config()

    assert (cfg.model, cfg.reasoning_effort, cfg.verbosity) == (
        "gpt-5.6-luna", "medium", "medium")

    monkeypatch.delenv("CODOC_REASONING_EFFORT")
    monkeypatch.delenv("CODOC_VERBOSITY")
    assert config.get_llm_config().reasoning_effort is None
