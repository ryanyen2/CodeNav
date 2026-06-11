"""The change-classification decision table — codoc's core routing algorithm.

Every change, from either side (code or doc), by any actor (human, agent, or
codoc's own machinery), is classified and reacted to according to ONE explicit
table. The loops keep their plumbing (diffing, LLM calls, rendering); the
*decisions* — what auto-applies, what becomes a reviewable proposal, what
queues a code-change directive, what is suppressed by a pending doc edit —
live here.

The table (rows referenced from code below and from docs/codoc-change-ledger.md):

  #  side  detected change                       origin         reaction
  1  code  modified bound chunk                  any            REFRESH binding (auto; actor=loop/mode=auto)
  2  code  removed bound chunk                   any            DETACH (auto); feature emptied AND not held
                                                                → RETIRE proposal (authority pass only)
  3  code  relocated chunk (tokens_hash move /   any            deterministic ATTACH to the prior feature (auto)
           unique types_hash rename)
  4  code  added unbound chunk                   any            LLM placement → ATTACH (auto) or ADD/MOVE
                                                                proposal (suggest); coverage net fallback
  5  code  in-place modify on realized feature   any            stale-description AMEND via LLM
           with prose, not held                                 (small → auto, large → suggest)
  6  code  any change during a realize epoch     agent          rows 1–5, ops stamped caused_by=⟨directive id⟩
  7  doc   descriptive AMEND / title edit        human/pen      apply immediately; NO directive
  8  doc   imperative AMEND · plan ADD           human/pen      apply + mint directive d-… + queue realize.md
           (realized=False) · imperative ADD ·
           RETIRE with bound code
  9  doc   any edit                              human/suggest  doc-ahead suggestion: pending intent (hold);
                                                                applied by Loop B's intent drain — the
                                                                agent-side apply (then row 7/8); the human's
                                                                only verb is Withdraw (before the drain)
 10  doc   structural op via MCP                 agent          pending proposal (suggest; code-ahead)
 11  doc   safe op via MCP (reflect/attach)      agent          auto-apply; recorded in the changes feed
                                                                as actor=agent / mode=auto
 12  verdict  accept / reject                    human          apply+consume / delete (RETIRE accept stays
                                                                detach-only unless op.delete_code)
 13  code  drift on a HELD feature (pending      any            bindings still maintained (rows 1,3);
           doc-ahead intent or queued directive)                AMEND/RETIRE/MOVE proposals SUPPRESSED
                                                                until the hold releases (doc always wins)

Rows 1–5 are mechanised by ``apply.derive_auto_ops`` + ``apply.should_auto_apply``
+ Loop A's pipeline; rows 7–9 by :func:`implies_code` (the imperative gate) +
the edits.json annotation channel; row 12 by Loop B's inbox drain; row 13 by
:func:`suppressed_by_hold`.
"""
from __future__ import annotations

import re

from codoc.model.event import NodeOp, NodeOpKind
from codoc.store.db import Store

# ---------------------------------------------------------------------------
# Rows 7/8 — the imperative gate: does a doc edit REQUEST code, or describe it?
# ---------------------------------------------------------------------------

# Obligation/directive phrases that mark a description as a REQUEST for code
# rather than a description of code that already exists. Case-insensitive.
_IMPERATIVE_CUES = (
    r"\bshould\b", r"\bmust\b", r"\bshall\b", r"\bneeds?\s+to\b", r"\bhas\s+to\b",
    r"\bhave\s+to\b", r"\bought\s+to\b", r"\bTODO\b", r"\bFIXME\b",
)
# Base-form (imperative-mood) verbs. Descriptive prose uses the 3rd person
# ("Adds", "Validates", "Provides") or a noun phrase; a directive opens a
# sentence with the bare verb ("Add …", "Validate …"). We only match these at a
# sentence start so they don't fire mid-prose.
_IMPERATIVE_VERBS = frozenset({
    "add", "implement", "create", "make", "support", "remove", "delete",
    "rename", "refactor", "introduce", "replace", "extend", "build", "write",
    "change", "update", "allow", "enable", "handle", "wire", "hook", "expose",
    "validate", "ensure", "raise", "split", "merge", "move", "rewrite", "fix",
})


def is_imperative(text: str | None) -> bool:
    """Heuristic: does this description REQUEST a code change (imperative mood)
    rather than DESCRIBE existing code?

    Two signals: (1) an obligation cue ("should", "must", "needs to", "TODO");
    (2) a sentence that opens with a bare base-form verb ("Add …", "Validate …")
    — descriptive prose uses the 3rd person ("Adds", "Validates") or a noun
    phrase. Intentionally a cheap, deterministic gate; an LLM classifier can
    replace it later if precision matters.
    """
    if not text or not text.strip():
        return False
    for cue in _IMPERATIVE_CUES:
        if re.search(cue, text, re.IGNORECASE):
            return True
    for sentence in re.split(r"(?:[.\n!?]+)", text):
        s = sentence.strip()
        if not s:
            continue
        first = re.split(r"[\s,;:]+", s, maxsplit=1)[0].lower()
        if first in _IMPERATIVE_VERBS:
            return True
    return False


def implies_code(op: NodeOp, store: Store) -> bool:
    """Row 7 vs row 8: does this tree edit REQUEST a code change (→ directive)?

    The contract is *imperative detection*: documenting existing code never
    writes code. A tree edit only realizes into code when intent is explicit.
      - AMEND: directive iff the new description is imperative ("should validate …").
        A descriptive edit ("validates …") just persists the prose (row 7).
      - ADD_NODE: directive iff it is an explicit plan placeholder (``realized``
        is False) or its description is imperative. A title-only / descriptive
        hand-added node is a node, not a build request.
      - RETIRE_NODE: directive iff the feature actually owns code to remove.
    """
    k = op.kind
    if k is NodeOpKind.AMEND:
        return is_imperative(op.description)
    if k is NodeOpKind.ADD_NODE:
        if op.realized is False:
            return True
        return is_imperative(op.description)
    if k is NodeOpKind.RETIRE_NODE:
        return bool(op.feature_id and store.bindings_for_feature(op.feature_id))
    return False


# ---------------------------------------------------------------------------
# Row 13 — doc always wins: holds suppress intent-level code→doc proposals.
# ---------------------------------------------------------------------------

# Op kinds that carry *intent* about a feature (vs binding maintenance).
# These are suppressed on a held feature; ATTACH/DETACH/REFRESH are not —
# bindings are attribution, not intent, and must stay correct regardless.
_INTENT_OPS = frozenset({NodeOpKind.AMEND, NodeOpKind.RETIRE_NODE, NodeOpKind.MOVE_NODE})


def suppressed_by_hold(op: NodeOp, held: set[str]) -> bool:
    """Row 13: True if ``op`` is an intent-level code→doc op targeting a feature
    with pending doc-ahead intent (a live suggestion or a queued directive).
    The doc edit wins; the code-side observation is deferred until the hold
    releases — binding maintenance (ATTACH/DETACH/REFRESH) is never suppressed."""
    if not held or op.kind not in _INTENT_OPS:
        return False
    return bool(op.feature_id and op.feature_id in held)
