"""The translation LLM pass — move an existing tree into another language.

One call per batch of features, returning ``{id: (title, description)}``. The pass is
deliberately dumb about *policy*: it translates what it is given and validates
nothing. Which nodes to send, whether a returned translation is safe to apply, and
what to do when it is not all live in :mod:`codoc.loop.translate`, so the rules that
protect authored prose are testable without an LLM.

Batched rather than one-call-per-feature because a translator benefits from seeing
sibling nodes — consistent terminology across a tree is most of what makes a
translated document read as one document — and because one call per node on a
300-node tree is 300 round trips for no gain.
"""
from __future__ import annotations

import json
import logging

from codoc.agent.base import format_prompt, load_prompt, run_agent, split_prompt
from codoc.config import LLMConfig
from codoc.doclang import DocLanguage

_log = logging.getLogger(__name__)


def propose_translations(
    features: list[dict],
    language: DocLanguage,
    *,
    repo_name: str = "codebase",
    config: LLMConfig | None = None,
) -> dict[str, tuple[str, str]]:
    """Translate a batch of ``{id, title, description}`` dicts into ``language``.

    Returns ``{feature_id: (title, description)}`` for every item the model returned
    usably, omitting the rest. Omission is not an error here: the caller reports
    untranslated nodes and leaves them in their original language, which is a
    correct partial outcome — half a tree translated and the other half intact is
    strictly better than a failed run that rolled back the good half.

    NOT the fast model tier. Every other structured-extraction call in codoc emits a
    short op list against explicit rules; this one rewrites a person's prose, where
    the cheap tier's fluency ceiling is exactly what the reader would notice.
    """
    if not features:
        return {}

    prefix_tpls, volatile_tpl = split_prompt(load_prompt("translate"))
    kwargs = dict(
        repo_name=repo_name,
        language=language.name,
        title_rule=f"A title is {language.title_rule}.",
        prose_rule=language.prose_rule,
        features=json.dumps(features, indent=2, ensure_ascii=False),
    )
    prefix_parts = [format_prompt(t, **kwargs) for t in prefix_tpls]
    volatile = format_prompt(volatile_tpl, **kwargs)
    raw = run_agent(volatile, config, prefix_parts=prefix_parts)

    rows = raw.get("features", []) if isinstance(raw, dict) else raw
    out: dict[str, tuple[str, str]] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        fid = str(row.get("id") or "").strip()
        title = row.get("title")
        description = row.get("description")
        if not fid or not isinstance(title, str) or not isinstance(description, str):
            _log.warning("codoc translate: dropping malformed row %r", row)
            continue
        out[fid] = (title.strip(), description.strip())
    return out
