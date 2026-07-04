"""Loop B: inline steering comments, bold-as-focus, and external Consult links.

Post-U6/U7, an inline comment is a one-shot ``edits.json`` STEER (the webview no
longer round-trips a ``> …`` line through the read-only tree.codoc text), and a
description edit is a ``set_description`` COMMAND. A steer is always a STEER
directive (imperative by construction), handed off on mint, and appended to an
in-flight queue rather than clobbering it. ``**bold**`` spans ride into a
command-driven AMEND's directive as ``Focus:`` lines — only the NEWLY-bolded spans,
computed against the stored baseline. ``[label](https://…)`` links become
``Consult:`` lines.
"""
from __future__ import annotations

import json

import pytest

from codoc.codoc_file.render import write_tree
from codoc.loop import edits as edits_channel
from codoc.loop.edits import Command, Steer, append_command, append_steer
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


_N = [0]


def _amend(codoc_dir, fid, description):
    """Queue a `set_description` command — the webview's edit channel (U3/U4)."""
    _N[0] += 1
    append_command(codoc_dir, Command(id=f"cmd-{_N[0]}", kind="set_description",
                                      feature_id=fid, payload={"description": description}))


def _steer(codoc_dir, fid, text):
    """Queue an inline-comment steer through edits.json (U2b) — the post-U6 path."""
    _N[0] += 1
    append_steer(codoc_dir, Steer(feature_id=fid, text=text, comment_id=f"c-{_N[0]}"))


# -- steering comments ----------------------------------------------------

def test_steering_comment_queues_steer_directive_and_is_consumed(dirs):
    root, codoc_dir = dirs
    f = _seed(codoc_dir)
    _steer(codoc_dir, f.id, "also cache palette lookups")

    res = run_loop_b(root, codoc_dir, dry_run=False)

    assert res.steered == 1 and res.queued is True
    body = realize_path(codoc_dir).read_text()
    assert 'STEER FEATURE: "Color palette"' in body
    assert "also cache palette lookups" in body
    assert "Edit only: colors.py" in body
    # The prose was untouched (a comment is not a description edit).
    s = open_store(codoc_dir)
    assert s.get_feature(f.id).description == "Holds brand colors."
    s.close()
    assert json.loads(status_path(codoc_dir).read_text())["state"] == AWAITING_IMPL
    # The steer directive holds the feature (doc-wins) until the queue clears.
    assert f.id in edits_channel.hold_set(codoc_dir)
    # one-shot: the steer was consumed, so a second pass re-queues nothing.
    res2 = run_loop_b(root, codoc_dir, dry_run=False)
    assert res2.steered == 0


def test_steering_comment_appends_to_inflight_queue(dirs):
    root, codoc_dir = dirs
    f = _seed(codoc_dir)
    _amend(codoc_dir, f.id, "Holds brand colors. Should also expose dark-mode variants.")
    run_loop_b(root, codoc_dir, dry_run=False)
    # The AMEND mints a held draft; hand it off so it is genuinely in-flight in realize.md.
    edits_channel.append_handoffs(codoc_dir, [f.id])
    first = run_loop_b(root, codoc_dir, dry_run=False)
    assert first.queued and "UPDATE FEATURE" in realize_path(codoc_dir).read_text()

    # Mid-realization, the user steers with a comment — a steer is handed off on mint,
    # so it APPENDS to the in-flight realize.md (does not clobber the UPDATE).
    _steer(codoc_dir, f.id, "use CSS custom properties, not a JS map")
    second = run_loop_b(root, codoc_dir, dry_run=False)

    assert second.steered == 1
    body = realize_path(codoc_dir).read_text()
    assert "UPDATE FEATURE" in body and "STEER FEATURE" in body  # appended, not clobbered
    assert "CSS custom properties" in body
    manifest = edits_channel.read_manifest(codoc_dir)
    assert len(manifest) == 2
    assert [d.kind for d in manifest] == ["amend", "steer"]
    assert all(d.text for d in manifest)


def test_dry_run_leaves_edits_json_steer_queued(dirs):
    """A dry pass must NOT consume a steer it didn't queue (it would silently destroy
    explicit user intent). The post-U6 steer is a one-shot edits.json entry; a dry pass
    leaves it undrained, so a later real pass still sees it. (Replaces the retired
    `> …` text round-trip / reinsert tests — tree.codoc is read-only now, U6.)"""
    root, codoc_dir = dirs
    f = _seed(codoc_dir)
    _steer(codoc_dir, f.id, "rename the constant")

    res = run_loop_b(root, codoc_dir, dry_run=True)

    # A dry pass does not drain the edits.json steer (step 2.8 is `if not dry_run`).
    assert res.steered == 0
    assert not realize_path(codoc_dir).exists()
    # …and a later real pass drains it normally.
    res2 = run_loop_b(root, codoc_dir, dry_run=False)
    assert res2.steered == 1 and res2.queued


def test_two_distinct_steers_stay_distinct(dirs):
    """Two id-scoped steers on one feature stay DISTINCT directives (KTD4: identity is
    the comment_id, not the text) — they never collapse into one. (Replaces the retired
    `> …` text reinsert-without-merging test.)"""
    root, codoc_dir = dirs
    f = _seed(codoc_dir)
    _steer(codoc_dir, f.id, "rename the constant")
    _steer(codoc_dir, f.id, "also add a dark variant")

    res = run_loop_b(root, codoc_dir, dry_run=False)

    assert res.steered == 2 and res.queued
    body = realize_path(codoc_dir).read_text()
    assert "rename the constant" in body and "also add a dark variant" in body


def test_steer_caused_by_matches_cooccurring_amend(dirs):
    """A steer riding along with an AMEND command on the same feature in the same pass
    inherits that edit's cause, so the IDE cascade cue groups them."""
    root, codoc_dir = dirs
    f = _seed(codoc_dir)
    _amend(codoc_dir, f.id, "Should expose dark-mode variants.")
    append_steer(codoc_dir, Steer(feature_id=f.id, text="keep the public API stable"))

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

    _steer(codoc_dir, f.id, "also cache palette lookups")
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
    f = _seed(codoc_dir)
    # The sentence reads descriptive ("Holds …"), but the author NEWLY bolded an
    # imperative span — it rides into the directive as a Focus: line.
    _amend(codoc_dir, f.id, "Holds brand colors and **validate hex inputs** on write.")

    res = run_loop_b(root, codoc_dir, dry_run=False)

    assert res.commands == 1
    assert len(res.directives) == 1
    assert "Focus:" in res.directives[0]
    assert '"validate hex inputs"' in res.directives[0]


def test_bold_span_no_longer_gates_realization(dirs):
    """Bold lost its queuing role (is_imperative is deleted — no prose heuristic). It
    is now a pure presentation signal: the AMEND mints a HELD draft like any other
    edit, carrying the bolded span as a Focus: line, and stays held until hand-off."""
    root, codoc_dir = dirs
    f = _seed(codoc_dir)
    _amend(codoc_dir, f.id, "Holds **brand** colors.")

    res = run_loop_b(root, codoc_dir, dry_run=False)

    assert res.commands == 1
    assert res.queued is False            # held — bold does not force realization
    from codoc.loop.edits import read_manifest
    manifest = read_manifest(codoc_dir)
    assert len(manifest) == 1 and manifest[0].handed_off is False
    assert "Focus:" in manifest[0].text and '"brand"' in manifest[0].text


def test_focus_lists_only_newly_bolded_spans(dirs):
    root, codoc_dir = dirs
    f = _seed(codoc_dir, description="Holds **fast** brand colors.")
    _amend(codoc_dir, f.id, "Holds **fast** brand colors. Must keep writes **atomic**.")

    res = run_loop_b(root, codoc_dir, dry_run=False)

    assert len(res.directives) == 1
    focus = next(l for l in res.directives[0].splitlines() if "Focus:" in l)
    assert "atomic" in focus and "fast" not in focus


# -- external links -----------------------------------------------------------

def test_external_link_becomes_consult_line(dirs):
    root, codoc_dir = dirs
    f = _seed(codoc_dir)
    _amend(codoc_dir, f.id,
           "Should follow the palette spec in [the spec](https://example.com/palette-spec).")

    res = run_loop_b(root, codoc_dir, dry_run=False)

    assert len(res.directives) == 1
    assert "Consult: https://example.com/palette-spec  (the spec)" in res.directives[0]


def test_steer_comment_carries_its_own_consult_link(dirs):
    root, codoc_dir = dirs
    f = _seed(codoc_dir)
    _steer(codoc_dir, f.id, "match the tokens at [design tokens](https://example.com/tokens)")

    res = run_loop_b(root, codoc_dir, dry_run=False)

    assert res.steered == 1
    assert "Consult: https://example.com/tokens" in res.directives[0]
