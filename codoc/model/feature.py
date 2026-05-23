from pydantic import BaseModel

from codoc.model.hlc import HLC


class Feature(BaseModel):
    uuid: str  # UUIDv7 string; time-ordered for efficient range scans
    slug: str  # human-navigable, user-editable; not guaranteed unique across branches
    title: str = ""  # 3–6 word NL display name (sentence case); falls back to slug if empty
    parent_uuid: str | None = None  # last RENAME/RESTRUCTURE wins
    intent: str = ""  # one or two sentences summary
    description: str = ""  # multi-paragraph prose explaining what + why; preserved newlines
    purpose: str = ""     # one-line WHAT this feature does in product terms
    rationale: str = ""   # one-line WHY this design, not an obvious alternative
    scenario: str = ""    # BDD multi-line: "given ...\nwhen ...\nthen ..."
    retired: bool = False  # set True by RETIRE transaction; never deleted
    # Authoring lifecycle: placeholder → feedforward_pending → realized
    status: str = "realized"
    created_at_hlc: HLC
    updated_at_hlc: HLC  # advances on any field mutation
