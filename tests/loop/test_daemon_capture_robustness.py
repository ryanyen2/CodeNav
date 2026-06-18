"""Daemon edit-capture robustness — the substrate the in-situ suggesting UX sits on.

Simulates the webview→daemon channel faithfully: write ``tree.doc.json`` (the
single-writer authored doc), run Loop B, inspect what the daemon captured. Hammers the
editing sequences a real user produces — minimal edits, undo/redo, whitespace/quote
toggles, codeRefs, multi-paragraph, add/retire, rapid successive edits — and asserts the
daemon captures the RIGHT edits with no phantom/oscillation (R19) and no stacked
directives (R10). These are the cases that broke the system in the field; if ProseMirror
can express the edit, the daemon must capture it correctly.
"""
from __future__ import annotations

import json

import pytest

from codoc.codoc_file.doc_parse import doc_path
from codoc.codoc_file.parse import parse_tree_file
from codoc.codoc_file.diff import diff_codoc
from codoc.codoc_file.render import write_tree
from codoc.loop.edits import hold_set, read_manifest, set_drafts
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


def _para(text):
    return {"type": "paragraph", "content": ([{"type": "text", "text": text}] if text else [])}


def _write_doc(codoc_dir, features):
    """Write tree.doc.json the way the webview host does: a flat doc of
    featureHeading + paragraph blocks. `features` = [(fid, title, [para_text, ...])]."""
    content = []
    for fid, title, paras in features:
        content.append({"type": "featureHeading",
                        "attrs": {"fid": fid, "level": 0, "retired": False, "realized": True},
                        "content": [{"type": "text", "text": title}]})
        content.extend(_para(p) for p in paras)
    doc_path(codoc_dir).write_text(json.dumps({"version": 1, "doc": {"type": "doc", "content": content}}))


def _desc(codoc_dir, fid):
    with open_store(codoc_dir) as s:
        f = s.get_feature(fid)
        return f.description if f else None


def _pass(root, codoc_dir):
    """One Loop B pass; returns (user_edits, queued_total, directive_feature_ids)."""
    res = run_loop_b(root, codoc_dir, dry_run=False)
    return res.user_edits, res.queued_total, [d.feature_id for d in read_manifest(codoc_dir)]


# ── minimal edit + idempotent re-pass (AE6) ──────────────────────────────────

def test_minimal_edit_captured_once_then_idempotent(repo):
    root, codoc_dir = repo
    f = _seed(codoc_dir, description="Holds colors.")
    _write_doc(codoc_dir, [(f.id, "Feat", ["Holds brand colors."])])
    edits, _, _ = _pass(root, codoc_dir)
    assert edits == 1 and _desc(codoc_dir, f.id) == "Holds brand colors."
    # host re-persists the same doc → second pass must be a no-op (no phantom).
    edits2, _, _ = _pass(root, codoc_dir)
    assert edits2 == 0


# ── whitespace / quote toggle — the field oscillation (R19) ───────────────────

@pytest.mark.parametrize("text", [
    "Holds brand colors. ",            # trailing space
    'Holds brand colors. "extra',      # a stray quote mid-edit
    "Holds brand colors.\n",           # trailing newline (as a doc the host may emit)
])
def test_whitespace_and_quote_edits_do_not_oscillate(repo, text):
    root, codoc_dir = repo
    f = _seed(codoc_dir, description="seed.")
    _write_doc(codoc_dir, [(f.id, "Feat", [text])])
    _pass(root, codoc_dir)
    # re-persist the SAME doc repeatedly: each must be a no-op (no re-apply loop).
    for _ in range(3):
        edits, _, _ = _pass(root, codoc_dir)
        assert edits == 0, f"phantom re-apply for {text!r}"


# ── undo / redo — revert to a prior state, then forward again ─────────────────

def test_undo_redo_sequence_captured_correctly(repo):
    root, codoc_dir = repo
    f = _seed(codoc_dir, description="Original.")
    # edit → A
    _write_doc(codoc_dir, [(f.id, "Feat", ["Edited version A."])])
    _pass(root, codoc_dir)
    assert _desc(codoc_dir, f.id) == "Edited version A."
    # undo → back to Original (a real change the daemon should capture)
    _write_doc(codoc_dir, [(f.id, "Feat", ["Original."])])
    edits, _, _ = _pass(root, codoc_dir)
    assert edits == 1 and _desc(codoc_dir, f.id) == "Original."
    # redo → A again
    _write_doc(codoc_dir, [(f.id, "Feat", ["Edited version A."])])
    edits, _, _ = _pass(root, codoc_dir)
    assert edits == 1 and _desc(codoc_dir, f.id) == "Edited version A."
    # settle: re-persist the same → no-op
    assert _pass(root, codoc_dir)[0] == 0


# ── edge cases that might break the parser/diff ───────────────────────────────

def test_empty_description_round_trips(repo):
    root, codoc_dir = repo
    f = _seed(codoc_dir, description="Has text.")
    _write_doc(codoc_dir, [(f.id, "Feat", [""])])  # cleared to empty
    edits, _, _ = _pass(root, codoc_dir)
    assert edits == 1 and _desc(codoc_dir, f.id) == ""
    assert _pass(root, codoc_dir)[0] == 0  # idempotent


def test_coderef_edit_round_trips(repo):
    root, codoc_dir = repo
    f = _seed(codoc_dir, description="Plain.")
    # a codeRef rendered as its canonical markdown inside the paragraph text
    _write_doc(codoc_dir, [(f.id, "Feat", ["Uses [SYM](codoc:m.py#SYM) now."])])
    edits, _, _ = _pass(root, codoc_dir)
    assert edits == 1
    assert "[SYM](codoc:m.py#SYM)" in (_desc(codoc_dir, f.id) or "")
    assert _pass(root, codoc_dir)[0] == 0  # idempotent with a ref present


def test_multi_paragraph_edit_round_trips(repo):
    root, codoc_dir = repo
    f = _seed(codoc_dir, description="One para.")
    _write_doc(codoc_dir, [(f.id, "Feat", ["First paragraph.", "Second paragraph."])])
    edits, _, _ = _pass(root, codoc_dir)
    assert edits == 1 and _desc(codoc_dir, f.id) == "First paragraph.\n\nSecond paragraph."
    assert _pass(root, codoc_dir)[0] == 0  # idempotent across the paragraph break


# ── rapid successive imperative edits coalesce to one directive (R10) ─────────

def test_rapid_imperative_iterations_coalesce(repo):
    root, codoc_dir = repo
    f = _seed(codoc_dir, description="Caches values.")
    for v in ["Should also cache reads.",
              "Should also cache reads and writes.",
              "Should also cache reads, writes, and evictions."]:
        _write_doc(codoc_dir, [(f.id, "Feat", [v])])
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
    _write_doc(codoc_dir, [(f.id, "Feat", ["Should also cache reads."])])
    _pass(root, codoc_dir)
    assert read_manifest(codoc_dir)[0].baseline == "Caches values."  # episode start
    for v in ["Should also cache reads and writes.",
              "Should also cache reads, writes, and evictions."]:
        _write_doc(codoc_dir, [(f.id, "Feat", [v])])
        _pass(root, codoc_dir)
        assert read_manifest(codoc_dir)[0].baseline == "Caches values.", "baseline eroded mid-episode"


# ── U3/U4 — suggesting-mode drafts are HELD until hand-off ────────────────────

def test_draft_edit_is_held_then_realized_on_handoff(repo):
    """A code-implying edit on a feature the webview marks as a DRAFT stays held — in the
    manifest + hold set (so it shows the in-situ diff) but NOT in realize.md (the agent
    trigger) — until the host hands it off (removes it from `drafts`)."""
    root, codoc_dir = repo
    f = _seed(codoc_dir, description="Caches values.")
    set_drafts(codoc_dir, [f.id])  # webview holds this feature's edit as a suggesting-mode draft
    _write_doc(codoc_dir, [(f.id, "Feat", ["Should also cache reads."])])
    _pass(root, codoc_dir)

    m = read_manifest(codoc_dir)
    assert len(m) == 1 and m[0].handed_off is False         # held, not handed off
    assert not realize_path(codoc_dir).exists()             # no agent trigger yet
    assert f.id in hold_set(codoc_dir)                       # but surfaces as in-situ diff / pending dot

    # re-persisting the same draft doc must NOT clear it as stale, nor realize it
    _pass(root, codoc_dir)
    assert read_manifest(codoc_dir) and not realize_path(codoc_dir).exists()

    # HAND OFF: host removes the feature from drafts → next pass promotes + writes the trigger
    set_drafts(codoc_dir, [])
    _pass(root, codoc_dir)
    m2 = read_manifest(codoc_dir)
    assert m2 and m2[0].handed_off is True
    assert realize_path(codoc_dir).exists()                 # the agent trigger is now written


def test_default_no_drafts_realizes_immediately(repo):
    """Without any drafts signal (raw-text edits / today's flow), a code-implying edit
    realizes immediately — the draft gate is additive, not a behavior change."""
    root, codoc_dir = repo
    f = _seed(codoc_dir, description="Caches values.")
    _write_doc(codoc_dir, [(f.id, "Feat", ["Should also cache reads."])])
    _pass(root, codoc_dir)
    m = read_manifest(codoc_dir)
    assert m and m[0].handed_off is True and realize_path(codoc_dir).exists()


def test_mixed_draft_held_while_other_feature_realizes(repo):
    """One feature held as a draft, another not: the non-draft realizes (in realize.md);
    the draft stays held but in the hold set."""
    root, codoc_dir = repo
    fa = _seed(codoc_dir, title="A", description="A caches values.")
    s = open_store(codoc_dir)
    fb = Feature(title="B", description="B caches values.")
    s.upsert_feature(fb)
    s.upsert_binding(Binding(feature_id=fb.id, file="b.py", symbol_path="b.py::B", fingerprint="h"))
    write_tree(s, codoc_dir); s.close()

    set_drafts(codoc_dir, [fa.id])  # only A is held
    _write_doc(codoc_dir, [(fa.id, "A", ["Should also cache reads."]),
                           (fb.id, "B", ["Should also cache writes."])])
    _pass(root, codoc_dir)

    m = {d.feature_id: d for d in read_manifest(codoc_dir)}
    assert m[fa.id].handed_off is False and m[fb.id].handed_off is True
    body = realize_path(codoc_dir).read_text()
    assert "cache writes" in body and "cache reads" not in body  # only B handed off
    assert fa.id in hold_set(codoc_dir) and fb.id in hold_set(codoc_dir)
