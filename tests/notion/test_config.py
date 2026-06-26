"""U1 — NotionConfig env resolution + optional-dep guard."""
from __future__ import annotations

import pytest

from codoc.notion.config import (
    DEFAULT_NOTION_VERSION,
    DEFAULT_POLL_INTERVAL_SECONDS,
    NotionConfig,
    NotionConfigError,
    missing_deps_message,
    missing_optional_deps,
)

_MINIMAL = {"CODOC_NOTION_TOKEN": "secret_tok", "CODOC_NOTION_PAGE_ID": "page-123"}


def test_from_env_minimal_applies_defaults():
    cfg = NotionConfig.from_env(dict(_MINIMAL))
    assert cfg.token == "secret_tok"
    assert cfg.page_id == "page-123"
    assert cfg.notion_version == DEFAULT_NOTION_VERSION
    assert cfg.poll_interval_seconds == DEFAULT_POLL_INTERVAL_SECONDS
    assert cfg.webhook_secret is None
    # No verification secret → polling-only.
    assert cfg.webhooks_enabled is False


def test_from_env_full_override():
    cfg = NotionConfig.from_env({
        **_MINIMAL,
        "CODOC_NOTION_VERSION": "2026-09-01",
        "CODOC_NOTION_POLL_INTERVAL": "15",
        "CODOC_NOTION_WEBHOOK_SECRET": "whsec",
    })
    assert cfg.notion_version == "2026-09-01"
    assert cfg.poll_interval_seconds == 15
    assert cfg.webhook_secret == "whsec"
    assert cfg.webhooks_enabled is True


def test_values_are_stripped():
    cfg = NotionConfig.from_env({
        "CODOC_NOTION_TOKEN": "  tok  ",
        "CODOC_NOTION_PAGE_ID": "  pg  ",
    })
    assert cfg.token == "tok"
    assert cfg.page_id == "pg"


@pytest.mark.parametrize("env", [
    {},  # nothing set
    {"CODOC_NOTION_PAGE_ID": "pg"},  # token missing
    {"CODOC_NOTION_TOKEN": "tok"},  # page id missing
    {"CODOC_NOTION_TOKEN": "  ", "CODOC_NOTION_PAGE_ID": "pg"},  # blank token
])
def test_missing_required_fields_raise(env):
    with pytest.raises(NotionConfigError):
        NotionConfig.from_env(env)


@pytest.mark.parametrize("bad", ["abc", "0", "-5"])
def test_invalid_poll_interval_raises(bad):
    with pytest.raises(NotionConfigError):
        NotionConfig.from_env({**_MINIMAL, "CODOC_NOTION_POLL_INTERVAL": bad})


def test_frozen_config_is_immutable():
    cfg = NotionConfig.from_env(dict(_MINIMAL))
    with pytest.raises(Exception):
        cfg.token = "other"  # type: ignore[misc]


def test_missing_deps_message_empty_when_none():
    assert missing_deps_message([]) == ""


def test_missing_deps_message_lists_modules_and_install_hint():
    msg = missing_deps_message(["notion_client", "fastapi"])
    assert "notion_client" in msg and "fastapi" in msg
    assert "pip install -e '.[notion]'" in msg


def test_missing_optional_deps_returns_list():
    # Deterministic shape regardless of whether the extra is installed in this env.
    result = missing_optional_deps()
    assert isinstance(result, list)
    assert all(isinstance(m, str) for m in result)
