"""U3 — doc presence reconciliation: a human deletion becomes a soft (lifecycle-only)
retire that never resurrects, a re-appearance un-retires, and an agent-added feature
not yet in the human doc is NEVER falsely retired.

INV2: soft-retire is lifecycle-only — bindings are NOT deleted, they survive and
reactivate on un-retire. Only explicit ~ marker retire destroys bindings.

INV5: reconcile_doc_presence returns (retired, unretired, current_fids) and the
CALLER writes doc-fids.json after write_tree. Tests simulate this with _reconcile().
"""
from __future__ import annotations

import json

import pytest

from codoc.codoc_file.doc_parse import doc_path
from codoc.loop.doc_presence import reconcile_doc_presence, read_doc_fids, write_doc_fids
from codoc.model.binding import Binding
from codoc.model.feature import Feature
from codoc.store.db import open_store


@pytest.fixture
def codoc(tmp_path):
    d = tmp_path / ".codoc"
    d.mkdir()
    return d


def _write_doc(codoc_dir, features):
    """Write a tree.doc.json containing the given (fid, title) features as headings."""
    content = []
    for fid, title in features:
        content.append({"type": "featureHeading", "attrs": {"fid": fid, "level": 0},
                        "content": [{"type": "text", "text": title}]})
        content.append({"type": "paragraph", "content": []})
    (doc_path(codoc_dir)).write_text(json.dumps({"type": "doc", "content": content}))


def _reconcile(store, codoc_dir):
    """Simulate one Loop B pass: reconcile + write doc-fids (as _apply_edits does)."""
    retired, unretired, current_fids = reconcile_doc_presence(store, codoc_dir)
    write_doc_fids(codoc_dir, current_fids)
    return retired, unretired


def test_deletion_soft_retires_and_does_not_resurrect(codoc):
    with open_store(codoc) as s:
        a = Feature(title="Keep me"); b = Feature(title="Delete me")
        s.upsert_feature(a); s.upsert_feature(b)
        s.upsert_binding(Binding(feature_id=b.id, file="x.py", symbol_path="x.py::f", fingerprint="h"))

        # Pass 1: both present → seed the previous-doc fid set, no deletions.
        _write_doc(codoc, [(a.id, "Keep me"), (b.id, "Delete me")])
        assert _reconcile(s, codoc) == (0, 0)
        assert read_doc_fids(codoc) == {a.id, b.id}

        # Pass 2: the human removed "Delete me" → soft retire, bindings PRESERVED (INV2).
        _write_doc(codoc, [(a.id, "Keep me")])
        retired, unretired = _reconcile(s, codoc)
        assert (retired, unretired) == (1, 0)
        assert s.get_feature(b.id).retired is True           # tombstoned, recoverable
        assert len(s.bindings_for_feature(b.id)) == 1        # INV2: bindings preserved
        assert s.get_feature(a.id).retired is False          # untouched


def test_reappearance_unretires(codoc):
    with open_store(codoc) as s:
        a = Feature(title="A"); b = Feature(title="B")
        s.upsert_feature(a); s.upsert_feature(b)
        _write_doc(codoc, [(a.id, "A"), (b.id, "B")]); _reconcile(s, codoc)
        _write_doc(codoc, [(a.id, "A")]); _reconcile(s, codoc)   # delete B
        assert s.get_feature(b.id).retired is True
        # undo: B reappears in the doc → un-retired (resurrected intentionally).
        _write_doc(codoc, [(a.id, "A"), (b.id, "B")])
        retired, unretired = _reconcile(s, codoc)
        assert (retired, unretired) == (0, 1)
        assert s.get_feature(b.id).retired is False


def test_agent_added_feature_not_in_doc_is_never_retired(codoc):
    """The key safety property: a feature the agent/MCP added to the store that the
    human's doc has not yet caught up to must NOT be retired."""
    with open_store(codoc) as s:
        a = Feature(title="A")
        s.upsert_feature(a)
        _write_doc(codoc, [(a.id, "A")]); _reconcile(s, codoc)  # prev = {a}
        # Agent adds C directly to the store; the human doc still only has A.
        c = Feature(title="Agent feature")
        s.upsert_feature(c)
        retired, unretired = _reconcile(s, codoc)
        assert retired == 0                       # C was never in the doc → not "removed"
        assert s.get_feature(c.id).retired is False


def test_no_doc_is_a_noop(codoc):
    with open_store(codoc) as s:
        s.upsert_feature(Feature(title="A"))
        retired, unretired = _reconcile(s, codoc)
        assert (retired, unretired) == (0, 0)    # no tree.doc.json → never infers
