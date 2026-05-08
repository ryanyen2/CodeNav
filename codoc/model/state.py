from enum import Enum


class FeatureState(str, Enum):
    STUB = "stub"             # has prose, zero bindings
    DRAFTING = "drafting"     # has bindings AND (any unresolved OR intent empty OR recently created)
    STABLE = "stable"         # all bindings resolve, fingerprints match, zero open obligations, not retired
    STRAINED = "strained"     # ≥1 binding fingerprint diverged OR ≥1 open obligation on this feature
    DEPRECATED = "deprecated" # retired=True (via RETIRE transaction)
    SEVERED = "severed"       # ALL bindings fail to resolve

    # Resolution priority when multiple conditions hold simultaneously:
    # Severed > Deprecated > Strained > Drafting > Stable > Stub
