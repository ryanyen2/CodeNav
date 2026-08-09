"""Concurrency on the hub path — where it lives now, and what a settle no longer is.

U9 guarded a remote settle with a whole-doc ``baseRev``: the settle POSTED the document,
the hub wrote it to ``tree.doc.json``, and a version older than the hub's was rejected
409 so a stale browser could not overwrite a newer state. Two things retired that:

  * ``tree.doc.json`` became the DAEMON's own projection output (U4/KTD9) and stopped
    being read as an edit channel (U7). Writing it from the hub dropped the author's
    prose at the next render AND made ``reconcile.safe_write_tree`` treat the projection
    as ahead of the store, so it skipped re-rendering both exports.
  * The edit now arrives as identity-keyed COMMANDS carrying ``base_text`` — the value
    the author last knew for the field being replaced — so divergence is resolved per
    field by the daemon's three-way merge (``loop_b._resolve_content``), which is the
    finer merge U9's docstring called deferred work.

So these tests pin the current contract: a settle stores nothing and refuses nothing, and
the concurrency claim rides on the commands instead.
"""
from __future__ import annotations

import json
from pathlib import Path

from codoc.loop import edits
from codoc.model.hlc import HLC
from codoc.serve.auth import Capability
from codoc.serve.dispatch import dispatch
from codoc.serve.payload import payload_version


def _set_version(cd: str, wall: int) -> None:
    Path(cd).mkdir(parents=True, exist_ok=True)
    (Path(cd) / "status.json").write_text(json.dumps(
        {"state": "in_sync", "pending": 0, "at": HLC(wall_clock=wall, logical_time=0).to_str()}))


def test_a_settle_is_acknowledged_and_held(tmp_path):
    cd = str(tmp_path)
    _set_version(cd, 1000)
    res = dispatch({"kind": "doc-settle", "doc": {"type": "doc"}}, Capability.SUGGEST, cd)
    assert res["held"] is True


def test_a_settle_never_writes_the_daemon_s_projection(tmp_path):
    """The wedge: a hub write to tree.doc.json made the daemon skip re-rendering the
    exports, and the prose it carried was overwritten at the next pass anyway."""
    cd = str(tmp_path)
    _set_version(cd, 1000)
    dispatch({"kind": "doc-settle", "doc": {"marker": "browser"}}, Capability.SUGGEST, cd)
    dispatch({"kind": "commit", "doc": {"marker": "browser"}}, Capability.SUGGEST, cd)
    assert not (tmp_path / "tree.doc.json").exists()


def test_a_stale_settle_is_not_refused_because_it_carries_no_change(tmp_path):
    """A 4xx is a DEFINITE rejection: the client's outbox drops the message and tells the
    author their change was refused. Doing that to a contentless acknowledgement would
    report a loss that never happened — and hide nothing, since the change is in the
    commands, each of which states its own base."""
    cd = str(tmp_path)
    _set_version(cd, 2000)
    res = dispatch({"kind": "doc-settle", "doc": {"type": "doc"}, "baseRev": 0},
                   Capability.SUGGEST, cd)
    assert res["held"] is True


def test_the_concurrency_claim_rides_on_the_command(tmp_path):
    """What replaced the whole-doc guard: the command names the text it is REPLACING and
    the session that authored it, so the daemon can tell a clean continuation from
    somebody else's write instead of comparing one number for the whole tree."""
    cd = str(tmp_path)
    _set_version(cd, 1000)
    dispatch({"kind": "set_description", "id": "c-1", "featureId": "f-1",
              "baseText": "what the author last saw", "session": "hub-a",
              "payload": {"description": "their new prose"}},
             Capability.SUGGEST, cd)

    cmd = edits.read_commands(cd)[0]
    assert (cmd.base_text, cmd.session) == ("what the author last saw", "hub-a")
    assert cmd.payload["description"] == "their new prose"


def test_two_sessions_editing_one_feature_each_state_their_own_base(tmp_path):
    """Both land in the channel; neither is dropped at the door. Which one wins where
    they overlap is the daemon's decision (rank + merge3), not the transport's."""
    cd = str(tmp_path)
    _set_version(cd, 1000)
    for i, (session, base) in enumerate([("hub-a", "original"), ("hub-b", "original")]):
        dispatch({"kind": "set_description", "id": f"c-{i}", "featureId": "f-1",
                  "baseText": base, "session": session,
                  "payload": {"description": f"prose from {session}"}},
                 Capability.SUGGEST, cd)

    assert [(c.session, c.base_text) for c in edits.read_commands(cd)] == [
        ("hub-a", "original"), ("hub-b", "original"),
    ]


def test_the_payload_version_still_reports_the_hub_s_state(tmp_path):
    """`payload_version` survives as the coarse staleness signal the SSE re-push guard
    uses; it is simply no longer a gate on writes."""
    cd = str(tmp_path)
    _set_version(cd, 1000)
    first = payload_version(cd)
    _set_version(cd, 5000)
    assert payload_version(cd) > first
