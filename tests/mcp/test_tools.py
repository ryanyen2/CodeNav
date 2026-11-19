"""The agent's own write surface (`codoc/mcp/tools.py`).

An agent reaches the tree through the MCP tools and nothing else, so what the
tools promise — "small repairs land, rewrites and repairs on a feature with
pending intent become a plan" — has to be what the tree does.
"""
import pytest

from codoc.calculus import ops as K
from codoc.calculus.derive import accept_handoffs, markdown, phase
from codoc.calculus.doc import PARAGRAPH
from codoc.calculus.ids import START
from codoc.calculus.text import BOLD
from codoc.daemon import client, reads
from codoc.daemon.publish import publish
from codoc.mcp import tools
from codoc.workspace import Workspace
from tests.helpers import H, T, handoff, code_holds, refs, seed


def workspace(tmp_path):
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    publish(cd, ws.store, root_dir=str(tmp_path))
    return cd, ws


def test_an_agents_rewrite_of_a_queued_feature_is_a_plan(tmp_path):
    """While a feature is queued the document wins, whatever the size of the
    repair: an agent's rewrite through the tool is a plan, as the loop's is."""
    cd, ws = workspace(tmp_path)
    handoff(ws, "h1", actor=H, asked="retry twice")
    assert phase(ws.store, "h1") == "queued"
    ws.close()
    r = tools.reflect(str(cd), ops=[{"kind": "amend", "feature_id": "h1",
                                     "blocks": [{"id": "p1", "text": "Completely different words, nothing kept."}]}],
                      rationale="rewrites the retry paragraph", root_dir=str(tmp_path))
    client.release(cd)
    assert r["ok"], r
    st = Workspace(cd, readonly=True).store
    trunk = [s for s in st.blocks["p1"].text().segs if s.live and s.plan is None and s.actor.startswith("agent:")]
    assert not trunk, [s.to_json() for s in st.blocks["p1"].text().segs]
    assert markdown(st.blocks["p1"].text(), "rejected", st) == "Retries once."
    assert [(p.actor, p.note) for p in st.plans.values() if p.status == "open"] == [
        ("agent:claude-code", "rewrites the retry paragraph")]


def test_an_agents_amend_does_not_drop_a_mark_the_person_placed(tmp_path):
    """A rewriter may not drop a mark a person placed. An agent's amend
    through the tool is held to it, as the loop and a translator are."""
    cd, ws = workspace(tmp_path)
    r = refs(ws, "p1")
    ws.submit([{"kind": K.TEXT_MARK, "target": {"bid": "p1", "path": "text"},
                "payload": {"kind": BOLD, "attrs": {}, "start": list(r[0]), "end": list(r[6]), "on": True}}], actor=H)
    ws.close()
    tools.reflect(str(cd), ops=[{"kind": "amend", "feature_id": "h1",
                                 "blocks": [{"id": "p1", "text": "Retries twice."}]}], root_dir=str(tmp_path))
    client.release(cd)
    st = Workspace(cd, readonly=True).store
    assert markdown(st.blocks["p1"].text(), "rejected", st) == "**Retries** once."


def test_a_small_repair_to_the_machines_own_prose_still_lands(tmp_path):
    """The gate is a gate, not a wall: the tool's contract is that small
    repairs land."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws, actor="loop")
    publish(cd, ws.store, root_dir=str(tmp_path))
    ws.close()
    tools.reflect(str(cd), ops=[{"kind": "amend", "feature_id": "h1",
                                 "blocks": [{"id": "p1", "text": "Retries once more."}]}], root_dir=str(tmp_path))
    client.release(cd)
    st = Workspace(cd, readonly=True).store
    assert st.blocks["p1"].text().live_text() == "Retries once more."
    assert not [p for p in st.plans.values() if p.status == "open"]


def test_one_amend_over_two_paragraphs_is_one_proposal(tmp_path):
    """One amend is one plan: every block it repairs rides under the same
    plan id, so one repair gets one verdict, not one per paragraph."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "p2"}, "payload": {"kind": PARAGRAPH, "after": "p1"}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "p2", "path": "text"}, "payload": {"after": START, "text": "Backs off."}}], actor=H)
    handoff(ws, "h1", actor=H)                            # queued: the document wins, so both repairs propose
    publish(cd, ws.store, root_dir=str(tmp_path))
    ws.close()
    try:
        out = tools.reflect(str(cd), ops=[{"kind": "amend", "feature_id": "h1", "blocks": [
            {"id": "p1", "text": "Retries twice."}, {"id": "p2", "text": "Backs off exponentially."}]}],
            root_dir=str(tmp_path))
        assert out["ok"], out
        st = client.read(cd).store
        open_plans = [p for p in st.plans.values() if p.status == "open" and p.actor == "agent:claude-code"]
        assert len(open_plans) == 1, [(p.id, p.target, p.ops) for p in open_plans]
        assert len(open_plans[0].ops) > 1                  # and both repairs are under it
    finally:
        client.release(cd)


def test_a_repair_never_rides_in_the_agents_open_proposal_to_delete(tmp_path):
    """The applier reuses an open `edit` plan of the actor on the same block,
    never a `delete` plan. A repair an agent makes after proposing to retire a
    feature is therefore its own proposal, and the verdict on the deletion does
    not decide it."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    handoff(ws, "h1", actor=H)                            # queued, so the repair must propose
    publish(cd, ws.store, root_dir=str(tmp_path))
    ws.close()
    try:
        assert tools.propose(str(cd), ops=[{"kind": "delete_node", "feature_id": "h1"}], note="dead code",
                             root_dir=str(tmp_path))["ok"]
        tools.reflect(str(cd), ops=[{"kind": "amend", "feature_id": "h1", "title": "Server client"}],
                      root_dir=str(tmp_path))
        st = client.read(cd).store
        riders = {st.plans[o.plan].kind for o in st.ops
                  if o.kind == K.TEXT_INSERT and o.plan and "Server" in o.payload.get("text", "")}
        assert riders and "delete" not in riders, riders
    finally:
        client.release(cd)


def test_the_tools_do_not_offer_a_proposal_nobody_can_answer(tmp_path):
    """A plan whose target block is dead is not offered (`derive.offered_plans`),
    and `read_tree` and `read_status` count the plans the editor draws, so the
    agent never reads a proposal the person cannot answer."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    pid = ws.open_plan(actor="agent:claude-code", kind="edit", target="p1")
    r = refs(ws, "p1")
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                "payload": {"after": list(r[-1]), "text": " Proposed."}}], actor="agent:claude-code", plan=pid)
    publish(cd, ws.store, root_dir=str(tmp_path))
    ws.close()
    assert reads.queue_status(str(cd))["plans"] == 1
    assert [p["plan_id"] for p in tools.read_tree(str(cd))["plans"]] == [pid]
    client.release(cd)
    ws = Workspace(cd)
    d = ws.submit([{"kind": K.BLOCK_DELETE, "target": {"bid": "h1"}, "payload": {}}], actor=H)[0]
    publish(cd, ws.store, root_dir=str(tmp_path))
    ws.close()
    assert reads.queue_status(str(cd))["plans"] == 0
    assert tools.read_tree(str(cd))["plans"] == []
    client.release(cd)
    ws = Workspace(cd)                                   # …and it comes back with the block
    ws.submit([dict(b) for b in d.inverse], actor=H)
    publish(cd, ws.store, root_dir=str(tmp_path))
    ws.close()
    assert reads.queue_status(str(cd))["plans"] == 1
    client.release(cd)


def test_accepting_a_proposal_builds_but_accepting_a_reflection_does_not(tmp_path):
    """A `codoc_propose` plan comes before the code, so accepting it hands off
    the feature it adds. A `codoc_reflect` plan describes code the agent
    already wrote, so accepting it takes the words and queues nothing."""
    cd, ws = workspace(tmp_path)
    ws.close()
    try:
        refl = tools.reflect(str(cd), ops=[{"kind": "add_node", "title": "Backoff", "description": "Waits."}],
                             root_dir=str(tmp_path))
        prop = tools.propose(str(cd), ops=[{"kind": "add_node", "title": "Jitter", "description": "Spreads."}],
                             root_dir=str(tmp_path))
        st = client.read(cd).store
        assert accept_handoffs(st, st.plans[refl["planned"]["plan_id"]]) == []
        jitter = next(h.id for h in st.headings() if h.text().live_text() == "Jitter")
        assert accept_handoffs(st, st.plans[prop["plan_id"]]) == [jitter]
    finally:
        client.release(cd)


def test_a_proposal_that_writes_nothing_says_so_and_leaves_no_plan(tmp_path):
    """A second rewording of a paragraph that holds the first proposal's words
    is refused. The call reports it, and the empty plan is taken back, so the
    person is not asked to accept (and build) nothing."""
    cd, ws = workspace(tmp_path)
    ws.close()
    try:
        def reword(text: str) -> dict:
            return tools.propose(str(cd), ops=[{"kind": "amend", "feature_id": "h1",
                                                "blocks": [{"id": "p1", "text": text}]}], root_dir=str(tmp_path))

        unchanged = reword(T(ws.store, "p1"))
        assert not unchanged["ok"] and "no block would change" in unchanged["error"]
        assert reword("Retries twice.")["ok"]
        second = reword("Retries three times.")
        assert not second["ok"] and "open_plan" in second["error"]
        st = client.read(cd).store
        assert [p for p in st.open_plans.values() if not p.ops] == []
    finally:
        client.release(cd)


def test_a_refused_part_does_not_hide_the_plans_that_were_written(tmp_path):
    """A refused rewording beside a removal: the call is ok, names the delete
    plan it opened (a delete plan needs no struck words), and reports the refusal."""
    cd, ws = workspace(tmp_path)
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "h2"}, "payload": {"kind": "heading", "level": 1, "after": "p1"}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "h2", "path": "text"}, "payload": {"after": START, "text": "Old"}}],
              actor=H)
    publish(cd, ws.store, root_dir=str(tmp_path))
    ws.close()
    try:
        reword = {"kind": "amend", "feature_id": "h1", "blocks": [{"id": "p1", "text": "Retries twice."}]}
        assert tools.propose(str(cd), ops=[reword], root_dir=str(tmp_path))["ok"]
        got = tools.propose(str(cd), ops=[reword, {"kind": "delete_node", "feature_id": "h2"}], root_dir=str(tmp_path))
        st = client.read(cd).store
        assert got["ok"] and [st.plans[p].kind for p in got["plan_ids"]] == ["delete"]
        assert any("open_plan" in r for r in got["refused"])
    finally:
        client.release(cd)


def test_a_feature_is_proposed_for_removal_once(tmp_path):
    """Two `delete_node` of one feature in one call open one delete plan, and
    a later call adds none while that plan is open."""
    cd, ws = workspace(tmp_path)
    ws.close()
    try:
        retire = {"kind": "delete_node", "feature_id": "h1"}
        assert tools.propose(str(cd), ops=[retire, retire], root_dir=str(tmp_path))["ok"]
        assert not tools.propose(str(cd), ops=[retire], root_dir=str(tmp_path))["ok"]
        st = client.read(cd).store
        assert [p.target for p in st.plans.values() if p.kind == "delete" and p.status == "open"] == ["h1"]
    finally:
        client.release(cd)


def test_codoc_agent_names_the_actor_and_its_citing_attach_settles_the_handoff(tmp_path, monkeypatch):
    """Cursor or Codex run this MCP server with CODOC_AGENT set and run no
    Claude Code hooks, so no agent epoch ever closes. Their attach that cites
    the hand-off is the evidence, and the applier settles on it, here with no
    daemon running."""
    from codoc.calculus.derive import queued
    cd, ws = workspace(tmp_path)
    ho = handoff(ws, "h1", actor=H)
    ws.close()
    code_holds(monkeypatch, "c.py::retry")
    monkeypatch.setenv("CODOC_AGENT", "cursor")
    try:
        r = tools.reflect(str(cd), ops=[{"kind": "attach", "feature_id": "h1", "bindings": ["c.py::retry"]}],
                          caused_by=ho.id, root_dir=str(tmp_path))
    finally:
        client.release(cd)
    assert r["ok"], r
    st = Workspace(cd, readonly=True).store
    assert st.by_id[r["applied"][0]["op"]].actor == "agent:cursor"
    assert queued(st) == [] and phase(st, "h1") == "synced"
    assert [o.caused_by for o in st.ops if o.kind == K.FEATURE_REALIZED] == [ho.id]


def test_the_queues_d_id_settles_the_handoff(tmp_path, monkeypatch):
    """realize.md shows a hand-off as ⟨d:<op id>⟩ and the agent is told to pass
    that id as caused_by; it must cite the hand-off the same as the bare id."""
    from codoc.calculus.derive import queued
    cd, ws = workspace(tmp_path)
    ho = handoff(ws, "h1", actor=H)
    ws.close()
    code_holds(monkeypatch, "c.py::retry")
    try:
        r = tools.reflect(str(cd), ops=[{"kind": "attach", "feature_id": "h1", "bindings": ["c.py::retry"]}],
                          caused_by=f"d:{ho.id}", root_dir=str(tmp_path))
    finally:
        client.release(cd)
    assert r["ok"], r
    assert queued(Workspace(cd, readonly=True).store) == []


def test_only_a_call_whose_ops_landed_cites_its_handoff(tmp_path, monkeypatch):
    """The Stop hook asks about each queued item no call of the turn cited. A
    call that errored reported nothing, so it cites nothing."""
    from codoc.workspace.epoch import read_activity, write_activity
    cd, ws = workspace(tmp_path)
    ho = handoff(ws, "h1", actor=H)
    ws.close()
    code_holds(monkeypatch, "c.py::retry", "c.py::x")
    write_activity(str(cd), {"epoch": {"id": "ep", "open": True}, "cited": []})
    cite, root = f"d:{ho.id}", str(tmp_path)
    try:
        assert not tools.reflect(str(cd), ops=[{"kind": "attach", "feature_id": "nope", "bindings": ["c.py::x"]}],
                                 caused_by=cite, root_dir=root)["ok"]
        assert not tools.propose(str(cd), ops=[], caused_by=cite, root_dir=root)["ok"]
        assert not tools.write_block(str(cd), content={}, kind="nope", feature_id="h1", caused_by=cite, root_dir=root)["ok"]
        assert read_activity(str(cd))["cited"] == []
        assert tools.reflect(str(cd), ops=[{"kind": "attach", "feature_id": "h1", "bindings": ["c.py::retry"]}],
                             caused_by=cite, root_dir=root)["ok"]
    finally:
        client.release(cd)
    assert read_activity(str(cd))["cited"] == [ho.id]


def test_status_lists_an_item_reported_this_turn_as_settling_when_the_turn_ends(tmp_path, monkeypatch):
    """A hand-off settles when the agent's turn ends, not when its citing
    reflect lands. Until then the agent's status lists the item as reported,
    and the lease its own progress calls hold does not read as another pass."""
    from codoc.workspace.epoch import write_activity
    from codoc.daemon.publish import REALIZE_OWNER_ENV
    monkeypatch.setenv(REALIZE_OWNER_ENV, "")
    monkeypatch.delenv(REALIZE_OWNER_ENV)             # an interactive session; restored after the test
    cd, ws = workspace(tmp_path)
    ho = handoff(ws, "h1", actor=H)
    publish(cd, ws.store, root_dir=str(tmp_path))
    ws.close()
    code_holds(monkeypatch, "c.py::retry")
    write_activity(str(cd), {"epoch": {"id": "ep", "open": True}, "cited": []})
    item = {"feature_id": "h1", "title": "Client", "handoff": f"d:{ho.id}"}
    try:
        tools.realize_progress(str(cd), done=0, total=1, current="Client")
        st = reads.queue_status(str(cd))
        assert (st["state"], st["queued_list"], st["reported_list"]) == ("queued", [item], [])
        assert tools.reflect(str(cd), ops=[{"kind": "attach", "feature_id": "h1", "bindings": ["c.py::retry"]}],
                             caused_by=item["handoff"], root_dir=str(tmp_path))["ok"]
        st = reads.queue_status(str(cd))
        assert (st["state"], st["queued"], st["reported_list"]) == ("reported", 0, [item])
        assert tools.realize_progress(str(cd), done=1, total=1)["reported"] == [item["handoff"]]
    finally:
        client.release(cd)


def test_an_attach_settles_a_handoff_only_on_code_the_build_changed(tmp_path, monkeypatch):
    """With no daemon and no epoch (Cursor, Codex), the agent's attach is the
    only evidence a hand-off was built. It is read off the code on disk: a
    symbol the files do not define is refused, a re-attach of code nobody
    changed writes nothing, and a re-attach of code the build changed writes a
    `bind.refresh` that settles the hand-off, even when a reflect pass took the
    change first."""
    from codoc.calculus import ops as K
    from codoc.calculus.derive import queued
    from codoc.index.app import code_on_disk
    monkeypatch.setenv("CODOC_AGENT", "cursor")
    cd, ws = workspace(tmp_path)
    (tmp_path / "c.py").write_text("def send():\n    return 1\n")
    ws.submit([{"kind": K.BIND_REFRESH, "target": {"fid": "h1"},
                "payload": dict(zip(("fingerprint", "types_hash"), code_on_disk(tmp_path, {"c.py"})["c.py::send"]),
                                symbol="c.py::send")}], actor="loop")
    ho = handoff(ws, "h1", actor=H)
    ws.close()
    cite, root = f"d:{ho.id}", str(tmp_path)

    def attach(*symbols):
        try:
            return tools.reflect(str(cd), ops=[{"kind": "attach", "feature_id": "h1", "bindings": list(symbols)}],
                                 caused_by=cite, root_dir=root)
        finally:
            client.release(cd)
    r = attach("c.py::send", "nope/ghost.py::does_not_exist")
    assert not r["ok"] and "does not define nope/ghost.py::does_not_exist" in r["error"], r
    assert attach("c.py::send")["applied"] == []                   # the code is as it was
    assert queued(Workspace(cd, readonly=True).store) == ["h1"]

    (tmp_path / "c.py").write_text("def send():\n    return 2\n")  # the build
    w = Workspace(cd)                                              # a reflect pass takes it first
    w.submit([{"kind": K.BIND_REFRESH, "target": {"fid": "h1"},
               "payload": dict(zip(("fingerprint", "types_hash"), code_on_disk(tmp_path, {"c.py"})["c.py::send"]),
                               symbol="c.py::send")}], actor="loop")
    w.close()
    r = attach("c.py::send")
    assert [a["conflict"] for a in r["applied"]] == [None], r
    st = Workspace(cd, readonly=True).store
    assert queued(st) == [] and [o.kind for o in st.ops if o.caused_by == ho.id][0] == K.BIND_REFRESH


def test_new_code_a_daemonless_turn_wrote_binds_and_settles_when_the_turn_ends(tmp_path):
    """No daemon, so no pass indexes what the turn wrote: the attach reads the
    new function off the disk, and the process the Stop hook starts settles."""
    from codoc.calculus.derive import queued
    from codoc.workspace.epoch import close_epoch, write_activity
    cd, ws = workspace(tmp_path)
    ho = handoff(ws, "h1", actor=H)
    ws.close()
    (tmp_path / "c.py").write_text("def send():\n    return 1\n\n\ndef retry_twice():\n    return send()\n")
    write_activity(cd, {"version": 1, "epoch": {"id": "ep-s", "open": True}, "cited": []})
    try:
        r = tools.reflect(str(cd), ops=[{"kind": "attach", "feature_id": "h1", "bindings": ["c.py::retry_twice"]}],
                          caused_by=f"d:{ho.id}", root_dir=str(tmp_path))
    finally:
        client.release(cd)
    assert r["ok"], r
    close_epoch(cd, "ep-s")
    client.settle_closed_epoch(cd)
    client.release(cd)
    assert queued(Workspace(cd, readonly=True).store) == []


def _owned_as_indexed(tmp_path, raw: bytes):
    """A workspace whose `h1` owns `c.py::send`, bound with the hashes the
    index gives `raw` (it reads through `file_chunks`), and a hand-off of it."""
    from codoc.calculus import ops as K
    from codoc.index.app import file_chunks
    (tmp_path / "c.py").write_bytes(raw)
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws, bind=False)
    w = {c.symbol: h for c, h in file_chunks("c.py", raw)}["c.py::send"]
    ws.submit([{"kind": K.BIND_ATTACH, "target": {"fid": "h1"},
                "payload": {"symbol": "c.py::send", "fingerprint": w.fingerprint, "types_hash": w.types_hash}}],
              actor="loop")
    ho = handoff(ws, "h1", actor=H)
    publish(cd, ws.store, root_dir=str(tmp_path))
    ws.close()
    return cd, ho


@pytest.mark.parametrize("raw", [
    b'def send():\r\n    """Send once.\r\n\r\n    Then stop."""\r\n    return 1\r\n',
    b"# caf\xe9\ndef send():\n    return 1\n",
    "def send():\n    return 1\n".encode("utf-16"),
], ids=["crlf", "latin-1 byte", "utf-16 bom"])
def test_the_evidence_is_read_as_the_index_reads_the_file(tmp_path, raw):
    """CRLF line endings, a byte that is not UTF-8, a UTF-16 file: an attach
    reads the code the way the index does, so a re-attach of code nobody
    changed writes nothing, and a function the build added binds."""
    from codoc.calculus.derive import queued
    cd, ho = _owned_as_indexed(tmp_path, raw)
    try:
        r = tools.reflect(str(cd), ops=[{"kind": "attach", "feature_id": "h1", "bindings": ["c.py::send"]}],
                          caused_by=f"d:{ho.id}", root_dir=str(tmp_path))
        assert r["ok"] and r["applied"] == [], r
        added = raw.decode("utf-16") if raw.startswith(b"\xff\xfe") else None
        with (tmp_path / "c.py").open("ab") as fh:
            fh.write(("\n\ndef send_twice():\n    return send() + send()\n".encode("utf-16-le")) if added
                     else b"\n\ndef send_twice():\n    return send() + send()\n")
        r = tools.reflect(str(cd), ops=[{"kind": "attach", "feature_id": "h1", "bindings": ["c.py::send_twice"]}],
                          caused_by=f"d:{ho.id}", root_dir=str(tmp_path))
        assert r["ok"], r
    finally:
        client.release(cd)
    assert queued(Workspace(cd, readonly=True).store) == []


def test_a_binding_is_keyed_by_the_file_as_the_index_names_it(tmp_path):
    """An agent may name a file absolutely, with `./`, or through `..`: the
    binding lands under the key a reflect pass would produce, so it neither
    doubles a binding the feature has nor hides from the pass. A file outside
    the repository is refused."""
    cd, ho = _owned_as_indexed(tmp_path, b"def send():\n    return 1\n\n\ndef retry():\n    return send()\n")
    root = str(tmp_path)

    def attach(*symbols):
        try:
            return tools.reflect(str(cd), ops=[{"kind": "attach", "feature_id": "h1", "bindings": list(symbols)}],
                                 caused_by=f"d:{ho.id}", root_dir=root)
        finally:
            client.release(cd)
    assert attach(f"{tmp_path.resolve()}/c.py::send")["applied"] == []      # owned, unchanged
    assert attach("./c.py::retry")["ok"]
    r = attach("../elsewhere.py::x")
    assert not r["ok"] and "outside the repository" in r["error"], r
    st = Workspace(cd, readonly=True).store
    assert sorted(st.bindings) == ["c.py::retry", "c.py::send"]


def test_a_binding_names_a_file_as_the_index_walk_does(tmp_path):
    """The index's walk names a linked file by its link and does not enter a
    linked directory. A binding's file is put the same way, lexically and never
    through a symlink, so an agent can name every key the index holds and only
    those."""
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path
    from codoc.index.app import canonical_file, code_on_disk
    (tmp_path / "real.py").write_text("def f():\n    return 1\n")
    os.symlink("real.py", tmp_path / "link.py")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("def g():\n    return 2\n")
    os.symlink("pkg", tmp_path / "pkg_link")
    assert {canonical_file(tmp_path, f) for f in ("link.py", "./link.py", "pkg/../link.py",
                                                  str(tmp_path / "link.py"))} == {"link.py"}
    assert canonical_file(tmp_path, "../elsewhere.py") is None
    walk = subprocess.run(
        [sys.executable, "-c", "import json, sys; from codoc.index.changes import compute_changeset; "
         "print(json.dumps(sorted(r.symbol for r in compute_changeset(sys.argv[1], sys.argv[1] + '/.codoc').rows)))",
         str(tmp_path)], capture_output=True, text=True, timeout=120,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])})
    assert walk.returncode == 0, walk.stderr
    named = {canonical_file(tmp_path, f) for f in ("real.py", "link.py", "pkg/mod.py", "pkg_link/mod.py")}
    assert sorted(code_on_disk(tmp_path, named)) == json.loads(walk.stdout) == [
        "link.py::f", "pkg/mod.py::g", "real.py::f"]


def test_a_binding_may_reach_the_repository_by_another_path(tmp_path):
    """The MCP server's root is resolved, while an agent reports paths the way
    its working directory shows them: through a symlinked parent (VS Code opens
    the folder as the person named it), or climbing out of the repository and
    back in. Both name the same file; only a path no ancestor of which is the
    repository is refused."""
    import os
    from codoc.index.app import canonical_file
    real = tmp_path.resolve() / "real" / "repo"
    real.mkdir(parents=True)
    (real / "c.py").write_text("def send():\n    return 1\n")
    os.symlink(real.parent, tmp_path / "alias")
    assert canonical_file(real, str(tmp_path / "alias" / "repo" / "c.py")) == "c.py"
    assert canonical_file(real, "../repo/c.py") == "c.py"
    assert canonical_file(real, "../elsewhere/c.py") is None


def test_a_binding_may_name_a_file_through_an_outside_link_into_a_subdirectory(tmp_path):
    """`outside/src -> repo/src`: no ancestor of the path is the repository, so
    the file is named by its real path below it, the key the index's walk gives
    it. A link into a directory the walk leaves out (`vendor/`) names no key,
    and is refused as outside the repository."""
    import os
    repo = tmp_path.resolve() / "repo"
    (tmp_path / "outside").mkdir()
    for d in ("src", "vendor"):
        (repo / d).mkdir(parents=True)
        (repo / d / "c.py").write_text("def retry():\n    return 1\n")
        os.symlink(repo / d, tmp_path / "outside" / d)
    cd = repo / ".codoc"
    ws = Workspace(cd)
    seed(ws, bind=False)
    ws.close()

    def attach(sym):
        try:
            return tools.reflect(str(cd), ops=[{"kind": "attach", "feature_id": "h1", "bindings": [sym]}],
                                 root_dir=str(repo))
        finally:
            client.release(cd)
    assert attach(f"{tmp_path}/outside/src/c.py::retry")["ok"]
    r = attach(f"{tmp_path}/outside/vendor/c.py::retry")
    assert not r["ok"] and "outside the repository" in r["error"], r
    assert sorted(Workspace(cd, readonly=True).store.bindings) == ["src/c.py::retry"]


def test_a_plan_reports_no_build_so_it_refreshes_nothing(tmp_path):
    """`codoc_propose` describes code not yet written: an owned symbol whose
    code changed is not refreshed under its plan, which would conflict
    `not_plannable`."""
    cd, ho = _owned_as_indexed(tmp_path, b"def send():\n    return 1\n")
    (tmp_path / "c.py").write_bytes(b"def send():\n    return 2\n")
    try:
        r = tools.propose(str(cd), ops=[{"kind": "attach", "feature_id": "h1", "bindings": ["c.py::send"]}],
                          caused_by=f"d:{ho.id}", root_dir=str(tmp_path))
    finally:
        client.release(cd)
    assert "not_plannable" not in str(r), r


def test_context_for_a_feature_is_its_range_and_the_features_one_call_away(tmp_path):
    """An agent about to build a feature reads it, the features under it, and
    each feature owning a symbol their code calls or that calls their code
    (a caller breaks when the build changes a signature). A feature that only
    shares a file with it is not part of that."""
    from codoc.index.graph import Graph
    from codoc.workspace.layout import GRAPH_CACHE
    cd, ws = workspace(tmp_path)

    def feature(bid, text, level, after, symbol):
        ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": bid}, "payload": {"kind": "heading", "level": level, "after": after}},
                   {"kind": K.TEXT_INSERT, "target": {"bid": bid, "path": "text"}, "payload": {"after": START, "text": text}},
                   {"kind": K.BIND_ATTACH, "target": {"fid": bid}, "payload": {"symbol": symbol}}], actor="loop")
    feature("h1a", "Sending one", 2, "p1", "c.py::send_one")
    feature("h2", "Server", 1, "h1a", "c.py::serve")
    feature("h3", "Retry policy", 1, "h2", "r.py::retry")
    feature("h4", "Logging", 1, "h3", "c.py::log")
    calls = [("c.py::send_one", "r.py::retry"), ("c.py::serve", "c.py::send")]
    (cd / GRAPH_CACHE).parent.mkdir(exist_ok=True)
    (cd / GRAPH_CACHE).write_text(Graph([{"src_symbol": s, "dst_name": d, "dst_symbol": d, "kind": "call",
                                          "src_file": s.split("::")[0], "internal": True} for s, d in calls]).dumps())
    ws.close()
    ctx = tools.read_context(str(cd), feature_id="h1")
    assert [e["id"] for e in ctx["subtree"]] == ["h1", "h1a", "h2", "h3"]
    assert ctx["subtree"][0]["blocks"] == [{"id": "p1", "text": "Retries once."}]
    assert not tools.read_context(str(cd), feature_id="p1")["ok"]


def test_the_agent_is_claude_code_unless_codoc_agent_names_another(monkeypatch):
    from codoc.workspace.inbox import actor_for
    monkeypatch.delenv("CODOC_AGENT", raising=False)
    assert actor_for(tools.agent_client()) == "agent:claude-code"
    monkeypatch.setenv("CODOC_AGENT", "codex")
    assert actor_for(tools.agent_client()) == "agent:codex"
    # The name becomes an inbox file name and an actor id: anything else refuses to start.
    import pytest
    from codoc.mcp import server
    monkeypatch.setenv("CODOC_AGENT", "../Cursor")
    with pytest.raises(SystemExit, match="CODOC_AGENT"):
        server.main()
    assert actor_for("agent-../Cursor") is None


def test_an_unbindable_symbol_is_refused_with_the_reason(tmp_path):
    """A symbol the index's walk leaves out is defined but cannot be bound, and
    the refusal says why: under an excluded directory (tests/ by default),
    under a symlinked directory the walk does not enter, in an excluded file,
    or in a file too large to index. A symbol no file defines is refused as
    undefined."""
    cd, ws = workspace(tmp_path)
    ws.close()
    (tmp_path / "c.py").write_text("def send():\n    return 1\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_lists.py").write_text("def test_natural_list_conjunction():\n    pass\n")
    (tmp_path / "test_root.py").write_text("def test_root():\n    pass\n")
    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "lib.py").write_text("def f():\n    return 2\n")
    (tmp_path / "linked").symlink_to(tmp_path / "real", target_is_directory=True)
    (tmp_path / "bundle.py").write_text("def big():\n    return 1\n" + "#" * 1_500_000)

    def attach(sym):
        try:
            return tools.reflect(str(cd), ops=[{"kind": "attach", "feature_id": "h1", "bindings": [sym]}],
                                 root_dir=str(tmp_path))
        finally:
            client.release(cd)
    got = {sym: attach(sym) for sym in ("tests/test_lists.py::test_natural_list_conjunction", "test_root.py::test_root",
                                        "linked/lib.py::f", "bundle.py::big", "c.py::nope")}
    assert not any(r["ok"] for r in got.values()), got
    assert "is under tests/, a directory codoc does not index (index_exclude in .codoc/config.json" in \
        got["tests/test_lists.py::test_natural_list_conjunction"]["error"]
    assert "test_root.py is a file codoc does not index (index_exclude" in got["test_root.py::test_root"]["error"]
    assert "is under linked/, a symlinked directory the index does not enter" in got["linked/lib.py::f"]["error"]
    assert "bundle.py is over 1,500,000 bytes" in got["bundle.py::big"]["error"]
    assert all("does not define" not in r["error"] for sym, r in got.items() if sym != "c.py::nope")
    assert "the code does not define c.py::nope" in got["c.py::nope"]["error"]
