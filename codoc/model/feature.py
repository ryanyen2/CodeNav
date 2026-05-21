from pydantic import BaseModel

from codoc.model.hlc import HLC


class Feature(BaseModel):
    uuid: str  # UUIDv7 string; time-ordered for efficient range scans
    slug: str  # human-navigable, user-editable; not guaranteed unique across branches
    title: str = ""  # 2–5 word prose display name; falls back to slug if empty
    parent_uuid: str | None = None  # LWW register: last RENAME/RESTRUCTURE wins
    intent: str = ""  # one or two sentences describing the feature; LWW register
    retired: bool = False  # set True by RETIRE transaction; never deleted
    created_at_hlc: HLC
    updated_at_hlc: HLC  # advances on any field mutation
