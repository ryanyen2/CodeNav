"""Feature — a named unit of authored intent that binds to code chunks.

The tree of features is first-class authored intent; code attribution
(:class:`~codoc.model.binding.Binding`) is a secondary index. A feature carries
exactly one prose field, ``description`` (what + why), plus a short display
``title``. Identity is the stable ``id``; ``title`` is free text the user may
rename at will.

**Lifecycle (Proposal A1).** A feature's persistent state is the named
:class:`Lifecycle` enum — ``planned → active → retired`` — instead of two
independent booleans (``retired`` + ``realized``) whose four combinations
encoded a three-state machine by convention. ``lifecycle`` is the single
authoritative field; ``retired`` and ``realized`` remain as derived read-only
views (computed properties) so every existing reader and the IDE sidecar keep
working unchanged. Constructing a feature with the legacy ``retired=`` /
``realized=`` keywords still works — a validator folds them into ``lifecycle`` —
so call sites migrate incrementally.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, computed_field, model_validator

from codoc.model.hlc import HLC
from codoc.model.ids import new_feature_id


class Lifecycle(str, Enum):
    """The named, persistent state machine for a feature (Proposal A1).

    ``planned`` — an accepted ``/codoc:plan`` placeholder with no code yet; the
      first binding promotes it to ``active`` (see :meth:`Feature.realize`).
    ``active``  — a real, code-bound (or org-pass theme) feature. The default for
      every code-derived / bootstrap / hand-added node.
    ``retired`` — tombstoned; never hard-deleted.

    Transient mid-flight phases (drafting / queued / realizing / drifted /
    divergent) are NOT lifecycle states — they are a derived projection over the
    control files, computed in :mod:`codoc.loop.phase`. Lifecycle is only the
    durable identity state carried on the row."""

    PLANNED = "planned"
    ACTIVE = "active"
    RETIRED = "retired"


def _lifecycle_from_bools(retired: bool, realized: bool) -> Lifecycle:
    """Fold the legacy ``(retired, realized)`` bool pair into one lifecycle state.
    ``retired`` dominates (a retired node is terminal regardless of whether it had
    code); otherwise an unrealized node is ``planned`` and a realized one ``active``."""
    if retired:
        return Lifecycle.RETIRED
    return Lifecycle.ACTIVE if realized else Lifecycle.PLANNED


class Feature(BaseModel):
    id: str = Field(default_factory=new_feature_id)
    title: str  # 3–6 word display name (sentence case)
    description: str = ""  # the one prose field: what the feature does + why; newlines preserved
    parent_id: str | None = None  # None = top-level
    # The single named state machine. Defaults ACTIVE so every code-derived /
    # bootstrap node is born real; a plan placeholder is created PLANNED and
    # promoted on its first binding.
    lifecycle: Lifecycle = Lifecycle.ACTIVE
    created_at: HLC = Field(default_factory=HLC.now)
    updated_at: HLC = Field(default_factory=HLC.now)  # advances on any field mutation

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_bools(cls, data):
        """Back-compat: a caller (or a legacy DB row) that passes ``retired=`` /
        ``realized=`` instead of ``lifecycle=`` still works — fold the bools into
        ``lifecycle`` when ``lifecycle`` wasn't given explicitly, then drop them so
        pydantic doesn't choke on the now-nonexistent fields. An explicit
        ``lifecycle`` always wins (the bools are ignored)."""
        if not isinstance(data, dict):
            return data
        has_bools = "retired" in data or "realized" in data
        retired = bool(data.pop("retired", False))
        realized = bool(data.pop("realized", True))
        if "lifecycle" not in data and has_bools:
            data["lifecycle"] = _lifecycle_from_bools(retired, realized)
        return data

    # ── derived read-only views (deprecated; prefer `lifecycle`) ──────────────
    @computed_field  # type: ignore[prop-decorator]
    @property
    def retired(self) -> bool:
        """True iff the feature is tombstoned. Derived view of ``lifecycle``."""
        return self.lifecycle is Lifecycle.RETIRED

    @computed_field  # type: ignore[prop-decorator]
    @property
    def realized(self) -> bool:
        """True iff the feature has real code (or is a theme parent) — i.e. NOT a
        plan placeholder. Derived view of ``lifecycle`` (``active``/``retired`` →
        True, ``planned`` → False)."""
        return self.lifecycle is not Lifecycle.PLANNED

    def realize(self) -> None:
        """Promote a plan placeholder to ``active`` (its first code just bound). The
        named lifecycle transition that replaces the silent ``realized`` bool flip —
        a no-op on an already-active or retired feature."""
        if self.lifecycle is Lifecycle.PLANNED:
            self.lifecycle = Lifecycle.ACTIVE
