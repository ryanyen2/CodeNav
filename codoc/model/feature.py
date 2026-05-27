"""Feature — a named unit of authored intent that binds to code chunks.

The tree of features is first-class authored intent; code attribution
(:class:`~codoc.model.binding.Binding`) is a secondary index. A feature carries
exactly one prose field, ``description`` (what + why), plus a short display
``title``. Identity is the stable ``id``; ``title`` is free text the user may
rename at will.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from codoc.model.hlc import HLC
from codoc.model.ids import new_feature_id


class Feature(BaseModel):
    id: str = Field(default_factory=new_feature_id)
    title: str  # 3–6 word display name (sentence case)
    description: str = ""  # the one prose field: what the feature does + why; newlines preserved
    parent_id: str | None = None  # None = top-level
    retired: bool = False  # tombstoned; never hard-deleted
    # Lifecycle bit parallel to `retired` (NOT a status taxonomy): False marks an
    # accepted plan placeholder with no code yet; Loop A flips it True on the first
    # binding. Born True for every code-derived / bootstrap node.
    realized: bool = True
    created_at: HLC = Field(default_factory=HLC.now)
    updated_at: HLC = Field(default_factory=HLC.now)  # advances on any field mutation
