"""U2b — single-writer: Loop B reads tree.doc.json; the daemon writes tree.codoc.

The webview persists tree.doc.json (no tree.codoc write). Loop B picks the doc as
its edit source when it carries a pending edit, applies it, and re-renders
tree.codoc itself (the daemon is the sole writer). Inline comments arrive as
one-shot edits.json steers. The daemon routes a tree.doc.json change to Loop B,
guarded so a non-edit persist doesn't ping-pong.
"""
from __future__ import annotations

import json

import pytest

from codoc.codoc_file.doc_parse import doc_path
from codoc.codoc_file.parse import parse_tree_file
from codoc.codoc_file.render import tree_path, write_tree
from codoc.loop.edits import Steer, append_steer, read_manifest
from codoc.loop.loop_b import _pick_parsed, run_loop_b
from codoc.model.feature import Feature
from codoc.store.db import open_store


@pytest.fixture
def codoc_dir(tmp_path):
    d = tmp_path / ".codoc"; d.mkdir()
    return str(d)


def _write_doc(codoc_dir, features: list[tuple[str, str, str]]):
    """features = [(fid, title, description)] → a tree.doc.json the webview would write."""
    content = []
    for fid, title, desc in features:
        content.append({"type": "featureHeading",
                        "attrs": {"fid": fid, "level": 0, "retired": False, "realized": True},
                        "content": [{"type": "text", "text": title}]})
        content.append({"type": "paragraph",
                        "content": ([{"type": "text", "text": desc}] if desc else [])})
    payload = {"version": 1, "doc": {"type": "doc", "content": content},
               "suggestions": [], "comments": []}
    doc_path(codoc_dir).write_text(json.dumps(payload))


def _seed(codoc_dir, title, desc):
    s = open_store(codoc_dir)
    try:
        f = Feature(title=title, description=desc)
        s.upsert_feature(f)
        write_tree(s, codoc_dir)  # tree.codoc == store (no pending text edit)
    finally:
        s.close()
    return f.id


# ─── source selection ────────────────────────────────────────────────────────

def test_pick_parsed_prefers_doc_json_when_it_has_a_pending_edit(codoc_dir):
    fid = _seed(codoc_dir, "Auth", "Old prose.")
    _write_doc(codoc_dir, [(fid, "Auth", "New prose from the webview.")])
    with open_store(codoc_dir) as s:
        parsed = _pick_parsed(codoc_dir, s)
        assert parsed.nodes[0].description == "New prose from the webview."


def test_pick_parsed_falls_back_to_text_when_doc_is_in_sync(codoc_dir):
    fid = _seed(codoc_dir, "Auth", "Same.")
    _write_doc(codoc_dir, [(fid, "Auth", "Same.")])  # doc == store → no pending doc edit
    # A raw-text edit moves tree.codoc ahead instead.
    tp = tree_path(codoc_dir)
    tp.write_text(tp.read_text().replace("Same.", "Edited in the raw text editor."))
    with open_store(codoc_dir) as s:
        parsed = _pick_parsed(codoc_dir, s)
        assert parsed.nodes[0].description == "Edited in the raw text editor."


# ─── Loop B applies a doc.json edit + re-renders tree.codoc (sole writer) ─────

def test_loop_b_applies_doc_json_edit_and_rerenders_text(codoc_dir, tmp_path):
    fid = _seed(codoc_dir, "Auth", "Login.")
    _write_doc(codoc_dir, [(fid, "Auth", "Add validation for empty input.")])

    res = run_loop_b(str(tmp_path), codoc_dir)

    assert res.user_edits == 1
    with open_store(codoc_dir) as s:
        assert s.get_feature(fid).description == "Add validation for empty input."
    # imperative description → a realize directive was queued for the agent.
    assert res.queued and res.directives
    # the daemon (Loop B) re-rendered tree.codoc to the new state itself.
    assert "Add validation for empty input." in parse_tree_file(codoc_dir).nodes[0].description


# ─── inline-comment steers (edits.json one-shot) ─────────────────────────────

def test_steer_drains_into_a_directive(codoc_dir, tmp_path):
    fid = _seed(codoc_dir, "Auth", "Login and sessions.")
    _write_doc(codoc_dir, [(fid, "Auth", "Login and sessions.")])  # no feature edit
    append_steer(codoc_dir, Steer(feature_id=fid, text="Use bcrypt, not md5.", comment_id="c1"))

    res = run_loop_b(str(tmp_path), codoc_dir)

    assert res.steered == 1
    assert any("Use bcrypt" in d for d in res.directives)
    # one-shot: the steer was consumed, so a second pass re-queues nothing.
    res2 = run_loop_b(str(tmp_path), codoc_dir)
    assert res2.steered == 0


def test_dry_run_leaves_steers_queued(codoc_dir, tmp_path):
    fid = _seed(codoc_dir, "Auth", "Login.")
    _write_doc(codoc_dir, [(fid, "Auth", "Login.")])
    append_steer(codoc_dir, Steer(feature_id=fid, text="Note for later.", comment_id="c1"))

    run_loop_b(str(tmp_path), codoc_dir, dry_run=True)
    # not consumed by a dry pass → a real pass still sees it.
    res = run_loop_b(str(tmp_path), codoc_dir)
    assert res.steered == 1


# ─── daemon routes a tree.doc.json change to Loop B (guarded) ─────────────────

def test_daemon_routes_doc_edit_to_loop_b(codoc_dir, tmp_path):
    from codoc.loop.watch import WatchState, process_batch

    fid = _seed(codoc_dir, "Auth", "Old.")
    _write_doc(codoc_dir, [(fid, "Auth", "New webview intent.")])

    called = {}

    def fake_loop_b(root, cd, *, dry_run=False):
        from codoc.loop.loop_b import LoopBResult
        called["ran"] = True
        return LoopBResult(user_edits=1)

    out = process_batch([str(doc_path(codoc_dir))], str(tmp_path), codoc_dir,
                        WatchState(), loop_b=fake_loop_b, loop_a=lambda *a, **k: None,
                        render=lambda *a, **k: None)
    assert called.get("ran") and out[0] == "codoc→code"


def test_safe_write_tree_yields_to_pending_doc_edit(codoc_dir):
    """The daemon's non-destructive render must NOT overwrite tree.codoc while a
    webview edit is pending in tree.doc.json — else it would push stale text the host
    adopts, reverting the settle. It renders again once Loop B applies the edit."""
    from codoc.loop.reconcile import safe_write_tree

    fid = _seed(codoc_dir, "Auth", "Old.")
    _write_doc(codoc_dir, [(fid, "Auth", "New webview intent.")])  # doc ahead of store
    with open_store(codoc_dir) as s:
        assert safe_write_tree(s, codoc_dir) is False  # yielded — tree.codoc untouched
    assert "Old." in parse_tree_file(codoc_dir).nodes[0].description  # not reverted

    # After the doc edit is in sync (store caught up), the render proceeds.
    _write_doc(codoc_dir, [(fid, "Auth", "Old.")])  # doc == store now
    with open_store(codoc_dir) as s:
        assert safe_write_tree(s, codoc_dir) is True


def test_daemon_skips_non_edit_doc_persist(codoc_dir, tmp_path):
    from codoc.loop.watch import WatchState, process_batch

    fid = _seed(codoc_dir, "Auth", "Same.")
    _write_doc(codoc_dir, [(fid, "Auth", "Same.")])  # doc == store → no pending edit

    ran = {}

    def fake_loop_b(root, cd, *, dry_run=False):
        ran["ran"] = True
        from codoc.loop.loop_b import LoopBResult
        return LoopBResult()

    out = process_batch([str(doc_path(codoc_dir))], str(tmp_path), codoc_dir,
                        WatchState(), loop_b=fake_loop_b, loop_a=lambda *a, **k: None,
                        render=lambda *a, **k: None)
    assert out is None and "ran" not in ran  # guarded — no ping-pong
