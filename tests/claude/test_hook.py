"""The Claude Code hook: a turn that reported some queued items but not all is
asked once to report or explain the rest, and a turn with no daemon settles
when it ends."""
import json
import os
import subprocess
from pathlib import Path

import pytest

from codoc.claude import hook
from codoc.daemon import client
from codoc.workspace.epoch import read_activity, write_activity
from codoc.daemon.publish import publish
from codoc.calculus.derive import queued
from codoc.mcp import tools
from codoc.workspace import Workspace
from codoc.workspace import inbox as I
from codoc.workspace.layout import REALIZE
from tests.helpers import H, handoff, index_holds, seed


@pytest.fixture(autouse=True)
def started(monkeypatch):
    """The processes the Stop hook starts, recorded rather than run."""
    got: list[list[str]] = []
    monkeypatch.setattr(hook, "Popen", lambda argv, **_: got.append(argv))
    return got


def _setup(tmp_path, cited: list[str], *, open_: bool = True):
    cd = tmp_path / ".codoc"
    cd.mkdir(parents=True)
    (cd / REALIZE).write_text("# Realize\n\n### ⟨d:s:1⟩ Retry\nx\n\n### ⟨d:s:2⟩ Timeout\ny\n", encoding="utf-8")
    write_activity(str(cd), {"epoch": {"id": "ep-a", "open": open_}, "cited": cited})
    return str(cd)


def test_a_turn_that_reported_one_of_two_items_is_asked_once_about_the_other(tmp_path, capsys):
    cd = _setup(tmp_path, ["s:1"])
    hook.handle_stop({"session_id": "a"}, cd)
    out = json.loads(capsys.readouterr().out)
    assert out["decision"] == "block" and "d:s:2" in out["reason"] and "d:s:1" not in out["reason"]
    assert read_activity(cd)["epoch"]["open"]                     # the turn goes on
    hook.handle_stop({"session_id": "a", "stop_hook_active": True}, cd)
    assert capsys.readouterr().out == "" and not read_activity(cd)["epoch"]["open"]


def test_a_turn_that_never_touched_the_queue_or_reported_all_of_it_just_ends(tmp_path, capsys):
    for cited in ([], ["s:1", "s:2"]):
        cd = _setup(tmp_path / str(len(cited)), cited)
        hook.handle_stop({"session_id": "a"}, cd)
        assert capsys.readouterr().out == "" and not read_activity(cd)["epoch"]["open"]


def test_a_citation_from_an_earlier_turn_does_not_ask_about_this_one(tmp_path, capsys):
    """The inbox is never truncated and the last turn's citations stay in
    activity.json until the next turn opens. A turn that touched nothing in
    the queue just ends."""
    cd = _setup(tmp_path, ["s:1"], open_=False)
    I.submit(cd, "agent-claude-code", [I.Submission(cid="c1", kind="bind.attach", target={}, payload={},
                                                    caused_by="s:1")])
    hook.handle_user_prompt({"session_id": "a"}, cd)
    capsys.readouterr()
    hook.handle_stop({"session_id": "a"}, cd)
    assert capsys.readouterr().out == "" and not read_activity(cd)["epoch"]["open"]


def test_a_turn_with_no_daemon_settles_what_it_cited_when_it_ends(tmp_path, monkeypatch, capsys):
    """With no daemon the MCP server applies, and every write of the turn lands
    while its epoch is open, so none settles. The Stop hook starts a process
    that settles once the epoch is closed, so the next prompt is not told to
    build the item again."""
    root = Path(__file__).resolve().parents[2]

    def run_now(argv, **_):
        r = subprocess.run(argv, env={**os.environ, "PYTHONPATH": str(root)}, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
    monkeypatch.setattr(hook, "Popen", run_now)
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    ho = handoff(ws, "h1", actor=H)
    publish(cd, ws.store, root_dir=str(tmp_path))
    ws.close()
    index_holds(monkeypatch, "c.py::retry")
    hook.handle_user_prompt({"session_id": "s"}, str(cd))
    try:
        r = tools.reflect(str(cd), ops=[{"kind": "attach", "feature_id": "h1", "bindings": ["c.py::retry"]}],
                          caused_by=f"d:{ho.id}", root_dir=str(tmp_path))
    finally:
        client.release(cd)
    assert r["ok"] and queued(Workspace(cd, readonly=True).store) == ["h1"]      # held while the epoch is open
    capsys.readouterr()
    hook.handle_stop({"session_id": "s"}, str(cd))
    assert capsys.readouterr().out == ""                                          # it cited the one queued item
    assert queued(Workspace(cd, readonly=True).store) == []
    hook.handle_user_prompt({"session_id": "s"}, str(cd))
    assert capsys.readouterr().out == ""


def test_the_queue_a_prompt_announced_is_not_announced_again_as_growth(tmp_path, capsys):
    """The prompt tells the session what is queued. Its first tool call stays
    quiet about it; a hand-off added while the agent works is announced."""
    cd = _setup(tmp_path, [], open_=False)
    hook.handle_user_prompt({"session_id": "a"}, cd)
    assert "2 hand-off(s)" in capsys.readouterr().out
    hook.handle_post_tool({"session_id": "a", "tool_input": {}}, cd)
    assert capsys.readouterr().out == ""
    with (Path(cd) / REALIZE).open("a", encoding="utf-8") as fh:
        fh.write("\n### ⟨d:s:3⟩ Backoff\nz\n")
    hook.handle_post_tool({"session_id": "a", "tool_input": {}}, cd)
    assert "queue grew: 3 hand-off(s)" in capsys.readouterr().out

