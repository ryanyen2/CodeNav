"""Tests for the codoc propose script (codoc/agent/propose.py)."""
from __future__ import annotations

import json
import os
import re

import pytest

from codoc.agent.propose import propose_plan
from codoc.codoc_file.diff import diff_codoc
from codoc.codoc_file.parse import parse_text
from codoc.loop import inbox
from codoc.loop.loop_b import run_loop_b
from codoc.model.event import PLAN_SOURCE
from codoc.model.feature import Feature
from codoc.store.db import open_store


@pytest.fixture
def repo(tmp_path):
    """A minimal repo: tmp_path is root, tmp_path/.codoc is the store dir."""
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    # Seed one feature (auto-generated id so the parser regex matches).
    s = open_store(str(codoc_dir))
    parent = Feature(title="Parent feature")  # id auto-generated as f-[0-9a-f]{8}
    s.upsert_feature(parent)
    s.close()
    return str(tmp_path), str(codoc_dir), parent.id


def _tree_text(codoc_dir: str) -> str:
    return open(os.path.join(codoc_dir, "tree.codoc")).read()


# ── propose_plan creates a proper pending Event ────────────────────────────────

def test_propose_creates_applied_false_event(repo):
    root, codoc_dir, _parent_id = repo
    eid = propose_plan(root, kind="add_node", title="Date formatting",
                       description="ISO-8601 date helpers.")
    s = open_store(codoc_dir)
    pending = s.pending_events()
    s.close()
    assert len(pending) == 1
    assert pending[0].id == eid
    assert pending[0].source == PLAN_SOURCE
    assert not pending[0].applied
    assert pending[0].op.title == "Date formatting"


def test_propose_renders_pending_block(repo):
    root, codoc_dir, _parent_id = repo
    propose_plan(root, kind="add_node", title="Auth flow",
                 description="OAuth login flow.")
    text = _tree_text(codoc_dir)
    # in-situ add hunk: col-0 '+' op char + the proposed node + agent-plan tag
    assert re.search(r"(?m)^\+ \s*- Auth flow", text)
    assert "agent plan" in text


def test_propose_annotates_source_tag(repo):
    root, codoc_dir, _parent_id = repo
    propose_plan(root, kind="add_node", title="Widget", description="UI widget.")
    text = _tree_text(codoc_dir)
    assert "agent plan" in text


def test_propose_invalid_kind_raises(repo):
    root, codoc_dir, _parent_id = repo
    with pytest.raises(ValueError, match="bad_kind"):
        propose_plan(root, kind="bad_kind", title="X")


def test_propose_plan_roundtrip_noop(repo):
    """render → parse → diff_codoc must yield no user_ops (round-trip invariant)."""
    root, codoc_dir, _parent_id = repo
    propose_plan(root, kind="add_node", title="Auth", description="Login flow.")
    s = open_store(codoc_dir)
    text = _tree_text(codoc_dir)
    diff = diff_codoc(parse_text(text), s)
    s.close()
    assert diff.user_ops == [], "pending block leaked into parsed user ops"


def test_propose_with_binds(repo):
    root, codoc_dir, _parent_id = repo
    eid = propose_plan(root, kind="add_node", title="Widget",
                       description="A UI widget.", binds=["ui/widget.py::Widget"])
    s = open_store(codoc_dir)
    pending = s.pending_events()
    s.close()
    assert len(pending) == 1
    assert ("ui/widget.py", "Widget") in pending[0].op.bindings


def test_propose_amend(repo):
    root, codoc_dir, parent_id = repo
    eid = propose_plan(root, kind="amend", feature_id=parent_id,
                       description="Updated description for the parent feature.")
    s = open_store(codoc_dir)
    pending = s.pending_events()
    s.close()
    assert len(pending) == 1
    assert pending[0].op.feature_id == parent_id
    assert pending[0].source == PLAN_SOURCE


def test_proposed_plan_accepted_builds_directive(repo):
    """Accepted plan proposal → Loop B builds a directive (dry_run=True)."""
    root, codoc_dir, _parent_id = repo
    eid = propose_plan(root, kind="add_node", title="Theme system",
                       description="Light/dark theme support.")
    inbox.append_verdict(codoc_dir, eid, accept=True)
    res = run_loop_b(root, codoc_dir, dry_run=True)
    assert res.accepted == 1
    assert any("NEW FEATURE" in d and "Theme system" in d for d in res.directives)
    # After acceptance, no more pending proposals.
    s = open_store(codoc_dir)
    assert s.pending_events() == []
    s.close()
