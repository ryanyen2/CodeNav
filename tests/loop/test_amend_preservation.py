"""The amend gate: a repair lands, a rewrite goes to review.

The distinction these tests pin is not size. An amend that appends a true
sentence changes the text a lot and destroys nothing; an amend that re-says the
same paragraph in the model's own voice may score as similar while replacing
every word a person chose. The gate has to separate those, and it has to be
stricter about prose a human wrote than about prose the loop wrote.
"""
from __future__ import annotations

import pytest

from codoc.loop.apply import (
    apply_op,
    is_small_amend,
    preserved_ratio,
    should_auto_apply,
)
from codoc.model.event import ACTOR_HUMAN, NodeOp, NodeOpKind
from codoc.store.db import open_store


@pytest.fixture
def store(tmp_path):
    s = open_store(tmp_path)
    yield s
    s.close()


BASE = (
    "Holds the queue of edits waiting to be implemented. Readers tolerate a "
    "missing file, since an empty queue and no queue mean the same thing."
)


_next_title = iter(f"Edit queue {i}" for i in range(1000))


def _feature(store, description=BASE, *, writer_role=""):
    title = next(_next_title)
    apply_op(NodeOp(kind=NodeOpKind.ADD_NODE, title=title, description=description),
             store, source="loop_a", applied=True)
    fid = next(f.id for f in store.list_features() if f.title == title)
    if writer_role:
        store.set_feature_writer(fid, "someone", writer_role)
    return fid


def _amend(fid, text):
    return NodeOp(kind=NodeOpKind.AMEND, feature_id=fid, description=text)


class TestPreservedRatio:
    def test_untouched_text_is_fully_preserved(self):
        assert preserved_ratio(BASE, BASE) == 1.0

    def test_appending_a_sentence_preserves_everything(self):
        assert preserved_ratio(BASE, BASE + " Writes go through a rename.") == 1.0

    def test_a_small_factual_fix_preserves_nearly_everything(self):
        fixed = BASE.replace("Readers tolerate", "Readers now tolerate")
        assert preserved_ratio(BASE, fixed) > 0.9

    def test_a_restatement_in_another_voice_does_not_count_as_preserved(self):
        restated = (
            "Maintains the pending edit queue for later implementation. A "
            "missing file is treated as an empty queue by consumers."
        )
        # Shares most of its vocabulary with the original and still preserves
        # almost none of it — the case a similarity score gets backwards.
        assert preserved_ratio(BASE, restated) < 0.3

    def test_empty_original_is_vacuously_preserved(self):
        assert preserved_ratio("", "anything") == 1.0


class TestHumanProse:
    def test_a_small_repair_of_human_prose_auto_applies(self, store):
        fid = _feature(store, writer_role=ACTOR_HUMAN)
        fixed = BASE.replace("an empty queue", "an empty queue on disk")
        assert is_small_amend(_amend(fid, fixed), store) is True

    def test_appending_to_human_prose_auto_applies(self, store):
        fid = _feature(store, writer_role=ACTOR_HUMAN)
        grown = BASE + " Writes go through a temp file and a rename."
        assert is_small_amend(_amend(fid, grown), store) is True

    def test_rewriting_human_prose_goes_to_review(self, store):
        fid = _feature(store, writer_role=ACTOR_HUMAN)
        restated = (
            "Maintains the pending edit queue for later implementation. A "
            "missing file is treated as an empty queue by consumers."
        )
        assert is_small_amend(_amend(fid, restated), store) is False

    def test_partial_rewrite_of_human_prose_goes_to_review(self, store):
        """Half kept verbatim is still half of someone's writing replaced."""
        fid = _feature(store, writer_role=ACTOR_HUMAN)
        half = (
            "Holds the queue of edits waiting to be implemented. Consumers "
            "treat an absent file as though the queue were empty."
        )
        assert is_small_amend(_amend(fid, half), store) is False


BASE3 = (
    "Holds the queue of edits waiting to be implemented. "
    "Writes go through a temp file and a rename, so a crash mid-write cannot "
    "leave a half-parsed queue. "
    "Readers tolerate a missing file, since an empty queue and no queue mean "
    "the same thing."
)
ONE_SENTENCE_REWRITTEN = (
    "Holds the queue of edits waiting to be implemented. "
    "Writes go through a temp file and a rename, so a crash mid-write cannot "
    "leave a half-parsed queue. "
    "A missing file is treated by consumers as though the queue were empty."
)


class TestAuthorshipAsymmetry:
    """The same edit, judged differently by who wrote what it replaces."""

    def test_one_rewritten_sentence_lands_on_loop_prose(self, store):
        fid = _feature(store, description=BASE3, writer_role="loop")
        assert is_small_amend(_amend(fid, ONE_SENTENCE_REWRITTEN), store) is True

    def test_one_rewritten_sentence_goes_to_review_on_human_prose(self, store):
        fid = _feature(store, description=BASE3, writer_role=ACTOR_HUMAN)
        assert is_small_amend(_amend(fid, ONE_SENTENCE_REWRITTEN), store) is False

    @pytest.mark.parametrize("role", ["loop", ACTOR_HUMAN])
    def test_wholesale_restatement_goes_to_review_either_way(self, store, role):
        """Authorship changes who gets protected, not whether a rewrite this
        large is worth a person's glance."""
        fid = _feature(store, writer_role=role)
        restated = (
            "Maintains the pending edit queue for later implementation. A "
            "missing file is treated as an empty queue by consumers."
        )
        assert is_small_amend(_amend(fid, restated), store) is False


class TestLoopProse:
    def test_a_long_true_addition_lands(self, store):
        """The old size rule blocked this: appending two sentences changes a
        lot of characters while destroying none of them, and a document that
        cannot grow without review does not stay current."""
        fid = _feature(store, writer_role="loop")
        grown = BASE + (
            " Writes go through a temp file and a rename, so a crash mid-write "
            "cannot leave a half-parsed queue on disk. The queue is drained "
            "under the loop lock, because two daemons reading it at once would "
            "each implement the same edit."
        )
        assert is_small_amend(_amend(fid, grown), store) is True

    def test_unknown_authorship_is_treated_as_the_loop(self, store):
        fid = _feature(store)  # no writer row (a pre-provenance feature)
        assert is_small_amend(_amend(fid, BASE + " And more."), store) is True


class TestEdges:
    def test_first_prose_on_a_bare_node_auto_applies(self, store):
        fid = _feature(store, description="", writer_role=ACTOR_HUMAN)
        assert is_small_amend(_amend(fid, BASE), store) is True

    def test_a_non_amend_op_is_never_a_small_amend(self, store):
        fid = _feature(store)
        op = NodeOp(kind=NodeOpKind.RETIRE_NODE, feature_id=fid)
        assert is_small_amend(op, store) is False

    def test_an_amend_with_no_description_is_a_no_op(self, store):
        fid = _feature(store, writer_role=ACTOR_HUMAN)
        assert is_small_amend(NodeOp(kind=NodeOpKind.AMEND, feature_id=fid), store) is True


class TestAPlanNeverAutoApplies:
    """``builds=True`` mints an amend with ``realized=False`` — prose saying what the
    feature WILL do, whose code does not exist yet. The size test cannot see that
    distinction, and judging a plan by how much wording it preserved is what let a
    ``/codoc:plan`` turn apply two of its three amendments outright: no proposal row,
    so no Accept & build, so no realize directive, and the document then diffed the
    new words against the displaced ones and painted them in the CODE channel —
    reporting a build that had never run.
    """

    def test_a_small_plan_amend_still_awaits_a_verdict(self, store):
        fid = _feature(store)
        plan = _amend(fid, BASE + " The queue is drained oldest-first.")
        plan.realized = False
        # It IS small — that is the whole trap.
        assert is_small_amend(plan, store) is True
        assert should_auto_apply(plan, store) is False

    def test_a_reflection_of_the_same_size_still_auto_applies(self, store):
        fid = _feature(store)
        # Same words, `builds=False`: the code already changed and the tree is
        # catching up. Nothing is being asked of anyone, so nothing waits.
        reflection = _amend(fid, BASE + " The queue is drained oldest-first.")
        assert reflection.realized is None
        assert should_auto_apply(reflection, store) is True

    def test_a_large_plan_amend_is_unchanged(self, store):
        fid = _feature(store)
        plan = _amend(fid, "Something else entirely, in another voice.")
        plan.realized = False
        assert should_auto_apply(plan, store) is False
