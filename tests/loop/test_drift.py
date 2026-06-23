"""U4 — the per-feature drift/trust signal (loop-computed slice + drift.json).

Drift is computed in the loop pass (which has the fresh index) and persisted to
``.codoc/drift.json``; ``render.write_sidecar`` re-emits it passively as the
``feature_drift`` slice — never reading the index itself (KTD2).

Three states, of which only two are recorded (``followed`` is the absence of an
entry → no badge): ``questioned`` (realized feature owns a modified bound chunk,
prose un-amended) and ``binding-lost`` (realized feature lost its last binding).
Held features and unrealized placeholders are excluded (KTD5 / classify row 13).
"""
from __future__ import annotations

import pytest

from codoc.codoc_file.render import write_sidecar
from codoc.loop import edits as edits_channel
from codoc.loop.diff import ChangeSet, ChunkRef
from codoc.loop.edits import (
    DRIFT_BINDING_LOST,
    DRIFT_QUESTIONED,
    drift_path,
    read_drift,
    write_drift,
)
from codoc.loop.loop_a import apply_changeset
from codoc.model.binding import Binding
from codoc.model.feature import Feature
from codoc.store.db import open_store


@pytest.fixture
def codoc_dir(tmp_path):
    d = tmp_path / ".codoc"
    d.mkdir()
    return str(d)


@pytest.fixture
def store(codoc_dir):
    s = open_store(codoc_dir)
    yield s
    s.close()


def _propose_nothing(*_a, **_k):
    return []


def _bind(store, fid, file, sym, fp):
    store.upsert_binding(Binding(feature_id=fid, file=file, symbol_path=sym, fingerprint=fp))


def _modified(file, sym, tok="new"):
    return ChangeSet(modified=[ChunkRef(file, sym, tok, source="src")])


def _removed(file, sym, tok="old"):
    return ChangeSet(removed=[ChunkRef(file, sym, tok)])


# ─── the three states ────────────────────────────────────────────────────────

def test_modified_bound_chunk_is_questioned(store, codoc_dir):
    f = Feature(title="Validator", description="Validates input.")
    store.upsert_feature(f)
    _bind(store, f.id, "v.py", "v.py::check", "old")  # fingerprint != new code

    apply_changeset(_modified("v.py", "v.py::check"), store,
                    propose=_propose_nothing, codoc_dir=codoc_dir)

    assert read_drift(codoc_dir) == {f.id: DRIFT_QUESTIONED}


def test_lost_last_binding_is_binding_lost(store, codoc_dir):
    f = Feature(title="Validator", description="Validates input.")
    store.upsert_feature(f)
    _bind(store, f.id, "v.py", "v.py::check", "h")

    apply_changeset(_removed("v.py", "v.py::check"), store,
                    propose=_propose_nothing, codoc_dir=codoc_dir)

    assert read_drift(codoc_dir) == {f.id: DRIFT_BINDING_LOST}


def test_matching_fingerprint_is_followed_not_recorded(store, codoc_dir):
    """A chunk whose fingerprint still matches is `followed` — no entry, no badge.
    An empty change set drifts nothing → an empty (cleared) drift map."""
    f = Feature(title="Validator", description="Validates input.")
    store.upsert_feature(f)
    _bind(store, f.id, "v.py", "v.py::check", "h")

    apply_changeset(ChangeSet(), store, propose=_propose_nothing, codoc_dir=codoc_dir)

    assert read_drift(codoc_dir) == {}  # absence = followed = no badge


def test_amended_this_pass_is_not_questioned(store, codoc_dir):
    """When the LLM amends a feature's prose this pass (bringing it in line with
    the new code) the feature is `followed`, not `questioned`."""
    from codoc.model.event import NodeOp, NodeOpKind

    f = Feature(title="Validator", description="Validates input.")
    store.upsert_feature(f)
    _bind(store, f.id, "v.py", "v.py::check", "old")

    # amend_on_change so the LLM pass runs on the in-place modification; the
    # injected propose returns an AMEND for the drifted feature.
    amend = NodeOp(kind=NodeOpKind.AMEND, feature_id=f.id,
                   description="Validates and sanitizes input.")
    apply_changeset(_modified("v.py", "v.py::check"), store,
                    propose=lambda *a, **k: [amend], amend_on_change=True,
                    codoc_dir=codoc_dir)

    assert read_drift(codoc_dir) == {}  # prose was addressed → followed


# ─── doc-wins exclusions (KTD5 / classify row 13) ────────────────────────────

def test_held_feature_excluded_even_when_modified(store, codoc_dir):
    f = Feature(title="Validator", description="Validates input.")
    store.upsert_feature(f)
    _bind(store, f.id, "v.py", "v.py::check", "old")

    apply_changeset(_modified("v.py", "v.py::check"), store,
                    propose=_propose_nothing, held={f.id}, codoc_dir=codoc_dir)

    assert read_drift(codoc_dir) == {}  # doc wins — never flagged stale


def test_unrealized_placeholder_excluded(store, codoc_dir):
    """An unrealized placeholder (realized=False) is never a trust signal — losing
    its (transient) binding mid-implementation must not light a `binding-lost`
    badge. (A *modified* placeholder binding would auto-realize the node via the
    REFRESH in apply._mutate, so the removal path is what exercises this gate.)"""
    f = Feature(title="Rate limiting", description="Caps request rates.", realized=False)
    store.upsert_feature(f)
    _bind(store, f.id, "v.py", "v.py::rl", "old")

    apply_changeset(_removed("v.py", "v.py::rl"), store,
                    propose=_propose_nothing, codoc_dir=codoc_dir)

    assert read_drift(codoc_dir) == {}  # placeholder — not a trust signal yet


# ─── mixed bindings: worst case wins ─────────────────────────────────────────

def test_mixed_bindings_binding_lost_dominates_questioned(store, codoc_dir):
    """A feature with one modified binding (questioned) AND its other (last)
    binding removed lands as `binding-lost` — having no code left is graver."""
    f = Feature(title="Validator", description="Validates input.")
    store.upsert_feature(f)
    _bind(store, f.id, "v.py", "v.py::check", "old")
    _bind(store, f.id, "v.py", "v.py::also", "old")

    # both owned chunks leave the index → the feature loses its last binding.
    cs = ChangeSet(removed=[ChunkRef("v.py", "v.py::check", "old"),
                            ChunkRef("v.py", "v.py::also", "old")])
    apply_changeset(cs, store, propose=_propose_nothing, codoc_dir=codoc_dir)

    assert read_drift(codoc_dir) == {f.id: DRIFT_BINDING_LOST}


def test_one_removed_but_code_remains_is_questioned_not_lost(store, codoc_dir):
    """A feature that loses ONE binding but keeps another is not `binding-lost`;
    if its surviving binding's code changed it is `questioned` instead."""
    f = Feature(title="Validator", description="Validates input.")
    store.upsert_feature(f)
    _bind(store, f.id, "v.py", "v.py::check", "old")  # survives, but modified
    _bind(store, f.id, "v.py", "v.py::gone", "old")   # removed

    cs = ChangeSet(modified=[ChunkRef("v.py", "v.py::check", "new", source="s")],
                   removed=[ChunkRef("v.py", "v.py::gone", "old")])
    apply_changeset(cs, store, propose=_propose_nothing, codoc_dir=codoc_dir)

    assert read_drift(codoc_dir) == {f.id: DRIFT_QUESTIONED}  # still owns code


# ─── persistence + render re-emission WITHOUT an index read ───────────────────

def test_drift_written_to_control_file(store, codoc_dir):
    f = Feature(title="Validator", description="Validates input.")
    store.upsert_feature(f)
    _bind(store, f.id, "v.py", "v.py::check", "old")

    apply_changeset(_modified("v.py", "v.py::check"), store,
                    propose=_propose_nothing, codoc_dir=codoc_dir)

    p = drift_path(codoc_dir)
    assert p.exists()
    import json
    data = json.loads(p.read_text())
    assert data["drift"] == {f.id: DRIFT_QUESTIONED}


def test_write_sidecar_reemits_drift_without_reading_index(store, codoc_dir, monkeypatch):
    """render.write_sidecar re-emits the persisted drift unchanged and NEVER
    reads the index (KTD2). We persist a drift map, blow up update_index /
    read_all_chunks if anyone calls them, and assert the sidecar carries the
    drift verbatim — proving render is a pure store/file read."""
    f = Feature(title="Validator", description="Validates input.")
    store.upsert_feature(f)
    _bind(store, f.id, "v.py", "v.py::check", "h")

    # The drift signal a prior loop pass left behind.
    write_drift(codoc_dir, {f.id: DRIFT_QUESTIONED})

    def _boom(*_a, **_k):  # pragma: no cover - asserts render never indexes
        raise AssertionError("write_sidecar must not read the index")

    monkeypatch.setattr("codoc.pipelines.indexing.runner.update_index", _boom)
    monkeypatch.setattr("codoc.pipelines.indexing.reader.read_all_chunks", _boom)

    write_sidecar(store, codoc_dir)

    import json
    from codoc.codoc_file.render import BINDINGS_FILENAME
    from pathlib import Path
    sidecar = json.loads((Path(codoc_dir) / BINDINGS_FILENAME).read_text())
    assert sidecar["version"] == 6
    assert sidecar["feature_drift"] == {f.id: DRIFT_QUESTIONED}  # re-emitted unchanged


def test_write_sidecar_missing_drift_file_is_empty(store, codoc_dir):
    """No drift.json (never a loop pass) → an empty feature_drift slice, tolerant."""
    f = Feature(title="Validator", description="Validates input.")
    store.upsert_feature(f)

    write_sidecar(store, codoc_dir)

    import json
    from codoc.codoc_file.render import BINDINGS_FILENAME
    from pathlib import Path
    sidecar = json.loads((Path(codoc_dir) / BINDINGS_FILENAME).read_text())
    assert sidecar["feature_drift"] == {}


# ─── Fix 1: a SCOPED pass merges drift (out-of-scope badges survive) ──────────

def test_scoped_pass_preserves_out_of_scope_drift(store, codoc_dir):
    """A feature questioned from a prior pass (bound to file A) keeps its badge
    when a later SCOPED pass touches only file B. The scoped pass full-replacing
    drift.json would wrongly wipe the still-valid A badge."""
    fa = Feature(title="A feature", description="In file a.")
    fb = Feature(title="B feature", description="In file b.")
    store.upsert_feature(fa)
    store.upsert_feature(fb)
    _bind(store, fa.id, "a.py", "a.py::a_fn", "old")
    _bind(store, fb.id, "b.py", "b.py::b_fn", "old")

    # Prior (full) pass questioned A (its bound code drifted).
    write_drift(codoc_dir, {fa.id: DRIFT_QUESTIONED})

    # Later SCOPED pass touches only b.py — B's chunk modified. A is out of scope
    # (no binding in {b.py}) so its badge must survive.
    apply_changeset(_modified("b.py", "b.py::b_fn"), store,
                    propose=_propose_nothing, codoc_dir=codoc_dir,
                    file_scope={"b.py"})

    drift = read_drift(codoc_dir)
    assert drift[fa.id] == DRIFT_QUESTIONED   # out-of-scope — preserved
    assert drift[fb.id] == DRIFT_QUESTIONED   # in-scope — freshly computed


def test_scoped_pass_clears_in_scope_drift_that_refollowed(store, codoc_dir):
    """An in-scope feature whose code now follows again loses its badge even on a
    scoped pass (merge clears entries for re-examined features)."""
    fb = Feature(title="B feature", description="In file b.")
    store.upsert_feature(fb)
    _bind(store, fb.id, "b.py", "b.py::b_fn", "h")  # fingerprint matches new code

    write_drift(codoc_dir, {fb.id: DRIFT_QUESTIONED})  # stale prior badge

    # Scoped pass over b.py with an empty change set → B re-followed → cleared.
    apply_changeset(ChangeSet(), store, propose=_propose_nothing,
                    codoc_dir=codoc_dir, file_scope={"b.py"})

    assert read_drift(codoc_dir) == {}  # in-scope + re-followed → badge dropped


def test_full_unscoped_pass_still_clears_stale_drift(store, codoc_dir):
    """A full (file_scope=None) pass keeps the full-replace behavior: a stale
    badge for a feature that re-followed is cleared."""
    fa = Feature(title="A feature", description="In file a.")
    store.upsert_feature(fa)
    _bind(store, fa.id, "a.py", "a.py::a_fn", "h")  # matches → followed

    write_drift(codoc_dir, {fa.id: DRIFT_QUESTIONED, "f-ghost": DRIFT_BINDING_LOST})

    apply_changeset(ChangeSet(), store, propose=_propose_nothing,
                    codoc_dir=codoc_dir)  # file_scope=None → full replace

    assert read_drift(codoc_dir) == {}  # everything re-examined → all cleared


# ─── Fix 2: interactive re-emit filters drift against live store state ────────

def test_write_sidecar_drops_binding_lost_for_rebound_feature(store, codoc_dir):
    """An interactive write (Accept/Reject, MCP reflect ATTACH) re-emits drift.json
    verbatim, but a `binding-lost` badge on a feature that now owns a binding is
    contradictory — write_sidecar filters it out (pure store reads, no index)."""
    import json
    from codoc.codoc_file.render import BINDINGS_FILENAME
    from pathlib import Path

    f = Feature(title="Validator", description="Validates input.")
    store.upsert_feature(f)
    write_drift(codoc_dir, {f.id: DRIFT_BINDING_LOST})

    # An ATTACH re-bound the feature after the loop pass left the badge behind.
    _bind(store, f.id, "v.py", "v.py::check", "h")

    write_sidecar(store, codoc_dir)

    sidecar = json.loads((Path(codoc_dir) / BINDINGS_FILENAME).read_text())
    assert f.id not in sidecar["feature_drift"]  # re-bound → no contradictory badge


def test_write_sidecar_drops_drift_for_retired_feature(store, codoc_dir):
    """A drift entry for a feature that is now retired is not re-emitted."""
    import json
    from codoc.codoc_file.render import BINDINGS_FILENAME
    from pathlib import Path

    f = Feature(title="Validator", description="Validates input.")
    store.upsert_feature(f)
    write_drift(codoc_dir, {f.id: DRIFT_QUESTIONED})
    store.retire_feature(f.id)

    write_sidecar(store, codoc_dir)

    sidecar = json.loads((Path(codoc_dir) / BINDINGS_FILENAME).read_text())
    assert f.id not in sidecar["feature_drift"]  # retired → badge meaningless


def test_write_sidecar_keeps_questioned_for_live_bound_feature(store, codoc_dir):
    """A `questioned` badge on a still-live, still-bound feature is KEPT — only a
    loop pass with a fresh index can tell whether the prose drift was resolved."""
    import json
    from codoc.codoc_file.render import BINDINGS_FILENAME
    from pathlib import Path

    f = Feature(title="Validator", description="Validates input.")
    store.upsert_feature(f)
    _bind(store, f.id, "v.py", "v.py::check", "h")
    write_drift(codoc_dir, {f.id: DRIFT_QUESTIONED})

    write_sidecar(store, codoc_dir)

    sidecar = json.loads((Path(codoc_dir) / BINDINGS_FILENAME).read_text())
    assert sidecar["feature_drift"] == {f.id: DRIFT_QUESTIONED}  # kept


# ─── Fix 3: write_registry failure does not abort write_sidecar ───────────────

def test_write_sidecar_survives_write_registry_oserror(store, codoc_dir, monkeypatch):
    """A disk error writing the (pure derived) registry must not propagate out of
    write_sidecar — the bindings sidecar is still written and the loop pass
    continues (the IDE degrades gracefully when the registry is stale/absent)."""
    import json
    from codoc.codoc_file.render import BINDINGS_FILENAME
    from pathlib import Path

    f = Feature(title="Validator", description="Validates input.")
    store.upsert_feature(f)

    def _boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr("codoc.codoc_file.render.write_registry", _boom)

    write_sidecar(store, codoc_dir)  # must NOT raise

    sidecar = json.loads((Path(codoc_dir) / BINDINGS_FILENAME).read_text())
    assert f.id in sidecar["by_feature"]  # bindings sidecar completed


# ─── Fix B: drift through the PRODUCTION reconcile_drift / run_loop_a path ─────
#
# The cases above drive ``apply_changeset`` directly. The production daemon path
# is ``reconcile_drift`` (``amend_on_change=True``, ``allow_retire=True``,
# ``file_scope``, ``codoc_dir``) / ``run_loop_a``. These tests exercise those
# entrypoints end-to-end with the index layer faked out (a fixture chunk table)
# and the single LLM pass replaced by an injected deterministic ``propose`` — so
# no real index and no real LLM are needed, yet the full state-changeset →
# apply_changeset → drift-persist (scoped merge) wiring is covered.

def _chunk_row(file, sym, tok, *, types_hash="t"):
    """A minimal LanceDB ChunkRow stand-in for the faked index reader."""
    from codoc.pipelines.indexing.reader import ChunkRow

    return ChunkRow(id=0, file=file, symbol_path=sym, language="python",
                    source="def f(): ...", tokens_hash=tok, types_hash=types_hash,
                    start_byte=0, end_byte=0, embedding=None)


def _fake_index(monkeypatch, rows):
    """Make ``reconcile_drift`` / ``run_loop_a`` read ``rows`` from a fake index
    (``update_index`` is a no-op; ``read_all_chunks`` returns the fixture)."""
    monkeypatch.setattr("codoc.pipelines.indexing.runner.update_index",
                        lambda *a, **k: None)
    monkeypatch.setattr("codoc.pipelines.indexing.reader.read_all_chunks",
                        lambda *a, **k: list(rows))


def _inject_propose(monkeypatch, ops):
    """Replace the single Loop A LLM pass with a fixed op list (BDD-style).

    ``reconcile_drift`` / ``run_loop_a`` call ``apply_changeset`` WITHOUT
    forwarding ``propose``, so it uses the ``propose=propose_tree_update``
    keyword default captured at def-time. Patch that kwdefault (not just the
    module attribute) so the injected pass actually flows through the production
    entrypoints — no real LLM."""
    from codoc.loop.loop_a import apply_changeset

    monkeypatch.setitem(apply_changeset.__kwdefaults__, "propose",
                        lambda *a, **k: list(ops))


def test_reconcile_drift_writes_questioned_through_production_path(codoc_dir, monkeypatch):
    """reconcile_drift (daemon path) flags a realized feature whose bound code
    changed in place as `questioned`, persisting drift.json — proven with an
    injected propose (no LLM) and a faked index (no cocoindex)."""
    s = open_store(codoc_dir)
    try:
        f = Feature(title="Validator", description="Validates input.")
        s.upsert_feature(f)
        _bind(s, f.id, "v.py", "v.py::check", "old")  # fingerprint != new code
    finally:
        s.close()

    # The live index reports the bound chunk with a NEW tokens_hash → modified.
    _fake_index(monkeypatch, [_chunk_row("v.py", "v.py::check", "new")])
    # The LLM pass would run (amend_on_change=True) but proposes nothing → the
    # prose stays un-amended → the feature is questioned.
    _inject_propose(monkeypatch, [])

    from codoc.loop.loop_a import reconcile_drift

    reconcile_drift("root", codoc_dir)

    assert read_drift(codoc_dir) == {f.id: DRIFT_QUESTIONED}


def test_reconcile_drift_clears_when_amended_through_production_path(codoc_dir, monkeypatch):
    """When the injected LLM pass amends the drifted feature's prose, the
    production path records it as `followed` (no badge) — exercising the
    amend_on_change trigger + the amended-set drift carve-out."""
    from codoc.model.event import NodeOp, NodeOpKind

    s = open_store(codoc_dir)
    try:
        f = Feature(title="Validator", description="Validates input.")
        s.upsert_feature(f)
        _bind(s, f.id, "v.py", "v.py::check", "old")
    finally:
        s.close()

    _fake_index(monkeypatch, [_chunk_row("v.py", "v.py::check", "new")])
    _inject_propose(monkeypatch, [NodeOp(kind=NodeOpKind.AMEND, feature_id=f.id,
                                         description="Validates and sanitizes input.")])

    from codoc.loop.loop_a import reconcile_drift

    reconcile_drift("root", codoc_dir)

    assert read_drift(codoc_dir) == {}  # prose addressed this pass → followed


def test_reconcile_drift_scoped_merge_preserves_out_of_scope_drift(codoc_dir, monkeypatch):
    """reconcile_drift with file_scope set (the watch daemon's scoped pass) MERGES
    drift through the production path: an out-of-scope feature's badge survives
    while the in-scope feature is freshly computed — asserting the scoped-merge
    behavior end-to-end through the daemon entrypoint."""
    s = open_store(codoc_dir)
    try:
        fa = Feature(title="A feature", description="In file a.")
        fb = Feature(title="B feature", description="In file b.")
        s.upsert_feature(fa)
        s.upsert_feature(fb)
        _bind(s, fa.id, "a.py", "a.py::a_fn", "old")
        _bind(s, fb.id, "b.py", "b.py::b_fn", "old")
    finally:
        s.close()

    # Prior (full) pass questioned A.
    write_drift(codoc_dir, {fa.id: DRIFT_QUESTIONED})

    # The scoped pass examines only b.py — its bound chunk changed (modified).
    # reconcile_drift's _state_changeset reads the whole index; the scope is
    # carried into the drift merge. a.py is out of scope so its badge survives.
    _fake_index(monkeypatch, [
        _chunk_row("a.py", "a.py::a_fn", "old"),   # unchanged (matches fingerprint)
        _chunk_row("b.py", "b.py::b_fn", "new"),   # modified vs stored "old"
    ])
    _inject_propose(monkeypatch, [])

    from codoc.loop.loop_a import reconcile_drift

    reconcile_drift("root", codoc_dir, file_scope={"b.py"})

    drift = read_drift(codoc_dir)
    assert drift[fa.id] == DRIFT_QUESTIONED   # out-of-scope — preserved by merge
    assert drift[fb.id] == DRIFT_QUESTIONED   # in-scope — freshly computed
