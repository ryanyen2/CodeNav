"""Loop A — code → codoc.

Deterministic change set → auto-apply the safe parts → if anything needs
judgment, ONE LLM pass returns the minimal node ops → safe ops auto-apply,
structural ops are logged as pending proposals for review in the .codoc file.

``apply_changeset`` holds the logic and takes an injectable ``propose`` callable,
so it is unit-testable with a fake store and a mocked LLM. ``run_loop_a`` wires
it to the real index + store.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from codoc.agent.tree_update import propose_tree_update
from codoc.loop import edits as edits_channel
from codoc.loop.apply import apply_op, derive_auto_ops, should_auto_apply
from codoc.loop.edits import (
    DRIFT_BINDING_LOST, DRIFT_QUESTIONED, merge_drift, write_drift,
    read_resolution, write_resolution,
)
from codoc.loop.classify import suppressed_by_hold
from codoc.loop.locks import loop_lock
from codoc.loop.divergence import Divergence, Realization, classify_realization
from codoc.loop.diff import ChangeSet, ChunkRef, compute_changeset
from codoc.loop.phase import is_held
from codoc.loop.subtree import select_relevant_subtree
from codoc.loop.title_dedup import (
    DEFAULT_THRESHOLD,
    SemanticTitleMatcher,
    make_loop_embedder,
    semantic_dedup_enabled,
)
from codoc.model.event import NodeOp, NodeOpKind, SAFE_OPS
from codoc.store.db import Store, open_store

_SNIPPET = 600


def _detect_relocations(
    cs: ChangeSet,
    removed_owner: dict[tuple[str, str], str],
) -> list[tuple[ChunkRef, ChunkRef, str]]:
    """Pair removed↔added chunks that are the same code relocated.

    A *move* is an exact content match (``tokens_hash``, any file); a *rename* is
    an AST-shape match (``types_hash``) — first same-file (pass 2), then cross-file
    (pass 3, D3) when the shape is *globally 1:1-unique* among the still-unmatched
    chunks, so an incidental shape collision never mis-pairs unrelated symbols.
    Only removed chunks that were actually bound (in ``removed_owner``) are
    candidates, since the point is to carry an existing feature attribution across.
    Returns ``(added, removed, kind)`` triples.
    """
    added = [a for a in cs.added]
    removed = [r for r in cs.removed if (r.file, r.symbol_path) in removed_owner]
    used_removed: set[tuple[str, str]] = set()
    out: list[tuple[ChunkRef, ChunkRef, str]] = []

    # Pass 1 — move: identical content (tokens_hash).
    by_tok: dict[str, list[ChunkRef]] = {}
    for r in removed:
        by_tok.setdefault(r.fingerprint, []).append(r)
    matched_added: set[tuple[str, str]] = set()
    for a in added:
        cand = next(
            (c for c in by_tok.get(a.fingerprint, [])
             if (c.file, c.symbol_path) not in used_removed),
            None,
        )
        if cand:
            used_removed.add((cand.file, cand.symbol_path))
            matched_added.add((a.file, a.symbol_path))
            out.append((a, cand, "move"))

    # Pass 2 — rename: same file, unique AST-shape (types_hash) on both sides.
    add_typ_count = Counter(
        a.types_hash for a in added if (a.file, a.symbol_path) not in matched_added
    )
    for a in added:
        if (a.file, a.symbol_path) in matched_added or not a.types_hash:
            continue
        cands = [
            c for c in removed
            if c.types_hash == a.types_hash
            and c.file == a.file
            and (c.file, c.symbol_path) not in used_removed
        ]
        if len(cands) == 1 and add_typ_count[a.types_hash] == 1:
            cand = cands[0]
            used_removed.add((cand.file, cand.symbol_path))
            matched_added.add((a.file, a.symbol_path))
            out.append((a, cand, "rename"))

    # Pass 3 — cross-file rename (D3): same AST-shape across DIFFERENT files. A
    # cross-file rename changes name + file + content at once, so it falls through
    # passes 1–2; the LLM would then re-place it as a NEW node, dropping the
    # attribution. We recover it ONLY when the shape is GLOBALLY 1:1-unique among
    # the still-unmatched chunks — exactly one unmatched add and exactly one
    # unmatched bound-removed carry that ``types_hash``. That strict uniqueness gate
    # is what keeps a common/trivial shape (shared by many chunks) from mis-pairing
    # unrelated symbols across the repo (the "moderate risk" the design flagged).
    rem_added = [a for a in added
                 if (a.file, a.symbol_path) not in matched_added and a.types_hash]
    # Pairing carries attribution, so only BOUND removed chunks (`removed`) are
    # candidates — but UNIQUENESS must be judged over the FULL change set
    # (`cs.removed`, bound + unbound). An unbound removed chunk that shares the
    # shape still makes it ambiguous; counting only bound removals here would let a
    # bound removal be mis-paired with an added chunk that is really the rename of
    # the unbound one — the exact false-attribution the gate exists to prevent.
    rem_removed = [c for c in removed
                   if (c.file, c.symbol_path) not in used_removed and c.types_hash]
    rem_add_count = Counter(a.types_hash for a in rem_added)
    rem_rem_count = Counter(
        c.types_hash for c in cs.removed
        if (c.file, c.symbol_path) not in used_removed and c.types_hash
    )
    for a in rem_added:
        if (a.file, a.symbol_path) in matched_added:
            continue
        if rem_add_count[a.types_hash] != 1 or rem_rem_count[a.types_hash] != 1:
            continue  # ambiguous shape — fall through to the LLM rather than guess
        cand = next(
            (c for c in rem_removed
             if c.types_hash == a.types_hash
             and (c.file, c.symbol_path) not in used_removed),
            None,
        )
        if cand:
            used_removed.add((cand.file, cand.symbol_path))
            matched_added.add((a.file, a.symbol_path))
            out.append((a, cand, "rename"))

    return out


def _pending_coverage(store: Store) -> tuple[set[tuple[str, str]], set[str]]:
    """What the *pending proposals* already cover, so Loop A doesn't duplicate them.

    Returns ``(claimed_chunks, claimed_features)``:
    - ``claimed_chunks`` — every ``(file, symbol_path)`` named in a pending op's
      bindings (e.g. an agent-submitted ADD_NODE or a prior proposal). Re-proposing
      a home for these would create a duplicate node.
    - ``claimed_features`` — feature ids with a pending RETIRE/AMEND/MOVE. The agent
      (or an earlier pass) already raised that structural change.

    This is the dedup that lets agent-driven MCP reflection and the automatic Loop A
    verification net coexist without double proposals.
    """
    claimed_chunks: set[tuple[str, str]] = set()
    claimed_features: set[str] = set()
    for e in store.pending_events():
        op = e.op
        for b in op.bindings:
            claimed_chunks.add((b[0], b[1]))
        if op.kind in (NodeOpKind.RETIRE_NODE, NodeOpKind.AMEND, NodeOpKind.MOVE_NODE) \
                and op.feature_id:
            claimed_features.add(op.feature_id)
    return claimed_chunks, claimed_features


def _norm_title(t: str | None) -> str:
    return re.sub(r"\s+", " ", (t or "").strip().lower())


def _live_title_parent_keys(feats) -> set[tuple[str, str | None]]:
    """``(normalized_title, parent_id)`` for every live feature — the soft
    feature-identity key (D2).

    ``UNIQUE(file, symbol_path)`` structurally dedups *bound* nodes but is silent
    on binding-LESS ones (org-pass theme parents, plan placeholders), so the LLM /
    org pass can re-propose a same-named sibling under the same parent. This set
    lets the apply loop recognise that collision and FOLD the duplicate instead of
    minting it."""
    return {(_norm_title(f.title), f.parent_id) for f in feats if not f.retired}


def _unbound_features_by_title(feats, bound_ids: set[str]) -> dict[str, str]:
    """``normalized title → feature_id`` for every live feature that owns NO code.

    These are adoptable: an ADD_NODE the LLM/coverage net would mint with the
    same title is the SAME concept (e.g. a hand-added empty node the model
    re-proposed), so we bind into the existing node instead of minting a
    duplicate-titled sibling. Pure over the pass's preloaded feature list + the
    one-query ``bound_feature_ids`` set (no per-feature binding lookups).
    """
    out: dict[str, str] = {}
    for f in feats:
        if f.retired or f.id in bound_ids:
            continue
        out.setdefault(_norm_title(f.title), f.id)
    return out


def _placeholder_owner(placeholders: list, symbol_path: str, *, sole_ok: bool) -> str | None:
    """An unrealized, still-unbound plan placeholder that should ADOPT this new
    symbol rather than have a duplicate node minted for it.

    Prefers a placeholder whose title/description names the symbol; falls back to
    the *sole* placeholder when ``sole_ok`` (a Loop B post-implementation reflect,
    where the agent wrote this code expressly for the one accepted plan node).
    Adopting flips the placeholder ``realized`` via the ATTACH in ``_mutate``.
    ``placeholders`` is the pass's live candidate list (the caller removes a
    placeholder once adopted, mirroring the old per-call unbound re-query).
    """
    if not placeholders:
        return None
    leaf = symbol_path.split("::", 1)[-1].split(".")[-1].lower()
    leaf_compact = leaf.replace("_", "")
    for f in placeholders:
        hay = f"{f.title} {f.description or ''}".lower()
        hay_compact = re.sub(r"[\s_]+", "", hay)
        if leaf and (leaf in hay or (leaf_compact and leaf_compact in hay_compact)):
            return f.id
    if sole_ok and len(placeholders) == 1:
        return placeholders[0].id
    return None


def _gc_superseded_proposals(
    store: Store, removed_keys: frozenset[tuple[str, str]] = frozenset()
) -> int:
    """Drop pending proposals whose premise is no longer true.

    - ``ADD_NODE``: every chunk it would introduce is already bound elsewhere.
    - ``RETIRE_NODE``: the target feature still owns code that THIS change set is
      not removing. A retire is only ever raised when a feature lost its *last*
      binding; once code rebinds (a mid-implementation lull, or the agent
      reflected the code in) the proposal is a false positive. ``removed_keys`` is
      the set of ``(file, symbol)`` being detached this pass — they don't count as
      "still owns code", so a retire raised by the very removal in flight is NOT
      GC'd (the dedup path still suppresses a duplicate retire for it). Human ``~``
      retires never sit pending here (Loop B applies them immediately), so every
      pending retire is an auto proposal safe to clear this way.

    Without this a stale proposal lingers forever, pinning status at
    ``code_drift`` so a no-op ``codoc sync`` never converges to ``in_sync`` — and,
    for a stale retire, lets the user wrongly retire a feature that still owns
    live code.
    """
    dropped = 0
    for e in store.pending_events():
        op = e.op
        superseded = False
        if op.kind is NodeOpKind.ADD_NODE and op.bindings and all(
            store.binding_at(f, s) is not None for f, s in op.bindings
        ):
            superseded = True
        elif op.kind is NodeOpKind.RETIRE_NODE and op.feature_id:
            f = store.get_feature(op.feature_id)
            if f and not f.retired and any(
                (b.file, b.symbol_path) not in removed_keys
                for b in store.bindings_for_feature(op.feature_id)
            ):
                superseded = True
        if superseded:
            store.delete_event(e.id)
            dropped += 1
    return dropped


def _has_modified_realized(cs: ChangeSet, store: Store, claimed_features: set[str]) -> bool:
    """True if any in-place `modified` chunk is owned by a realized feature with prose
    whose description may now be stale (an amend-on-change LLM trigger candidate).

    Batched: one ``bindings_in_files`` lookup over the modified files + one
    ``get_feature`` per distinct owning feature — instead of a ``binding_at`` +
    ``get_feature`` round-trip per modified chunk. Features that already carry a
    pending amend/retire/move (``claimed_features``) are skipped so repeated
    reconcile passes don't queue duplicate AMEND proposals.
    """
    if not cs.modified:
        return False
    mod_keys = {(m.file, m.symbol_path) for m in cs.modified}
    owners = {
        b.feature_id
        for b in store.bindings_in_files({m.file for m in cs.modified})
        if (b.file, b.symbol_path) in mod_keys and b.feature_id not in claimed_features
    }
    return any(
        (f := store.get_feature(fid)) is not None
        and f.realized and not f.retired and (f.description or "").strip()
        for fid in owners
    )


def _compute_drift(
    cs: ChangeSet,
    store: Store,
    removed_owner: dict[tuple[str, str], str],
    *,
    held: set[str],
    amended: set[str],
) -> dict[str, str]:
    """The typed, doc-wins-aware per-feature drift/trust signal (KTD2/KTD5).

    Run from a loop pass that has a FRESH index (``cs`` carries the live
    ``tokens_hash``), so the signal is captured FROM the change set — not by
    re-comparing fingerprints afterward (a REFRESH applied earlier in the pass
    has already overwritten ``binding.fingerprint`` to the new hash, which would
    read as ``followed``). Called at the END of the pass so detaches/relocations
    have settled: ``removed_owner`` is the pre-detach capture of which feature
    each removed chunk belonged to.

    Three states, of which only the first two are recorded (``followed`` is the
    absence of an entry → no badge):

    - ``questioned``   — a realized feature owns a bound chunk the change set
      shows as ``modified`` (its ``tokens_hash`` changed) and whose prose was
      NOT amended this pass (``amended``). The description may now be stale.
    - ``binding-lost`` — a realized feature lost its LAST binding (every owned
      chunk left the index this pass, and nothing relocated back into it).

    Excludes held features (``held`` — doc-wins, classify row 13) and unrealized
    placeholders (``realized=False``); both are gated before the typed split, so
    a held feature is never badged even with a modified/removed chunk. Mixed
    bindings take the worst case: ``binding-lost`` (the feature has no code left)
    dominates ``questioned``."""
    out: dict[str, str] = {}

    def _realized_live(fid: str) -> bool:
        # doc-wins: a held feature is never badged drifted (D5 — same is_held
        # predicate as the emptied detection and suppressed_by_hold).
        if is_held(fid, held):
            return False
        f = store.get_feature(fid)
        return bool(f and f.realized and not f.retired)

    # questioned: a realized feature owns a modified bound chunk, prose un-amended.
    # The binding still exists (REFRESH kept the row, only the fingerprint moved),
    # so its current owner is the owner of the drifted chunk.
    for m in cs.modified:
        b = store.binding_at(m.file, m.symbol_path)
        if not b or b.feature_id in amended:
            continue
        if _realized_live(b.feature_id):
            out[b.feature_id] = DRIFT_QUESTIONED

    # binding-lost: a realized feature that owned a removed chunk and, after the
    # detaches/relocations applied this pass, has NO bindings left. Worst case —
    # overwrites a `questioned` for the same feature (no code left is graver).
    for fid in set(removed_owner.values()):
        if _realized_live(fid) and not store.bindings_for_feature(fid):
            out[fid] = DRIFT_BINDING_LOST
    return out


def _compute_impacted(cs: ChangeSet, store: Store) -> dict[str, list[str]]:
    """Phase 4: upstream dependents of changed/removed symbols.

    Returns {feature_id → [symbol_paths]} for features whose code directly
    calls or imports the changed symbols. Advisory only; never auto-applied.
    """
    changed_syms = {c.symbol_path for c in (cs.modified + cs.removed)}
    if not changed_syms:
        return {}

    dependents: set[str] = set()
    for sym in changed_syms:
        for e in store.edges_in(sym, internal_only=True):
            if e["kind"] in {"call", "import", "inherit"}:
                dependents.add(e["src_symbol"])
    dependents -= changed_syms

    dep_features: dict[str, list[str]] = {}
    for sym in dependents:
        if "::" not in sym:
            continue
        file, _ = sym.split("::", 1)
        b = store.binding_at(file, sym)
        if b:
            dep_features.setdefault(b.feature_id, []).append(sym)
    return dep_features


@dataclass
class LoopAResult:
    auto: dict[str, int] = field(default_factory=dict)        # safe-op kind → count
    applied_structural: list[NodeOp] = field(default_factory=list)
    proposed: list[NodeOp] = field(default_factory=list)      # pending review hunks
    llm_called: bool = False
    impacted: list[str] = field(default_factory=list)         # feature IDs of upstream dependents
    held_back: int = 0  # intent ops suppressed by a doc-wins hold (classify row 13)
    # U5: realize-divergence outcome for this pass — {receiving feature_id → reason}
    # for features changed BEYOND a directive's target (scope divergence). A faithful
    # realization records nothing (its badge just clears). Persisted to resolution.json.
    realize_outcomes: dict[str, str] = field(default_factory=dict)

    def summary(self) -> str:
        auto = ", ".join(f"{n} {k}" for k, n in sorted(self.auto.items())) or "none"
        parts = [f"auto: {auto}"]
        if self.proposed:
            kinds = Counter(op.kind.value for op in self.proposed)
            parts.append("proposed: " + ", ".join(f"{n} {k}" for k, n in sorted(kinds.items())))
        if self.applied_structural:
            parts.append(f"applied {len(self.applied_structural)} structural")
        if self.impacted:
            parts.append(f"{len(self.impacted)} impacted features")
        if self.held_back:
            parts.append(f"held {self.held_back} op(s) — doc edit pending")
        if self.realize_outcomes:
            parts.append(f"{len(self.realize_outcomes)} divergent realization(s)")
        return " · ".join(parts)


def apply_changeset(
    cs: ChangeSet,
    store: Store,
    *,
    source: str = "loop_a",
    propose=propose_tree_update,
    repo_name: str = "codebase",
    config=None,
    adopt_placeholders: bool = False,
    # Two orthogonal authority knobs with DIFFERENT defaults — kept separate, not
    # collapsed into one "authoritative" flag, precisely because the defaults differ:
    # bare callers (the BDD world harness, most unit tests) rely on allow_retire=True
    # (retires may be proposed) AND amend_on_change=False (no amend LLM trigger). The
    # two production entrypoints happen to pair them (run_loop_a → both off;
    # reconcile_drift → both on), but a single flag couldn't preserve both defaults.
    allow_retire: bool = True,       # False ⇒ drop LLM-proposed RETIRE ops (twitchy temporal pass)
    amend_on_change: bool = False,   # True ⇒ in-place modifications can trigger a description-amend LLM pass
    # Doc-wins + causality (classify rows 13 / 6). ``held`` = features with pending
    # doc-ahead intent (live suggestion or queued directive): code-side
    # AMEND/RETIRE/MOVE on them is deferred; binding maintenance still applies.
    # ``caused_by_map`` (feature_id → directive id) + ``default_caused_by`` stamp
    # ops produced while a realize queue is being implemented, so the IDE can group
    # the surfaced-back changes under the doc edit that triggered them.
    held: set[str] | None = None,
    caused_by_map: dict[str, str] | None = None,
    default_caused_by: str = "",
    # When given, the loop-computed per-feature drift map is persisted to
    # ``<codoc_dir>/drift.json`` so render's index-free ``write_sidecar`` can
    # re-emit it (KTD2). Bare unit-test callers pass None → drift is skipped.
    codoc_dir: str | None = None,
    # The file scope this pass examined (the watch daemon passes the edited
    # files). When set, drift is MERGED — only features re-examined this pass
    # (those owning a binding in scope) have their entry refreshed; out-of-scope
    # badges survive. None ⇒ a full pass that examined every file ⇒ full-replace.
    file_scope: set[str] | None = None,
    # D1 — semantic title dedup (opt-in). When ``embed_fn`` is given, an ADD_NODE
    # whose title is embedding-close (≥ ``semantic_threshold``) to an adoptable
    # unbound feature folds into it instead of minting a paraphrased duplicate.
    # None ⇒ exact-string dedup only (today's behavior); the run entrypoints pass a
    # warm embedder iff CODOC_SEMANTIC_DEDUP is set. Tests inject a deterministic fake.
    embed_fn=None,
    semantic_threshold: float = DEFAULT_THRESHOLD,
) -> LoopAResult:
    held = held or set()
    caused_by_map = caused_by_map or {}

    def _cause(op: NodeOp) -> str:
        if op.feature_id and op.feature_id in caused_by_map:
            return caused_by_map[op.feature_id]
        return default_caused_by

    # U5 — realize-divergence: directive id → its target feature (inverse of
    # caused_by_map), and the per-directive Realization built from the proposed
    # intent ops produced this pass. Lets us tell a directive's ON-TARGET work from
    # a change it made to ANOTHER feature (scope divergence) at end of pass.
    dir_target = {d: f for f, d in caused_by_map.items()}
    realizations: dict[str, Realization] = {}

    def _note_realization(op: NodeOp, cause: str) -> None:
        """Record a PROPOSED intent op produced under a realize directive so the
        epoch's faithfulness can be classified. On-target work is expected; an op on
        another feature / a new node is the scope-divergence signal (F3)."""
        if not cause or cause not in dir_target:
            return
        r = realizations.setdefault(cause, Realization(target_feature_id=dir_target[cause]))
        if op.kind is NodeOpKind.ADD_NODE:
            r.added_feature = True
        elif op.feature_id:
            r.touched_feature_ids.add(op.feature_id)

    def _persist_resolution(fresh: dict[str, str]) -> None:
        """Persist the realize-divergence map (receiving feature → reason): retain
        prior entries whose feature still has a pending proposal to review, then add
        this pass's fresh divergences (each backed by a pending proposal). Self-clears
        as proposals are accepted/rejected. Bare callers (no codoc_dir) skip."""
        if codoc_dir is None:
            return
        pend = {e.op.feature_id for e in store.pending_events() if e.op.feature_id}
        keep = {f: r for f, r in read_resolution(codoc_dir).items() if f in pend}
        for f, r in fresh.items():
            if f in pend:
                keep[f] = r
        write_resolution(codoc_dir, keep)

    def _classify_realizations() -> dict[str, str]:
        """End-of-pass: classify each directive's realization → {receiving feature →
        reason} for the divergent ones (faithful records nothing — its badge clears)."""
        fresh: dict[str, str] = {}
        for r in realizations.values():
            verdict = classify_realization(r)
            if verdict is Divergence.FAITHFUL:
                continue
            for recv in r.touched_feature_ids - {r.target_feature_id}:
                fresh[recv] = verdict.value
        return fresh

    # GC stale proposals first so a no-op pass can converge to in_sync even when
    # there is no change set to process. Bindings this pass is about to remove
    # don't count as "still owns code" — so a retire driven by the in-flight
    # removal survives GC (the dedup path handles it instead).
    removed_keys = frozenset((r.file, r.symbol_path) for r in cs.removed)
    gc = _gc_superseded_proposals(store, removed_keys)

    def _persist_drift_map(fresh: dict[str, str]) -> None:
        """Persist the fresh drift map: full-replace on an unscoped pass; on a
        scoped pass MERGE so a still-valid badge on a feature bound only to an
        out-of-scope file survives (it was never re-examined this pass)."""
        if codoc_dir is None:
            return
        if file_scope is None:
            write_drift(codoc_dir, fresh)
            return
        # In-scope feature ids = features re-examined this pass = those owning a
        # binding in one of the scoped files. Only these may have their entry
        # cleared/updated; everything else is preserved.
        in_scope = {b.feature_id for b in store.bindings_in_files(file_scope)}
        merge_drift(codoc_dir, fresh, in_scope=in_scope)

    if cs.is_empty():
        # An empty change set drifted nothing — persist an empty (fresh) map so a
        # feature that re-followed since the last pass loses its badge. (Scoped
        # passes still preserve out-of-scope badges via the merge.)
        _persist_drift_map({})
        _persist_resolution({})  # prune resolved divergences (no new realize ops this pass)
        return LoopAResult(auto={"gc": gc} if gc else {})

    # Feature ids whose prose was AMENDed this pass — drained into _compute_drift
    # so a feature whose description was brought back in line with the new code is
    # `followed`, not `questioned`.
    amended: set[str] = set()

    fp = cs.fingerprints()
    th = cs.types_hashes()

    # ONE feature-table read per pass — every helper below works off this list
    # (plus one-query bound-id sets) instead of re-scanning the store.
    feats = store.list_features()

    # Which feature did each removed chunk belong to (captured before detach)?
    removed_owner: dict[tuple[str, str], str] = {}
    for r in cs.removed:
        b = store.binding_at(r.file, r.symbol_path)
        if b:
            removed_owner[(r.file, r.symbol_path)] = b.feature_id

    # 1. Auto-apply the trivially-resolvable safe ops (no LLM): the removed-bound
    #    chunks DETACH here, freeing their (file, symbol) so a relocation can rebind.
    auto_ops = derive_auto_ops(cs, store)
    for op in auto_ops:
        apply_op(op, store, source=source, applied=True, fp_lookup=fp, th_lookup=th,
                 caused_by=_cause(op))
    result = LoopAResult(auto=dict(Counter(op.kind.value for op in auto_ops)))
    if gc:
        result.auto["gc"] = gc

    # 1b. Correspondence: a remove+add of the same code is a move/rename, not new
    #     work. Carry the existing feature attribution to the new location with a
    #     deterministic ATTACH — no LLM, no risk of the model dropping the chunk.
    relocations = _detect_relocations(cs, removed_owner)
    relocated_added: set[tuple[str, str]] = set()
    for added_ref, removed_ref, _kind in relocations:
        owner = removed_owner[(removed_ref.file, removed_ref.symbol_path)]
        reloc = NodeOp(
            kind=NodeOpKind.ATTACH,
            feature_id=owner,
            bindings=[(added_ref.file, added_ref.symbol_path)],
            rationale=f"{_kind}: {removed_ref.symbol_path} → {added_ref.symbol_path}",
        )
        apply_op(reloc, store, source=source, applied=True, fp_lookup=fp, th_lookup=th,
                 caused_by=_cause(reloc))
        relocated_added.add((added_ref.file, added_ref.symbol_path))
    if relocations:
        result.auto["relocate"] = result.auto.get("relocate", 0) + len(relocations)

    # 2. Features that just lost their last binding (after relocations rebind).
    #    Exclude unrealized plan placeholders: a placeholder that loses a transient
    #    binding mid-implementation reverts to awaiting-impl — it never "lost code",
    #    so it is never a retire candidate (guards the plan→implement window).
    emptied = {
        fid for fid in set(removed_owner.values())
        # doc-wins: a held feature is being re-specified, not emptied (D5 — the
        # single is_held predicate shared by all three loop hold-guards).
        if not is_held(fid, held)
        and not store.bindings_for_feature(fid)
        and (f := store.get_feature(fid)) and not f.retired and f.realized
    }
    added_unbound = [
        a for a in cs.added
        if store.binding_at(a.file, a.symbol_path) is None
        and (a.file, a.symbol_path) not in relocated_added
    ]

    # Verification-net dedup: drop anything a pending proposal already covers
    # (e.g. the agent reflected via MCP just before this pass). This makes Loop A
    # a safety net that only surfaces the GAPS, never a second proposal for the
    # same change — and lets it skip the LLM entirely when the agent covered all.
    claimed_chunks, claimed_features = _pending_coverage(store)
    added_unbound = [a for a in added_unbound if (a.file, a.symbol_path) not in claimed_chunks]
    emptied = {fid for fid in emptied if fid not in claimed_features}

    # 2b. Placeholder adoption (deterministic, no LLM): a new unbound chunk that
    #     an unrealized plan placeholder was created to host binds to THAT
    #     placeholder — not a fresh duplicate node. This is what stops the
    #     "accepted plan node ends with 0 bindings while Loop A mints function_v2"
    #     desync. ``adopt_placeholders`` (set by Loop B's post-implement reflect)
    #     lets the SOLE placeholder adopt code even without a name match.
    still_unbound = []
    bound_ids = store.bound_feature_ids()
    placeholders = [f for f in feats
                    if not f.realized and not f.retired and f.id not in bound_ids]
    for a in added_unbound:
        owner = _placeholder_owner(placeholders, a.symbol_path, sole_ok=adopt_placeholders)
        if owner:
            placeholders = [f for f in placeholders if f.id != owner]
            adopt = NodeOp(kind=NodeOpKind.ATTACH, feature_id=owner,
                           bindings=[(a.file, a.symbol_path)],
                           rationale="adopt: bound to the plan placeholder it implements")
            apply_op(adopt, store, source=source, applied=True, fp_lookup=fp,
                     th_lookup=th, caused_by=_cause(adopt))
            result.auto["adopt"] = result.auto.get("adopt", 0) + 1
        else:
            still_unbound.append(a)
    added_unbound = still_unbound

    # Phase 4: compute upstream dependents before early return (observability).
    dep_features = _compute_impacted(cs, store)
    result.impacted = list(dep_features.keys())

    # Amend-on-change: when an authoritative pass (reconcile_drift) sees in-place
    # edits to code owned by a realized feature with real prose, run the LLM so it
    # can propose a description AMEND if the change made the prose stale — even
    # when nothing was added or emptied. Gated by amend_on_change so the frequent
    # temporal pass (run_loop_a) never pays for an LLM call on every edit.
    modified_realized = amend_on_change and _has_modified_realized(
        cs, store, claimed_features | held)

    def _persist_drift() -> None:
        _persist_drift_map(_compute_drift(
            cs, store, removed_owner, held=held, amended=amended))

    if not (added_unbound or emptied or modified_realized):
        # No LLM pass — but `modified`/`removed` chunks still carry drift the badge
        # must reflect (a run_loop_a pass with modified-only changes lands here).
        _persist_drift()
        _persist_resolution({})  # no realize ops this pass → only prune resolved ones
        return result

    # 3. The single LLM pass.
    result.llm_called = True
    changes: dict = {
        "added": [
            {"file": a.file, "symbol_path": a.symbol_path, "source": a.source[:_SNIPPET]}
            for a in added_unbound
        ],
        "removed": [
            {"file": r.file, "symbol_path": r.symbol_path,
             "current_feature_id": removed_owner[(r.file, r.symbol_path)]}
            for r in cs.removed
            if removed_owner.get((r.file, r.symbol_path)) in emptied
        ],
        "modified": [
            {"file": m.file, "symbol_path": m.symbol_path, "source": m.source[:_SNIPPET],
             "current_feature_id": (b.feature_id if (b := store.binding_at(m.file, m.symbol_path)) else None)}
            for m in cs.modified
        ],
    }
    subtree, all_titles, graph_ctx = select_relevant_subtree(cs, store, features=feats)
    if graph_ctx.get("edges") or graph_ctx.get("recent"):
        changes["graph"] = graph_ctx
    if dep_features:
        changes["impacted"] = [
            {
                "feature_id": fid,
                "feature_title": (f.title if (f := store.get_feature(fid)) else fid),
                "dependent_symbols": syms[:5],
            }
            for fid, syms in dep_features.items()
        ]
    # Author intent (captured by the UserPromptSubmit hook): what the human
    # actually asked their coding agent for. Rides into the prompt so amended /
    # added descriptions can state the why instead of guessing it from the diff.
    if codoc_dir:
        try:
            from codoc.loop.intent import recent_intent
            intents = recent_intent(codoc_dir)
        except Exception:  # noqa: BLE001 — advisory context only
            intents = []
        if intents:
            changes["author_intent"] = intents

    ops = propose(changes, subtree, all_titles, repo_name=repo_name, config=config)

    # 4. Apply: safe → now; structural → pending proposal. An ADD_NODE whose
    #    (title) already names a live, still-unbound feature is the SAME concept
    #    (e.g. a hand-added empty node the model re-proposed) — rewrite it to an
    #    ATTACH onto that node so we never mint a duplicate-titled sibling.
    bound_ids_now = store.bound_feature_ids()
    unbound_titles = _unbound_features_by_title(feats, bound_ids_now)
    # D1 — semantic dedup (opt-in): a matcher over the SAME adoptable unbound nodes,
    # used only as the fallback when the exact-string title match misses.
    matcher = None
    if embed_fn is not None:
        unbound_pairs = [(f.title, f.id) for f in feats
                         if not f.retired and f.id not in bound_ids_now]
        matcher = SemanticTitleMatcher(embed_fn, unbound_pairs, threshold=semantic_threshold)
    # D2 — feature-identity guard: the soft-unique (normalized_title, parent_id)
    # key for every live node, grown as this batch emits ADDs so two identical
    # binding-less ADDs in one LLM response also collapse to one.
    live_title_parent = _live_title_parent_keys(feats)
    for op in ops:
        # The temporal index diff (run_loop_a) is twitchy — a feature can look
        # "emptied" mid-edit and rebind a save later. Never let that path surface a
        # destructive RETIRE; only the authoritative state pass (reconcile_drift,
        # allow_retire=True) may. Stale retires are also GC'd once code rebinds.
        if op.kind is NodeOpKind.RETIRE_NODE and not allow_retire:
            continue
        # Doc-wins (classify row 13): a feature with pending doc-ahead intent is
        # being re-specified by the user — defer code-side intent ops on it (even
        # a small auto-applicable AMEND would rewrite the prose under their edit).
        if suppressed_by_hold(op, held):
            result.held_back += 1
            continue
        if op.kind is NodeOpKind.ADD_NODE and op.bindings:
            existing = unbound_titles.get(_norm_title(op.title))
            rationale = "dedup: bound to existing same-title node"
            if not existing and matcher is not None:
                # D1: exact match missed — try a semantic (paraphrase) match.
                existing = matcher.best_match(op.title)
                if existing:
                    rationale = "dedup: bound to existing near-duplicate-title node (semantic)"
            if existing:
                dedup = NodeOp(kind=NodeOpKind.ATTACH, feature_id=existing,
                               bindings=op.bindings, rationale=rationale)
                apply_op(dedup, store, source=source, applied=True, fp_lookup=fp,
                         th_lookup=th, caused_by=_cause(dedup))
                result.auto["attach"] = result.auto.get("attach", 0) + 1
                continue
        # D2 — feature-identity guard for binding-LESS ADDs: a new theme parent /
        # placeholder whose (normalized title, parent) already names a live feature
        # is that same node (the org pass re-proposing it, or a duplicate within
        # this batch). Fold it — minting would create the duplicate the UNIQUE
        # binding constraint can't catch, since the node carries no binding.
        if op.kind is NodeOpKind.ADD_NODE and not op.bindings:
            key = (_norm_title(op.title), op.parent_id)
            if key in live_title_parent:
                result.auto["dedup_node"] = result.auto.get("dedup_node", 0) + 1
                continue
            live_title_parent.add(key)
        # An AMEND (applied small one, or a proposed larger one) means the prose
        # for this feature was addressed this pass → it is no longer `questioned`.
        if op.kind is NodeOpKind.AMEND and op.feature_id:
            amended.add(op.feature_id)
        applied = should_auto_apply(op, store)
        cause = _cause(op)
        apply_op(op, store, source=source, applied=applied, fp_lookup=fp, th_lookup=th,
                 caused_by=cause)
        if not applied:
            result.proposed.append(op)
            _note_realization(op, cause)  # U5: track a proposed op against its directive
        elif op.kind not in SAFE_OPS:
            result.applied_structural.append(op)
        else:
            # An applied safe op (e.g. an LLM AMEND/ATTACH small enough to
            # auto-apply) is a real tree mutation — surface it in the summary so
            # the user is never told "nothing changed" while a description was
            # silently rewritten.
            result.auto[op.kind.value] = result.auto.get(op.kind.value, 0) + 1

    # 5. Coverage net: never silently drop an added chunk the LLM failed to place.
    #    A chunk named in any op (applied ATTACH/ADD_NODE *or* a pending ADD_NODE
    #    proposal) is already placed; only genuinely unplaced chunks fall through.
    covered_by_ops = {b for op in ops for b in op.bindings}
    _cover_uncovered_adds(added_unbound, covered_by_ops, store, result, fp, th, source,
                          cause=_cause)
    # U5: classify this epoch's realizations → flag features changed beyond a
    # directive's target (scope divergence) for "review what the AI did" (F3).
    fresh_div = _classify_realizations()
    result.realize_outcomes = fresh_div
    _persist_resolution(fresh_div)
    _persist_drift()
    return result


def _cover_uncovered_adds(
    added_unbound: list,
    covered_by_ops: set[tuple[str, str]],
    store: Store,
    result: LoopAResult,
    fp: dict[tuple[str, str], str],
    th: dict[tuple[str, str], str],
    source: str,
    cause=None,
) -> None:
    from codoc.graph.query import neighbor_feature

    cause = cause or (lambda op: "")

    for a in added_unbound:
        if (a.file, a.symbol_path) in covered_by_ops:
            continue  # placed by an LLM op (applied or pending proposal)
        if store.binding_at(a.file, a.symbol_path) is not None:
            continue  # already bound
        owner = neighbor_feature(store, a.symbol_path)
        if owner:
            op = NodeOp(
                kind=NodeOpKind.ATTACH,
                feature_id=owner,
                bindings=[(a.file, a.symbol_path)],
                rationale="coverage: attached to graph-neighbor feature",
            )
            apply_op(op, store, source=source, applied=True, fp_lookup=fp, th_lookup=th,
                     caused_by=cause(op))
            result.auto["attach"] = result.auto.get("attach", 0) + 1
        else:
            op = NodeOp(
                kind=NodeOpKind.ADD_NODE,
                title=a.symbol_path.split("::", 1)[-1],
                description="",
                bindings=[(a.file, a.symbol_path)],
                rationale="coverage: unplaced added chunk — needs a home",
            )
            apply_op(op, store, source=source, applied=False, fp_lookup=fp, th_lookup=th,
                     caused_by=cause(op))
            result.proposed.append(op)


def run_loop_a(
    root_dir: str,
    codoc_dir: str,
    *,
    file_scope: set[str] | None = None,
    source: str = "loop_a",
    repo_name: str = "codebase",
    config=None,
    adopt_placeholders: bool = False,
) -> LoopAResult:
    from codoc.graph.query import update_graph

    # Shared codoc-loop lock: serialize this whole pass against Loop B and any other
    # Loop A across processes (daemon / CLI / hub / Stop-hook), so the store + tree.codoc
    # never interleave between this pass's diff and its render (loop/locks.py).
    with loop_lock(codoc_dir):
        cs = compute_changeset(root_dir, codoc_dir, file_scope=file_scope)
        held, cb_map, default_cb = _doc_intent(codoc_dir)
        # Only pay the (heavy) embedder model load when there are ADDITIONS that could
        # need semantic dedup — a pure modify/remove/refresh pass never mints an
        # ADD_NODE, so it never consults the matcher (the common watch-save case).
        embed_fn = (make_loop_embedder()
                    if cs.added and semantic_dedup_enabled() else None)
        with open_store(codoc_dir) as store:
            update_graph(store, cs.rows, cs.touched_files())
            # Temporal index diff: never retire (a mid-edit "emptied" can rebind on the
            # next save). Retires are the authoritative state pass's job (reconcile_drift).
            result = apply_changeset(cs, store, source=source, repo_name=repo_name,
                                     config=config, adopt_placeholders=adopt_placeholders,
                                     allow_retire=False, held=held,
                                     caused_by_map=cb_map, default_caused_by=default_cb,
                                     codoc_dir=codoc_dir, file_scope=file_scope,
                                     embed_fn=embed_fn)
            # Block `lift` (U3): re-derive persistent, LIFT-capable blocks (e.g. diagrams)
            # from the freshly-updated graph/bindings. Read-only on code (attribution),
            # and doc-wins — a block on a held feature is skipped, so a human's pending
            # edit is never clobbered. Re-render the sidecar so a refreshed diagram shows.
            from codoc.blocks.refresh import refresh_lift_blocks

            if refresh_lift_blocks(store, codoc_dir):
                from codoc.codoc_file.render import write_sidecar
                write_sidecar(store, codoc_dir)

            from codoc.loop.status import refresh_status

            refresh_status(codoc_dir, store)
            return result


def _doc_intent(codoc_dir: str) -> tuple[set[str], dict[str, str], str]:
    """Pending doc-ahead intent for a pass: ``(held, caused_by_map, default_caused_by)``.

    ``held`` (live suggestions ∪ queued directives) drives doc-wins suppression;
    the manifest's feature→directive map (+ the sole directive id as the default)
    stamps ``caused_by`` on ops produced while the realize queue is implemented.
    Once ``/codoc:sync`` deletes the queue, all three are empty again."""
    held = edits_channel.hold_set(codoc_dir)
    manifest = edits_channel.read_manifest(codoc_dir)
    cb_map = {d.feature_id: d.id for d in manifest if d.feature_id}
    default_cb = manifest[0].id if len(manifest) == 1 else ""
    return held, cb_map, default_cb


def _backfill_types_hashes(store: Store, rows, bindings: list | None = None) -> int:
    """D4: backfill ``types_hash`` on bound chunks attributed without an AST shape
    (legacy rows, MCP/propose binds), reading the shape from the current index.
    Idempotent (only fills empties) and event-free — so rename detection works on
    the NEXT edit instead of staying permanently blind for those bindings. Returns
    the number of bindings filled. Runs in the authoritative reconcile pass, which
    already holds the full index ``rows`` — and, on a scoped pass, that pass's
    already-fetched ``bindings`` (scoped sweeps cover the touched files now; the
    unscoped recovery passes still sweep everything)."""
    row_th = {(r.file, r.symbol_path): r.types_hash for r in rows if r.types_hash}
    if not row_th:
        return 0
    filled = 0
    for b in (bindings if bindings is not None else store.all_bindings()):
        if not b.types_hash:
            th = row_th.get((b.file, b.symbol_path))
            if th and store.backfill_types_hash(b.file, b.symbol_path, th):
                filled += 1
    return filled


def _scoped_bindings(store: Store, file_scope: set[str] | None) -> list:
    return (store.bindings_in_files(file_scope) if file_scope is not None
            else store.all_bindings())


def _materialize_divergent_rows(
    codoc_dir: str,
    rows_light,
    bindings: list,
    file_scope: set[str] | None,
    *,
    full: bool,
) -> list:
    """Return the index rows with SOURCE filled in only where it's needed.

    An in-sync reconcile pass reads the light identity projection (no source)
    and never materializes a chunk body. Source is fetched for the files that
    actually diverged — a chunk that was added, whose fingerprint changed, or
    whose sibling symbol was REMOVED (a removed-only file still lands in the
    graph scope, and re-extracting its surviving rows from ``source=''`` would
    silently wipe the file's call/import edges — P0 from the 2026-08-01 review).
    ``full`` sources every file (a never-built graph needs one full extraction).
    """
    if full:
        candidates = {r.file for r in rows_light}
    else:
        scoped = (rows_light if file_scope is None
                  else [r for r in rows_light if r.file in file_scope])
        scoped_keys = {(r.file, r.symbol_path) for r in scoped}
        by_key = {(b.file, b.symbol_path): b for b in bindings}
        candidates = set()
        for r in scoped:  # added or fingerprint-changed chunks
            b = by_key.get((r.file, r.symbol_path))
            if b is None or (b.fingerprint and b.fingerprint != r.tokens_hash):
                candidates.add(r.file)
        candidates.update(  # removed-symbol files (binding present, chunk gone)
            b.file for b in bindings if (b.file, b.symbol_path) not in scoped_keys)

    if not candidates:
        return rows_light
    from codoc.pipelines.indexing.reader import read_all_chunks

    sourced = {
        (r.file, r.symbol_path): r
        for r in read_all_chunks(codoc_dir, files=candidates, with_embeddings=False)
    }
    return [sourced.get((r.file, r.symbol_path), r) for r in rows_light]


def _state_changeset(rows, store: Store, file_scope: set[str] | None,
                     bindings: list | None = None) -> ChangeSet:
    """Build a change set by comparing the index to the store's BINDINGS, not to a
    prior index snapshot. State-based ⇒ idempotent and self-healing.

    - a chunk with no binding → ``added`` (an attribution gap to close);
    - a bound chunk whose ``tokens_hash`` ≠ the binding's fingerprint → ``modified``;
    - a binding whose ``(file, symbol)`` is gone from the index → ``removed``.

    Unlike :func:`compute_changeset` (which diffs the index over time and so goes
    blind once the index advances without a reflection), this recovers a missed
    cycle: it always re-derives the full divergence between code and the tree.
    ``bindings`` lets the caller pass the pass's already-fetched scoped bindings
    (one bulk query keys the whole comparison; this used to issue one
    ``binding_at`` SELECT per chunk)."""
    scoped = rows if file_scope is None else [r for r in rows if r.file in file_scope]
    index_keys = {(r.file, r.symbol_path) for r in scoped}

    if bindings is None:
        bindings = _scoped_bindings(store, file_scope)
    by_key = {(b.file, b.symbol_path): b for b in bindings}

    added, modified = [], []
    for r in scoped:
        b = by_key.get((r.file, r.symbol_path))
        ref = ChunkRef(r.file, r.symbol_path, r.tokens_hash, r.source, r.types_hash)
        if b is None:
            added.append(ref)
        elif b.fingerprint and b.fingerprint != r.tokens_hash:
            modified.append(ref)

    removed = [
        # carry the binding's stored types_hash so a rename (shape match, new
        # name) is still recognised after the old symbol left the index.
        ChunkRef(b.file, b.symbol_path, b.fingerprint, types_hash=b.types_hash)
        for b in bindings if (b.file, b.symbol_path) not in index_keys
    ]
    return ChangeSet(added=added, removed=removed, modified=modified, rows=rows)


def heal_tree_integrity(store: Store) -> int:
    """Re-home orphaned or cyclic LIVE features so the whole tree stays reachable from
    the roots. Returns the number of features re-homed (0 when the tree is sound).

    An orphan (``parent_id`` points at a retired or missing feature) or a cycle makes a
    subtree invisible to EVERY walk-from-root surface — ``render_tree``, the doc
    projection, the sidecar — while its features stay live and bound (their chunks read
    as "covered", so Loop A never re-homes them). The result is a silent, unrecoverable
    disappearance. ``apply_op`` now rejects cycle-forming moves and re-parents children on
    retire, so this is the recovery-grade backstop for state that predates those guards or
    was written by another path — idempotent, safe to run every reconcile pass.

    Broken nodes are re-parented to root (``parent_id=None``): the minimal, always-correct
    repair (it both reattaches an orphan and breaks a cycle). Fixing a top-level orphan
    first reconnects its live descendants, so only genuinely-broken nodes move."""
    healed = 0
    # Phase 1 — orphans: a live feature whose parent is retired/missing → re-home to root.
    live = {f.id: f for f in store.list_features()}
    for f in list(live.values()):
        if f.parent_id is not None and f.parent_id not in live:
            f.parent_id = None
            f.updated_at = f.updated_at.advance()
            store.upsert_feature(f)
            healed += 1
    if healed:
        live = {f.id: f for f in store.list_features()}  # refresh after re-homing orphans

    # Phase 2 — cycles: a live feature unreachable from a root via live parent links is in
    # a cycle. BFS down from the roots; anything unvisited gets rooted to break the loop.
    children_of: dict[str | None, list[str]] = {}
    for f in live.values():
        children_of.setdefault(f.parent_id, []).append(f.id)
    reachable: set[str] = set()
    stack = list(children_of.get(None, []))
    while stack:
        nid = stack.pop()
        if nid in reachable:
            continue
        reachable.add(nid)
        stack.extend(children_of.get(nid, []))
    for f in live.values():
        if f.id not in reachable:
            f.parent_id = None
            f.updated_at = f.updated_at.advance()
            store.upsert_feature(f)
            healed += 1
    return healed


def reconcile_drift(
    root_dir: str,
    codoc_dir: str,
    *,
    file_scope: set[str] | None = None,
    source: str = "loop_a",
    repo_name: str = "codebase",
    config=None,
    adopt_placeholders: bool = False,
) -> LoopAResult:
    """Reflect code → tree by reconciling the index against the store's bindings.

    The recovery-grade counterpart to :func:`run_loop_a`: where the latter relies
    on the temporal index diff (and so silently no-ops if a cycle was missed and
    the index already advanced), this re-derives the full code↔tree divergence
    from current state. Idempotent — safe to run on daemon startup, from the Stop
    hook, and from ``codoc sync`` without producing duplicate work."""
    from codoc.graph.query import update_graph
    from codoc.loop.status import refresh_status
    from codoc.pipelines.indexing.reader import read_all_chunks
    from codoc.pipelines.indexing.runner import update_index

    # Shared codoc-loop lock: the authority pass updates the index, mutates the store,
    # and re-renders — serialize the whole thing against Loop B and any other Loop A
    # across processes so nothing interleaves between diff and render (loop/locks.py).
    with loop_lock(codoc_dir):
        update_index(root_dir, codoc_dir)
        # The authority pass walks the whole index but never needs embeddings, and
        # needs SOURCE only for the chunks that actually diverged (they feed the
        # LLM context + graph re-extraction). Read the light identity projection
        # first, find the divergent files against the bindings, then fetch source
        # for just those files — on an in-sync repo the pass never materializes a
        # single chunk body.
        rows_light = read_all_chunks(codoc_dir, with_embeddings=False, with_source=False)
        held, cb_map, default_cb = _doc_intent(codoc_dir)
        with open_store(codoc_dir) as store:
            # Recovery-grade invariant: re-home any orphaned/cyclic subtree BEFORE
            # reconciling, so a feature made unreachable (by pre-guard state or a
            # non-apply_op path) is pulled back onto a root instead of staying invisible
            # yet bound (which would keep Loop A from ever re-homing it). Heal is a
            # cheap in-memory sweep and runs on EVERY pass (the Stop hook's reflect is
            # scoped, so gating it to unscoped passes would leave hook-driven
            # workspaces unhealed forever). The types-hash backfill scans bindings, so
            # a scoped pass backfills only its scope; unscoped recovery passes
            # (startup, `codoc sync`) still sweep everything.
            heal_tree_integrity(store)
            bindings = _scoped_bindings(store, file_scope)
            _backfill_types_hashes(store, rows_light, bindings)
            # Empty-index invariant: a vanished/blank index beside real bindings is a
            # torn state (a concurrent wipe/rebuild, a deleted .codoc/lancedb), not a
            # repo where every file was deleted. Reconciling would mass-detach every
            # binding with no LLM and no proposal — refuse the pass; the next tick
            # (index rebuilt) reconciles normally.
            if not rows_light and bindings:
                import logging
                logging.getLogger(__name__).warning(
                    "codoc: index read returned 0 chunks but %d bindings exist — "
                    "skipping this reconcile pass (torn/absent index)", len(bindings))
                return LoopAResult()
            # A never-built graph (rebuilt codoc.db beside an intact index) needs a
            # one-time full extraction, which parses source — so source every file.
            graph_full_build = bool(rows_light) and not store.has_edges()
            rows = _materialize_divergent_rows(
                codoc_dir, rows_light, bindings, file_scope, full=graph_full_build)
            cs = _state_changeset(rows, store, file_scope, bindings)
            # Only pay the embedder model load when there are additions to dedup
            # (built after the changeset, so a pure-drift pass skips it entirely).
            embed_fn = (make_loop_embedder()
                        if cs.added and semantic_dedup_enabled() else None)
            # Graph maintenance is scoped to what actually changed. An EMPTY
            # changeset previously fell back to ALL files — a full tree-sitter
            # re-extraction on every clean startup / Stop-hook / no-op reconcile.
            # The full extraction now happens only when the graph has never been
            # built (graph_full_build above, which also sourced every row).
            graph_scope = ({r.file for r in rows} if graph_full_build
                           else cs.touched_files())
            update_graph(store, cs.rows, graph_scope)
            # Authoritative full-state reconciliation — the only pass allowed to raise
            # retires (a feature empty *in current state* genuinely lost its code) and
            # to propose description amends when bound code changed in place.
            result = apply_changeset(cs, store, source=source, repo_name=repo_name,
                                     config=config, adopt_placeholders=adopt_placeholders,
                                     allow_retire=True, amend_on_change=True, held=held,
                                     caused_by_map=cb_map, default_caused_by=default_cb,
                                     codoc_dir=codoc_dir, file_scope=file_scope,
                                     embed_fn=embed_fn)
            refresh_status(codoc_dir, store)
            return result
