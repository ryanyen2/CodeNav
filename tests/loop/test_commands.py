"""U3 — identity-keyed command channel + applier (KTD3 / KTD8 / KTD10).

Authored edits arrive as EXPLICIT commands (add/set_title/set_description/move/
retire) keyed by feature/local id and applied via ``apply_op`` — NOT inferred from
a doc diff. These tests pin the applier's contract: minted-fid correlation,
idempotency on the store ledger, the ``(normalized_title, parent_id)`` dedup guard,
structural retire/move, in-place amend, drain-order (commands before legacy
annotations), and the vanished-target skip.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import json

from codoc.codoc_file.doc_render import build_doc_from_store
from codoc.codoc_file.render import write_tree
from codoc.loop import edits as edits_channel
from codoc.loop.edits import Command, append_command
from codoc.loop.filenames import DOC_FILENAME
from codoc.loop.loop_b import run_loop_b
from codoc.model.feature import Feature
from codoc.store.db import open_store


@pytest.fixture
def dirs(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    return str(root), str(codoc_dir)


def _seed_tree(codoc_dir, *features: Feature) -> None:
    s = open_store(codoc_dir)
    for f in features:
        s.upsert_feature(f)
    write_tree(s, codoc_dir)
    s.close()


# ── add: mints exactly one feature + echoes fid keyed to local_id ────────────
def test_add_mints_one_feature_and_echoes_fid_by_local_id(dirs):
    root, codoc_dir = dirs
    _seed_tree(codoc_dir)  # empty tree
    append_command(codoc_dir, Command(
        id="cmd-1", kind="add", local_id="local-42",
        payload={"title": "Theme system", "description": "Light/dark switcher."}))

    res = run_loop_b(root, codoc_dir, dry_run=False)

    assert res.commands == 1
    s = open_store(codoc_dir)
    minted = [f for f in s.list_features() if f.title == "Theme system"]
    assert len(minted) == 1
    fid = minted[0].id
    assert minted[0].description == "Light/dark switcher."
    assert minted[0].local_id == "local-42"
    s.close()
    # The minted fid is echoed back keyed to the submitted local_id (host adopts it).
    assert res.fids_by_local == {"local-42": fid}


# ── idempotency: re-applying the same command id is a no-op (the ledger) ─────
def test_reapplying_same_command_id_is_noop(dirs):
    root, codoc_dir = dirs
    _seed_tree(codoc_dir)
    cmd = Command(id="cmd-dup", kind="add", local_id="L1",
                  payload={"title": "Once", "description": "d"})
    append_command(codoc_dir, cmd)
    run_loop_b(root, codoc_dir, dry_run=False)

    # Re-queue the SAME id (a crash-replay / re-send). The drain sees it; the ledger
    # skips it — no second feature.
    append_command(codoc_dir, cmd)
    res2 = run_loop_b(root, codoc_dir, dry_run=False)

    assert res2.commands == 0
    s = open_store(codoc_dir)
    assert len([f for f in s.list_features() if f.title == "Once"]) == 1
    s.close()


# ── dedup: a fresh-id second add with the same (norm title, parent) is rejected ─
def test_second_add_same_title_different_local_id_mints_sibling(dirs):
    """#5 — a second `add` carrying a DIFFERENT local_id but the same (title, parent)
    is a DELIBERATE same-titled sibling, NOT a duplicate: it mints its own feature and
    echoes its fid. The old (normalized_title, parent_id) fold swallowed it — which both
    made same-titled siblings impossible to author AND stranded the webview's optimistic
    node with no fid to adopt (a zombie heading that vanished on reload). The local_id is
    the identity here (KTD8); re-sends are caught by the ledger + feature_by_local_id."""
    root, codoc_dir = dirs
    _seed_tree(codoc_dir)
    append_command(codoc_dir, Command(id="c-a", kind="add", local_id="L1",
                                      payload={"title": "Palette"}))
    res1 = run_loop_b(root, codoc_dir, dry_run=False)

    # Same normalized title (whitespace/case differ), same parent (None), a NEW local_id.
    append_command(codoc_dir, Command(id="c-b", kind="add", local_id="L2",
                                      payload={"title": "  palette "}))
    res2 = run_loop_b(root, codoc_dir, dry_run=False)

    s = open_store(codoc_dir)
    palettes = [f for f in s.list_features() if f.title.strip().lower() == "palette"]
    assert len(palettes) == 2                       # two deliberate siblings
    s.close()
    # Each add echoed its own minted fid so the webview adopts the right node.
    assert set(res1.fids_by_local) == {"L1"}
    assert set(res2.fids_by_local) == {"L2"}
    assert res1.fids_by_local["L1"] != res2.fids_by_local["L2"]


def test_second_add_same_title_no_local_id_folds(dirs):
    """The (normalized_title, parent_id) fold survives for an add carrying NO local_id —
    the Loop-A LLM-apply / CLI path, whose replays have no stable client identity to key
    on. A second no-local_id add with the same title+parent folds (mints nothing)."""
    root, codoc_dir = dirs
    _seed_tree(codoc_dir)
    append_command(codoc_dir, Command(id="c-a", kind="add", local_id="",
                                      payload={"title": "Palette"}))
    run_loop_b(root, codoc_dir, dry_run=False)
    append_command(codoc_dir, Command(id="c-b", kind="add", local_id="",
                                      payload={"title": "  palette "}))
    res2 = run_loop_b(root, codoc_dir, dry_run=False)

    s = open_store(codoc_dir)
    assert len([f for f in s.list_features() if f.title.strip().lower() == "palette"]) == 1
    s.close()
    assert res2.fids_by_local == {}  # no local_id → nothing to echo


# ── add re-mint guard via local_id (FIX B): same local_id, CHANGED title ─────
def test_add_reemitted_same_local_id_changed_title_does_not_remint(dirs):
    """A re-emitted `add` carrying the SAME local_id but a CHANGED title (a settle
    fired again before the mint echoed back, after the user kept typing) must FOLD
    onto the feature that local_id already minted — not slip past the
    (normalized_title, parent_id) guard and mint a second node (FIX B). The minted
    fid is re-echoed so the host still adopts the original node."""
    root, codoc_dir = dirs
    _seed_tree(codoc_dir)  # empty
    append_command(codoc_dir, Command(id="c-add-1", kind="add", local_id="L-stable",
                                      payload={"title": "Palette"}))
    res1 = run_loop_b(root, codoc_dir, dry_run=False)
    fid = res1.fids_by_local["L-stable"]

    # Same local_id, a DIFFERENT title (the user kept editing) and a fresh command id.
    append_command(codoc_dir, Command(id="c-add-2", kind="add", local_id="L-stable",
                                      payload={"title": "Palette renamed"}))
    res2 = run_loop_b(root, codoc_dir, dry_run=False)

    s = open_store(codoc_dir)
    owners = [f for f in s.list_features() if f.local_id == "L-stable"]
    assert len(owners) == 1                  # exactly one feature owns the local_id
    assert owners[0].id == fid               # the same node, not a re-mint
    s.close()
    assert res2.fids_by_local == {"L-stable": fid}  # re-echoed to the prior mint


# ── crash-consistency (FIX C): a re-run skips an already-applied command ─────
def test_command_already_in_ledger_is_skipped_no_double_apply(dirs):
    """A command id already recorded in the store ledger (a crash AFTER apply but
    BEFORE the channel was cleared re-delivers it) is skipped on the re-run — never
    applied twice (FIX C). Simulated by pre-stamping the ledger, then queueing the
    same command id."""
    root, codoc_dir = dirs
    f = Feature(title="Title A", description="d")
    _seed_tree(codoc_dir, f)
    # Pre-stamp the ledger as if a prior pass applied this id but crashed before clear.
    s = open_store(codoc_dir)
    s.mark_command_applied("c-amend-1")
    s.close()

    append_command(codoc_dir, Command(id="c-amend-1", kind="set_title",
                                      feature_id=f.id, payload={"title": "Title B"}))
    res = run_loop_b(root, codoc_dir, dry_run=False)

    assert res.commands == 0                 # the replayed id was skipped, not re-applied
    s2 = open_store(codoc_dir)
    assert s2.get_feature(f.id).title == "Title A"   # unchanged — no double-apply
    s2.close()
    # And the channel was cleared of the settled-but-uncleared command.
    assert edits_channel.read_commands(codoc_dir) == []


def test_erroring_command_does_not_consume_the_others(dirs):
    """A command that errors mid-apply leaves the OTHER queued commands intact (FIX C):
    the channel is cleared only of the ids that were durably applied, so a re-run
    re-delivers the un-applied ones. Here a poisoned set_title raises; a sibling add
    in the same batch is unaffected and a re-run still has the failed command to retry."""
    import codoc.loop.loop_b as loop_b_mod
    root, codoc_dir = dirs
    f = Feature(title="Existing", description="d")
    _seed_tree(codoc_dir, f)
    append_command(codoc_dir, Command(id="ok-add", kind="add", local_id="LA",
                                      payload={"title": "Healthy"}))
    append_command(codoc_dir, Command(id="boom", kind="set_title",
                                      feature_id=f.id, payload={"title": "Boom"}))

    real_apply = loop_b_mod.apply_op

    def flaky_apply(op, store, **kw):
        if getattr(op, "title", None) == "Boom":
            raise RuntimeError("simulated crash applying set_title")
        return real_apply(op, store, **kw)

    loop_b_mod.apply_op = flaky_apply
    try:
        with pytest.raises(RuntimeError):
            run_loop_b(root, codoc_dir, dry_run=False)
    finally:
        loop_b_mod.apply_op = real_apply

    # The errored command's claim rolled back (transaction): NOT on the ledger, still
    # queued for retry. The healthy add committed BEFORE the crash, so it is on the
    # ledger — even if the abort skipped the channel clear, the ledger guards it from a
    # double-apply on the re-run. The crash must lose NEITHER command.
    s = open_store(codoc_dir)
    assert s.command_applied("boom") is False   # rolled back → re-delivered
    assert s.command_applied("ok-add") is True  # committed before the crash
    s.close()
    remaining = {c.id for c in edits_channel.read_commands(codoc_dir)}
    assert "boom" in remaining           # the failed command survives for retry

    # Re-run with a healed applier: the surviving `boom` applies exactly once, and the
    # already-ledgered `ok-add` is skipped (no double-apply) — the add is not duplicated.
    res2 = run_loop_b(root, codoc_dir, dry_run=False)
    assert res2.commands == 1            # only `boom` newly applied; `ok-add` skipped
    s2 = open_store(codoc_dir)
    assert s2.get_feature(f.id).title == "Boom"
    assert len([x for x in s2.list_features() if x.title == "Healthy"]) == 1  # not duplicated
    s2.close()


# ── retire: tombstones and never re-adds on a later pass ─────────────────────
def test_retire_tombstones_and_does_not_readd(dirs):
    root, codoc_dir = dirs
    f = Feature(title="Legacy", description="old")
    _seed_tree(codoc_dir, f)
    append_command(codoc_dir, Command(id="r-1", kind="retire", feature_id=f.id))

    run_loop_b(root, codoc_dir, dry_run=False)
    s = open_store(codoc_dir)
    assert s.get_feature(f.id).retired is True
    assert all(lf.id != f.id for lf in s.list_features())  # not in the live set
    s.close()

    # A subsequent pass must NOT resurrect it.
    run_loop_b(root, codoc_dir, dry_run=False)
    s2 = open_store(codoc_dir)
    assert s2.get_feature(f.id).retired is True
    s2.close()


# ── move: reparents without minting ──────────────────────────────────────────
def test_move_reparents_without_minting(dirs):
    root, codoc_dir = dirs
    parent = Feature(title="Parent", description="p")
    child = Feature(title="Child", description="c")
    _seed_tree(codoc_dir, parent, child)
    before = {f.id for f in open_store(codoc_dir).list_features()}

    append_command(codoc_dir, Command(id="m-1", kind="move", feature_id=child.id,
                                      payload={"parent_id": parent.id}))
    res = run_loop_b(root, codoc_dir, dry_run=False)

    assert res.commands == 1
    s = open_store(codoc_dir)
    assert s.get_feature(child.id).parent_id == parent.id
    assert {f.id for f in s.list_features()} == before  # no new node
    s.close()


# ── set_title / set_description: amend in place by fid ───────────────────────
def test_set_title_and_description_amend_in_place(dirs):
    root, codoc_dir = dirs
    f = Feature(title="Old title", description="Old desc.")
    _seed_tree(codoc_dir, f)
    append_command(codoc_dir, Command(id="t-1", kind="set_title", feature_id=f.id,
                                      payload={"title": "New title"}))
    append_command(codoc_dir, Command(id="d-1", kind="set_description", feature_id=f.id,
                                      payload={"description": "New desc."}))

    res = run_loop_b(root, codoc_dir, dry_run=False)

    assert res.commands == 2
    s = open_store(codoc_dir)
    g = s.get_feature(f.id)
    assert g.title == "New title" and g.description == "New desc."
    s.close()


# ── drain order: commands apply BEFORE the legacy annotation edits list ──────
def test_commands_apply_before_legacy_annotation(dirs):
    """A pass carrying BOTH `commands` and a matching `edits` annotation for the same
    feature applies the command (step 0.5) AND stamps the command's AMEND event with the
    annotation's authorship — the annotation channel survives (the loop consults it on
    the command apply path), even though the doc-diff text inference it used to stamp is
    retired (U7). Two commands (rename TITLE then rewrite DESCRIPTION) land in order; the
    description AMEND carries the declared author from the annotation."""
    root, codoc_dir = dirs
    f = Feature(title="Feature A", description="Original prose.")
    _seed_tree(codoc_dir, f)

    # Command 1: rename the title (applied first, in submission order).
    append_command(codoc_dir, Command(id="cmd-x", kind="set_title",
                                      feature_id=f.id, payload={"title": "Feature A renamed"}))
    # Command 2: rewrite the description.
    append_command(codoc_dir, Command(id="cmd-y", kind="set_description",
                                      feature_id=f.id, payload={"description": "Rewritten prose."}))
    # Annotation: the IDE host declares who authored the edit for this feature.
    edits_channel.append_annotation(codoc_dir, edits_channel.EditAnnotation(
        feature_id=f.id, fields=["description"], actor="claude-code", mode="pen"))

    run_loop_b(root, codoc_dir, dry_run=False)

    s = open_store(codoc_dir)
    g = s.get_feature(f.id)
    # Both commands applied (title renamed AND description rewritten).
    assert g.title == "Feature A renamed"
    assert g.description == "Rewritten prose."
    # The annotation stamped the command's AMEND events with the declared author.
    fid_events = [e for e in s.recent_events(limit=20) if e.op.feature_id == f.id]
    cmd_titles = [e for e in fid_events if e.op.title == "Feature A renamed"
                  and e.op.description is None]
    desc_amends = [e for e in fid_events if e.op.description == "Rewritten prose."]
    assert cmd_titles, "command's set_title AMEND should be logged"
    assert desc_amends and desc_amends[0].actor == "claude-code"
    s.close()


# ── a command whose feature_id has vanished is skipped without crashing ──────
def test_command_with_vanished_feature_id_is_skipped(dirs):
    root, codoc_dir = dirs
    _seed_tree(codoc_dir)  # empty store — no such feature
    append_command(codoc_dir, Command(id="v-1", kind="set_title",
                                      feature_id="f-does-not-exist", payload={"title": "X"}))

    res = run_loop_b(root, codoc_dir, dry_run=False)  # must not raise

    assert res.commands == 0 and not res.error
    # The command is ledger-stamped so it isn't retried forever.
    s = open_store(codoc_dir)
    assert s.command_applied("v-1") is True
    s.close()
    # And the channel was drained.
    assert edits_channel.read_commands(codoc_dir) == []


# ── a command landing on a RETIRED feature is stale, not applicable ──────────
def test_command_on_retired_feature_is_skipped_not_written_to_the_tombstone(dirs):
    """The projection only ever shows LIVE features, so a command naming a retired
    one was authored before the retire landed. Applying it wrote prose onto a
    tombstone: invisible in every render, but real enough to mint a directive and
    hold the feature forever."""
    root, codoc_dir = dirs
    _seed_tree(codoc_dir, Feature(id="f-1", title="Auth", description="original"))
    s = open_store(codoc_dir)
    s.retire_feature("f-1")
    s.close()

    append_command(codoc_dir, Command(id="r-1", kind="set_description",
                                      feature_id="f-1", payload={"description": "typed after the retire"}))
    res = run_loop_b(root, codoc_dir, dry_run=False)

    assert not res.error
    s = open_store(codoc_dir)
    assert s.get_feature("f-1").description == "original"   # the tombstone is untouched
    assert s.command_applied("r-1") is True                 # and not retried forever
    s.close()
    assert edits_channel.read_commands(codoc_dir) == []


def test_lifecycle_version_advances_even_when_the_clock_goes_backwards(dirs, monkeypatch):
    """A lifecycle change must be strictly NEWER than what it followed.

    Stamping the raw wall clock meant a backwards correction — NTP, a laptop waking
    in another timezone — produced a version LOWER than the amend before it. The
    webview adopts a projection only when it is strictly newer, so it would then
    refuse to ever show the retire: the editor keeps offering a feature the store
    has already tombstoned, and no later render can talk it round.
    """
    import codoc.model.hlc as hlc_mod

    root, codoc_dir = dirs
    _seed_tree(codoc_dir, Feature(id="f-1", title="Auth", description="body"))
    s = open_store(codoc_dir)
    before = s.get_feature("f-1").updated_at

    class _ClockJumpedBack:
        @staticmethod
        def time() -> float:
            return 0.0
    monkeypatch.setattr(hlc_mod, "time", _ClockJumpedBack)

    s.retire_feature("f-1")
    after = s.get_feature("f-1").updated_at
    s.close()

    assert after.to_str() > before.to_str()


# ── KTD9: the daemon is the sole writer of tree.doc.json (U4) ────────────────
def test_loop_b_pass_writes_tree_doc_json_matching_projection(dirs):
    """A Loop B pass that moved the store re-renders tree.doc.json from the store
    projection (KTD9), so the file content matches ``build_doc_from_store`` exactly."""
    root, codoc_dir = dirs
    _seed_tree(codoc_dir)  # empty tree
    append_command(codoc_dir, Command(
        id="cmd-doc", kind="add", local_id="local-7",
        payload={"title": "Theme system", "description": "Light/dark switcher."}))

    run_loop_b(root, codoc_dir, dry_run=False)

    doc_file = Path(codoc_dir) / DOC_FILENAME
    assert doc_file.exists()
    written = json.loads(doc_file.read_text())
    s = open_store(codoc_dir)
    expected = build_doc_from_store(s)
    s.close()
    assert written == expected
    # The projection actually carries the just-added feature (not an empty doc).
    headings = [n for n in written["content"] if n["type"] == "featureHeading"]
    assert any(h.get("content", [{}])[0].get("text") == "Theme system" for h in headings)


def test_dry_run_does_not_write_tree_doc_json(dirs):
    """A dry pass mutates no durable state — it must not write the projection."""
    root, codoc_dir = dirs
    _seed_tree(codoc_dir)
    append_command(codoc_dir, Command(
        id="cmd-dry", kind="add", local_id="local-d",
        payload={"title": "Dry feature", "description": "x"}))

    run_loop_b(root, codoc_dir, dry_run=True)

    assert not (Path(codoc_dir) / DOC_FILENAME).exists()


# ─── base enforcement: neither side's text is overwritten in silence ──────────

def _write_as(codoc_dir, fid: str, description: str, *, source: str, writer: str = "") -> None:
    """Write a description through the real boundary, so the writer AND the role
    are recorded the way rank arbitration will read them."""
    from codoc.loop.apply import apply_op
    from codoc.model.event import NodeOp, NodeOpKind

    s = open_store(codoc_dir)
    apply_op(NodeOp(kind=NodeOpKind.AMEND, feature_id=fid, description=description),
             s, source=source, applied=True, writer=writer)
    s.close()


def test_a_command_a_PEER_overlapped_is_kept_for_review(dirs):
    """Content commands used to apply blind — last writer won, silently, in both
    directions. Here another PERSON rewrote the same line after the author started
    typing, so the author's whole-description command would erase it. Neither of
    them outranks the other, so the store keeps the peer's text and the author's
    version survives as a pending proposal: nobody's work is discarded, and the
    disagreement is visible rather than resolved by whoever typed last."""
    root, codoc_dir = dirs
    _seed_tree(codoc_dir, Feature(id="f-1", title="Auth", description="as the author saw it"))
    _write_as(codoc_dir, "f-1", "a colleague rewrote this",
              source="user", writer="sess-b")   # a person, on another session

    append_command(codoc_dir, Command(
        id="c-1", kind="set_description", feature_id="f-1", session="sess-a",
        base_text="as the author saw it",           # what the author's editor last knew
        payload={"description": "as the author saw it, extended"}))
    res = run_loop_b(root, codoc_dir, dry_run=False)

    assert res.conflicted == 1 and res.merged == 0 and not res.error
    s = open_store(codoc_dir)
    assert s.get_feature("f-1").description == "a colleague rewrote this"  # not overwritten
    pending = s.pending_events()
    assert len(pending) == 1
    assert pending[0].op.description == "as the author saw it, extended"   # nor discarded
    s.close()


# ─── role precedence: a person outranks an agent where the two contend ───────

def test_the_author_wins_the_line_an_agent_rewrote(dirs):
    """The human authors the intent; the agent maintains an index of it. Where the
    two rewrote the SAME line, sending the author to a review surface to accept
    their own words back would teach them the tree argues with them. Their edit
    lands, and the agent's superseded text stays in the event ledger — which is
    what `codoc history` reads, so it is recorded, not destroyed."""
    root, codoc_dir = dirs
    _seed_tree(codoc_dir, Feature(id="f-1", title="Auth", description="as the author saw it"))
    _write_as(codoc_dir, "f-1", "the agent rewrote this", source="loop_a")

    append_command(codoc_dir, Command(
        id="c-1", kind="set_description", feature_id="f-1", session="sess-a",
        base_text="as the author saw it",
        payload={"description": "as the author saw it, extended"}))
    res = run_loop_b(root, codoc_dir, dry_run=False)

    assert res.merged == 1 and res.conflicted == 0 and not res.error
    s = open_store(codoc_dir)
    assert s.get_feature("f-1").description == "as the author saw it, extended"
    assert not s.pending_events()          # nothing for the author to arbitrate
    superseded = [e for e in s.events_for_feature("f-1")
                  if e.op.description == "the agent rewrote this"]
    assert superseded, "the agent's text must remain findable in the ledger"
    s.close()


def test_an_agent_does_not_win_the_line_a_person_wrote(dirs):
    """The rule is asymmetric on purpose, and the asymmetry is the whole point.
    An agent relaying a command through the same channel does NOT get to overwrite
    a person's words — its version goes up for review instead."""
    root, codoc_dir = dirs
    _seed_tree(codoc_dir, Feature(id="f-1", title="Auth", description="the shared base"))
    _write_as(codoc_dir, "f-1", "what the person wrote", source="user", writer="sess-a")

    edits_channel.append_annotation(codoc_dir, edits_channel.EditAnnotation(
        feature_id="f-1", fields=["description"], actor="claude-code", mode="pen"))
    append_command(codoc_dir, Command(
        id="c-1", kind="set_description", feature_id="f-1", session="agent-1",
        base_text="the shared base",
        payload={"description": "what the agent wrote"}))
    res = run_loop_b(root, codoc_dir, dry_run=False)

    assert res.conflicted == 1 and res.merged == 0
    s = open_store(codoc_dir)
    assert s.get_feature("f-1").description == "what the person wrote"
    assert [e.op.description for e in s.pending_events()] == ["what the agent wrote"]
    s.close()


def test_edits_on_different_lines_merge_instead_of_conflicting(dirs):
    """Rank only arbitrates OVERLAP. An author fixing the first paragraph while an
    agent rewrote the third has not disagreed with anyone, and the old all-or-
    nothing refusal handled exactly this case worst: it called the edit a conflict
    and threw the author onto a review surface over words nobody contested."""
    root, codoc_dir = dirs
    base = "first paragraph\n\nsecond paragraph\n\nthird paragraph"
    _seed_tree(codoc_dir, Feature(id="f-1", title="Auth", description=base))
    _write_as(codoc_dir, "f-1",
              "first paragraph\n\nsecond paragraph\n\nthird paragraph, rewritten by the agent",
              source="loop_a")

    append_command(codoc_dir, Command(
        id="c-1", kind="set_description", feature_id="f-1", session="sess-a",
        base_text=base,
        payload={"description": "first paragraph, fixed by the author\n\nsecond paragraph\n\nthird paragraph"}))
    res = run_loop_b(root, codoc_dir, dry_run=False)

    assert res.merged == 1 and res.conflicted == 0
    s = open_store(codoc_dir)
    assert s.get_feature("f-1").description == (
        "first paragraph, fixed by the author\n\nsecond paragraph\n\n"
        "third paragraph, rewritten by the agent"
    )
    s.close()


def test_an_agents_new_paragraph_survives_the_authors_whole_description_settle(dirs):
    """The shape this whole mechanism exists for.

    A settle carries the WHOLE description, computed from a baseline taken before
    the agent appended anything. So the author's text does not merely disagree
    with the agent's paragraph — it does not contain it at all, and applying the
    command verbatim reads as a deliberate deletion. The three-way merge is what
    tells the difference between "never saw it" and "removed it": the paragraph
    is absent from the author's version but also absent from their base, so it
    was never theirs to delete."""
    root, codoc_dir = dirs
    base = "what the feature does\n\nhow it is used"
    _seed_tree(codoc_dir, Feature(id="f-1", title="Auth", description=base))
    _write_as(codoc_dir, "f-1", base + "\n\nan agent appended this", source="loop_a")

    append_command(codoc_dir, Command(
        id="c-1", kind="set_description", feature_id="f-1", session="sess-a",
        base_text=base,   # taken BEFORE the agent's paragraph existed
        payload={"description": "what the feature really does\n\nhow it is used"}))
    res = run_loop_b(root, codoc_dir, dry_run=False)

    assert res.merged == 1 and res.conflicted == 0
    s = open_store(codoc_dir)
    assert s.get_feature("f-1").description == (
        "what the feature really does\n\nhow it is used\n\nan agent appended this"
    )
    s.close()


def test_deleting_a_paragraph_the_author_actually_saw_is_honoured(dirs):
    """The other side of the same coin, and the reason the merge keys off the
    author's BASE rather than off "is this paragraph missing". Here the agent's
    paragraph was in the text they were editing, so leaving it out is a deletion
    and must survive — otherwise nothing an agent writes could ever be removed."""
    root, codoc_dir = dirs
    base = "what the feature does\n\nan agent appended this"
    _seed_tree(codoc_dir, Feature(id="f-1", title="Auth", description=base))
    _write_as(codoc_dir, "f-1", base, source="loop_a")   # agent wrote it; author saw it

    append_command(codoc_dir, Command(
        id="c-1", kind="set_description", feature_id="f-1", session="sess-a",
        base_text=base, payload={"description": "what the feature does"}))
    run_loop_b(root, codoc_dir, dry_run=False)

    s = open_store(codoc_dir)
    assert s.get_feature("f-1").description == "what the feature does"
    s.close()


def test_peers_editing_different_lines_also_merge(dirs):
    """Merging is a question about TEXT, not authority — two people who never
    touched the same words both get their edit. Rank is consulted only when the
    lines actually contend."""
    root, codoc_dir = dirs
    base = "alpha\n\nbeta\n\ngamma"
    _seed_tree(codoc_dir, Feature(id="f-1", title="Auth", description=base))
    _write_as(codoc_dir, "f-1", "alpha\n\nbeta\n\ngamma, by a colleague",
              source="user", writer="sess-b")

    append_command(codoc_dir, Command(
        id="c-1", kind="set_description", feature_id="f-1", session="sess-a",
        base_text=base, payload={"description": "alpha, by me\n\nbeta\n\ngamma"}))
    res = run_loop_b(root, codoc_dir, dry_run=False)

    assert res.merged == 1 and res.conflicted == 0
    s = open_store(codoc_dir)
    assert s.get_feature("f-1").description == "alpha, by me\n\nbeta\n\ngamma, by a colleague"
    s.close()


def test_a_command_that_survives_nothing_leaves_the_writer_record_alone(dirs):
    """The type-then-undo shape, arriving at the daemon. The command's merged
    result is exactly what the store already holds, so there is nothing to write.
    Writing it back anyway would be harmless to the TEXT and corrosive to the
    writer record: this session would be stamped as the author of the agent's
    prose, and its next stale command would then read as 'continuing my own work'
    and overwrite that prose with no merge at all."""
    root, codoc_dir = dirs
    _seed_tree(codoc_dir, Feature(id="f-1", title="Auth", description="the base"))
    _write_as(codoc_dir, "f-1", "the agent's text", source="loop_a")

    append_command(codoc_dir, Command(
        id="c-1", kind="set_description", feature_id="f-1", session="sess-a",
        base_text="the base", payload={"description": "the base"}))  # typed and undone
    res = run_loop_b(root, codoc_dir, dry_run=False)

    assert res.merged == 0 and res.conflicted == 0
    s = open_store(codoc_dir)
    assert s.get_feature("f-1").description == "the agent's text"
    assert s.feature_writer_info("f-1")[1] == "loop"   # still the agent's, not ours
    assert s.command_applied("c-1") is True            # and it never replays
    s.close()


def test_both_sides_arriving_at_the_same_text_is_not_a_conflict(dirs):
    """Convergence, not disagreement. A lagging projection racing an echo of the
    author's own edit produces exactly this, and calling it a conflict would ask
    someone to arbitrate between two identical paragraphs."""
    root, codoc_dir = dirs
    _seed_tree(codoc_dir, Feature(id="f-1", title="Auth", description="the base"))
    _write_as(codoc_dir, "f-1", "the very same words", source="loop_a")

    append_command(codoc_dir, Command(
        id="c-1", kind="set_description", feature_id="f-1", session="sess-a",
        base_text="the base", payload={"description": "the very same words"}))
    res = run_loop_b(root, codoc_dir, dry_run=False)

    assert res.conflicted == 0
    s = open_store(codoc_dir)
    assert s.get_feature("f-1").description == "the very same words"
    s.close()


def test_ordinary_consecutive_typing_never_conflicts(dirs):
    """The load-bearing negative. A second settle arrives before any projection has
    returned, so it cites the text the FIRST settle wrote — which the editor tracks
    optimistically. If that read as a conflict, every burst of typing would stall
    behind a review prompt and the feature would be unusable."""
    root, codoc_dir = dirs
    _seed_tree(codoc_dir, Feature(id="f-1", title="Auth", description="one"))

    append_command(codoc_dir, Command(id="c-1", kind="set_description", feature_id="f-1",
                                      base_text="one", payload={"description": "one two"}))
    run_loop_b(root, codoc_dir, dry_run=False)
    append_command(codoc_dir, Command(id="c-2", kind="set_description", feature_id="f-1",
                                      base_text="one two", payload={"description": "one two three"}))
    res = run_loop_b(root, codoc_dir, dry_run=False)

    assert res.conflicted == 0
    s = open_store(codoc_dir)
    assert s.get_feature("f-1").description == "one two three"
    s.close()


def test_whitespace_the_author_cannot_see_is_not_a_conflict(dirs):
    """The base is compared through the daemon's own normalizer. Render and parse
    both reshape blank lines and trailing spaces, so text can differ by bytes the
    author never typed — treating that as a disagreement would conflict constantly."""
    root, codoc_dir = dirs
    _seed_tree(codoc_dir, Feature(id="f-1", title="Auth", description="one\n\n\ntwo  "))

    append_command(codoc_dir, Command(id="c-1", kind="set_description", feature_id="f-1",
                                      base_text="one\n\ntwo", payload={"description": "edited"}))
    res = run_loop_b(root, codoc_dir, dry_run=False)

    assert res.conflicted == 0
    s = open_store(codoc_dir)
    assert s.get_feature("f-1").description == "edited"
    s.close()


def test_a_command_making_no_base_claim_applies_as_before(dirs):
    """`base_text=None` is how the CLI, tests and any pre-existing queued command
    speak. They must keep working exactly as they did."""
    root, codoc_dir = dirs
    _seed_tree(codoc_dir, Feature(id="f-1", title="Auth", description="original"))

    append_command(codoc_dir, Command(id="c-1", kind="set_description",
                                      feature_id="f-1", payload={"description": "no claim"}))
    res = run_loop_b(root, codoc_dir, dry_run=False)

    assert res.conflicted == 0
    s = open_store(codoc_dir)
    assert s.get_feature("f-1").description == "no claim"
    s.close()


def test_a_title_command_checks_the_title_it_replaces(dirs):
    """A title is one line, so any two renames necessarily contend — the merge
    degenerates to pure precedence, with no special case for it. Between peers
    that means neither wins and the rename goes up for review."""
    root, codoc_dir = dirs
    from codoc.loop.apply import apply_op
    from codoc.model.event import NodeOp, NodeOpKind

    _seed_tree(codoc_dir, Feature(id="f-1", title="Auth", description="d"))
    s = open_store(codoc_dir)
    apply_op(NodeOp(kind=NodeOpKind.AMEND, feature_id="f-1", title="Renamed by someone else"),
             s, source="user", applied=True, writer="sess-b")
    s.close()

    append_command(codoc_dir, Command(id="c-1", kind="set_title", feature_id="f-1",
                                      session="sess-a", base_text="Auth",
                                      payload={"title": "Authentication"}))
    res = run_loop_b(root, codoc_dir, dry_run=False)

    assert res.conflicted == 1
    s = open_store(codoc_dir)
    assert s.get_feature("f-1").title == "Renamed by someone else"
    s.close()


def test_a_title_an_agent_renamed_yields_to_the_author(dirs):
    """Same degenerate merge, opposite ranks: the author's rename lands."""
    root, codoc_dir = dirs
    from codoc.loop.apply import apply_op
    from codoc.model.event import NodeOp, NodeOpKind

    _seed_tree(codoc_dir, Feature(id="f-1", title="Auth", description="d"))
    s = open_store(codoc_dir)
    apply_op(NodeOp(kind=NodeOpKind.AMEND, feature_id="f-1", title="Renamed by the loop"),
             s, source="loop_a", applied=True)
    s.close()

    append_command(codoc_dir, Command(id="c-1", kind="set_title", feature_id="f-1",
                                      session="sess-a", base_text="Auth",
                                      payload={"title": "Authentication"}))
    res = run_loop_b(root, codoc_dir, dry_run=False)

    assert res.conflicted == 0 and res.merged == 1
    s = open_store(codoc_dir)
    assert s.get_feature("f-1").title == "Authentication"
    s.close()


def test_a_conflicted_command_is_ledgered_and_not_retried_forever(dirs):
    root, codoc_dir = dirs
    _seed_tree(codoc_dir, Feature(id="f-1", title="Auth", description="moved on"))
    append_command(codoc_dir, Command(id="c-1", kind="set_description", feature_id="f-1",
                                      base_text="stale", payload={"description": "mine"}))
    run_loop_b(root, codoc_dir, dry_run=False)

    s = open_store(codoc_dir)
    assert s.command_applied("c-1") is True
    s.close()
    assert edits_channel.read_commands(codoc_dir) == []


def test_the_same_session_may_keep_typing_past_its_own_commands(dirs):
    """A stale base is not by itself a disagreement. Someone typing faster than the
    projection round-trip sends commands against a store that has already absorbed
    the earlier ones, so their base legitimately trails. Treating that as a conflict
    would stall every burst of typing behind a review prompt — the timing-aware
    harness caught exactly this, with a single author and no agent anywhere."""
    root, codoc_dir = dirs
    _seed_tree(codoc_dir, Feature(id="f-1", title="Auth", description="one"))

    append_command(codoc_dir, Command(id="c-1", kind="set_description", feature_id="f-1",
                                      base_text="one", session="sess-a",
                                      payload={"description": "one two"}))
    run_loop_b(root, codoc_dir, dry_run=False)
    # The projection has not returned, so this still cites the ORIGINAL text.
    append_command(codoc_dir, Command(id="c-2", kind="set_description", feature_id="f-1",
                                      base_text="one", session="sess-a",
                                      payload={"description": "one two three"}))
    res = run_loop_b(root, codoc_dir, dry_run=False)

    assert res.conflicted == 0
    s = open_store(codoc_dir)
    assert s.get_feature("f-1").description == "one two three"
    s.close()


def test_a_second_editing_session_is_somebody_else(dirs):
    """Two windows on one repository are two authors. The second must not silently
    erase the first."""
    root, codoc_dir = dirs
    _seed_tree(codoc_dir, Feature(id="f-1", title="Auth", description="one"))

    append_command(codoc_dir, Command(id="c-1", kind="set_description", feature_id="f-1",
                                      base_text="one", session="window-a",
                                      payload={"description": "window A wrote this"}))
    run_loop_b(root, codoc_dir, dry_run=False)
    append_command(codoc_dir, Command(id="c-2", kind="set_description", feature_id="f-1",
                                      base_text="one", session="window-b",
                                      payload={"description": "window B wrote this"}))
    res = run_loop_b(root, codoc_dir, dry_run=False)

    assert res.conflicted == 1
    s = open_store(codoc_dir)
    assert s.get_feature("f-1").description == "window A wrote this"
    s.close()


def test_an_agent_write_breaks_the_authors_own_lineage(dirs):
    """The writer AND the role are recorded at the one apply boundary, so an
    agent's write counts as somebody else without every agent path having to say
    so. The author's next command is therefore reconciled rather than applied
    blind — and because it is a person's edit against the loop's, they win the
    line they both rewrote."""
    from codoc.loop.apply import apply_op
    from codoc.model.event import NodeOp, NodeOpKind

    root, codoc_dir = dirs
    _seed_tree(codoc_dir, Feature(id="f-1", title="Auth", description="one"))

    append_command(codoc_dir, Command(id="c-1", kind="set_description", feature_id="f-1",
                                      base_text="one", session="sess-a",
                                      payload={"description": "the author wrote this"}))
    run_loop_b(root, codoc_dir, dry_run=False)

    s = open_store(codoc_dir)
    assert s.feature_writer_info("f-1") == ("sess-a", "human")
    apply_op(NodeOp(kind=NodeOpKind.AMEND, feature_id="f-1", description="the agent wrote this"),
             s, source="loop_a", applied=True)
    assert s.feature_writer_info("f-1") == ("loop_a", "loop")   # lineage broken
    s.close()

    append_command(codoc_dir, Command(id="c-2", kind="set_description", feature_id="f-1",
                                      base_text="the author wrote this", session="sess-a",
                                      payload={"description": "the author kept typing"}))
    res = run_loop_b(root, codoc_dir, dry_run=False)

    assert res.merged == 1 and res.conflicted == 0
    s = open_store(codoc_dir)
    assert s.get_feature("f-1").description == "the author kept typing"
    s.close()
