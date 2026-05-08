from pydantic import BaseModel

from codoc.model.hlc import HLC


class Constraint(BaseModel):
    uuid: str  # UUIDv7; OR-set identity tag so concurrent instates on different nodes merge cleanly
    feature_uuid: str
    rule: str  # natural-language invariant, e.g. "stays synchronous"
    instated_at_hlc: HLC
    lifted_at_hlc: HLC | None = None  # None → active; non-None → lifted by LIFT_CONSTRAINT
