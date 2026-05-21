from pydantic import BaseModel

from codoc.model.hlc import HLC


class Feature(BaseModel):
    uuid: str  # UUIDv7 string; time-ordered for efficient range scans
    slug: str  # human-navigable, user-editable; not guaranteed unique across branches
    title: str = ""  # 3–6 word NL display name (sentence case); falls back to slug if empty
    parent_uuid: str | None = None  # LWW register: last RENAME/RESTRUCTURE wins
    intent: str = ""  # one or two sentences summary; LWW register
    description: str = ""  # multi-paragraph prose explaining what + why; preserved newlines
    retired: bool = False  # set True by RETIRE transaction; never deleted
    created_at_hlc: HLC
    updated_at_hlc: HLC  # advances on any field mutation
