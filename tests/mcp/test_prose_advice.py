"""The prose gate at the agent's door (`mcp/tools._prose_advice`).

The gate runs where prose is GENERATED, because a defect there can be repaired by
rerunning the call with the critique appended. The MCP tools are the one writing path
with no such rerun and no check at all -- and they are the path a coding agent uses,
so a description that opens on a mechanism could reach the tree uncontested. The cost
that makes it worth fixing is the second-order one: the author repairs it by hand and
the style memory reads their repair as a preference, so a defect we could have named
becomes a lesson about the author's taste (see `codoc/loop/voice.py`).

These pin the channel and its limits. It is ADVISORY -- the write always lands -- and
it never reads a person's prose.
"""
from __future__ import annotations

import pytest

from codoc.loop import prose
from codoc.mcp import tools
from codoc.model.event import ACTOR_HUMAN, NodeOp, NodeOpKind
from codoc.model.feature import Feature
from codoc.store.db import open_store

# Opens on a snake_case identifier inside the first three words, which is what
# `_opens_on_mechanism` is looking for.
MECHANISM = ("drain_queue walks the pending list and calls each entry in turn, then "
             "truncates the file it read them from.")
CLEAN = ("Work the author asked for outlives the session that asked for it, so a "
         "request survives a crash and a restart rather than leaving with the tab "
         "that made it.")


@pytest.fixture
def codoc_dir(tmp_path):
    cd = tmp_path / ".codoc"
    cd.mkdir()
    return str(cd)


def _seed(codoc_dir, **kw):
    with open_store(codoc_dir) as s:
        f = Feature(**{"title": "Edit queue",
                       "description": "Holds work the daemon has not run yet.", **kw})
        s.upsert_feature(f)
        return f


def test_an_agents_defective_prose_comes_back_with_the_defect_named(codoc_dir):
    f = _seed(codoc_dir)

    res = tools.propose_amend(codoc_dir, feature_id=f.id, description=MECHANISM,
                              rationale="reflected after the rename")

    assert res["ok"] is True
    said = " ".join(res["prose"]["defects"])
    assert "drain_queue" in said, "the words that tripped the rule, not just a code"
    assert res["prose"]["fix"]


def test_the_write_lands_anyway(codoc_dir):
    """Advisory, for `_language_advice`'s reason and one of the gate's own.

    Refusing would throw away work the agent has already done and leave the tree
    describing code that has already changed, which is worse than one awkward
    paragraph -- and a check that can refuse is a check that can lose a whole
    reflection to a rule about dashes.
    """
    f = _seed(codoc_dir)

    res = tools.propose_amend(codoc_dir, feature_id=f.id, description=MECHANISM,
                              rationale="x")

    assert res["prose"]["defects"]
    assert res["event_id"], "recorded either way"
    with open_store(codoc_dir) as s:
        # Held for a verdict rather than applied -- a rewrite this size is what the
        # amend gate reviews -- and recorded either way, which is the point: the
        # advice did not cost the agent its write.
        assert s.get_event(res["event_id"]) is not None
        assert len(s.pending_events()) == 1


def test_prose_that_reads_well_says_nothing_back(codoc_dir):
    f = _seed(codoc_dir)
    res = tools.propose_amend(codoc_dir, feature_id=f.id, description=CLEAN,
                              rationale="x")
    assert "prose" not in res


def test_a_persons_own_words_are_never_checked(codoc_dir):
    """An author who opens on a symbol is writing, not committing a defect.

    Same rule the scorecard keeps at `apply_op`, asked of the same field: the actor
    is the ledger's own notion of who is writing.
    """
    f = _seed(codoc_dir)

    res = tools.propose_amend(codoc_dir, feature_id=f.id, description=MECHANISM,
                              rationale="x", actor=ACTOR_HUMAN)

    assert "prose" not in res


def test_a_whole_reflection_is_advised_per_op(codoc_dir):
    """`reflect` is the bulk path -- a change set at a time -- so the advice is a row
    property, not a call property: one op's defect must not be reported against
    another's node."""
    a = _seed(codoc_dir, id="f-a", title="Edit queue")
    b = _seed(codoc_dir, id="f-b", title="Retry policy",
              description="A failed request waits longer each time.")

    res = tools.reflect(codoc_dir, ops=[
        {"kind": "amend", "feature_id": a.id, "description": MECHANISM},
        {"kind": "amend", "feature_id": b.id, "description": CLEAN},
    ], rationale="reflected the rename")

    assert res["results"][0]["prose"]["defects"]
    assert "prose" not in res["results"][1]


def test_the_advice_and_the_recorded_rate_describe_the_same_node(codoc_dir):
    """One context builder, two readers (`prose.advise`).

    Two would drift, and drift produces the worst version of both failures: an agent
    told to fix a defect the rate does not count, or a rate counting one nobody was
    told about.
    """
    f = _seed(codoc_dir)
    res = tools.propose_amend(codoc_dir, feature_id=f.id, description=MECHANISM,
                              rationale="x")

    with open_store(codoc_dir) as s:
        rate = prose.defect_rate(s)
    assert rate["checked"] == 1 and rate["defective"] == 1
    counted = {code for code, _n in rate["top"]}
    assert counted, "the same write that produced advice was also scored"
    assert len(counted) == len(res["prose"]["defects"])


# -- the node's own altitude -------------------------------------------------
#
# `prose.advise` reads depth and children from the store, which the scorecard did not
# do before it existed. It is the difference between checking a theme node's register
# and checking a leaf's, and `prompts/style.txt` asks for different prose at each.

CITED = ("`drain_queue` reads `.codoc/edits.json`. `apply_op` writes each op to the "
         "store.")


def test_a_theme_node_is_told_its_opening_has_to_hold_without_a_symbol(codoc_dir):
    root = _seed(codoc_dir, id="f-root", title="Editing the tree")
    _seed(codoc_dir, id="f-kid", title="Draining edits", parent_id=root.id)

    res = tools.propose_amend(codoc_dir, feature_id=root.id, description=CITED,
                              rationale="x")

    assert any("without a symbol" in line for line in res["prose"]["defects"])


def test_the_same_prose_on_a_leaf_is_left_alone(codoc_dir):
    """The rule is about where the node sits, so a leaf must not inherit it.

    A leaf is the last stop and has to carry the detail; enforcing the theme register
    on it would ask an author for prose that cannot say anything.
    """
    root = _seed(codoc_dir, id="f-root", title="Editing the tree")
    leaf = _seed(codoc_dir, id="f-leaf", title="Draining edits", parent_id=root.id)

    res = tools.propose_amend(codoc_dir, feature_id=leaf.id, description=CITED,
                              rationale="x")

    lines = (res.get("prose") or {}).get("defects", [])
    assert not any("without a symbol" in line for line in lines)


def test_a_new_node_is_judged_at_the_depth_it_would_take(codoc_dir):
    """An ADD has no depth on disk yet, so it is read from the parent it names.

    Skipping that would judge every proposed root as a leaf -- and a root is the node
    whose register matters most, since it is the one a reader meets first.
    """
    with open_store(codoc_dir) as s:
        add = NodeOp(kind=NodeOpKind.ADD_NODE, title="Editing the tree",
                     description=CITED)
        assert any(d.code == "altitude-too-low" for d in prose.advise(s, add))

        root = Feature(id="f-root", title="Editing the tree")
        s.upsert_feature(root)
        deep = NodeOp(kind=NodeOpKind.ADD_NODE, parent_id=root.id,
                      title="Draining edits", description=CITED)
        assert not any(d.code == "altitude-too-low" for d in prose.advise(s, deep))
