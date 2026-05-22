from datetime import datetime
from enum import Enum

from pydantic import BaseModel

from codoc.model.hlc import HLC


class TransactionKind(str, Enum):
    # Reflective kinds: proposed by the reflective pipeline; require user acceptance before becoming canonical.
    INTRODUCE = "introduce"
    ABSORB = "absorb"
    EVICT = "evict"
    RETIRE_REFLECTIVE = "retire_reflective"
    REATTRIBUTE = "reattribute"
    FRACTURE = "fracture"
    COALESCE = "coalesce"
    RENAME_INFER = "rename_infer"
    MOVED = "moved"          # code-side move/rename detected by chunk matcher
    # Intentional v1: user-authored mutations that are canonical immediately.
    AMEND = "amend"
    RENAME = "rename"
    RETIRE = "retire"
    # Intentional Phase 2 (reserved; handlers not implemented at v1).
    SPLIT = "split"
    MERGE = "merge"
    RESTRUCTURE = "restructure"
    REWIND = "rewind"
    # Intentional Phase 3.
    BRANCH = "branch"
    MERGE_BRANCH = "merge_branch"
    # Intentional Phase 5.
    INSTATE_CONSTRAINT = "instate_constraint"
    LIFT_CONSTRAINT = "lift_constraint"
    # Administrative — written by post-commit hook; immediately canonical.
    SNAPSHOT = "snapshot"


REFLECTIVE_KINDS: frozenset[TransactionKind] = frozenset({
    TransactionKind.INTRODUCE,
    TransactionKind.ABSORB,
    TransactionKind.EVICT,
    TransactionKind.RETIRE_REFLECTIVE,
    TransactionKind.REATTRIBUTE,
    TransactionKind.FRACTURE,
    TransactionKind.COALESCE,
    TransactionKind.RENAME_INFER,
    TransactionKind.MOVED,
})

INTENTIONAL_V1_KINDS: frozenset[TransactionKind] = frozenset({
    TransactionKind.AMEND,
    TransactionKind.RENAME,
    TransactionKind.RETIRE,
})


class Transaction(BaseModel):
    hlc: HLC  # total-order key; unique per transaction in the DAG
    parent_hlcs: list[HLC]  # single entry in Phase 1; multiple at Phase 3 branch merges
    kind: TransactionKind
    payload: dict  # kind-specific structure; validated by pipeline handlers, not here
    author: str  # user-id or "reflective" for pipeline-proposed transactions
    proposal: bool = False  # True → pending user review; not yet canonical
    accepted_at: datetime | None = None  # wall-clock moment the user accepted from the proposal queue
    label: str | None = None  # validation gate label: accept-verbatim / accept-light-edit / accept-heavy-edit / reject
