"""Unit tests for the ``claude`` LLM provider (single-Claude-auth reflection).

These never invoke the real ``claude`` CLI — ``shutil.which`` and
``subprocess.run`` are monkeypatched so the test asserts on the *contract*:
the argv built, the child env scrubbed of ``ANTHROPIC_API_KEY``, the JSON
envelope parsing, and the failure paths.
"""
from __future__ import annotations

import json
import subprocess

import pytest

from codoc import config
from codoc.agent.base import parse_solution


def _fake_run(stdout: str = "", *, returncode: int = 0, stderr: str = ""):
    """Build a subprocess.run replacement that records its call and returns a
    canned CompletedProcess."""
    calls: dict = {}

    def run(cmd, *args, **kwargs):  # noqa: ANN001
        calls["cmd"] = cmd
        calls["env"] = kwargs.get("env")
        calls["cwd"] = kwargs.get("cwd")
        calls["input"] = kwargs.get("input")
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)

    return run, calls


def _claude_cfg(model: str = "sonnet") -> config.LLMConfig:
    return config.LLMConfig(provider="claude", model=model)


def test_claude_happy_path_returns_result_and_parses(monkeypatch):
    envelope = json.dumps(
        {"subtype": "success", "is_error": False,
         "result": "<solution>{\"ops\": []}</solution>"}
    )
    run, _ = _fake_run(envelope)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr("subprocess.run", run)

    out = config.complete("hello", _claude_cfg())
    # complete() returns the raw model text; parse_solution recovers the JSON.
    assert parse_solution(out) == {"ops": []}


def test_claude_scrubs_api_key_and_never_bare(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-be-dropped")
    run, calls = _fake_run(json.dumps({"subtype": "success", "result": "ok"}))
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr("subprocess.run", run)

    config.complete("hi", _claude_cfg("opus"))

    assert "ANTHROPIC_API_KEY" not in calls["env"]
    assert "--bare" not in calls["cmd"]
    assert "--output-format" in calls["cmd"] and "json" in calls["cmd"]
    assert "--model" in calls["cmd"] and "opus" in calls["cmd"]
    # neutral cwd, not the repo root
    assert calls["cwd"] is not None
    # the prompt rides on STDIN, never argv (no argv re-lex / process-listing leak)
    assert calls["input"] == "hi"
    assert "hi" not in calls["cmd"]


def test_claude_rejects_flag_shaped_model(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/claude")
    # A flag-shaped model (e.g. from a malicious .env) must be refused before spawn.
    with pytest.raises(ValueError, match="flag-shaped CODOC_MODEL"):
        config.complete("hi", _claude_cfg("--dangerously-skip-permissions"))


def test_claude_not_on_path_raises(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(RuntimeError, match="claude` CLI on PATH"):
        config.complete("hi", _claude_cfg())


def test_claude_nonzero_exit_raises(monkeypatch):
    run, _ = _fake_run("", returncode=1, stderr="boom")
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr("subprocess.run", run)
    with pytest.raises(RuntimeError, match="failed"):
        config.complete("hi", _claude_cfg())


def test_claude_error_subtype_raises(monkeypatch):
    run, _ = _fake_run(json.dumps({"subtype": "error_max_turns", "result": "ran out"}))
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr("subprocess.run", run)
    with pytest.raises(RuntimeError, match="error_max_turns"):
        config.complete("hi", _claude_cfg())


def test_claude_non_json_output_raises(monkeypatch):
    run, _ = _fake_run("not json at all")
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr("subprocess.run", run)
    with pytest.raises(RuntimeError, match="non-JSON"):
        config.complete("hi", _claude_cfg())


def test_unknown_provider_still_raises(monkeypatch):
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        config.complete("hi", config.LLMConfig(provider="bogus", model="x"))


def test_default_model_tracks_provider(monkeypatch):
    monkeypatch.delenv("CODOC_MODEL", raising=False)

    monkeypatch.setenv("CODOC_PROVIDER", "claude")
    assert config.get_llm_config().model == "sonnet"

    monkeypatch.setenv("CODOC_PROVIDER", "openai")
    assert config.get_llm_config().model == "gpt-5.4-mini"

    monkeypatch.setenv("CODOC_PROVIDER", "anthropic")
    assert config.get_llm_config().model == "claude-sonnet-4-6"


def _clear_provider_env(monkeypatch):
    for var in ("CODOC_PROVIDER", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "CODOC_MODEL"):
        monkeypatch.delenv(var, raising=False)


def test_default_provider_is_keyless_claude(monkeypatch):
    # No explicit provider and NO key → keyless Claude Code, never an openai crash.
    _clear_provider_env(monkeypatch)
    cfg = config.get_llm_config()
    assert cfg.provider == "claude"
    assert cfg.model == "sonnet"
    assert cfg.api_key is None


def test_openai_key_infers_openai_provider(monkeypatch):
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
    cfg = config.get_llm_config()
    assert cfg.provider == "openai"
    assert cfg.api_key == "sk-openai-test"


def test_anthropic_key_infers_anthropic_provider(monkeypatch):
    # ANTHROPIC_API_KEY (and no OpenAI key) → the managed Anthropic API path.
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    cfg = config.get_llm_config()
    assert cfg.provider == "anthropic"
    assert cfg.api_key == "sk-ant-test"
    assert cfg.model == "claude-sonnet-4-6"


def test_stray_openai_model_ignored_on_claude_path(monkeypatch):
    # A globally-exported CODOC_MODEL=gpt-… must not leak onto the claude CLI path.
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("CODOC_PROVIDER", "claude")
    monkeypatch.setenv("CODOC_MODEL", "gpt-5.4-mini")
    assert config.get_llm_config().model == "sonnet"


def test_stray_claude_model_ignored_on_openai_path(monkeypatch):
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("CODOC_PROVIDER", "openai")
    monkeypatch.setenv("CODOC_MODEL", "sonnet")
    assert config.get_llm_config().model == "gpt-5.4-mini"


def test_compatible_explicit_model_is_respected(monkeypatch):
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("CODOC_PROVIDER", "claude")
    monkeypatch.setenv("CODOC_MODEL", "opus")
    assert config.get_llm_config().model == "opus"


def test_explicit_provider_overrides_key_inference(monkeypatch):
    # An explicit CODOC_PROVIDER always wins over key-presence inference.
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
    monkeypatch.setenv("CODOC_PROVIDER", "claude")
    assert config.get_llm_config().provider == "claude"


def test_anthropic_provider_routes_to_anthropic(monkeypatch):
    # provider='anthropic' must call the Anthropic SDK, NOT the keyless claude CLI.
    captured: dict = {}

    class _FakeMessages:
        def create(self, **kwargs):  # noqa: ANN001
            captured.update(kwargs)

            class _Block:
                type = "text"
                text = "<solution>{\"ops\": []}</solution>"

            class _Msg:
                content = [_Block()]

            return _Msg()

    class _FakeAnthropic:
        def __init__(self, **kwargs):  # noqa: ANN001
            captured["init"] = kwargs
            self.messages = _FakeMessages()

    import types

    fake_mod = types.SimpleNamespace(Anthropic=_FakeAnthropic)
    monkeypatch.setitem(__import__("sys").modules, "anthropic", fake_mod)

    out = config.complete(
        "hi", config.LLMConfig(provider="anthropic", model="claude-sonnet-4-6", api_key="sk-ant-x")
    )
    assert parse_solution(out) == {"ops": []}
    assert captured["init"]["api_key"] == "sk-ant-x"
    assert captured["model"] == "claude-sonnet-4-6"
