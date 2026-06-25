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
from codoc.loop.loop_b import _merge_channels, realize_path, run_loop_b
from codoc.model.event import NodeOpKind
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


# ─── channel merge (INV3) ──────────────────────────────────────────────────

def test_merge_channels_prefers_doc_when_it_has_pending_edit(codoc_dir):
    """Doc-path AMEND is authoritative for features it has edits for (INV3)."""
    fid = _seed(codoc_dir, "Auth", "Old prose.")
    _write_doc(codoc_dir, [(fid, "Auth", "New prose from the webview.")])
    with open_store(codoc_dir) as s:
        diff, errors = _merge_channels(codoc_dir, s)
    assert not errors
    amends = [op for op in diff.user_ops if op.kind is NodeOpKind.AMEND]
    assert len(amends) == 1
    assert amends[0].description == "New prose from the webview."


def test_merge_channels_falls_back_to_text_when_doc_is_in_sync(codoc_dir):
    """When doc has no pending edits, text path is authoritative (INV3)."""
    fid = _seed(codoc_dir, "Auth", "Same.")
    _write_doc(codoc_dir, [(fid, "Auth", "Same.")])  # doc == store → no pending doc edit
    # A raw-text edit moves tree.codoc ahead instead.
    tp = tree_path(codoc_dir)
    tp.write_text(tp.read_text().replace("Same.", "Edited in the raw text editor."))
    with open_store(codoc_dir) as s:
        diff, errors = _merge_channels(codoc_dir, s)
    assert not errors
    amends = [op for op in diff.user_ops if op.kind is NodeOpKind.AMEND]
    assert len(amends) == 1
    assert amends[0].description == "Edited in the raw text editor."


def test_merge_channels_includes_text_edit_when_doc_has_edit_for_different_feature(codoc_dir):
    """Text-path op for feature B is NOT dropped when doc has an edit for feature A (INV3).
    This was the A2/A6 attack: raw-text-editor edit silently discarded."""
    fid_a = _seed(codoc_dir, "Auth", "Old A.")
    # Add a second feature B to the store.
    with open_store(codoc_dir) as s:
        from codoc.model.feature import Feature as F
        feat_b = F(title="Billing", description="Old B.")
        s.upsert_feature(feat_b)
        fid_b = feat_b.id
        write_tree(s, codoc_dir)
    # Doc has a pending edit for A only.
    _write_doc(codoc_dir, [(fid_a, "Auth", "New webview intent for A."),
                           (fid_b, "Billing", "Old B.")])
    # Text editor also edits B (simulates user editing tree.codoc directly).
    tp = tree_path(codoc_dir)
    tp.write_text(tp.read_text().replace("Old B.", "Text editor edit for B."))
    with open_store(codoc_dir) as s:
        diff, errors = _merge_channels(codoc_dir, s)
    ops_by_fid = {op.feature_id: op for op in diff.user_ops if op.kind is NodeOpKind.AMEND}
    assert fid_a in ops_by_fid, "doc-path edit for A must be included"
    assert ops_by_fid[fid_a].description == "New webview intent for A."
    assert fid_b in ops_by_fid, "text-path edit for B must NOT be dropped (INV3)"
    assert ops_by_fid[fid_b].description == "Text editor edit for B."


def test_merge_channels_text_retire_overrides_doc_amend(codoc_dir):
    """RETIRE_NODE from text path beats AMEND from doc path for the same feature (INV3)."""
    from codoc.codoc_file.render import write_tree
    fid = _seed(codoc_dir, "Feature", "Some desc.")
    # Doc has an AMEND for the feature.
    _write_doc(codoc_dir, [(fid, "Feature", "Updated doc desc.")])
    # Text editor retires it (changes - to ~).
    tp = tree_path(codoc_dir)
    tp.write_text(tp.read_text().replace("- Feature", "~ Feature"))
    with open_store(codoc_dir) as s:
        diff, _ = _merge_channels(codoc_dir, s)
    retires = [op for op in diff.user_ops if op.kind is NodeOpKind.RETIRE_NODE]
    amends = [op for op in diff.user_ops if op.kind is NodeOpKind.AMEND]
    assert any(op.feature_id == fid for op in retires), "text RETIRE must win"
    assert not any(op.feature_id == fid for op in amends), "doc AMEND must be suppressed"


# ─── Loop B applies a doc.json edit + re-renders tree.codoc (sole writer) ─────

def test_loop_b_applies_doc_json_edit_and_rerenders_text(codoc_dir, tmp_path):
    fid = _seed(codoc_dir, "Auth", "Login.")
    _write_doc(codoc_dir, [(fid, "Auth", "Add validation for empty input.")])

    res = run_loop_b(str(tmp_path), codoc_dir)

    assert res.user_edits == 1
    with open_store(codoc_dir) as s:
        assert s.get_feature(fid).description == "Add validation for empty input."
    # Held-draft model: the edit applies + tree.codoc re-renders, but the directive is
    # HELD (not realized) until an explicit hand-off — no surprise code from a doc edit.
    assert res.queued is False
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


# ─── INV7: local_id-keyed identity prevents undo/redo duplicates ─────────────

def test_undo_of_add_node_does_not_create_duplicate_feature(codoc_dir, tmp_path):
    """INV7: a TipTap undo restores the heading to fid=null (its history snapshot).
    diff_codoc(has_local_ids=True) resolves identity by the author-stable local_id
    against the store, so Loop B recognizes the existing feature (no op / AMEND) and
    never emits a duplicate ADD. (This replaced the _apply_minted_fids pre-pass —
    identity is resolved inside the diff, not laundered into the fid field first.)"""
    # Pass 1: new feature with a localId (simulates TipTap's node creation).
    payload = {
        "version": 1,
        "doc": {"type": "doc", "content": [
            {"type": "featureHeading",
             "attrs": {"fid": None, "localId": "lid-test-1", "level": 0,
                       "retired": False, "realized": True},
             "content": [{"type": "text", "text": "Brand new feature"}]},
            {"type": "paragraph", "content": []},
        ]},
        "suggestions": [], "comments": [],
    }
    doc_path(codoc_dir).write_text(__import__("json").dumps(payload))
    res = run_loop_b(str(tmp_path), codoc_dir)
    assert res.user_edits == 1, "ADD_NODE should be applied"
    with open_store(codoc_dir) as s:
        features = s.list_features()
    assert len(features) == 1
    minted_fid = features[0].id
    assert features[0].local_id == "lid-test-1"

    # Pass 2: simulate TipTap undo — heading is restored with fid=null, same localId.
    # The local_id-keyed diff recognizes the existing feature → no duplicate ADD.
    doc_path(codoc_dir).write_text(__import__("json").dumps(payload))  # fid still null
    res2 = run_loop_b(str(tmp_path), codoc_dir)
    with open_store(codoc_dir) as s:
        all_features = s.list_features()
    assert len(all_features) == 1, "no duplicate should be created (INV7)"
    assert all_features[0].id == minted_fid, "same fid must survive undo/redo"


# ─── INV8: a HANDED-OFF directive is protected mid-realization ───────────────

def test_handed_off_directive_in_realize_md_is_not_superseded(codoc_dir, tmp_path):
    """INV8 (Step 8): a directive in realize.md (handed off, possibly mid-realization)
    must NOT be superseded by a fresh edit to the same feature — dropping it would break
    the caused_by causality chain and waste the agent's work. The protection is purely
    STRUCTURAL (realize.md membership) — it does NOT depend on activity.json's epoch, so
    a stale/missing epoch can never wrongly expose an in-flight directive. Held drafts
    (the default) are not in realize.md, so they coalesce freely."""
    from codoc.loop.edits import append_handoffs, read_manifest

    fid = _seed(codoc_dir, "Feature", "Old desc.")
    # Pass 1: edit → held draft D1; hand it off → realize.md written.
    _write_doc(codoc_dir, [(fid, "Feature", "Add caching layer.")])
    run_loop_b(str(tmp_path), codoc_dir)
    append_handoffs(codoc_dir, [fid])
    run_loop_b(str(tmp_path), codoc_dir)
    manifest = read_manifest(codoc_dir)
    assert len(manifest) == 1 and manifest[0].handed_off is True
    d1_id = manifest[0].id
    assert d1_id in realize_path(codoc_dir).read_text()  # genuinely in-flight

    # A fresh edit to the same feature would normally supersede — but D1 is in realize.md.
    # No activity.json epoch is written: protection comes from realize.md membership alone.
    _write_doc(codoc_dir, [(fid, "Feature", "Add caching layer with TTL.")])
    run_loop_b(str(tmp_path), codoc_dir)
    assert d1_id in {d.id for d in read_manifest(codoc_dir)}, \
        "a handed-off, in-flight directive must survive supersede (INV8)"


def test_doc_plan_node_mints_and_hands_off_a_build_directive(codoc_dir, tmp_path):
    """Step 10: a NEW heading authored as a PLAN (realized=False — the webview's ◇ plan
    gesture) is an explicit build request. Its ADD mints a directive that is handed off
    on mint (an explicit gesture, not a held draft) → realize.md written → the agent
    builds it. This is the typed replacement for the deleted is_imperative prose guess:
    a descriptive new feature does NOT build; a plan one does."""
    import json as _json
    # A plan heading (realized=False) with a purely DESCRIPTIVE body.
    payload = {
        "version": 1,
        "doc": {"type": "doc", "content": [
            {"type": "featureHeading",
             "attrs": {"fid": None, "localId": "lid-plan", "level": 0,
                       "retired": False, "realized": False},
             "content": [{"type": "text", "text": "Dark mode"}]},
            {"type": "paragraph", "content": [{"type": "text", "text": "A light/dark theme toggle."}]},
        ]},
        "suggestions": [], "comments": [],
    }
    doc_path(codoc_dir).write_text(_json.dumps(payload))
    res = run_loop_b(str(tmp_path), codoc_dir)

    assert res.user_edits == 1
    assert res.queued is True, "a plan ADD is handed off on mint → realize.md written"
    assert any("NEW FEATURE" in d and "Dark mode" in d for d in res.directives)
    with open_store(codoc_dir) as s:
        f = next(f for f in s.list_features() if f.title == "Dark mode")
        assert f.realized is False  # stored as a plan placeholder


def test_doc_descriptive_new_node_does_not_build(codoc_dir, tmp_path):
    """The contrast: a NEW heading authored normally (realized defaults True) is a
    documentation node — it is created but mints NO directive (no prose guessing)."""
    import json as _json
    payload = {
        "version": 1,
        "doc": {"type": "doc", "content": [
            {"type": "featureHeading",
             "attrs": {"fid": None, "localId": "lid-doc", "level": 0,
                       "retired": False, "realized": True},
             "content": [{"type": "text", "text": "Add a dark theme toggle"}]},  # verb-led prose
            {"type": "paragraph", "content": []},
        ]},
        "suggestions": [], "comments": [],
    }
    doc_path(codoc_dir).write_text(_json.dumps(payload))
    res = run_loop_b(str(tmp_path), codoc_dir)
    assert res.user_edits == 1
    assert res.queued is False and res.directives == []  # verb-led prose no longer builds


def test_held_drafts_coalesce_to_one(codoc_dir, tmp_path):
    """Held drafts (the default) coalesce: iterating one feature across passes leaves a
    SINGLE held draft, never a stack. No epoch involved — held drafts aren't in-flight."""
    from codoc.loop.edits import read_manifest

    fid = _seed(codoc_dir, "Feature", "Old desc.")
    _write_doc(codoc_dir, [(fid, "Feature", "Add caching layer.")])
    run_loop_b(str(tmp_path), codoc_dir)
    assert len(read_manifest(codoc_dir)) == 1

    _write_doc(codoc_dir, [(fid, "Feature", "Add caching layer with TTL.")])
    run_loop_b(str(tmp_path), codoc_dir)
    manifest = read_manifest(codoc_dir)
    assert len(manifest) == 1, "a fresh edit supersedes the prior held draft (coalesce)"
    assert manifest[0].handed_off is False


# ─── Loop-B lock: serializes a whole pass (double-fire / multi-process safety) ─

def test_loop_lock_is_cached_reentrant_and_releases(codoc_dir):
    """The shared codoc-loop lock is cached per repo (daemon + hub + CLI in one process
    share one instance), reentrant within a thread (a nested acquire never self-deadlocks),
    and fully released on exit. Cross-PROCESS exclusion is provided by filelock itself.
    It is SHARED by Loop A and Loop B — the same lock object guards both."""
    from codoc.loop.locks import loop_lock

    lock = loop_lock(str(codoc_dir))
    assert loop_lock(str(codoc_dir)) is lock, "lock must be cached per repo"
    with lock:
        with lock:  # reentrant in-thread — must not deadlock
            assert lock.is_locked
    assert not lock.is_locked, "lock must be fully released after the pass"


def test_loop_a_and_loop_b_share_one_lock():
    """Loop A and Loop B must serialize against EACH OTHER, not just against their own
    kind. Both modules bind the SAME `loop_lock` from loop/locks.py, and it is cached
    per repo — so for any one repo, a Loop A pass and a Loop B pass acquire the identical
    FileLock and cannot interleave their store mutation + tree.codoc re-render."""
    from codoc.loop import loop_a, loop_b, locks
    assert loop_a.loop_lock is locks.loop_lock is loop_b.loop_lock, \
        "both loops must use the shared codoc-loop lock"


def test_loop_b_still_correct_under_the_lock(codoc_dir, tmp_path):
    """A normal pass produces the same result with the lock wrapping it (the lock is
    uncontended in a single-threaded test, so behavior is unchanged)."""
    fid = _seed(codoc_dir, "Auth", "Login.")
    _write_doc(codoc_dir, [(fid, "Auth", "Add validation for empty input.")])
    res = run_loop_b(str(tmp_path), codoc_dir)
    assert res.user_edits == 1
    with open_store(codoc_dir) as s:
        assert s.get_feature(fid).description == "Add validation for empty input."
