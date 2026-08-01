"""The repo-.env loading policy (codoc/config.py).

codoc runs against ARBITRARY user repos, so the repo's own .env must be a FALLBACK
only (never override the real shell environment), and the redirect/logging vars that
could exfiltrate keys or dump prompts must never be honored from a repo .env at all.
The load happens at import time, so these tests exercise the same helper against a
temp .env by re-running the module-level logic explicitly.
"""
from __future__ import annotations

import importlib
import os


def _apply_dotenv_policy(path, environ):
    """Mirror codoc.config's import-time policy against an explicit env dict."""
    from dotenv import dotenv_values
    from codoc.config import _UNTRUSTED_FROM_DOTENV

    for k, v in dotenv_values(str(path)).items():
        if v is None or k in _UNTRUSTED_FROM_DOTENV:
            continue
        environ.setdefault(k, v)
    return environ


def test_repo_env_is_fallback_not_override(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("CODOC_PROVIDER=openai\nCODOC_MODEL=repo-model\n")
    # The shell already chose a provider — the repo .env must NOT override it.
    environ = {"CODOC_PROVIDER": "claude"}
    _apply_dotenv_policy(env_file, environ)
    assert environ["CODOC_PROVIDER"] == "claude"      # shell wins
    assert environ["CODOC_MODEL"] == "repo-model"     # unset var filled from .env


def test_repo_env_cannot_set_redirect_or_logging_vars(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "CODOC_BASE_URL=https://evil.example\n"
        "OPENAI_BASE_URL=https://evil.example\n"
        "CODOC_LOG_PROMPTS=1\n"
        "CODOC_TEMPERATURE=0.9\n"
    )
    environ: dict = {}
    _apply_dotenv_policy(env_file, environ)
    assert "CODOC_BASE_URL" not in environ            # exfil vector refused
    assert "OPENAI_BASE_URL" not in environ
    assert "CODOC_LOG_PROMPTS" not in environ         # prompt dump refused
    assert environ["CODOC_TEMPERATURE"] == "0.9"      # benign var still honored


def test_config_module_imports_clean():
    # The import-time policy must not raise on a normal environment.
    import codoc.config as cfg
    importlib.reload(cfg)
    assert hasattr(cfg, "get_llm_config")
