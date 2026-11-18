"""The Claude Code hook: a turn that reported some queued items but not all is
asked once to report or explain the rest."""
import json

from codoc.claude import hook
from codoc.daemon.epoch import read_activity, write_activity
from codoc.workspace import log as L
from codoc.workspace.layout import REALIZE


def _setup(tmp_path, cited: list[str]):
    cd = tmp_path / ".codoc"
    (cd / "inbox").mkdir(parents=True)
    (cd / REALIZE).write_text("# Realize\n\n### ⟨d:s:1⟩ Retry\nx\n\n### ⟨d:s:2⟩ Timeout\ny\n", encoding="utf-8")
    lines = [json.dumps({"cid": f"c{i}", "kind": "bind.attach", "target": {}, "payload": {}, "caused_by": c})
             for i, c in enumerate(cited)]
    (L.inbox_dir(cd) / "agent-claude-code.jsonl").write_text("".join(x + "\n" for x in lines), encoding="utf-8")
    write_activity(str(cd), {"epoch": {"id": "ep-a", "open": True}})
    return str(cd)


def test_a_turn_that_reported_one_of_two_items_is_asked_once_about_the_other(tmp_path, capsys):
    cd = _setup(tmp_path, ["d:s:1"])
    hook.handle_turn_stop({"session_id": "a"}, cd)
    out = json.loads(capsys.readouterr().out)
    assert out["decision"] == "block" and "d:s:2" in out["reason"] and "d:s:1" not in out["reason"]
    assert read_activity(cd)["epoch"]["open"]                     # the turn goes on
    hook.handle_turn_stop({"session_id": "a", "stop_hook_active": True}, cd)
    assert capsys.readouterr().out == "" and not read_activity(cd)["epoch"]["open"]


def test_a_turn_that_never_touched_the_queue_or_reported_all_of_it_just_ends(tmp_path, capsys):
    for cited in ([], ["s:1", "d:s:2"]):
        cd = _setup(tmp_path / str(len(cited)), cited)
        hook.handle_turn_stop({"session_id": "a"}, cd)
        assert capsys.readouterr().out == "" and not read_activity(cd)["epoch"]["open"]
