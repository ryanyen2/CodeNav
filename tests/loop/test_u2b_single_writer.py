"""U2b/U7 — single-writer: the daemon writes BOTH tree.doc.json and tree.codoc; the
webview emits identity-keyed COMMANDS instead of authoring either file.

Post-U7 the doc-diff / text-diff inference is retired (R18): user edits arrive as
``commands`` (U3) applied via ``apply_op``, and ``_merge_channels`` returns an empty
diff. Loop B applies the command, re-renders both files (sole writer), and builds the
codoc→code directive from the command. Inline comments arrive as one-shot edits.json
steers. The daemon routes a tree.doc.json change to Loop B (the routing guard in
reconcile.py keeps a read-only ``diff_codoc`` for the pending-edit check).
"""
from __future__ import annotations

import json

import pytest

from codoc.codoc_file.doc_parse import doc_path
from codoc.codoc_file.parse import parse_tree_file
from codoc.codoc_file.render import tree_path, write_tree
from codoc.loop.edits import Command, Steer, append_command, append_steer, read_manifest
from codoc.loop.loop_b import _merge_channels, realize_path, run_loop_b
from codoc.model.event import NodeOpKind
from codoc.model.feature import Feature
from codoc.store.db import open_store


@pytest.fixture
def codoc_dir(tmp_path):
    d = tmp_path / ".codoc"; d.mkdir()
    return str(d)


_CMD_N = [0]


def _amend_cmd(codoc_dir, fid, description):
    """Queue a `set_description` command — the webview's edit channel (U3/U4)."""
    _CMD_N[0] += 1
    append_command(codoc_dir, Command(
        id=f"cmd-{_CMD_N[0]}", kind="set_description", feature_id=fid,
        payload={"description": description}))


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


# ─── channel merge is RETIRED (U7 / R18) ─────────────────────────────────────

def test_merge_channels_is_retired_and_returns_empty(codoc_dir):
    """U7: ``_merge_channels`` no longer infers user edits by diffing tree.doc.json or
    tree.codoc against the store — both are the daemon's own output now, so reading them
    back as input was a feedback loop. It returns an empty diff; user edits come from the
    `commands` channel. This replaces the former doc-vs-text arbitration tests (the doc-
    path/text-path/retire-override merge rules they pinned no longer exist)."""
    fid = _seed(codoc_dir, "Auth", "Old prose.")
    # Even with a divergent doc AND a divergent raw-text file, no edit is inferred.
    _write_doc(codoc_dir, [(fid, "Auth", "New prose from the webview.")])
    tp = tree_path(codoc_dir)
    tp.write_text(tp.read_text().replace("Old prose.", "Raw-text edit."))
    with open_store(codoc_dir) as s:
        diff, errors = _merge_channels(codoc_dir, s)
    assert diff.is_empty(), "no user edits are inferred from files post-U7"
    assert errors == []


# ─── Loop B applies a command edit + re-renders tree.codoc (sole writer) ──────

def test_loop_b_applies_command_edit_and_rerenders_text(codoc_dir, tmp_path):
    fid = _seed(codoc_dir, "Auth", "Login.")
    _amend_cmd(codoc_dir, fid, "Add validation for empty input.")

    res = run_loop_b(str(tmp_path), codoc_dir)

    assert res.commands == 1
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


# ─── KTD8: command idempotency prevents undo/redo duplicates ─────────────────

def test_undo_of_add_node_does_not_create_duplicate_feature(codoc_dir, tmp_path):
    """Post-U7 a TipTap undo can no longer re-mint a feature, because the doc is never
    read back as input: an `add` arrives as one identity-keyed command, applied once. A
    crash-replay / re-send of the SAME command id is a ledger no-op (KTD8), and a fresh
    add with the same (normalized_title, parent_id) is deduped (KTD3) — both paths leave
    exactly one feature. (Replaces the INV7 doc-diff local_id-keyed dedup, which is moot
    once the daemon is the sole writer of tree.doc.json.)"""
    _seed(codoc_dir, "Existing", "x")  # empty-ish tree (one unrelated feature)
    cmd = Command(id="cmd-add-undo", kind="add", local_id="lid-test-1",
                  payload={"title": "Brand new feature", "description": "b"})
    append_command(codoc_dir, cmd)
    res = run_loop_b(str(tmp_path), codoc_dir)
    assert res.commands == 1
    with open_store(codoc_dir) as s:
        minted = [f for f in s.list_features() if f.title == "Brand new feature"]
    assert len(minted) == 1
    minted_fid = minted[0].id
    assert minted[0].local_id == "lid-test-1"

    # Re-send the SAME command id (a crash-replay) → ledger no-op, no duplicate.
    append_command(codoc_dir, cmd)
    res2 = run_loop_b(str(tmp_path), codoc_dir)
    assert res2.commands == 0
    with open_store(codoc_dir) as s:
        again = [f for f in s.list_features() if f.title == "Brand new feature"]
    assert len(again) == 1 and again[0].id == minted_fid, "no duplicate on replay (KTD8)"


# ─── INV8: a HANDED-OFF directive is protected mid-realization ───────────────

def test_handed_off_directive_in_realize_md_is_not_superseded(codoc_dir, tmp_path):
    """INV8 (Step 8): a directive in realize.md (handed off, possibly mid-realization)
    must NOT be superseded by a fresh edit to the same feature — dropping it would break
    the caused_by causality chain and waste the agent's work. The protection is purely
    STRUCTURAL (realize.md membership) — it does NOT depend on activity.json's epoch, so
    a stale/missing epoch can never wrongly expose an in-flight directive. Held drafts
    (the default) are not in realize.md, so they coalesce freely. (Edits now arrive as
    `set_description` commands; the supersede pruning keys off feature_id, unchanged.)"""
    from codoc.loop.edits import append_handoffs, read_manifest

    fid = _seed(codoc_dir, "Feature", "Old desc.")
    # Pass 1: edit → held draft D1; hand it off → realize.md written.
    _amend_cmd(codoc_dir, fid, "Add caching layer.")
    run_loop_b(str(tmp_path), codoc_dir)
    append_handoffs(codoc_dir, [fid])
    run_loop_b(str(tmp_path), codoc_dir)
    manifest = read_manifest(codoc_dir)
    assert len(manifest) == 1 and manifest[0].handed_off is True
    d1_id = manifest[0].id
    assert d1_id in realize_path(codoc_dir).read_text()  # genuinely in-flight

    # A fresh edit to the same feature would normally supersede — but D1 is in realize.md.
    # No activity.json epoch is written: protection comes from realize.md membership alone.
    _amend_cmd(codoc_dir, fid, "Add caching layer with TTL.")
    run_loop_b(str(tmp_path), codoc_dir)
    assert d1_id in {d.id for d in read_manifest(codoc_dir)}, \
        "a handed-off, in-flight directive must survive supersede (INV8)"


def test_descriptive_new_node_command_does_not_build(codoc_dir, tmp_path):
    """A plain `add` command (a documentation node, realized defaults True) is created
    but mints NO directive — only an explicit plan (verdict path) builds. (Replaces the
    doc-diff descriptive-vs-plan ADD tests; the plan-builds path is covered by
    test_loop_b.test_accept_plan_proposal_applies_and_builds_directive.)"""
    _seed(codoc_dir, "Existing", "x")
    append_command(codoc_dir, Command(
        id="cmd-desc-add", kind="add", local_id="lid-doc",
        payload={"title": "Add a dark theme toggle", "description": "verb-led prose"}))
    res = run_loop_b(str(tmp_path), codoc_dir)
    assert res.commands == 1
    assert res.queued is False and res.directives == []  # a plain add never builds
    with open_store(codoc_dir) as s:
        assert any(f.title == "Add a dark theme toggle" for f in s.list_features())


def test_held_drafts_coalesce_to_one(codoc_dir, tmp_path):
    """Held drafts (the default) coalesce: iterating one feature across passes leaves a
    SINGLE held draft, never a stack. No epoch involved — held drafts aren't in-flight.
    (Edits arrive as `set_description` commands; the per-feature supersede is unchanged.)"""
    from codoc.loop.edits import read_manifest

    fid = _seed(codoc_dir, "Feature", "Old desc.")
    _amend_cmd(codoc_dir, fid, "Add caching layer.")
    run_loop_b(str(tmp_path), codoc_dir)
    assert len(read_manifest(codoc_dir)) == 1

    _amend_cmd(codoc_dir, fid, "Add caching layer with TTL.")
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
    _amend_cmd(codoc_dir, fid, "Add validation for empty input.")
    res = run_loop_b(str(tmp_path), codoc_dir)
    assert res.commands == 1
    with open_store(codoc_dir) as s:
        assert s.get_feature(fid).description == "Add validation for empty input."
