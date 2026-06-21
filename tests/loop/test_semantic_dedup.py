"""D1 — semantic (embedding) title dedup, with a deterministic fake embedder.

The exact-string guard mints a fresh node for a paraphrased duplicate ("Persist
drafts" vs "Save draft edits"). The opt-in semantic gate folds an ADD_NODE into
an embedding-close adoptable node instead. These tests pin the matcher behavior
and the Loop A integration WITHOUT loading a real model: a fake ``embed_fn`` maps
known phrases to hand-placed vectors so cosine is exact and reproducible.
"""
from __future__ import annotations

from codoc.loop.diff import ChangeSet, ChunkRef
from codoc.loop.loop_a import apply_changeset
from codoc.loop.title_dedup import SemanticTitleMatcher
from codoc.model.binding import Binding
from codoc.model.event import NodeOp, NodeOpKind
from codoc.model.feature import Feature
from codoc.store.db import open_store


# A tiny deterministic "embedder": two near-synonym phrases get nearly-parallel
# vectors; an unrelated phrase is orthogonal. No model, fully reproducible.
_VECTORS = {
    "Persist drafts": [1.0, 0.0],
    "Save draft edits": [0.99, 0.14],   # cosine ≈ 0.99 with "Persist drafts"
    "Rate limiting": [0.0, 1.0],        # orthogonal
}


def _fake_embed(texts):
    return [_VECTORS.get(t, [0.0, 0.0]) for t in texts]


def _store(tmp_path):
    return open_store(tmp_path)


def _feature(store, **kw):
    f = Feature(**kw)
    store.upsert_feature(f)
    return f


def _raising(*a, **k):
    raise AssertionError("LLM should not be called")


def _propose(ops):
    def p(changes, subtree, all_titles, *, repo_name="codebase", config=None):
        return list(ops)
    return p


# ─── the matcher in isolation ────────────────────────────────────────────────

def test_matcher_folds_paraphrase_above_threshold():
    m = SemanticTitleMatcher(_fake_embed, [("Persist drafts", "f-1")], threshold=0.82)
    assert m.active
    assert m.best_match("Save draft edits") == "f-1"   # cosine ≈ 0.99 ≥ 0.82


def test_matcher_rejects_unrelated_title():
    m = SemanticTitleMatcher(_fake_embed, [("Persist drafts", "f-1")], threshold=0.82)
    assert m.best_match("Rate limiting") is None       # orthogonal → no fold


def test_matcher_inert_on_no_candidates_or_embedder_error():
    assert SemanticTitleMatcher(_fake_embed, []).active is False

    def _boom(_texts):
        raise RuntimeError("embedder down")

    m = SemanticTitleMatcher(_boom, [("Persist drafts", "f-1")])
    assert m.active is False                            # fails safe (inert)
    assert m.best_match("Save draft edits") is None


# ─── Loop A integration ──────────────────────────────────────────────────────

def test_loop_a_folds_semantic_duplicate_into_unbound_node(tmp_path):
    """An LLM ADD_NODE whose title PARAPHRASES a live unbound node binds into that
    node (semantic fold) instead of minting a duplicate — only with embed_fn on."""
    store = _store(tmp_path)
    try:
        existing = _feature(store, title="Persist drafts")  # binding-less, adoptable
        cs = ChangeSet(added=[ChunkRef("d.py", "d.py::save", "h", "def save(): ...")])

        res = apply_changeset(cs, store, propose=_propose([
            NodeOp(kind=NodeOpKind.ADD_NODE, title="Save draft edits",
                   bindings=[("d.py", "d.py::save")]),
        ]), embed_fn=_fake_embed)

        b = store.binding_at("d.py", "d.py::save")
        assert b is not None and b.feature_id == existing.id   # folded, not minted
        assert [f.title for f in store.list_features()] == ["Persist drafts"]
        assert not res.proposed
    finally:
        store.close()


def test_loop_a_mints_when_semantic_dedup_disabled(tmp_path):
    """Without embed_fn (the default), the paraphrase is NOT folded — it falls to
    the normal placement path (here a pending ADD), proving the gate is opt-in."""
    store = _store(tmp_path)
    try:
        _feature(store, title="Persist drafts")
        cs = ChangeSet(added=[ChunkRef("d.py", "d.py::save", "h", "def save(): ...")])

        res = apply_changeset(cs, store, propose=_propose([
            NodeOp(kind=NodeOpKind.ADD_NODE, title="Save draft edits",
                   bindings=[("d.py", "d.py::save")]),
        ]))  # no embed_fn

        # The new node was proposed (a distinct title to the exact-string guard).
        assert any(e.op.kind is NodeOpKind.ADD_NODE and e.op.title == "Save draft edits"
                   for e in store.pending_events())
    finally:
        store.close()


def test_loop_a_semantic_never_folds_into_bound_node(tmp_path):
    """The semantic fallback only considers ADOPTABLE (unbound) nodes — a bound
    feature with a near title is left alone (folding would mis-attribute)."""
    store = _store(tmp_path)
    try:
        bound = _feature(store, title="Persist drafts")
        store.upsert_binding(Binding(feature_id=bound.id, file="p.py",
                                     symbol_path="p.py::persist", fingerprint="h"))
        cs = ChangeSet(added=[ChunkRef("d.py", "d.py::save", "h", "def save(): ...")])

        res = apply_changeset(cs, store, propose=_propose([
            NodeOp(kind=NodeOpKind.ADD_NODE, title="Save draft edits",
                   bindings=[("d.py", "d.py::save")]),
        ]), embed_fn=_fake_embed)

        # Not folded into the bound node; the new chunk is placed on its own.
        assert store.binding_at("d.py", "d.py::save") is None or \
            store.binding_at("d.py", "d.py::save").feature_id != bound.id
    finally:
        store.close()
