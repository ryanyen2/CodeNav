"""Review-7 probes for the hub (`codoc/serve/*`), the transport both surfaces
share, and the two round trips. Every test asserts the CORRECT behaviour, so a
failing test is a confirmed fault (or, where the docstring says so, a design
decision the review questions).

From the worktree:

    P=../CodeNav/.venv/bin/python; S=<scratchpad>
    PYTHONPATH=. $P -m pytest $S/probe7_hub.py -q -p no:cacheprovider

`test_R1` and `test_R2` drive vitest themselves (`probe7_editor.test.ts`, §R)
and drain what it writes through the real applier; they need the extension's
`node_modules`.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from codoc.calculus import ops as K
from codoc.doclang import read_config, write_config
from codoc.serve import realize_agent
from codoc.serve.app import build_app, client_name
from codoc.serve.auth import MAINTAINER, NONE, SUGGEST, record_role
from codoc.serve.push import Feed
from codoc.serve.realize_agent import realize_tool_policy
from codoc.serve.worker import process_ready
from codoc.substrate import log as L
from codoc.substrate.log import Submission
from codoc.substrate.publish import publish
from codoc.substrate.workspace import Roles, Workspace
from tests.loops.test_reflect import seed
from tests.serve.test_worker import ready

SCRATCH = Path("/private/tmp/claude-501/-Users-r4yen-Desktop-Research-CoDoc-repos-CodeNav/d28f4a40-c80f-4f4e-b0c9-17b9d836ed30/scratchpad")
FIX = SCRATCH / "probe7_fixtures"
WT = Path("/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate")
EXT = WT / "vscode-codoc"
H = {"x-codoc-csrf": "1"}


def workspace(tmp_path):
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    publish(cd, ws.store, root_dir=str(tmp_path))
    ws.close()
    return cd


def hub_ws(tmp_path):
    cd = tmp_path / ".codoc"
    cd.mkdir()
    write_config(cd, roles={"human:gh:alice": SUGGEST, "human:gh:bob": MAINTAINER})
    ws = Workspace(cd)
    seed(ws)
    return cd, ws


# ── A. the transport both surfaces share ──────────────────────────────────────

def test_H1_a_connected_viewer_keeps_receiving_acks_after_the_daemon_trims_the_ack_file(tmp_path):
    """`log.rotate_acks` rewrites `<client>.ack.jsonl` to its newest 2,000 lines
    at every daemon start. `push.Feed` (and `provider.ts`'s `readFrom`) tail the
    file by BYTE OFFSET; when the file shrinks under them the offset points past
    its end, `read()` returns nothing, and every ack written until the file grows
    back past the old offset is never delivered — those blocks stay held and
    ⌘Z waits for ever. A tail must notice `size < offset`."""
    cd = workspace(tmp_path)
    client = "hub-alice"
    for i in range(2100):
        L.ack(cd, client, f"c{i}", error=None)
    feed = Feed(cd, client)
    feed.pending()                                       # the snapshot, and the 200 replayed acks
    L.rotate_acks(cd, keep=2000)                         # the daemon restarted
    L.ack(cd, client, "fresh")
    got = [a["cid"] for m in feed.pending() if m["kind"] == "acks" for a in m["acks"]]
    assert "fresh" in got, got


def test_H2_the_feed_delivers_acks_before_the_projection_that_followed_them(tmp_path):
    """The daemon acks during the drain and publishes afterwards, so on disk the
    acks precede the projection. `Feed.pending` sends the projection FIRST. The
    editor adopts a projection per block and skips every block it holds; the
    hold is lifted by the ack that arrives next, and nothing re-reads the
    projection (probe7_editor E1). Sending them in disk order is the transport's
    half of that fault."""
    cd = workspace(tmp_path)
    feed = Feed(cd, "ide")
    feed.pending()
    ws = Workspace(cd)
    t = ws.store.blocks["p1"].text()
    L.submit(cd, "ide", [Submission(cid="k1", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"},
                                    payload={"after": list(t.last_live_ref()), "text": " More."}, batch="b1")])
    ws.drain_inbox()                                     # acks k1
    publish(cd, ws.store, root_dir=str(tmp_path))        # then publishes
    ws.close()
    kinds = [m["kind"] for m in feed.pending()]
    assert "acks" in kinds and "projection" in kinds, kinds
    assert kinds.index("acks") < kinds.index("projection"), kinds


# ── B. what a signed-in `suggest` contributor can still reach ─────────────────

def test_H3_a_contributor_cannot_resolve_or_rewrite_somebody_elses_comment_thread(tmp_path):
    """`Roles.policy` lets any actor write untagged inside any comment block
    ("a contributor can edit inside another person's comment thread"), and
    comments.ts offers Resolve to every viewer. So a `suggest` login can mark a
    maintainer's request as resolved — the state `Build it` and the margin read —
    and delete the words of the request. Editing INSIDE a thread should mean
    replying in it, not rewriting or closing what someone else asked."""
    cd, ws = hub_ws(tmp_path)
    t = ws.store.blocks["p1"].text()
    ws.submit([
        {"kind": K.BLOCK_ADD, "target": {"bid": "c1"}, "payload": {"kind": "comment", "after": "p1",
                                                                  "content": {"thread": "c1", "anchor": "p1", "anchor_text": "Retries", "scope": "code", "status": "open"}}},
        {"kind": K.TEXT_INSERT, "target": {"bid": "c1", "path": "body"}, "payload": {"after": "START", "text": "make it retry twice"}},
        {"kind": K.TEXT_MARK, "target": {"bid": "p1", "path": "text"}, "payload": {"kind": "comment", "attrs": {"block": "c1"},
                                                                                  "start": list(t.ref_at(0)), "end": list(t.last_live_ref()), "on": True}},
    ], actor="human:gh:bob")
    body = ws.store.blocks["c1"].content["body"]
    first, last = body.ref_at(0), body.last_live_ref()
    L.submit(cd, client_name("alice"), [
        Submission(cid="r1", kind=K.BLOCK_SET, target={"bid": "c1", "path": "status"}, payload={"value": "resolved"}, batch="b"),
        Submission(cid="r2", kind=K.TEXT_DELETE, target={"bid": "c1", "path": "body"}, payload={"from": list(first), "to": list(last)}, batch="b"),
    ])
    ops = ws.drain_inbox()
    status = ws.store.blocks["c1"].content.get("status")
    ws.close()
    assert [o.conflict for o in ops] == ["authority", "authority"], [(o.kind, o.conflict) for o in ops]
    assert status == "open"


def test_H5_a_glob_pattern_is_a_path_the_sandbox_must_confine(tmp_path):
    """`sandbox.can_use_tool` confines `file_path`/`path`; `Glob` takes its
    target in `pattern` as well, and an absolute pattern lists files anywhere
    (`Read` of them is still refused, so this leaks names, not contents)."""
    policy = realize_tool_policy(["c.py"], consult_allowlist=frozenset(), resolve=lambda h: [], root=str(tmp_path))
    ok, _ = policy("Glob", {"pattern": "/Users/*/.config/gh/*"})
    assert ok is False
    ok, _ = policy("Glob", {"pattern": "**/*.py"})
    assert ok is True


def test_H6_a_revoked_collaborator_recorded_as_none_is_not_a_suggester(tmp_path):
    """`auth.session` records `none` in the tracked roles table on revocation
    (review 6 A4). `Roles.policy` knows owner, maintainer, machine and
    "everyone else" — and `none` is everyone else, so the record that says a
    person was removed grants them exactly what a contributor has: plans and
    comments. An inbox line of theirs drained after the revocation, or a clone
    validating their pull request's log, is judged as a suggester's."""
    cd = tmp_path / ".codoc"
    cd.mkdir()
    record_role(cd, "eve", NONE)
    roles = Roles.load(cd)
    assert roles.role("human:gh:eve") == NONE
    ws = Workspace(cd, roles=roles)
    seed(ws)
    L.submit(cd, client_name("eve"), [
        Submission(cid="e1", kind=K.PLAN_OPEN, target={"pid": "pl-eve"}, payload={"kind": "edit", "target": "p1", "note": "hi"}, batch="pl-eve"),
        Submission(cid="e2", kind=K.BLOCK_ADD, target={"bid": "ce"}, payload={"kind": "comment", "after": "p1",
                                                                             "content": {"thread": "ce", "anchor": "p1", "anchor_text": "", "scope": "code", "status": "open"}}, batch="b"),
    ])
    ops = ws.drain_inbox()
    ws.close()
    assert all(o.conflict for o in ops), [(o.kind, o.conflict) for o in ops]


# ── C. the worker against a hub restart ───────────────────────────────────────

def test_H4_a_branch_the_agent_already_built_is_published_not_deleted_and_rebuilt(tmp_path):
    """The `built:` ledger entry is written AFTER `git commit`, `git push` and
    `gh pr create` have all been tried. A hub that dies between the commit and
    that write leaves the branch on disk and nothing in the ledger. The next
    pass runs `git worktree add -b <branch>`, which fails because the branch
    exists, calls it transient, and `_cleanup` runs `git branch -D` on it — the
    agent's committed work is deleted, and the pass after that pays for a fresh
    model session. A branch that already exists by the hand-off's name IS the
    built work: resume from the push."""
    cd, ws, ho = ready(tmp_path)
    ws.close()
    from codoc.serve.worker import branch_name
    branch = branch_name(ho.id)
    branches = {branch}                                   # left behind by the crashed pass
    calls: list[list[str]] = []

    def run(argv, cwd=None):
        calls.append(list(argv))
        if argv[:3] == ["git", "worktree", "add"]:
            if "-b" in argv:
                b = argv[argv.index("-b") + 1]
                if b in branches:
                    return 128                            # fatal: a branch named '…' already exists
                branches.add(b)
                return 0
            return 0 if argv[-1] in branches else 128
        if argv[:2] == ["git", "branch"] and "-D" in argv:
            branches.discard(argv[-1])
            return 0
        return 0

    agent_runs: list[str] = []
    agent = lambda d, w, scope=None: agent_runs.append(d["id"]) or ["c.py"]
    for _ in range(2):
        process_ready(str(tmp_path), str(cd), run=run, agent=agent, printer=lambda *_: None, retry=[])
    deleted = [a for a in calls if a[:2] == ["git", "branch"] and "-D" in a and a[-1] == branch]
    pushed = [a for a in calls if a[:2] == ["git", "push"]]
    assert deleted == [], f"the built branch was deleted: {deleted}"
    assert agent_runs == [], f"the agent ran again for work that was already committed: {agent_runs}"
    assert pushed, "the existing branch was never pushed"


# ── D. confirmed correct (probed) ─────────────────────────────────────────────

def test_H7_setting_the_language_on_the_hub_keeps_the_roles_table(tmp_path):
    cd = workspace(tmp_path)
    write_config(cd, roles={"human:gh:alice": SUGGEST})
    c = TestClient(build_app(str(cd)))
    assert c.post("/api/message", headers=H, json={"kind": "set-language", "tag": "ja"}).json() == {"ok": True}
    cfg = read_config(cd)
    assert cfg["doc_language"] == "ja" and cfg["roles"] == {"human:gh:alice": SUGGEST}


def test_H8_the_hook_denies_every_tool_the_sandbox_does_not_name_and_the_sdk_accepts_its_shape(monkeypatch, tmp_path):
    import asyncio
    import claude_agent_sdk
    from claude_agent_sdk.types import _hooks_to_internal_format
    captured = {}

    def fake_query(*, prompt, options):
        captured["options"] = options

        async def none():
            return
            yield
        return none()
    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
    wt = tmp_path / "wt"
    (wt / "src").mkdir(parents=True)
    (wt / "src" / "c.py").write_text("x = 1\n")
    policy = realize_tool_policy(["src/c.py"], consult_allowlist=frozenset(), resolve=lambda h: [], root=str(wt))
    assert realize_agent._run_sandboxed_sdk(prompt="x", worktree_path=str(wt), policy=policy) is True
    opts = captured["options"]
    internal = _hooks_to_internal_format(opts.hooks)
    assert "PreToolUse" in internal and internal["PreToolUse"]
    hook = opts.hooks["PreToolUse"][0].hooks[0]
    decide = lambda name, inp: asyncio.run(hook({"tool_name": name, "tool_input": inp}, None, None))["hookSpecificOutput"]["permissionDecision"]
    assert decide("Agent", {"prompt": "go"}) == "deny"                       # the subagent tool's current name; DENIED_TOOLS lists only `Task`
    assert decide("mcp__github__create_pr", {}) == "deny"
    assert decide("Bash", {"command": "gh auth token"}) == "deny"
    assert decide("WebFetch", {"url": "https://docs.python.org/3/"}) == "deny"   # empty allowlist
    assert decide("Edit", {"file_path": str(wt / "src" / "c.py"), "old_string": "1", "new_string": "2"}) == "allow"
    assert decide("Write", {"file_path": str(wt / "src" / "new.py"), "content": ""}) == "deny"    # not a bound file
    assert decide("Read", {"file_path": str(wt / ".codoc" / "config.json")}) == "deny"
    assert decide("Read", {"file_path": str(wt / "src" / "c.py")}) == "allow"
    # `setting_sources` is unset, so the worktree's own .claude/settings.json / .mcp.json / CLAUDE.md are not loaded.
    assert not getattr(opts, "setting_sources", None)


def test_H9_a_symlink_in_the_media_dir_does_not_serve_a_file_outside_it(tmp_path):
    cd = workspace(tmp_path)
    (cd / "media").mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("token")
    os.symlink(secret, cd / "media" / "x.txt")
    c = TestClient(build_app(str(cd)))
    assert c.get("/api/media/x.txt").status_code == 404
    r = c.get("/api/media/..%2Fconfig.json")
    assert r.status_code != 200 or r.headers["content-type"].startswith("text/html"), (r.status_code, r.text[:80])   # the shell, never the file


# ── R. round trips ────────────────────────────────────────────────────────────

def _vitest(phase: str, pattern: str, d: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "CODOC_P7_DIR": str(d), "CODOC_P7_PHASE": phase}
    return subprocess.run(["npx", "vitest", "run", "--config", str(SCRATCH / "vitest.probe7.config.mjs"), "-t", pattern],
                          cwd=EXT, env=env, capture_output=True, text=True, timeout=1200)


def _base(cd: Path, roles: dict | None = None) -> Workspace:
    cd.mkdir(parents=True)
    if roles:
        write_config(cd, roles=roles)
    ws = Workspace(cd)
    after = "START"
    for bid, kind, level, text in [
        ("h1", "heading", 1, "Client"), ("p1", "paragraph", 0, "Retries once before giving up."),
        ("p2", "paragraph", 0, "Second paragraph here."), ("h2", "heading", 1, "Server"),
        ("p3", "paragraph", 0, "Listens on a port."), ("h3", "heading", 2, "Sub"),
        ("p4", "paragraph", 0, "A child feature."), ("h4", "heading", 1, "Tail"),
        ("p5", "paragraph", 0, "Last words."),
    ]:
        ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": bid}, "payload": {"kind": kind, "level": level, "after": after}},
                   {"kind": K.TEXT_INSERT, "target": {"bid": bid, "path": "text"}, "payload": {"after": "START", "text": text}}], actor="human:local")
        after = bid
    return ws


def _page(store) -> list[list]:
    return [[b.id, b.kind, b.level if b.kind == "heading" else 0, b.text().live_text()] for b in store.ordered() if b.kind != "comment"]


def _drain(base_cd: Path, wd: Path, subs: list[dict], root: Path):
    shutil.copytree(base_cd, wd)
    L.submit(wd, "ide", [Submission(cid=s["cid"], kind=s["kind"], target=s["target"], payload=s["payload"],
                                    batch=s.get("batch") or "", plan=s.get("plan")) for s in subs])
    w = Workspace(wd)
    w.drain_inbox()
    acks = [json.loads(line) for line in (L.inbox_dir(wd) / "ide.ack.jsonl").read_text().splitlines()]
    acks = acks[-len(subs):] if subs else []
    publish(wd, w.store, root_dir=str(root))
    return w, acks


needs_node = pytest.mark.skipif(not (EXT / "node_modules" / "vitest").exists(), reason="no node_modules")


@needs_node
def test_R1_single_actor_more_gestures(tmp_path):
    """Review 6's round trip with four more gestures: a comment thread on a
    span (the anchor refs may be provisional), replace-all through the Find
    widget (one transaction, many steps, many blocks), a heading pasted (which
    `pasteBlocks` flattens), and Tab on a paragraph. Every ack clean, the store's
    page equal to the editor's, and every comment's anchor mark on a live block."""
    d = FIX / "r1"
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    cd = tmp_path / "base" / ".codoc"
    ws = _base(cd)
    publish(cd, ws.store, root_dir=str(tmp_path / "base"))
    ws.close()
    shutil.copy(cd / "projection.json", d / "projection.json")
    run = _vitest("R1", "R1:", d)
    files = sorted(d.glob("r1-seed*.json"))
    assert run.returncode == 0 and files, f"the fuzz did not run:\n{run.stdout[-4000:]}\n{run.stderr[-3000:]}"
    problems = []
    counts: dict[str, int] = {}
    for f in files:
        rec = json.loads(f.read_text())
        for s in rec["subs"]:
            counts[s["kind"]] = counts.get(s["kind"], 0) + 1
        w, acks = _drain(cd, tmp_path / f"r1-{rec['seed']}" / ".codoc", rec["subs"], tmp_path)
        refused = [(a["cid"], a["conflict"]) for a in acks if a.get("conflict")]
        got = _page(w.store)
        # every comment thread the editor opened is anchored on a live block by its mark
        for s in rec["subs"]:
            if s["kind"] == K.BLOCK_ADD and s["payload"].get("kind") == "comment":
                cb = w.store.blocks.get(s["target"]["bid"])
                if cb is None:
                    problems.append(f"seed {rec['seed']}: comment {s['target']['bid']} not in the store")
                    continue
                anchored = any(m.kind == "comment" and m.attrs.get("block") == cb.id
                               for b in w.store.blocks.values() if b.live and b.kind != "comment" and b.content.get("text") is not None
                               for m in b.text().marks)
                if not anchored:
                    problems.append(f"seed {rec['seed']}: comment {cb.id} has no anchor mark on a live block")
        w.close()
        if refused:
            problems.append(f"seed {rec['seed']} ({' → '.join(rec['log'])}): refused {refused}")
        if got != [list(x) for x in rec["expect"]]:
            problems.append(f"seed {rec['seed']} ({' → '.join(rec['log'])})\n store: {got}\n page:  {rec['expect']}")
    (d / "counts.json").write_text(json.dumps(counts, indent=1, sort_keys=True))
    assert problems == [], "\n".join(problems)


@needs_node
def test_R2_two_actors_and_a_plan_arriving_mid_edit(tmp_path):
    """Phase A: the editor makes N gestures from projection P1 and records its
    submissions. Meanwhile Bob (a maintainer) appended to p1, put a paragraph
    after p2 and prefixed p5, and an agent opened a plan proposing words in p4:
    that store's projection is P2. The editor's ops are drained into that store
    (Bob first, then the person) and P3 is published. Phase B replays the same
    gestures (seeded `Math.random`, so bids and batches match), adopts P2 while
    every block it touched is still held, acks every submission with the real
    acks, then adopts P3. The page after P3 must be the store's page, and no
    shadow may disagree with the page at any of the three points."""
    d = FIX / "r2"
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    roles = {"human:gh:bob": MAINTAINER}
    cd = tmp_path / "base" / ".codoc"
    ws = _base(cd, roles)
    publish(cd, ws.store, root_dir=str(tmp_path / "base"))
    ws.close()
    shutil.copy(cd / "projection.json", d / "p1.json")
    # Bob and the agent, on a copy: the store the editor's ops will land in.
    cd2 = tmp_path / "base2" / ".codoc"
    shutil.copytree(cd, cd2)
    w2 = Workspace(cd2)
    p1t, p5t = w2.store.blocks["p1"].text(), w2.store.blocks["p5"].text()
    w2.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": list(p1t.last_live_ref()), "text": " Bob says so."}},
               {"kind": K.BLOCK_ADD, "target": {"bid": "p6"}, "payload": {"kind": "paragraph", "after": "p2"}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "p6", "path": "text"}, "payload": {"after": "START", "text": "Bob's new paragraph."}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "p5", "path": "text"}, "payload": {"after": "START", "text": "Bob: "}}], actor="human:gh:bob")
    pid = w2.open_plan(actor="agent:x", kind="edit", target="p4")
    p4t = w2.store.blocks["p4"].text()
    w2.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p4", "path": "text"}, "payload": {"after": list(p4t.last_live_ref()), "text": " (agent proposes this.)"}}],
              actor="agent:x", plan=pid)
    publish(cd2, w2.store, root_dir=str(tmp_path / "base2"))
    w2.close()
    shutil.copy(cd2 / "projection.json", d / "p2.json")
    run = _vitest("R2A", "R2A:", d)
    files = sorted(d.glob("r2a-seed*.json"))
    assert run.returncode == 0 and files, f"phase A did not run:\n{run.stdout[-4000:]}\n{run.stderr[-3000:]}"
    pages3: dict[int, list] = {}
    refusals: list[str] = []
    for f in files:
        rec = json.loads(f.read_text())
        w, acks = _drain(cd2, tmp_path / f"r2-{rec['seed']}" / ".codoc", rec["subs"], tmp_path)
        pages3[rec["seed"]] = _page(w.store)
        bad = [(a["cid"], a["conflict"]) for a in acks if a.get("conflict")]
        if bad:
            refusals.append(f"seed {rec['seed']} ({' → '.join(rec['log'])}): {bad}")
        p3 = json.loads((tmp_path / f"r2-{rec['seed']}" / ".codoc" / "projection.json").read_text())
        w.close()
        (d / f"r2b-seed{rec['seed']:02d}.json").write_text(json.dumps({"acks": acks, "p3": p3, "subs": rec["subs"]}))
    run = _vitest("R2B", "R2B:", d)
    outs = sorted(d.glob("r2c-seed*.json"))
    assert run.returncode == 0 and len(outs) == len(files), f"phase B did not run:\n{run.stdout[-4000:]}\n{run.stderr[-3000:]}"
    problems: list[str] = list(refusals)
    stale_after_ack = 0
    for f in outs:
        rec = json.loads(f.read_text())
        want = pages3[rec["seed"]]
        for k in ("faultsP2", "faultsAck", "faultsP3"):
            if rec[k]:
                problems.append(f"seed {rec['seed']} {k}: {rec[k]}")
        if rec["afterP3"] != want:
            problems.append(f"seed {rec['seed']} ({' → '.join(rec['log'])}) after P3\n store: {want}\n page:  {rec['afterP3']}")
        if rec["afterAck"] != want:
            stale_after_ack += 1
    (d / "summary.json").write_text(json.dumps({"seeds": len(outs), "stale_after_ack": stale_after_ack, "refusals": refusals}, indent=1))
    assert problems == [], "\n".join(problems)
