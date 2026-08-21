"""Altitude: the prompts say what register a node's position asks for, and the gate
reads the same three signals off the payload the call was built from.

Two halves that have to agree, or the gate marks an expectation nobody was told.
The first half pins the contract in the prompts — the shared style guide states the
rule, every prose prompt pulls the guide in, and the tree-update prompt documents the
fields the payload actually carries. The second pins the signals `propose_tree_update`
derives, where the failure mode is silent: a wrong depth or an empty file span does
not raise, it just checks a broad node as though it were a leaf.
"""
from __future__ import annotations

from codoc.agent.base import load_prompt
from codoc.agent.tree_update import _node_context
from codoc.model.event import NodeOp, NodeOpKind

# --------------------------------------------------------------------------
# the contract in the prompts
# --------------------------------------------------------------------------

PROSE_PROMPTS = ("tree_update", "bootstrap_file", "bootstrap_org", "translate")


def test_the_style_guide_states_the_altitude_rule():
    style = load_prompt("style")
    assert "altitude of the node" in style
    # Both directions, because a guide that only warns about the top leaves a leaf
    # written as a theme, which is the commoner of the two.
    assert "children" in style and "leaf" in style.lower()


def test_every_prose_prompt_pulls_the_guide_in():
    # The rule has to reach all of them: the gate is applied to whatever they
    # return, so a prompt that never read the rule is a prompt whose output gets
    # marked for breaking one.
    for name in PROSE_PROMPTS:
        assert "altitude of the node" in load_prompt(name), name


def test_the_tree_update_prompt_documents_the_fields_the_payload_carries():
    # Field names, so the prompt and loop/subtree.py cannot drift apart silently:
    # the model cannot use an altitude it was never told the name of.
    text = load_prompt("tree_update")
    for field in ("depth", "children", "spans_files"):
        assert f"`{field}`" in text, field


def test_the_bootstrap_prompts_name_the_altitude_they_write_at():
    # Neither bootstrap pass is given per-node altitude, because neither knows it
    # yet — the file pass writes leaves and the organization pass writes the themes
    # above them, so the register is fixed by which pass you are in.
    assert "leaf altitude" in load_prompt("bootstrap_file")
    assert "top-level node with children under it" in load_prompt("bootstrap_org")


# --------------------------------------------------------------------------
# the signals the gate is given
# --------------------------------------------------------------------------

SUBTREE = [
    {"id": "f-top", "title": "Theme", "parent_id": None, "bindings": [],
     "depth": 0, "children": 2, "spans_files": 0},
    {"id": "f-leaf", "title": "Leaf", "parent_id": "f-top", "depth": 1,
     "children": 0, "spans_files": 2,
     "bindings": ["a.py::one", "b.py::two"]},
]
TITLES = [{"id": "f-top", "title": "Theme", "parent_id": None},
          {"id": "f-leaf", "title": "Leaf", "parent_id": "f-top"}]


def amend(fid):
    return NodeOp(kind=NodeOpKind.AMEND, feature_id=fid, description="x")


def test_a_bindings_file_span_is_recovered_from_the_symbol_paths():
    # The payload sends symbol paths alone (`file.py::sym`). The gate's file-span
    # signal wants the file beside each name, and without the split a two-file node
    # reads as a one-file node — which turns off the rule about broad nodes.
    names_of, _depth_of, _children_of = _node_context(SUBTREE, TITLES)
    assert names_of(amend("f-leaf")) == ["a.py a.py::one", "b.py b.py::two"]


def test_the_stated_depth_wins_over_one_counted_inside_the_window():
    # `f-top` is the top of the WINDOW, not of the tree: its row says depth 3, and a
    # parent walk over a payload that does not contain its ancestors says 0. Trusting
    # the walk would pitch a deep node as a top-level theme.
    deep = [{**SUBTREE[0], "depth": 3}, SUBTREE[1]]
    _names, depth_of, _kids = _node_context(deep, TITLES)
    assert depth_of(amend("f-top")) == 3


def test_a_node_added_this_pass_hangs_one_below_its_parent_and_has_no_children():
    _names, depth_of, children_of = _node_context(SUBTREE, TITLES)
    add = NodeOp(kind=NodeOpKind.ADD_NODE, parent_id="f-leaf", title="New",
                 description="x")
    assert depth_of(add) == 2
    assert children_of(add) is False


def test_children_come_from_the_row_that_states_them():
    _names, _depth, children_of = _node_context(SUBTREE, TITLES)
    assert children_of(amend("f-top")) is True
    assert children_of(amend("f-leaf")) is False


def test_a_feature_the_payload_never_mentioned_answers_none():
    # Not False: "no children" and "no idea" are different claims, and answering the
    # first would check an unknown node against the broad-node rule.
    _names, _depth, children_of = _node_context(SUBTREE, TITLES)
    assert children_of(amend("f-elsewhere")) is None


# --------------------------------------------------------------------------
# the two halves agreeing
# --------------------------------------------------------------------------

def test_the_prompts_threshold_is_the_gates_threshold():
    """The one number both halves have to share.

    The prompt states where "broad" begins and the gate marks prose against the
    same line. Written out rather than interpolated, so changing the constant fails
    here instead of quietly leaving the prompt asking for a different register than
    the one being enforced.
    """
    from codoc.loop.prose import _BROAD_FILES

    assert f"`spans_files` is {_BROAD_FILES} or more" in load_prompt("tree_update")


def test_a_theme_written_at_symbol_level_is_marked_with_the_payloads_own_altitude():
    from codoc.loop import prose

    names_of, depth_of, children_of = _node_context(SUBTREE, TITLES)
    op = NodeOp(kind=NodeOpKind.AMEND, feature_id="f-top", description=(
        "`loop_a` reads the code and corrects `tree.codoc`. `loop_b` reads "
        "`tree.codoc` and queues `realize.md`."))
    findings = prose.review_ops([op], names_of=names_of, depth_of=depth_of,
                                children_of=children_of)
    assert "altitude-too-low" in {d.code for d in findings[0]}

    # …and the same prose on the leaf under it is exactly where it belongs.
    leaf = NodeOp(kind=NodeOpKind.AMEND, feature_id="f-leaf",
                  description=op.description)
    findings = prose.review_ops([leaf], names_of=names_of, depth_of=depth_of,
                                children_of=children_of)
    assert "altitude-too-low" not in {d.code for d in findings.get(0, ())}
