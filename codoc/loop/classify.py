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
  7  doc   AMEND / title edit                    human/pen      apply + mint a HELD-DRAFT directive (NOT
                                                                realized until an explicit hand-off). No prose
                                                                inspection — the SYSTEM never guesses intent
                                                                from English mood.
  8  doc   plan ADD (realized=False) ·           human          apply + mint directive handed-off ON MINT (an
           RETIRE with bound code · steer (> …)                explicit code request: a plan flag, a destructive
                                                                ~, or a note addressed to the agent)
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
+ Loop A's pipeline; rows 7–8 by :func:`edit_mints_directive` (a STRUCTURAL gate —
no prose heuristic) + the held-draft hand-off model in Loop B's finalize; row 12 by
Loop B's inbox drain; row 13 by :func:`suppressed_by_hold`.

Robustness note: the previous ``is_imperative`` regex (matching "should"/"must" and a
hardcoded base-form-verb set, with hyphen/pronoun guards) tried to GUESS from prose
whether a doc edit requested code. That is fundamentally unreliable — false positives
on descriptive prose that opens with a verb, false negatives on requests phrased as
nouns, and a typo-fix on imperative text re-firing a directive. It is DELETED. Intent
is now expressed by an explicit, typed gesture (hand-off / steer / plan-flag /
RETIRE-with-code), never inferred from English mood.
"""
from __future__ import annotations

from codoc.model.event import NodeOp, NodeOpKind
from codoc.store.db import Store

# ---------------------------------------------------------------------------
# Rows 7/8 — does this tree edit MINT a code-change directive? (structural)
# ---------------------------------------------------------------------------


def edit_mints_directive(op: NodeOp, store: Store) -> bool:
    """Does this tree edit mint a realize directive at all? STRUCTURAL — it never
    inspects prose. (Whether the minted directive is realized NOW or held as a draft
    is a separate decision: the held-draft hand-off model in Loop B's finalize.)

      - AMEND with a description: mints a directive (born held; realized only on an
        explicit hand-off). A description edit is documentation by default, never
        surprise code, but it carries a draft the user can choose to realize — no
        prose-guessing about whether "this sentence sounds imperative".
      - AMEND that only renames (``description`` is None): mints NOTHING. Naming a
        node is doc curation; a directive built from it has no intent to state — it
        rendered as "New intent: None" and handed the agent a nonsense ask (observed
        live: a user typing a new node's title settled as set_title commands, and
        two of the three items in their realize queue were these).
      - ADD_NODE: mints iff it is an explicit plan placeholder (``realized`` is False).
        A descriptive / title-only hand-added node is a node, not a build request, and
        an authored "plan" toggle is the explicit way to request one.
      - RETIRE_NODE: mints iff the feature actually owns bound code to remove.
      - MOVE_NODE / binding ops: never mint (structural reorganization is not code work).
    """
    k = op.kind
    if k is NodeOpKind.AMEND:
        return op.description is not None
    if k is NodeOpKind.ADD_NODE:
        return op.realized is False
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
    releases — binding maintenance (ATTACH/DETACH/REFRESH) is never suppressed.

    The "is this feature held?" test is delegated to the single
    :func:`~codoc.loop.phase.is_held` predicate (D5), so this suppression and the
    other two loop guards (``emptied`` detection, drift) share one definition."""
    if not held or op.kind not in _INTENT_OPS:
        return False
    from codoc.loop.phase import is_held

    return is_held(op.feature_id, held)
