"""H1 — non-destructive writes: safe_write_tree never clobbers human edits."""
from __future__ import annotations

import pytest

from codoc.codoc_file.render import tree_path, write_tree
from codoc.loop.reconcile import has_pending_user_edits, safe_write_tree
from codoc.model.feature import Feature
from codoc.store.db import open_store


@pytest.fixture
def codoc(tmp_path):
    cd = tmp_path / ".codoc"
    cd.mkdir()
    return str(cd)


def test_write_tree_skips_byte_identical_rewrite(codoc):
    """U2: write_tree must NOT touch tree.codoc when the render is byte-identical to
    what's on disk. A redundant rewrite bumps the mtime and races the IDE's own save
    of tree.codoc ("content of the file is newer"). Proven via mtime: a skipped write
    leaves the pinned past-mtime intact; a real change updates it."""
    import os

    s = open_store(codoc)
    try:
        s.upsert_feature(Feature(title="Auth", description="Login."))
        tp = tree_path(codoc)
        write_tree(s, codoc)                       # initial write
        past = tp.stat().st_mtime - 100
        os.utime(tp, (past, past))                 # pin mtime to the past

        write_tree(s, codoc)                       # store unchanged → identical render → skip
        assert tp.stat().st_mtime == past          # NOT rewritten (mtime preserved)

        s.upsert_feature(Feature(title="Data"))    # genuine store change
        write_tree(s, codoc)                       # render differs → rewrite
        assert tp.stat().st_mtime != past          # rewritten
    finally:
        s.close()


def test_safe_write_renders_when_file_is_clean(codoc):
    s = open_store(codoc)
    try:
        s.upsert_feature(Feature(title="Auth"))
        write_tree(s, codoc)  # file now matches the store
        assert has_pending_user_edits(codoc) is False
        wrote = safe_write_tree(s, codoc)
        assert wrote is True
    finally:
        s.close()


def test_safe_write_skips_and_preserves_a_saved_human_edit(codoc):
    s = open_store(codoc)
    try:
        f = Feature(title="Auth", description="Login.")
        s.upsert_feature(f)
        write_tree(s, codoc)

        # Human saves an edit to tree.codoc that the store hasn't absorbed.
        tp = tree_path(codoc)
        edited = tp.read_text().replace("Auth", "Authentication")
        tp.write_text(edited)
        assert has_pending_user_edits(codoc) is True

        # An agent's MCP reflection re-renders: it must NOT overwrite the edit.
        f2 = Feature(title="Data")
        s.upsert_feature(f2)
        wrote = safe_write_tree(s, codoc)
        assert wrote is False
        assert "Authentication" in tp.read_text()  # human edit preserved
    finally:
        s.close()


def test_safe_write_always_refreshes_sidecar_even_when_text_skipped(codoc):
    """The sidecar is pure derived state — safe_write_tree must refresh it even when
    the tree.codoc text render is held back to preserve a human edit (so an applied
    verdict / new binding shows in the IDE immediately, not as a dead click)."""
    import json
    from pathlib import Path
    from codoc.model.binding import Binding

    s = open_store(codoc)
    try:
        f = Feature(title="Auth", description="Login.")
        s.upsert_feature(f)
        write_tree(s, codoc)

        # Human edit to tree.codoc the store hasn't absorbed → text render is guarded.
        tp = tree_path(codoc)
        tp.write_text(tp.read_text().replace("Auth", "Authentication"))
        assert has_pending_user_edits(codoc) is True

        # Store gains a new binding (as an applied verdict/attach would).
        s.upsert_binding(Binding(feature_id=f.id, file="auth.py",
                                 symbol_path="auth.py::login", fingerprint="h"))
        wrote = safe_write_tree(s, codoc)

        assert wrote is False                              # text guarded (edit preserved)
        assert "Authentication" in tp.read_text()          # human edit intact
        sidecar = json.loads(Path(codoc, "tree.bindings.json").read_text())
        assert sidecar["by_feature"][f.id] == [{"file": "auth.py", "symbol": "auth.py::login"}]
    finally:
        s.close()
