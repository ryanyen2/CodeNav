"""U6 — withdraw a queued realization + the cancellation channel.

The human can back out a code-implying edit that's queued for the agent: a
cancellation rides ``edits.json`` (alongside annotations + intents), Loop B drains
it and prunes the matching directive from the queue — releasing the doc-wins hold
(``hold_set`` reads the manifest) and rebuilding/removing ``realize.md``. The
committed prose is KEPT (withdraw cancels the code work, not the documented intent).

Reject (an agent change) is U4's verdict path and the doc-wins holds are the
existing ``suppressed_by_hold`` mechanism — both unchanged here; this pins the new
withdraw path end to end.
"""
from __future__ import annotations

import pytest

from codoc.loop import edits as edits_channel
from codoc.loop.edits import (
    Directive, append_cancellation, drain_annotations, drain_cancellations,
    hold_set, read_cancellations, read_manifest, write_manifest,
    EditAnnotation, append_annotation, read_annotations,
)
from codoc.loop.loop_b import _apply_cancellations, realize_path, _write_realize


@pytest.fixture
def codoc_dir(tmp_path):
    d = tmp_path / ".codoc"
    d.mkdir()
    return str(d)


def _queue(codoc_dir, directives: list[Directive]) -> None:
    """Stand up a queued realization: a manifest + a realize.md beside it (so
    read_manifest doesn't treat the manifest as stale)."""
    write_manifest(codoc_dir, directives)
    _write_realize(codoc_dir, "### 1. queued\n")


# ─── the cancellation channel (edits.json) ───────────────────────────────────

def test_append_read_drain_cancellation(codoc_dir):
    append_cancellation(codoc_dir, "f-x")
    append_cancellation(codoc_dir, "f-y")
    assert read_cancellations(codoc_dir) == ["f-x", "f-y"]
    assert drain_cancellations(codoc_dir) == ["f-x", "f-y"]
    assert read_cancellations(codoc_dir) == []  # consumed


def test_cancellation_dedups_by_feature(codoc_dir):
    append_cancellation(codoc_dir, "f-x")
    append_cancellation(codoc_dir, "f-x")
    assert read_cancellations(codoc_dir) == ["f-x"]


def test_drain_cancellations_keeps_annotations_and_intents(codoc_dir):
    append_annotation(codoc_dir, EditAnnotation(feature_id="f-a", fields=["description"],
                                                actor="human", mode="pen", ts=1))
    append_cancellation(codoc_dir, "f-x")
    drain_cancellations(codoc_dir)
    # annotations survive the cancellation drain
    assert "f-a" in read_annotations(codoc_dir)


def test_drain_annotations_keeps_cancellations(codoc_dir):
    append_cancellation(codoc_dir, "f-x")
    append_annotation(codoc_dir, EditAnnotation(feature_id="f-a", fields=["title"],
                                                actor="human", mode="pen", ts=1))
    drain_annotations(codoc_dir)
    assert read_cancellations(codoc_dir) == ["f-x"]  # cancellations survive


# ─── Loop B prune: withdraw releases the hold ────────────────────────────────

def test_withdraw_prunes_one_directive_and_releases_its_hold(codoc_dir):
    _queue(codoc_dir, [
        Directive(id="d-1", feature_id="f-x", kind="amend", caused_by="", text="UPDATE x"),
        Directive(id="d-2", feature_id="f-y", kind="amend", caused_by="", text="UPDATE y"),
    ])
    assert hold_set(codoc_dir) == {"f-x", "f-y"}

    append_cancellation(codoc_dir, "f-x")
    removed = _apply_cancellations("root", codoc_dir)

    assert removed == 1
    survivors = read_manifest(codoc_dir)
    assert [d.feature_id for d in survivors] == ["f-y"]   # f-x's directive pruned
    assert hold_set(codoc_dir) == {"f-y"}                  # f-x's hold released
    assert realize_path(codoc_dir).exists()                # queue rebuilt (survivor remains)


def test_withdraw_last_directive_removes_the_queue(codoc_dir):
    _queue(codoc_dir, [Directive(id="d-1", feature_id="f-x", kind="amend", text="UPDATE x")])
    append_cancellation(codoc_dir, "f-x")

    removed = _apply_cancellations("root", codoc_dir)

    assert removed == 1
    assert read_manifest(codoc_dir) == []        # manifest cleared
    assert not realize_path(codoc_dir).exists()  # realize.md removed → status falls back
    assert hold_set(codoc_dir) == set()          # no holds left


def test_withdraw_unqueued_feature_is_a_noop(codoc_dir):
    _queue(codoc_dir, [Directive(id="d-1", feature_id="f-x", kind="amend", text="UPDATE x")])
    append_cancellation(codoc_dir, "f-gone")  # never queued

    removed = _apply_cancellations("root", codoc_dir)

    assert removed == 0
    assert [d.feature_id for d in read_manifest(codoc_dir)] == ["f-x"]  # unchanged
    assert hold_set(codoc_dir) == {"f-x"}


def test_withdraw_with_no_cancellations_is_a_noop(codoc_dir):
    _queue(codoc_dir, [Directive(id="d-1", feature_id="f-x", kind="amend", text="UPDATE x")])
    assert _apply_cancellations("root", codoc_dir) == 0
    assert hold_set(codoc_dir) == {"f-x"}
