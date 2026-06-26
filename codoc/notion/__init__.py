"""Notion host — a file-channel client that makes a Notion page an ongoing
authoring surface for the codoc feature tree.

This package is the architectural sibling of ``codoc/serve/`` (the deployed hub):
a separate process that reads ``.codoc/*``, writes only the verdict/draft/intent
channels under the shared filelock, and **never writes** ``tree.codoc``. It adds
two pure mapping modules — :mod:`codoc.notion.render` (store + sidecar → Notion
blocks) and :mod:`codoc.notion.parse` (Notion blocks → ``ParsedTree``) — so it
reuses ``diff_codoc`` and Loop B unchanged.

The Notion SDK (``notion-client``) and FastAPI are optional; they are imported
lazily inside the entrypoints so the base ``codoc`` CLI stays light. Install with
``pip install -e '.[notion]'``.
"""
from __future__ import annotations

from codoc.notion.config import NotionConfig

__all__ = ["NotionConfig"]
