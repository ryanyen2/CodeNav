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
