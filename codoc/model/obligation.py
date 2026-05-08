from enum import Enum
from typing import Literal

from pydantic import BaseModel

from codoc.model.hlc import HLC


class ObligationKind(str, Enum):
    ATTRIBUTE = "attribute"                  # reflective: attribute a chunk to a feature
    RECONCILE_PROSE = "reconcile_prose"      # cascade: update intent prose of affected feature
    RECONCILE_BINDING = "reconcile_binding"  # cascade: repair a binding after structural change
    MANUAL_FIXUP = "manual_fixup"            # user-stubbed; survives commit as an open obligation


class Obligation(BaseModel):
    uuid: str  # UUIDv7
    kind: ObligationKind
    feature_uuid: str
    triggered_by_tx_hlc: HLC
    # SHA-256 of serialized inputs (feature + neighbours + chunk delta).
    # Stable inputs → stable hash → reproducible expected output shape, even when patch contents vary.
    context_hash: str
    expected_output_schema: str  # name of the patch shape the agent must produce
    context: dict  # actual inputs stored for agent dispatch; not re-hashed at resolution time
    status: Literal["pending", "in_flight", "stubbed", "resolved"] = "pending"
    result: dict | None = None  # agent output written here when resolved
