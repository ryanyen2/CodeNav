"""U6 — the transient bug-screenshot plugin + its end-to-end steer consumption.

A screenshot is CONSULT-only, AMBIENT, and TRANSIENT (KTD4/KTD5): it rides the
one-shot steering channel as an attachment, is folded into a realize directive as
a `Consult:` line, consumed exactly once, and never persisted as a block. Identity
is the author-minted ``comment_id`` (NOT ``(feature_id, text)``), so two byte-
identical notes stay distinct; serialized content never emits a bare leading `>`.
"""
from __future__ import annotations

import pytest

from codoc.blocks.base import BindingMode, Capability
from codoc.blocks.builtins import ensure_builtins
from codoc.blocks.screenshot import ScreenshotPlugin
from codoc.codoc_file.render import write_tree
from codoc.loop import edits as edits_channel
from codoc.loop.edits import Steer, append_steer
from codoc.loop.loop_b import realize_path, run_loop_b
from codoc.model.binding import Binding
from codoc.model.block import Block, BlockLifecycle
from codoc.model.feature import Feature
from codoc.store.db import open_store


# ── plugin contract ──────────────────────────────────────────────────────────

def test_screenshot_declares_transient_ambient_consult_only():
    p = ScreenshotPlugin()
    assert p.capabilities == frozenset({Capability.CONSULT})
    assert p.binding_mode is BindingMode.AMBIENT
    assert p.lifecycle is BlockLifecycle.TRANSIENT
    # consult-only: never round-trips to code.
    assert not hasattr(p, "lower") or Capability.LOWER not in p.capabilities


def test_screenshot_registered_and_resolvable_for_consult():
    reg = ensure_builtins()
    assert reg.for_capability("screenshot", Capability.CONSULT) is not None
    # not dispatchable for a capability it does not declare.
    assert reg.for_capability("screenshot", Capability.LOWER) is None


def test_consult_text_embeds_the_ref():
    out = ScreenshotPlugin().consult(Block(feature_id="f1", kind="screenshot",
                                           content=".codoc/media/cm-1.png"))
    assert ".codoc/media/cm-1.png" in out
    # no bare leading `>` in serialized consult content (steer-channel hazard).
    assert not out.lstrip().startswith(">")


# ── end-to-end: the transient steer ────────────────────────────────────────────

@pytest.fixture
def repo(tmp_path):
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    with open_store(codoc_dir) as s:
        f = Feature(title="Checkout", description="The cart → pay flow.")
        s.upsert_feature(f)
        s.upsert_binding(Binding(feature_id=f.id, file="pay.py",
                                 symbol_path="pay.py::charge", fingerprint="h"))
        write_tree(s, codoc_dir)
    return str(tmp_path), str(codoc_dir), f.id


def test_screenshot_steer_is_consulted_then_consumed_once(repo):
    root, codoc_dir, fid = repo
    append_steer(codoc_dir, Steer(feature_id=fid, text="re: this looks wrong",
                                  comment_id="cm-1", media=".codoc/media/cm-1.png",
                                  media_kind="screenshot"))
    res = run_loop_b(root, codoc_dir)
    assert res.steered == 1
    body = realize_path(codoc_dir).read_text()
    assert "STEER FEATURE" in body
    assert ".codoc/media/cm-1.png" in body          # the attachment reached the agent
    assert "screenshot" in body.lower()
    # AE3 / KTD4: the screenshot is NEVER persisted as a block.
    with open_store(codoc_dir) as s:
        assert s.blocks_for_feature(fid) == []
    # one-shot: a second pass re-queues nothing (drained-once).
    assert run_loop_b(root, codoc_dir).steered == 0


def test_two_identical_screenshot_notes_stay_distinct(repo):
    """Author/id-scoped identity (KTD4): two byte-identical notes with distinct
    comment_ids do NOT collapse — both drain into directives."""
    root, codoc_dir, fid = repo
    append_steer(codoc_dir, Steer(feature_id=fid, text="same note", comment_id="cm-a",
                                  media=".codoc/media/cm-a.png", media_kind="screenshot"))
    append_steer(codoc_dir, Steer(feature_id=fid, text="same note", comment_id="cm-b",
                                  media=".codoc/media/cm-b.png", media_kind="screenshot"))
    assert len(edits_channel.read_steers(codoc_dir)) == 2
    res = run_loop_b(root, codoc_dir)
    assert res.steered == 2


def test_media_only_steer_with_no_text_still_queues(repo):
    """A screenshot is itself the note — a steer with media but empty text is valid."""
    root, codoc_dir, fid = repo
    append_steer(codoc_dir, Steer(feature_id=fid, text="(see screenshot)", comment_id="cm-x",
                                  media=".codoc/media/cm-x.png", media_kind="screenshot"))
    res = run_loop_b(root, codoc_dir)
    assert res.steered == 1
    assert ".codoc/media/cm-x.png" in realize_path(codoc_dir).read_text()
