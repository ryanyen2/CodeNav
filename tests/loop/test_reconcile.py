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


def test_safe_write_rerenders_over_a_stray_manual_edit(codoc):
    """Anti-wedge regression. tree.codoc is a READ-ONLY derived export: the text-ingest
    channel is retired, so a stray manual edit (or a git merge/checkout) can never be
    absorbed. safe_write_tree must RE-RENDER from the store — not skip forever, which
    used to wedge both tree.codoc and tree.doc.json while status still read in_sync."""
    s = open_store(codoc)
    try:
        f = Feature(title="Auth", description="Login.")
        s.upsert_feature(f)
        write_tree(s, codoc)

        # Human hand-edits tree.codoc directly (the store never sees it).
        tp = tree_path(codoc)
        tp.write_text(tp.read_text().replace("Auth", "Authentication"))
        assert has_pending_user_edits(codoc) is True

        # A later render (agent MCP reflection / daemon pass) self-heals: it re-renders
        # the export from the store, overwriting the un-absorbable stray edit.
        f2 = Feature(title="Data")
        s.upsert_feature(f2)
        wrote = safe_write_tree(s, codoc)
        assert wrote is True
        text = tp.read_text()
        assert "Authentication" not in text        # stray edit overwritten (self-healed)
        assert "Auth" in text and "Data" in text    # store content rendered
        assert has_pending_user_edits(codoc) is False
    finally:
        s.close()


def test_safe_write_always_refreshes_sidecar(codoc):
    """The sidecar is pure derived state — safe_write_tree always refreshes it, and (in
    the read-only-export model) also re-renders the text, so an applied verdict / new
    binding shows in the IDE immediately even if tree.codoc had drifted."""
    import json
    from pathlib import Path
    from codoc.model.binding import Binding

    s = open_store(codoc)
    try:
        f = Feature(title="Auth", description="Login.")
        s.upsert_feature(f)
        write_tree(s, codoc)

        # tree.codoc drifts from the store (a stray edit / git op).
        tp = tree_path(codoc)
        tp.write_text(tp.read_text().replace("Auth", "Authentication"))
        assert has_pending_user_edits(codoc) is True

        # Store gains a new binding (as an applied verdict/attach would).
        s.upsert_binding(Binding(feature_id=f.id, file="auth.py",
                                 symbol_path="auth.py::login", fingerprint="h"))
        wrote = safe_write_tree(s, codoc)

        assert wrote is True                               # export re-rendered (self-heal)
        assert "Authentication" not in tp.read_text()      # drift overwritten
        sidecar = json.loads(Path(codoc, "tree.bindings.json").read_text())
        assert sidecar["by_feature"][f.id] == [{"file": "auth.py", "symbol": "auth.py::login"}]
    finally:
        s.close()
