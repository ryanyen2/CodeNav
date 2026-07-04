"""Daemon edit-capture robustness — the substrate the in-situ suggesting UX sits on.

Post-U7 the webview→daemon channel is identity-keyed COMMANDS (U3/U4), not a
``tree.doc.json`` diff: the daemon is the sole writer of both files, so reading either
back as input was a feedback loop (R18). These tests hammer the editing sequences a
real user produces — minimal edits, undo/redo, empty/coderef/multi-paragraph
descriptions, rapid successive edits — and assert the daemon captures the RIGHT edit
exactly once (the idempotency ledger, KTD8, replaces the R19 phantom-oscillation guard:
the daemon never re-reads its own render) with no stacked directives (R10).
"""
from __future__ import annotations

import pytest

from codoc.codoc_file.render import write_tree
from codoc.loop.edits import (Command, append_command, append_handoffs, hold_set,
                              read_manifest)
from codoc.loop.loop_b import realize_path, run_loop_b
from codoc.model.binding import Binding
from codoc.model.feature import Feature
from codoc.store.db import open_store


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"; root.mkdir()
    codoc_dir = tmp_path / ".codoc"; codoc_dir.mkdir()
    return str(root), str(codoc_dir)


def _seed(codoc_dir, *, title="Feat", description="seed.", bind=True):
    s = open_store(codoc_dir)
    f = Feature(title=title, description=description)
    s.upsert_feature(f)
    if bind:
        s.upsert_binding(Binding(feature_id=f.id, file="m.py",
                                 symbol_path="m.py::SYM", fingerprint="h"))
    write_tree(s, codoc_dir)
    s.close()
    return f


_N = [0]


def _amend(codoc_dir, fid, description):
    """Queue a `set_description` command with a fresh id — the webview's edit channel."""
    _N[0] += 1
    cid = f"cmd-{_N[0]}"
    append_command(codoc_dir, Command(id=cid, kind="set_description",
                                      feature_id=fid, payload={"description": description}))
    return cid


def _desc(codoc_dir, fid):
    with open_store(codoc_dir) as s:
        f = s.get_feature(fid)
        return f.description if f else None


def _pass(root, codoc_dir):
    """One Loop B pass; returns (commands, queued_total, directive_feature_ids)."""
    res = run_loop_b(root, codoc_dir, dry_run=False)
    return res.commands, res.queued_total, [d.feature_id for d in read_manifest(codoc_dir)]


# ── minimal edit + idempotent re-send (AE6 / KTD8) ────────────────────────────

def test_minimal_edit_captured_once_then_idempotent(repo):
    root, codoc_dir = repo
    f = _seed(codoc_dir, description="Holds colors.")
    cid = _amend(codoc_dir, f.id, "Holds brand colors.")
    cmds, _, _ = _pass(root, codoc_dir)
    assert cmds == 1 and _desc(codoc_dir, f.id) == "Holds brand colors."
    # Re-send the SAME command id (crash-replay) → ledger no-op, no phantom re-apply.
    append_command(codoc_dir, Command(id=cid, kind="set_description",
                                      feature_id=f.id, payload={"description": "Holds brand colors."}))
    cmds2, _, _ = _pass(root, codoc_dir)
    assert cmds2 == 0


# ── undo / redo — revert to a prior state, then forward again ─────────────────

def test_undo_redo_sequence_captured_correctly(repo):
    root, codoc_dir = repo
    f = _seed(codoc_dir, description="Original.")
    # edit → A
    _amend(codoc_dir, f.id, "Edited version A.")
    _pass(root, codoc_dir)
    assert _desc(codoc_dir, f.id) == "Edited version A."
    # undo → back to Original (a fresh command id, a real change)
    _amend(codoc_dir, f.id, "Original.")
    cmds, _, _ = _pass(root, codoc_dir)
    assert cmds == 1 and _desc(codoc_dir, f.id) == "Original."
    # redo → A again
    _amend(codoc_dir, f.id, "Edited version A.")
    cmds, _, _ = _pass(root, codoc_dir)
    assert cmds == 1 and _desc(codoc_dir, f.id) == "Edited version A."
    # no pending command → no-op pass
    assert _pass(root, codoc_dir)[0] == 0


# ── edge cases that might break the applier ───────────────────────────────────

def test_empty_description_round_trips(repo):
    root, codoc_dir = repo
    f = _seed(codoc_dir, description="Has text.")
    _amend(codoc_dir, f.id, "")  # cleared to empty
    cmds, _, _ = _pass(root, codoc_dir)
    assert cmds == 1 and _desc(codoc_dir, f.id) == ""
    assert _pass(root, codoc_dir)[0] == 0  # no further pending command


def test_coderef_edit_round_trips(repo):
    root, codoc_dir = repo
    f = _seed(codoc_dir, description="Plain.")
    # a codeRef rendered as its canonical markdown inside the description
    _amend(codoc_dir, f.id, "Uses [SYM](codoc:m.py#SYM) now.")
    cmds, _, _ = _pass(root, codoc_dir)
    assert cmds == 1
    assert "[SYM](codoc:m.py#SYM)" in (_desc(codoc_dir, f.id) or "")
    assert _pass(root, codoc_dir)[0] == 0


def test_multi_paragraph_edit_round_trips(repo):
    root, codoc_dir = repo
    f = _seed(codoc_dir, description="One para.")
    _amend(codoc_dir, f.id, "First paragraph.\n\nSecond paragraph.")
    cmds, _, _ = _pass(root, codoc_dir)
    assert cmds == 1 and _desc(codoc_dir, f.id) == "First paragraph.\n\nSecond paragraph."
    assert _pass(root, codoc_dir)[0] == 0


# ── rapid successive imperative edits coalesce to one directive (R10) ─────────

def test_rapid_imperative_iterations_coalesce(repo):
    root, codoc_dir = repo
    f = _seed(codoc_dir, description="Caches values.")
    for v in ["Should also cache reads.",
              "Should also cache reads and writes.",
              "Should also cache reads, writes, and evictions."]:
        _amend(codoc_dir, f.id, v)
        _pass(root, codoc_dir)
    fids = read_manifest(codoc_dir)
    assert [d.feature_id for d in fids] == [f.id], "iterating stacked >1 directive for one feature"
    assert "evictions" in fids[0].text  # the latest iteration won


def test_baseline_is_stable_across_iterations(repo):
    """R5/R6 — the in-situ diff baseline freezes at the START of the pending episode;
    iterating a feature's draft must NOT erode it to the previous keystroke (else the
    decoration shrinks/vanishes as the user keeps typing — the field bug)."""
    root, codoc_dir = repo
    f = _seed(codoc_dir, description="Caches values.")
    _amend(codoc_dir, f.id, "Should also cache reads.")
    _pass(root, codoc_dir)
    assert read_manifest(codoc_dir)[0].baseline == "Caches values."  # episode start
    for v in ["Should also cache reads and writes.",
              "Should also cache reads, writes, and evictions."]:
        _amend(codoc_dir, f.id, v)
        _pass(root, codoc_dir)
        assert read_manifest(codoc_dir)[0].baseline == "Caches values.", "baseline eroded mid-episode"


# ── held-draft model — a command AMEND is HELD by default until an explicit hand-off ──

def test_doc_amend_is_held_then_realized_on_handoff(repo):
    """A command AMEND is HELD by default — in the manifest + hold set (so it shows the
    in-situ diff) but NOT in realize.md (the agent trigger) — until an explicit hand-off
    appends its feature to the `handoffs` channel. No prose-guessing, no surprise code."""
    root, codoc_dir = repo
    f = _seed(codoc_dir, description="Caches values.")
    _amend(codoc_dir, f.id, "Should also cache reads.")
    _pass(root, codoc_dir)

    m = read_manifest(codoc_dir)
    assert len(m) == 1 and m[0].handed_off is False         # held, not handed off
    assert not realize_path(codoc_dir).exists()             # no agent trigger yet
    assert f.id in hold_set(codoc_dir)                       # surfaces as in-situ diff / pending dot

    # a pass with no new command must NOT realize it (still held)
    _pass(root, codoc_dir)
    assert read_manifest(codoc_dir) and not realize_path(codoc_dir).exists()

    # HAND OFF (the webview commit / `codoc realize`): the positive realize signal.
    append_handoffs(codoc_dir, [f.id])
    _pass(root, codoc_dir)
    m2 = read_manifest(codoc_dir)
    assert m2 and m2[0].handed_off is True
    assert realize_path(codoc_dir).exists()                 # the agent trigger is now written


def test_doc_amend_never_realizes_without_handoff(repo):
    """The held-draft default IS the behavior change: a command AMEND never auto-realizes
    from prose mood (deleting is_imperative). It stays a held draft until hand-off —
    a typo fix or a description reword never surprises the agent with code."""
    root, codoc_dir = repo
    f = _seed(codoc_dir, description="Caches values.")
    _amend(codoc_dir, f.id, "Should also cache reads.")
    _pass(root, codoc_dir)
    m = read_manifest(codoc_dir)
    assert m and m[0].handed_off is False and not realize_path(codoc_dir).exists()


def test_per_feature_handoff_realizes_only_the_selected(repo):
    """Per-draft hand-off: both features are held; handing off only B realizes B and
    leaves A's held draft untouched (a typo-fix draft on A is not flushed with B)."""
    root, codoc_dir = repo
    fa = _seed(codoc_dir, title="A", description="A caches values.")
    s = open_store(codoc_dir)
    fb = Feature(title="B", description="B caches values.")
    s.upsert_feature(fb)
    s.upsert_binding(Binding(feature_id=fb.id, file="b.py", symbol_path="b.py::B", fingerprint="h"))
    write_tree(s, codoc_dir); s.close()

    _amend(codoc_dir, fa.id, "Should also cache reads.")
    _amend(codoc_dir, fb.id, "Should also cache writes.")
    _pass(root, codoc_dir)
    # Both held by default.
    m0 = {d.feature_id: d for d in read_manifest(codoc_dir)}
    assert m0[fa.id].handed_off is False and m0[fb.id].handed_off is False

    # Hand off ONLY B.
    append_handoffs(codoc_dir, [fb.id])
    _pass(root, codoc_dir)
    m = {d.feature_id: d for d in read_manifest(codoc_dir)}
    assert m[fa.id].handed_off is False and m[fb.id].handed_off is True
    body = realize_path(codoc_dir).read_text()
    assert "cache writes" in body and "cache reads" not in body  # only B handed off
    assert fa.id in hold_set(codoc_dir) and fb.id in hold_set(codoc_dir)
