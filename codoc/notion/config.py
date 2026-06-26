"""Configuration for the Notion bridge.

A dependency-free leaf module (no ``notion-client`` import) so the CLI can build
and validate config without the optional extra installed, and so tests run without
a live token. Values are sourced from the environment, mirroring how ``codoc serve``
reads its config in ``cli/main.py``.
"""
from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass

# Modules provided by the optional ``notion`` extra. Checked (not imported) so the
# CLI can fail with an actionable message instead of an opaque ImportError.
_OPTIONAL_MODULES = ("notion_client", "fastapi")


def missing_optional_deps() -> list[str]:
    """Return the names of ``notion`` extra modules that are not importable."""
    return [m for m in _OPTIONAL_MODULES if importlib.util.find_spec(m) is None]


def missing_deps_message(missing: list[str]) -> str:
    """Actionable install message for absent optional deps (empty when none)."""
    if not missing:
        return ""
    return (
        "the 'notion' extra is not installed (missing: "
        f"{', '.join(missing)}). Install it with: pip install -e '.[notion]'"
    )

# Pin the Notion API version explicitly — the native-markdown endpoints and the
# data_source.* webhook events depend on it; do not rely on the SDK's default.
DEFAULT_NOTION_VERSION = "2026-03-11"
DEFAULT_POLL_INTERVAL_SECONDS = 60

ENV_TOKEN = "CODOC_NOTION_TOKEN"
ENV_PAGE_ID = "CODOC_NOTION_PAGE_ID"
ENV_VERSION = "CODOC_NOTION_VERSION"
ENV_POLL_INTERVAL = "CODOC_NOTION_POLL_INTERVAL"
ENV_WEBHOOK_SECRET = "CODOC_NOTION_WEBHOOK_SECRET"


class NotionConfigError(ValueError):
    """A required field is missing or invalid — raised with an actionable message."""


@dataclass(frozen=True)
class NotionConfig:
    """Resolved Notion bridge configuration.

    ``token`` and ``page_id`` are required (the bridge cannot read or write a page
    without them). ``webhook_secret`` is the ``verification_token`` Notion issues on
    subscription creation; when absent the bridge runs in polling-only mode.
    """

    token: str
    page_id: str
    notion_version: str = DEFAULT_NOTION_VERSION
    poll_interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS
    webhook_secret: str | None = None

    @property
    def webhooks_enabled(self) -> bool:
        """Webhook ingress requires a verification secret to verify signatures;
        without it the bridge falls back to ``last_edited_time`` polling."""
        return bool(self.webhook_secret)

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> "NotionConfig":
        """Build config from the environment (injectable for tests).

        Raises :class:`NotionConfigError` with a clear message when a required
        field is missing or the poll interval is non-numeric/non-positive."""
        env = environ if environ is not None else os.environ
        token = (env.get(ENV_TOKEN) or "").strip()
        page_id = (env.get(ENV_PAGE_ID) or "").strip()
        if not token:
            raise NotionConfigError(
                f"{ENV_TOKEN} is not set — create an internal Notion connection and "
                "export its token (see docs/notion-deployment.md)."
            )
        if not page_id:
            raise NotionConfigError(
                f"{ENV_PAGE_ID} is not set — share the page with the connection and "
                "export its id."
            )

        version = (env.get(ENV_VERSION) or "").strip() or DEFAULT_NOTION_VERSION

        raw_interval = (env.get(ENV_POLL_INTERVAL) or "").strip()
        if raw_interval:
            try:
                poll_interval = int(raw_interval)
            except ValueError as exc:
                raise NotionConfigError(
                    f"{ENV_POLL_INTERVAL} must be an integer number of seconds, "
                    f"got {raw_interval!r}."
                ) from exc
            if poll_interval <= 0:
                raise NotionConfigError(
                    f"{ENV_POLL_INTERVAL} must be a positive number of seconds, "
                    f"got {poll_interval}."
                )
        else:
            poll_interval = DEFAULT_POLL_INTERVAL_SECONDS

        secret = (env.get(ENV_WEBHOOK_SECRET) or "").strip() or None
        return cls(
            token=token,
            page_id=page_id,
            notion_version=version,
            poll_interval_seconds=poll_interval,
            webhook_secret=secret,
        )
