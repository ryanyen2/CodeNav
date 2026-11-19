"""Who may write what: the roles table and the policy the applier runs
(docs/concurrency.md, Roles and authority).

``Roles`` maps an actor to a role and answers, for one op against the current
store, whether that actor may write it. The helpers around it say whether an
op is part of a comment, whose words a delete names, and whether a plan is the
actor's own; the inbox drain uses the same helpers to turn a contributor's
trunk edit into a plan of theirs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from codoc.calculus import ops as K
from codoc.calculus.ids import LOOP, MACHINE_KINDS, actor_kind
from codoc.calculus.ops import Op
from codoc.calculus.store import Store
from codoc.calculus.text import AnchorError
from codoc.workspace.config import read_config
from codoc.workspace.inbox import hub_actor

OWNER = "owner"
SUGGEST = "suggest"
NONE = "none"                  # recorded on revocation: not a role, the absence of one
COMMENT = "comment"
# The ops a comment is made of, besides the `block.add` that mints it.
_COMMENT_EDITS = frozenset({K.TEXT_INSERT, K.TEXT_DELETE, K.TEXT_MARK, K.BLOCK_SET})


def _withdrawing_own_empty_plan(store: Store, op: Op) -> bool:
    """Taking back a proposal of your own that nobody has written into is not a
    verdict on anybody's work: there is nothing to decide. A plan opened for a
    submission that then did not land would otherwise stand for ever, since
    ``plan.open`` has no inverse and only an owner may reject."""
    if op.kind != K.PLAN_REJECT:
        return False
    plan = store.plans.get(str(op.target.get("pid") or ""))
    return plan is not None and plan.actor == op.actor and plan.status == "open" and not plan.ops


def deleted_ops(store: Store, target: dict, payload: dict) -> set[str]:
    """The ops that minted the characters a delete names. The ``segments`` form
    lists them; the ``from``/``to`` form has to be resolved against the text.
    Used by the DRAIN to decide whether a contributor's Backspace is a proposal
    or their own words coming back; never by the policy, which has only the
    plans to reason from (and the hub's door, which validates a stranger's log,
    has only what the log carries)."""
    if "segments" in payload:
        return {str(s[0]) for s in payload["segments"] or ()}
    try:
        start, end = _ref(payload["from"]), _ref(payload["to"])
        block, path = store.locate_char(start, (str(target.get("bid") or ""), str(target.get("path") or "text")))
        return {o for o, _ in block.text(path).live_chars_between(start, end)}
    except (AnchorError, KeyError, IndexError, TypeError, ValueError):
        return set()


def _ref(v) -> tuple[str, int]:
    return (str(v[0]), int(v[1]))


def own_plan_words(store: Store, actor: str, minted_by: set[str]) -> bool:
    """Whether every one of these characters was put there by an open plan of
    ``actor``'s: their own proposed words, which are theirs to take back."""
    if not minted_by:
        return False
    for oid in minted_by:
        minted = store.by_id.get(oid)
        plan = store.plans.get(minted.plan) if minted is not None and minted.plan else None
        if plan is None or plan.actor != actor or plan.status != "open":
            return False
    return True


def is_comment_op(store: Store, kind: str, target: dict, payload: dict) -> bool:
    """Whether this op is part of writing a comment. A comment hangs off words
    rather than standing among them, so a suggester may write one on the
    trunk: there is nothing for a maintainer to accept, only a thread to
    answer. Everything else they write becomes a plan of theirs."""
    if kind == K.BLOCK_ADD:
        return payload.get("kind") == COMMENT
    if kind not in _COMMENT_EDITS:
        return False
    if kind == K.TEXT_MARK and payload.get("kind") == COMMENT:
        return True            # the anchor mark, which lives on the prose
    b = store.blocks.get(str(target.get("bid") or ""))
    return b is not None and b.kind == COMMENT


def _own_comment(store: Store, op: Op) -> bool:
    """Whether a contributor writing inside a comment is writing their own.

    Editing INSIDE somebody's thread should mean replying in it. A reply is a
    new comment block, so adding one is always free, and so is writing into one
    they minted (the words they are still typing). Deleting another person's
    words is not, and neither is resolving a thread they did not start:
    ``status`` is what the margin and ``Build it`` read, so marking a
    maintainer's request-for-work done is answering it on their behalf."""
    if op.kind == K.BLOCK_ADD:
        return True
    bid = str(op.target.get("bid") or "")
    if op.kind == K.TEXT_MARK and op.payload.get("kind") == COMMENT:
        return True            # the anchor mark on the prose, which is theirs to place
    b = store.blocks.get(bid)
    if b is None:
        return False
    if op.kind == K.BLOCK_SET and op.target.get("path") == "status":
        first = store.blocks.get(str(b.content.get("thread") or bid))
        return first is not None and first.added_by == op.actor
    return b.added_by == op.actor


@dataclass
class Roles:
    """Actor → role, and the policy the applier runs.

    - ``owner`` / ``maintainer``: everything. A git person the table does not
      name is an owner.
    - ``machine`` (agents, the loop, translators, imports): the trunk and plans,
      but no verdicts and no hand-offs; ``feature.realized`` only from the loop
      itself, which is the daemon settling evidence.
    - ``suggest`` (everyone else: a hub sign-in, and an actor the grammar does
      not name, ``unknown:x``): ``plan.open``, ops tagged with a plan the
      same actor opened, and comments. Riding someone else's open plan would
      land a stranger's words when the maintainer accepts it. A contributor does not
      have to tag anything themselves: the drain opens the plan and tags their
      ops, turning a deletion into the plan's ``suggest_del``. What
      it cannot carry under a plan (a merge, a block delete) stays refused.
      Nothing untagged but a comment: taking back words they proposed is the
      proposal getting smaller, so it rides in the plan like everything else
      (``ops.PLANNABLE`` carries ``text.delete``/``text.restore`` for exactly
      that, and the applier holds them to the plan's own characters). The
      policy reads only ``plans`` and who added a comment block, so
      ``validate-logs`` can check a log without the document it was written
      against.

    A comment is writable untagged by anyone: it hangs off words rather than
    standing among them, and there is nothing for a maintainer to accept. What
    a contributor may write there is their own: a new comment (which is what a
    reply is) and the words of one they minted. Deleting somebody else's words,
    or resolving a thread they did not start, is answering for them.
    """
    table: dict[str, str] = field(default_factory=dict)
    default: str = "suggest"

    @classmethod
    def load(cls, codoc_dir: str | Path) -> "Roles":
        """The table every clone must agree on lives in the tracked
        ``config.json`` (``roles: {actor: role}``)."""
        return cls.from_config(read_config(codoc_dir))

    @classmethod
    def from_config(cls, cfg: dict) -> "Roles":
        """The same table from a ``config.json`` already in hand, so a caller
        that has read it does not read it twice."""
        roles = cfg.get("roles")
        return cls({str(k): str(v) for k, v in roles.items()} if isinstance(roles, dict) else {})

    def role(self, actor: str) -> str:
        if actor in self.table:
            return self.table[actor]
        kind = actor_kind(actor)
        if kind in MACHINE_KINDS:
            return "machine"
        # A git person (`human:<email>`, `human:local`) can push to the
        # repository, which is where trust in a merged log comes from. A hub
        # sign-in (`inbox.hub_actor`) cannot, so it is what the hub recorded.
        if kind == "human" and not actor.startswith(hub_actor("")):
            return OWNER
        return self.default

    def policy(self, store: Store, op: Op) -> str | None:
        role = self.role(op.actor)
        if role == NONE:
            # The table says this person was REMOVED. Their session is gone, but
            # an inbox line drained after the revocation and a clone validating
            # their pull request's logs both come through here, and "everyone
            # else" is a suggester, which would make the record of removal
            # grant exactly what a contributor has.
            return f"{op.actor} has no access"
        if role in (OWNER, "maintainer"):
            return None
        if role == "machine":
            if op.kind in K.OWNER_ONLY and not (op.kind == K.FEATURE_REALIZED and op.actor == LOOP) \
                    and not _withdrawing_own_empty_plan(store, op):
                return f"{op.actor} may not write {op.kind}"
            return None
        if op.kind == K.PLAN_OPEN:
            return None
        if _withdrawing_own_empty_plan(store, op):
            return None
        if is_comment_op(store, op.kind, op.target, op.payload):
            return None if _own_comment(store, op) else f"{role} may not change another person's comment"
        if op.plan:
            plan = store.plans.get(op.plan)
            if plan is not None and plan.actor == op.actor:
                return None
            return f"{role} may only write to its own plans"
        return f"{role} may only write plans"
