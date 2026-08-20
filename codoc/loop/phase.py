"""The single ``feature_phase`` projection — one home for "where is this feature
mid-flight?" (Proposal B + D5 of docs/plans/2026-06-20-001).

Before this module the one user-facing concept *"this feature is doc-ahead /
queued / being realized / drifted / divergent"* was independently encoded **eight
ways** across six control files, recomputed in different places with different
filters — and that hand-syncing was where the merge fragility lived (a held
feature had to be excluded in three separate loop guards; a stale drift/resolution
badge survived between passes; the hold-detail map could disagree with the hold
set).

This module collapses all of that to ONE pure function, :func:`compute_phases`,
that takes the authoritative + loop-computed inputs and emits a single
:class:`Projection`. Every UI slice the sidecar carries — ``holds``,
``hold_detail``, ``feature_drift``, ``feature_resolution`` — is now a *field of
that one projection* (a thin view computed in the same pass, against the same
inputs, with the doc-wins rule applied once), plus a new ``feature_phase`` slice
that names each feature's primary phase for the per-feature dot.

The loops consume the same doc-wins rule through :func:`is_held` (D5), so "a held
feature is suppressed from emptied-detection, intent-ops, and drift" has a single
definition instead of three hand-synced inline checks.

Pure + unit-testable: :func:`compute_phases` reads no files and no store. The
IO-bound convenience :func:`project_from_store` gathers the inputs (control-file
reads + store queries) and calls it; that is what ``render.write_sidecar`` uses.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from codoc.doclang import (
    DocLanguage, detect_prose_language, resolve, workspace_doc_language,
)
from codoc.loop.edits import (
    DRIFT_BINDING_LOST,
    ORIGIN_HUMAN,
    Directive,
    hold_set,
    read_drift,
    read_manifest,
    read_resolution,
)
from codoc.model.feature import Feature
from codoc.store.db import Store


class Phase(str, Enum):
    """A feature's primary mid-flight phase — the single closed enum the
    ``feature_phase`` slice and the per-feature dot read.

    ``SYNCED`` is the common case and is the ABSENCE of an entry in the slice (no
    dot), mirroring how ``feature_drift`` omits ``followed``. The ordering of the
    other phases is their *priority* when several signals are live at once
    (see :func:`compute_phases`): a held feature (doc-ahead intent) always wins
    over a code-side drift/divergence badge — doc wins."""

    SYNCED = "synced"        # code & intent agree — no badge
    RETIRED = "retired"      # tombstoned (lifecycle=retired)
    DRAFTING = "drafting"    # a suggesting-mode draft directive, not yet handed to the agent
    QUEUED = "queued"        # doc-ahead intent / handed-off directive awaiting realization
    DIVERGENT = "divergent"  # a realization touched this feature beyond its directive (U5)
    DRIFTED = "drifted"      # bound code drifted (questioned / binding-lost), prose un-updated
    PLANNED = "planned"      # accepted plan placeholder with no code yet (lifecycle=planned)


# Phases that mean "doc-ahead intent is pending on this feature" — the hold set.
# A feature in one of these is being (re)specified by the human/agent, so the
# code-side drift/divergence observation is deferred until the hold releases.
HELD_PHASES = frozenset({Phase.DRAFTING, Phase.QUEUED})


def is_held(feature_id: str | None, held: set[str]) -> bool:
    """The single doc-wins predicate (D5).

    True when ``feature_id`` has pending doc-ahead intent — a live suggestion or a
    queued directive (the membership test against :func:`codoc.loop.edits.hold_set`).
    Loop A consults this in all three places a held feature must be excluded — the
    ``emptied`` detection, the intent-op suppression, and the drift computation — so
    the rule lives in ONE function instead of three hand-synced ``fid in held``
    checks that could drift apart."""
    return bool(feature_id and feature_id in held)


#: The four glosses, per doc language. Keyed by the directive-kind bucket
#: :func:`intent_gloss` derives, then by BCP-47 tag. This is codoc speaking to the
#: author about their own edit, so it belongs in the tree's language and not the
#: daemon's: a Chinese tree that answers "what will this do?" in English has
#: broken the recognition this line exists to provide. A language with no entry
#: falls back to English rather than to nothing — an untranslated sentence still
#: informs, a blank hover does not.
_GLOSSES: dict[str, dict[str, str]] = {
    "steer": {
        "en": "apply your note to this feature's code",
        "zh-Hans": "把你的批注应用到该功能的代码上",
        "zh-Hant": "將你的批註套用到該功能的程式碼上",
        "ja": "このメモをこの機能のコードに反映する",
        "ko": "메모를 이 기능의 코드에 반영합니다",
    },
    "retire": {
        "en": "remove this feature's code",
        "zh-Hans": "删除该功能的代码",
        "zh-Hant": "刪除該功能的程式碼",
        "ja": "この機能のコードを削除する",
        "ko": "이 기능의 코드를 제거합니다",
    },
    "add": {
        "en": "implement this feature in code",
        "zh-Hans": "在代码中实现该功能",
        "zh-Hant": "在程式碼中實作該功能",
        "ja": "この機能をコードで実装する",
        "ko": "이 기능을 코드로 구현합니다",
    },
    "amend": {
        "en": "update the code to match your new intent",
        "zh-Hans": "更新代码以符合你新的意图",
        "zh-Hant": "更新程式碼以符合你新的意圖",
        "ja": "新しい意図に合わせてコードを更新する",
        "ko": "새 의도에 맞게 코드를 업데이트합니다",
    },
}


def intent_gloss(kind: str, lang: DocLanguage | None = None) -> str:
    """A one-line, plain-language summary of what a queued directive will DO,
    surfaced as the held feature's hover title. Recognition over count: the author
    confirms codoc understood the *kind* of work their edit implied (update vs
    implement vs remove vs steer), in their own words — which means in their own
    language, so ``lang`` picks the wording (None ⇒ English)."""
    k = (kind or "").lower()
    if "steer" in k:
        bucket = "steer"
    elif "retire" in k:
        bucket = "retire"
    elif "add" in k:
        bucket = "add"
    else:
        bucket = "amend"
    by_lang = _GLOSSES[bucket]
    code = (lang or resolve(None)).code
    return by_lang.get(code) or by_lang["en"]


@dataclass(frozen=True)
class PhaseInputs:
    """Everything :func:`compute_phases` needs — all authoritative or
    loop-computed, no IO. ``features`` is every live + retired feature so a
    retired node still gets its phase; the derived sets/maps are passed in
    precomputed so the projection is a pure function over data."""

    features: list[Feature]
    bound_ids: set[str]               # feature ids owning >=1 binding (store.bound_feature_ids)
    pending_feature_ids: set[str]     # features with a pending proposal (resolution gate)
    held: set[str]                    # the hold set (intents ∪ queued directives)
    directives: list[Directive]       # the realize.json manifest
    drift: dict[str, str]             # drift.json (questioned / binding-lost), unfiltered
    resolution: dict[str, str]        # resolution.json (scope / intent divergence), unfiltered
    # The tree's authoring language, for the one author-facing sentence this
    # projection generates (`hold_detail.intent`). None ⇒ English; the field keeps
    # `compute_phases` pure, so `project_from_store` resolves it at the IO seam.
    doc_language: DocLanguage | None = None


@dataclass(frozen=True)
class Projection:
    """The single output. Every sidecar mid-flight slice is a field here, computed
    in ONE pass from ONE set of inputs — so they share a source of truth and the
    derivations can't silently drift apart.

    Note the deliberate asymmetry: the doc-wins rule (a held feature is never
    *also* badged drifted/divergent) is applied to the primary ``phase`` slice
    only, where a single dot must be chosen. The ``drift``/``resolution`` slices
    reproduce the exact former ``_live_*`` filters and do NOT additionally suppress
    held features — the loop already excludes held features when WRITING
    drift.json/resolution.json, so a held feature normally has no entry to suppress;
    a stale entry from before the hold can still appear in those slices (as it did
    pre-refactor). Consumers wanting the doc-wins-resolved state read ``phase``."""

    phase: dict[str, str]            # feature_id → Phase value (SYNCED omitted) — the new slice + dot
    holds: list[str]                 # sorted held feature ids (the `holds` slice)
    hold_detail: dict[str, dict]     # feature_id → {kind, intent, baseline} (the `hold_detail` slice)
    drift: dict[str, str]            # filtered feature_id → reason (the `feature_drift` slice)
    resolution: dict[str, str]       # filtered feature_id → reason (the `feature_resolution` slice)


def _live_drift(features_by_id: dict[str, Feature], bound_ids: set[str],
                drift: dict[str, str]) -> dict[str, str]:
    """The drift slice, filtered against live state (the former
    ``render._live_drift``, centralized). An interactive re-emit (Accept/Reject,
    MCP reflect ATTACH) re-writes the sidecar without a fresh index, so a stale
    entry can outlive the state it described until the next loop pass. Drop the
    entries the store now provably contradicts:

    - ``binding-lost`` for a feature that NOW owns a binding (an ATTACH re-bound it);
    - any entry for a feature that is now retired or absent.

    ``questioned`` for a still-live, still-bound feature is KEPT — only a loop pass
    with a fresh index can tell whether the prose drift was resolved."""
    out: dict[str, str] = {}
    for fid, state in drift.items():
        f = features_by_id.get(fid)
        if f is None or f.retired:
            continue
        if state == DRIFT_BINDING_LOST and fid in bound_ids:
            continue
        out[fid] = state
    return out


def _live_resolution(features_by_id: dict[str, Feature], pending_feature_ids: set[str],
                     resolution: dict[str, str]) -> dict[str, str]:
    """The realize-divergence slice, filtered against live state (the former
    ``render._live_resolution``, centralized). A divergence flag is only meaningful
    while its surfaced proposal is still pending review: drop it once the proposal
    is accepted/rejected (the loop's own prune handles the daemon path; this covers
    the no-loop re-render), and drop gone/retired features."""
    out: dict[str, str] = {}
    for fid, reason in resolution.items():
        f = features_by_id.get(fid)
        if f is None or f.retired or fid not in pending_feature_ids:
            continue
        out[fid] = reason
    return out


def _hold_detail(directives: list[Directive],
                 features_by_id: dict[str, Feature],
                 lang: DocLanguage | None = None) -> dict[str, dict]:
    """Per-held-feature detail (the former ``render._hold_detail``, centralized):
    the queued directive's ``kind`` + a plain-language gloss + the AMEND baseline +
    its ``origin``, keyed by feature id. Every manifest directive's feature is in the
    hold set, so this is exactly the held-features-with-a-directive subset of ``holds``.
    First directive per feature wins (the oldest — its baseline is the earliest wording
    the queue displaced, which is the right thing to diff against); a gone/retired
    feature is skipped.

    ``origin`` is the ONE field not taken from the first directive: a feature holding
    both the author's own queued edit and an accepted plan reports ``human``. The
    IDE draws origin as authorship — the author's ink or the plan's opacity — and
    "first in the manifest" is arrival order, which is not evidence about whose words
    these are. Erring toward the person is the same rule ``model.event.outranks``
    states for the same reason: they are the one party who can be asked."""
    out: dict[str, dict] = {}
    human_held = {d.feature_id for d in directives
                  if d.feature_id and d.origin == ORIGIN_HUMAN}
    for d in directives:
        if not d.feature_id or d.feature_id in out:
            continue
        f = features_by_id.get(d.feature_id)
        if f is None or f.retired:
            continue
        # The gloss follows THIS feature's language, not the workspace default: it
        # is a sentence rendered next to the node's own prose, so a Chinese default
        # would caption an English node in Chinese and vice versa. The node's prose
        # is the only thing that can settle which reads correctly there.
        out[d.feature_id] = {
            "kind": d.kind,
            "intent": intent_gloss(
                d.kind,
                detect_prose_language(f.description or f.title or "",
                                      lang or resolve(None)),
            ),
            "baseline": d.baseline,
            # WHOSE words are waiting ("human" | "plan" — edits.Directive.origin). The
            # IDE draws the two in different channels: the author's own unlanded edit is
            # their ink, an accepted plan is the plan's opacity. Without it the surface
            # has to guess, and guessing means inking the agent's accepted wording as
            # something the reader typed.
            "origin": ORIGIN_HUMAN if d.feature_id in human_held else d.origin,
        }
    return out


def _held_phase(feature_id: str, directives_by_feature: dict[str, list[Directive]]) -> Phase:
    """A held feature's phase: DRAFTING while it holds a directive the human has
    NOT yet handed off (a suggesting-mode draft); QUEUED once a directive is handed
    off, or when the hold is a live intent with no directive yet (a pending
    suggestion awaiting the loop's apply)."""
    dirs = directives_by_feature.get(feature_id, [])
    if dirs and all(not d.handed_off for d in dirs):
        return Phase.DRAFTING
    return Phase.QUEUED


def compute_phases(inp: PhaseInputs) -> Projection:
    """The single mid-flight projection (Proposal B). Pure over :class:`PhaseInputs`.

    Computes every UI slice once, then assigns each feature ONE primary
    :class:`Phase` by a fixed priority so the doc-wins rule (a held feature is
    never also badged drifted/divergent) is applied in exactly one place:

        retired  >  held (drafting / queued)  >  divergent  >  drifted  >  planned  >  synced

    The ``drift``/``resolution`` slice fields preserve the exact former
    ``_live_*`` filters (so existing sidecar consumers are byte-for-byte
    unchanged); the doc-wins suppression of those signals is applied only to the
    primary ``phase`` slice, where a single dot must be chosen."""
    features_by_id = {f.id: f for f in inp.features}
    directives_by_feature: dict[str, list[Directive]] = {}
    for d in inp.directives:
        if d.feature_id:
            directives_by_feature.setdefault(d.feature_id, []).append(d)

    drift = _live_drift(features_by_id, inp.bound_ids, inp.drift)
    resolution = _live_resolution(features_by_id, inp.pending_feature_ids, inp.resolution)
    hold_detail = _hold_detail(inp.directives, features_by_id, inp.doc_language)

    phase: dict[str, str] = {}
    for f in inp.features:
        fid = f.id
        if f.retired:
            p = Phase.RETIRED
        elif is_held(fid, inp.held):
            p = _held_phase(fid, directives_by_feature)
        elif fid in resolution:
            p = Phase.DIVERGENT
        elif fid in drift:
            p = Phase.DRIFTED
        elif not f.realized and fid not in inp.bound_ids:
            p = Phase.PLANNED
        else:
            p = Phase.SYNCED
        if p is not Phase.SYNCED:
            phase[fid] = p.value

    return Projection(
        phase=phase,
        holds=sorted(inp.held),
        hold_detail=hold_detail,
        drift=drift,
        resolution=resolution,
    )


def project_from_store(store: Store, codoc_dir: str) -> Projection:
    """Gather the inputs (control-file reads + store queries) and run
    :func:`compute_phases`. The IO seam ``render.write_sidecar`` calls — keeping
    the projection logic itself pure and testable."""
    features = store.list_features(include_retired=True)
    pending_feature_ids = {e.op.feature_id for e in store.pending_events() if e.op.feature_id}
    return compute_phases(PhaseInputs(
        features=features,
        bound_ids=store.bound_feature_ids(),
        pending_feature_ids=pending_feature_ids,
        held=hold_set(codoc_dir),
        directives=read_manifest(codoc_dir),
        drift=read_drift(codoc_dir),
        resolution=read_resolution(codoc_dir),
        doc_language=workspace_doc_language(codoc_dir),
    ))
