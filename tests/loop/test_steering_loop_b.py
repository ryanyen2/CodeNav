"""Loop B: inline steering comments, bold-as-focus, and external Consult links.

A ``> …`` comment in tree.codoc is a note addressed to the agent: always a
STEER directive (imperative by construction), consumed from the text by the
end-of-pass re-render, and appended to an in-flight queue rather than
clobbering it. ``**bold**`` spans ride into directives as ``Focus:`` lines —
and a newly-bolded span that itself reads imperative queues a directive even
when the description as a whole is descriptive. ``[label](https://…)`` links
become ``Consult:`` lines.
"""
from __future__ import annotations

import json

import pytest

from codoc.codoc_file.render import tree_path, write_tree
from codoc.loop import edits as edits_channel
from codoc.loop.loop_b import realize_path, run_loop_b
from codoc.loop.status import AWAITING_IMPL, status_path
from codoc.model.binding import Binding
from codoc.model.feature import Feature
from codoc.store.db import open_store


@pytest.fixture
def dirs(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    return str(root), str(codoc_dir)


def _seed(codoc_dir, description="Holds brand colors."):
    s = open_store(codoc_dir)
    f = Feature(title="Color palette", description=description)
    s.upsert_feature(f)
    s.upsert_binding(Binding(feature_id=f.id, file="colors.py",
                             symbol_path="colors.py::PALETTE", fingerprint="h"))
    write_tree(s, codoc_dir)
    s.close()
    return f


def _edit_tree(codoc_dir, old, new):
    p = tree_path(codoc_dir)
    p.write_text(p.read_text().replace(old, new))


# -- steering comments ----------------------------------------------------

def test_steering_comment_queues_steer_directive_and_is_consumed(dirs):
    root, codoc_dir = dirs
    f = _seed(codoc_dir)
    _edit_tree(codoc_dir, "Holds brand colors.",
               "Holds brand colors.\n  > also cache palette lookups")

    res = run_loop_b(root, codoc_dir, dry_run=False)

    assert res.steered == 1 and res.queued is True
    body = realize_path(codoc_dir).read_text()
    assert 'STEER FEATURE: "Color palette"' in body
    assert "also cache palette lookups" in body
    assert "Edit only: colors.py" in body
    # The comment is consumed: gone from the re-rendered text…
    assert "> also cache" not in tree_path(codoc_dir).read_text()
    # …and the prose was untouched (a comment is not a description edit).
    s = open_store(codoc_dir)
    assert s.get_feature(f.id).description == "Holds brand colors."
    s.close()
    assert json.loads(status_path(codoc_dir).read_text())["state"] == AWAITING_IMPL
    # The steer directive holds the feature (doc-wins) until the queue clears.
    assert f.id in edits_channel.hold_set(codoc_dir)


def test_steering_comment_appends_to_inflight_queue(dirs):
    root, codoc_dir = dirs
    _seed(codoc_dir)
    _edit_tree(codoc_dir, "Holds brand colors.",
               "Holds brand colors. Should also expose dark-mode variants.")
    first = run_loop_b(root, codoc_dir, dry_run=False)
    assert first.queued and len(first.directives) == 1

    # Mid-realization, the user steers with a comment.
    _edit_tree(codoc_dir, "dark-mode variants.",
               "dark-mode variants.\n  > use CSS custom properties, not a JS map")
    second = run_loop_b(root, codoc_dir, dry_run=False)

    assert second.steered == 1
    body = realize_path(codoc_dir).read_text()
    assert "UPDATE FEATURE" in body and "STEER FEATURE" in body  # appended, not clobbered
    assert "CSS custom properties" in body
    manifest = edits_channel.read_manifest(codoc_dir)
    assert len(manifest) == 2
    assert [d.kind for d in manifest] == ["amend", "steer"]
    assert all(d.text for d in manifest)


def test_dry_run_preserves_steering_comment_in_text(dirs):
    """A dry pass must NOT consume a note it didn't queue — consuming without
    queueing would silently destroy explicit user intent."""
    root, codoc_dir = dirs
    _seed(codoc_dir)
    _edit_tree(codoc_dir, "Holds brand colors.",
               "Holds brand colors.\n  > rename the constant")

    res = run_loop_b(root, codoc_dir, dry_run=True)

    assert res.steered == 1
    assert res.directives and not res.queued
    assert not realize_path(codoc_dir).exists()
    assert "> rename the constant" in tree_path(codoc_dir).read_text()
    # …and a later real pass drains it normally.
    res2 = run_loop_b(root, codoc_dir, dry_run=False)
    assert res2.steered == 1 and res2.queued


def test_dry_run_with_prose_edit_reinserts_comment_after_rerender(dirs):
    """A dry pass that re-renders (prose edit absorbed) must put the un-queued
    `>` lines back — the store-driven render drops them."""
    root, codoc_dir = dirs
    _seed(codoc_dir)
    _edit_tree(codoc_dir, "Holds brand colors.",
               "Holds brand colors and tints.\n  > rename the constant")

    res = run_loop_b(root, codoc_dir, dry_run=True)

    assert res.user_edits == 1 and res.steered == 1 and not res.queued
    text = tree_path(codoc_dir).read_text()
    assert "Holds brand colors and tints." in text  # edit absorbed + re-rendered
    assert "> rename the constant" in text          # note preserved


def test_dry_run_reinserts_two_distinct_comments_without_merging(dirs):
    """Two `>` notes on one feature must re-insert as DISTINCT runs (blank-
    separated). Adjacent `>` lines would merge into a single comment on the next
    parse — silently collapsing two steering notes into one."""
    from codoc.codoc_file.parse import parse_text

    root, codoc_dir = dirs
    _seed(codoc_dir)
    # Two DISTINCT runs (blank-separated) — adjacent `>` lines are correctly one
    # comment; the bug under test is the dry-run RE-INSERTION merging them.
    _edit_tree(codoc_dir, "Holds brand colors.",
               "Holds brand colors and tints.\n  > rename the constant\n\n  > also add a dark variant")

    res = run_loop_b(root, codoc_dir, dry_run=True)

    assert res.user_edits == 1 and res.steered == 2 and not res.queued
    node = next(n for n in parse_text(tree_path(codoc_dir).read_text()).nodes
                if n.title == "Color palette")
    assert node.comments == ["rename the constant", "also add a dark variant"]
    # a later real pass drains BOTH as distinct directives
    res2 = run_loop_b(root, codoc_dir, dry_run=False)
    assert res2.steered == 2 and res2.queued


def test_comment_on_hand_added_node_resolves_minted_id(dirs):
    """A `>` note under a brand-new (id-less) node must not be destroyed: the
    ADD is applied first, then the steer resolves the freshly-minted id by
    title."""
    root, codoc_dir = dirs
    _seed(codoc_dir)
    p = tree_path(codoc_dir)
    p.write_text(p.read_text() + "\n- Theme tokens\n    Should expose design tokens.\n"
                 "    > start with the color scale\n")

    res = run_loop_b(root, codoc_dir, dry_run=False)

    assert res.steered == 1 and res.queued
    body = realize_path(codoc_dir).read_text()
    assert 'STEER FEATURE: "Theme tokens"' in body
    assert "start with the color scale" in body
    steer = next(d for d in edits_channel.read_manifest(codoc_dir) if d.kind == "steer")
    assert steer.feature_id.startswith("f-")


def test_steer_caused_by_matches_cooccurring_amend(dirs):
    """A comment riding along with an imperative edit on the same feature
    inherits that edit's cause, so the IDE cascade cue groups them."""
    root, codoc_dir = dirs
    _seed(codoc_dir)
    _edit_tree(codoc_dir, "Holds brand colors.",
               "Should expose dark-mode variants.\n  > keep the public API stable")

    res = run_loop_b(root, codoc_dir, dry_run=False)

    assert res.queued and res.steered == 1
    manifest = edits_channel.read_manifest(codoc_dir)
    amend = next(d for d in manifest if d.kind == "amend")
    steer = next(d for d in manifest if d.kind == "steer")
    assert amend.caused_by and steer.caused_by == amend.caused_by


def test_in_flight_handed_directive_preserved_on_append(dirs):
    """A handed-off directive already in flight must NOT be clobbered when a new one is
    appended — the queue is rebuilt from the manifest (existing + new), so both survive.
    (realize.md is rebuilt from the manifest's `text` each pass; pre-`text` text-less
    entries no longer occur — Loop B always writes text.)"""
    root, codoc_dir = dirs
    f = _seed(codoc_dir)
    edits_channel.write_manifest(codoc_dir, [
        edits_channel.Directive(
            id="d-existing1", feature_id=f.id, kind="amend", handed_off=True,
            text='UPDATE FEATURE: "Color palette"\n  New intent: existing directive body')])
    realize_path(codoc_dir).write_text(
        '### 1. ⟨d-existing1⟩ UPDATE FEATURE: "Color palette"\n  New intent: existing directive body\n')

    _edit_tree(codoc_dir, "Holds brand colors.",
               "Holds brand colors.\n  > also cache palette lookups")
    res = run_loop_b(root, codoc_dir, dry_run=False)

    assert res.steered == 1 and res.queued and res.queued_total == 2
    body = realize_path(codoc_dir).read_text()
    assert "existing directive body" in body         # in-flight queue preserved
    assert "STEER FEATURE" in body                    # new section appended
    manifest = edits_channel.read_manifest(codoc_dir)
    assert [d.id for d in manifest][0] == "d-existing1" and len(manifest) == 2


# -- bold = focus -----------------------------------------------------------

def test_newly_bolded_imperative_span_queues_despite_descriptive_prose(dirs):
    root, codoc_dir = dirs
    _seed(codoc_dir)
    # The sentence reads descriptive ("Holds …"), but the author NEWLY bolded an
    # imperative span — boldening amplifies the gate.
    _edit_tree(codoc_dir, "Holds brand colors.",
               "Holds brand colors and **validate hex inputs** on write.")

    res = run_loop_b(root, codoc_dir, dry_run=True)

    assert res.user_edits == 1
    assert len(res.directives) == 1
    assert "Focus:" in res.directives[0]
    assert '"validate hex inputs"' in res.directives[0]


def test_bold_descriptive_span_does_not_queue(dirs):
    root, codoc_dir = dirs
    _seed(codoc_dir)
    _edit_tree(codoc_dir, "Holds brand colors.", "Holds **brand** colors.")

    res = run_loop_b(root, codoc_dir, dry_run=True)

    assert res.user_edits == 1
    assert res.directives == []  # emphasis without imperative intent stays prose


def test_focus_lists_only_newly_bolded_spans(dirs):
    root, codoc_dir = dirs
    _seed(codoc_dir, description="Holds **fast** brand colors.")
    _edit_tree(codoc_dir, "Holds **fast** brand colors.",
               "Holds **fast** brand colors. Must keep writes **atomic**.")

    res = run_loop_b(root, codoc_dir, dry_run=True)

    assert len(res.directives) == 1
    focus = next(l for l in res.directives[0].splitlines() if "Focus:" in l)
    assert "atomic" in focus and "fast" not in focus


# -- external links -----------------------------------------------------------

def test_external_link_becomes_consult_line(dirs):
    root, codoc_dir = dirs
    _seed(codoc_dir)
    _edit_tree(codoc_dir, "Holds brand colors.",
               "Should follow the palette spec in "
               "[the spec](https://example.com/palette-spec).")

    res = run_loop_b(root, codoc_dir, dry_run=True)

    assert len(res.directives) == 1
    assert "Consult: https://example.com/palette-spec  (the spec)" in res.directives[0]


def test_steer_comment_carries_its_own_consult_link(dirs):
    root, codoc_dir = dirs
    _seed(codoc_dir)
    _edit_tree(codoc_dir, "Holds brand colors.",
               "Holds brand colors.\n"
               "  > match the tokens at [design tokens](https://example.com/tokens)")

    res = run_loop_b(root, codoc_dir, dry_run=True)

    assert res.steered == 1
    assert "Consult: https://example.com/tokens" in res.directives[0]
