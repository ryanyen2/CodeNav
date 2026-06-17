"""Divergence detection — did the AGENT's realization match the HUMAN's intent? (U5)

The human→agent loop: a code-implying tree edit queues a realize directive
(``d-…``) for its target feature; the agent implements it and reflects the result
back through Loop A. This module answers the question that closes that loop —
*faithful* (the agent did what was asked, on the feature asked) or *divergent*
(it did more / other) — so the IDE can auto-clear the "being realized" badge on a
faithful realization (F2) and surface the change "awaiting your review" on a
divergent one (F3).

The decision is a PURE function over op metadata Loop A already has — the
directive's target feature and the set of features that received an intent-level
op (AMEND / ADD / MOVE / RETIRE) stamped ``caused_by`` that directive during the
realize epoch (binding maintenance — ATTACH/DETACH/REFRESH — is attribution, not
intent, and never counts). Kept separate + tested so the rule is principled, not
an emergent side effect of the suppression heuristics, and tunable per OQ1.

Two signals, in priority order:

- **SCOPE** — the realization touched a feature *other* than the target, or added
  a new feature. "The AI changed more than you asked." The reliable, deterministic
  signal (it falls straight out of the change ledger's ``caused_by`` stamps).
- **INTENT** — the realization's reflected description of the TARGET drifted far
  from the stated intent text. Fuzzy by nature (imperative intent vs descriptive
  reflection), so it is OFF by default (``intent_ratio=0.0``) and opt-in; ship
  scope first, tune intent from real use (OQ1).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class Divergence(str, Enum):
    FAITHFUL = "faithful"  # did what was asked, on the feature asked — clear silently (F2)
    SCOPE = "scope"        # touched features beyond the target / added new ones (F3)
    INTENT = "intent"      # the target's reflected prose drifted far from the stated intent


@dataclass
class Realization:
    """What a single directive's realize epoch actually did, as Loop A observed it."""
    target_feature_id: str
    # Features that received an INTENT-level op (amend/move/retire) caused_by this
    # directive this epoch. Excludes the target itself only at classify time.
    touched_feature_ids: set[str] = field(default_factory=set)
    # The directive added ≥1 brand-new feature (ADD_NODE) — always a scope expansion
    # (a new node is, by definition, beyond the edited feature).
    added_feature: bool = False
    # Optional INTENT signal inputs (off unless an intent_ratio is supplied).
    intent_text: str | None = None
    realized_text: str | None = None


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (s or "").lower()))


def text_overlap(a: str | None, b: str | None) -> float:
    """Jaccard token overlap of two prose strings in [0,1] (1 == identical token
    sets, 0 == disjoint). A cheap, dependency-free similarity for the INTENT signal;
    deliberately lenient about word order / morphology so an imperative→descriptive
    rewrite of the SAME intent ("Add validation" → "Validates input") still scores
    high and is NOT flagged divergent."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def classify_realization(r: Realization, *, intent_ratio: float = 0.0) -> Divergence:
    """Faithful vs divergent for one directive's realization.

    SCOPE wins over INTENT (a realization that strayed to other features is the
    graver, more actionable signal). INTENT is only consulted when ``intent_ratio``
    > 0 AND both intent/realized texts are present; with the default 0.0 it never
    fires (scope-only first cut, OQ1)."""
    if r.added_feature or (r.touched_feature_ids - {r.target_feature_id}):
        return Divergence.SCOPE
    if intent_ratio > 0.0 and r.intent_text is not None and r.realized_text is not None:
        if text_overlap(r.intent_text, r.realized_text) < intent_ratio:
            return Divergence.INTENT
    return Divergence.FAITHFUL


def divergent_targets(
    realizations: dict[str, Realization], *, intent_ratio: float = 0.0
) -> dict[str, str]:
    """Classify a whole epoch's directives → ``{target_feature_id: reason}`` for the
    DIVERGENT ones only (faithful is the absence of an entry — the badge just clears,
    no review surface). Keyed by target feature so the sidecar can emit it like the
    drift slice. ``realizations`` is ``{directive_id: Realization}``."""
    out: dict[str, str] = {}
    for r in realizations.values():
        verdict = classify_realization(r, intent_ratio=intent_ratio)
        if verdict is not Divergence.FAITHFUL and r.target_feature_id:
            out[r.target_feature_id] = verdict.value
    return out
