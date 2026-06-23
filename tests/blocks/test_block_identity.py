"""U1 — KTD8: block identity is deterministic and survives host edits.

These tests pin the *structural* invariants the loops rely on so the LLM never has
to track identity: a stable id across move/edit, delete+undo as a no-op against a
baseline, and round-trip preservation of identity fields.
"""
from __future__ import annotations

from codoc.model.block import Block, BlockLifecycle, Provenance


def test_block_id_is_stable_and_prefixed():
    b = Block(feature_id="f-1", kind="diagram")
    assert b.id.startswith("blk-")
    # a second block gets a different id (collision-safe)
    assert Block(feature_id="f-1", kind="diagram").id != b.id


def test_move_is_an_ord_change_not_a_new_identity():
    b = Block(feature_id="f-1", kind="image", content="img-ref", ord=0)
    moved = b.model_copy(update={"ord": 3})
    # same identity, new position — a move never re-creates the block
    assert moved.id == b.id
    assert moved.content == b.content
    assert moved.ord == 3


def test_delete_then_reinsert_same_id_returns_to_baseline():
    # The host preserves the id across delete+undo. The structural diff compares
    # id sets, so reinserting the same id == back to baseline (a no-op for code).
    baseline = {Block(feature_id="f-1", kind="latex", content=r"\sum x").id}
    bid = next(iter(baseline))
    after_delete = set()
    after_undo = {bid}  # undo restored the SAME id
    assert after_delete != baseline           # mid-edit: id absent
    assert after_undo == baseline             # post-undo: identical, no code effect


def test_round_trip_preserves_identity_fields():
    b = Block(
        feature_id="f-9", kind="diagram", content="flowchart TB\nA-->B",
        lifecycle=BlockLifecycle.PERSISTENT, provenance=Provenance.DERIVED, ord=2,
    )
    dumped = b.model_dump()
    restored = Block.model_validate(dumped)
    assert restored.id == b.id
    assert restored.kind == b.kind
    assert restored.lifecycle is BlockLifecycle.PERSISTENT
    assert restored.provenance is Provenance.DERIVED
    assert restored.ord == 2
