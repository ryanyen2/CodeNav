"""Review-6 probes for the hub (`codoc/serve/*`) and the daemon facts the two
surfaces depend on. Every test asserts the CORRECT behaviour, so a failing test
is a confirmed fault. Two tests write fixtures for the vitest file
(`probe6_editor.test.ts`), and one consumes what the vitest fuzz wrote.

Run order, from the worktree:

    P=../CodeNav/.venv/bin/python; S=<scratchpad>
    PYTHONPATH=. $P -m pytest $S/probe6_hub.py -q -p no:cacheprovider        # writes fixtures; the round-trip consumer skips
    (cd vscode-codoc && npx vitest run --config $S/vitest.probe6.config.mjs)  # fuzz writes probe6_fixtures/roundtrip/out/*.json
    PYTHONPATH=. $P -m pytest $S/probe6_hub.py -q -p no:cacheprovider -k roundtrip   # the consumer now runs
"""
from __future__ import annotations

import json
import os
import random
import shutil
import warnings
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from codoc.calculus import ops as K
from codoc.calculus.apply import apply
from codoc.calculus.store import Store
from codoc.calculus.window import history_window
from codoc.doclang import write_config
from codoc.serve import realize_agent
from codoc.serve.app import SHELL, build_app, client_name
from codoc.serve.auth import COOKIE_NAME, MAINTAINER, SUGGEST, AuthContext, SessionStore, read_roles
from codoc.serve.push import status_message
from codoc.serve.realize_agent import realize_tool_policy
from codoc.serve.worker import Request, process_ready, realize_one
from codoc.substrate import log as L
from codoc.substrate.log import Submission
from codoc.substrate.publish import publish
from codoc.substrate.workspace import Workspace, _wire
from tests.loops.test_reflect import seed
from tests.serve.test_hub import FakeOAuth, FakeResolver
from tests.serve.test_worker import ready

FIX = Path("/private/tmp/claude-501/-Users-r4yen-Desktop-Research-CoDoc-repos-CodeNav/d28f4a40-c80f-4f4e-b0c9-17b9d836ed30/scratchpad/probe6_fixtures")
H = {"x-codoc-csrf": "1"}


def login(c: TestClient) -> None:
    state = c.get("/auth/login").headers["location"].split("state=")[1].split("&")[0]
    r = c.get("/auth/callback", params={"code": "good", "state": state})
    c.cookies.set(COOKIE_NAME, r.cookies[COOKIE_NAME])


# ── A. the sandbox as wired to the SDK ────────────────────────────────────────

def test_H1_the_sandbox_hook_is_consulted_for_every_tool_it_guards(monkeypatch):
    """`_run_sandboxed_sdk` passes `allowed_tools=[Read, Edit, Write, Glob, Grep,
    WebFetch]` AND `can_use_tool=policy`. In the installed SDK (0.2.148) a
    whole-tool `allowed_tools` entry auto-approves the tool BEFORE the callback
    is consulted (the SDK itself warns: CanUseToolShadowedWarning). So the only
    tool the policy ever sees is one it does not list — Bash — and a Read of
    ~/.config/gh/hosts.yml or a Write anywhere on disk is never asked about."""
    import claude_agent_sdk
    from claude_agent_sdk.types import _configure_can_use_tool, _get_can_use_tool_shadowed_warning

    captured = {}

    async def fake_query(*, prompt, options):
        captured["options"] = options
        if False:
            yield None

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
    policy = realize_tool_policy(["c.py"], consult_allowlist=frozenset(), resolve=lambda h: [], root="/srv/wt")
    ran = realize_agent._run_sandboxed_sdk(prompt="x", worktree_path="/srv/wt", policy=policy)
    assert ran is True and "options" in captured
    opts = captured["options"]
    hooks = opts.hooks or {}
    gated_by_hook = "PreToolUse" in hooks
    shadow = _get_can_use_tool_shadowed_warning(opts.permission_mode, list(opts.allowed_tools))
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        _configure_can_use_tool(opts)
    sdk_warned = [str(x.message) for x in w if "can_use_tool will not be invoked" in str(x.message)]
    assert gated_by_hook or (shadow is None and not sdk_warned), (
        f"the SDK says the policy is never consulted: {shadow or sdk_warned}")


def test_H1b_the_oauth_client_secret_is_scrubbed_from_the_agents_environment(monkeypatch):
    import claude_agent_sdk
    seen = {}

    async def fake_query(*, prompt, options):
        seen["env"] = dict(os.environ)
        if False:
            yield None

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
    monkeypatch.setenv("CODOC_GITHUB_CLIENT_SECRET", "s3cret")
    monkeypatch.setenv("CODOC_GITHUB_TOKEN", "ghp_x")
    realize_agent._run_sandboxed_sdk(prompt="x", worktree_path="/srv/wt", policy=lambda *a: (True, ""))
    assert "CODOC_GITHUB_TOKEN" not in seen["env"]
    assert "CODOC_GITHUB_CLIENT_SECRET" not in seen["env"]


def test_H2_the_worker_pushes_the_branch_before_asking_gh_for_a_pull_request():
    """`gh pr create --help` (2.96): 'When the current branch isn't fully pushed
    to a git remote, a prompt will ask where to push the branch … Use `--head`
    to explicitly skip any forking or pushing behavior.' The worker passes
    `--head` and never runs `git push`, so the branch is not on the remote and
    the PR cannot be created — a transient failure, retried every interval."""
    req = Request("h1", "s:9", "Client", "Retry twice.", ["c.py"])
    calls = []
    run = lambda argv, cwd=None: calls.append(list(argv)) or 0
    res = realize_one(req, "/tmp/wt", run=run, agent=lambda d, w, scope=None: ["c.py"], base="main")
    assert res.ok
    gh_at = next(i for i, a in enumerate(calls) if a[:3] == ["gh", "pr", "create"])
    pushes = [i for i, a in enumerate(calls) if a[:2] == ["git", "push"]]
    assert pushes and pushes[0] < gh_at, f"no `git push` before gh pr create: {calls}"


def test_H3_a_transient_pr_failure_does_not_run_the_agent_again(tmp_path):
    """serve-deployment.md: a git or gh failure 'leaves the request unanswered,
    and the worker comes back to it on the next interval'. Coming back re-runs
    the whole thing — worktree, a fresh sandboxed SDK session (an LLM run), commit —
    every 15 s for as long as gh keeps failing, with no cap and no backoff."""
    cd, ws, ho = ready(tmp_path)
    ws.close()
    agent_runs = []
    run = lambda argv, cwd=None: 1 if argv[0] == "gh" else 0
    agent = lambda d, w, scope=None: agent_runs.append(d["id"]) or ["c.py"]
    for _ in range(3):
        again = []
        process_ready(str(tmp_path), str(cd), run=run, agent=agent, printer=lambda *_: None, retry=again)
        assert again == [ho.id]
    assert len(agent_runs) <= 1, f"the agent ran {len(agent_runs)} times for one unanswered request"


def test_H4_grep_inside_the_worktree_cannot_read_a_file_read_may_not():
    """sandbox.py: 'a Read target inside the worktree that still looks like a
    secret (.env …) is refused, so the repo's own secrets can't be exfiltrated
    into a PR either.' Only `Read` is checked; `Grep` with the same path returns
    the file's matching lines."""
    wt = "/srv/repo/.codoc/worktrees/codoc-realize-s-9"
    policy = realize_tool_policy(["c.py"], consult_allowlist=frozenset(), resolve=lambda h: [], root=wt)
    assert policy("Read", {"file_path": f"{wt}/.env"})[0] is False
    assert policy("Grep", {"pattern": "KEY", "path": f"{wt}/.env"})[0] is False
    assert policy("Grep", {"pattern": "KEY", "path": f"{wt}/.codoc/config.json"})[0] is False


# ── B. sessions, roles, the daemon's view of a contributor ────────────────────

def workspace(tmp_path):
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    publish(cd, ws.store, root_dir=str(tmp_path))
    ws.close()
    return cd


def test_H5_a_demoted_collaborator_loses_maintainer_authority_everywhere(tmp_path):
    """The recheck cadence rewrites `Session.role` and nothing else. The daemon
    applies under the tracked `config.json`, which `record_role` wrote at sign-in
    and nobody rewrites; the page's buttons come from `status.role`, read from
    the same file. So a maintainer demoted to `read` keeps deciding verdicts."""
    cd = workspace(tmp_path)
    perms = {"alice": "admin"}
    auth = AuthContext(SessionStore(), resolver=FakeResolver(perms), oauth=FakeOAuth(), recheck_seconds=0)
    c = TestClient(build_app(str(cd), auth=auth), follow_redirects=False)
    login(c)
    assert read_roles(cd)["human:gh:alice"] == MAINTAINER
    perms["alice"] = "read"                                       # demoted on GitHub
    assert c.get("/api/whoami").json()["role"] == SUGGEST          # the session knows
    assert read_roles(cd)["human:gh:alice"] == SUGGEST, read_roles(cd)     # the daemon must know too
    st = status_message(cd, "human:gh:alice")
    assert st and st["status"]["role"] == SUGGEST                  # …and the page
    # and the applier refuses her verdict
    ws = Workspace(cd)
    pid = ws.open_plan(actor="loop", kind="edit", target="p1")
    c.post("/api/message", headers=H, json={"kind": "submit", "subs": [
        {"cid": "v1", "kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}, "batch": "b"}]})
    ops = ws.drain_inbox()
    ws.close()
    assert [o.conflict for o in ops] == ["authority"]


def hub_ws(tmp_path):
    cd = tmp_path / ".codoc"
    cd.mkdir()
    write_config(cd, roles={"human:gh:alice": SUGGEST, "human:gh:bob": MAINTAINER})
    ws = Workspace(cd)
    seed(ws)
    return cd, ws


def test_H6_a_contributor_can_undo_their_own_typing(tmp_path):
    """09-editor §4: undo emits the recorded inverses of the viewer's newest
    batch. A suggester's typing is wrapped in a plan of theirs; the inverse the
    window records is a bare `text.delete`, which `Roles.policy` refuses for a
    suggester, so ⌘Z in a contributor's browser is an `authority` conflict and a
    reload of the paragraph — every time."""
    cd, ws = hub_ws(tmp_path)
    t = ws.store.blocks["p1"].text()
    L.submit(cd, client_name("alice"), [Submission(cid="a1", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"},
                                                   payload={"after": list(t.last_live_ref()), "text": " More."}, batch="t-1")])
    ops = ws.drain_inbox()
    assert [o.conflict for o in ops] == [None] and ops[0].plan
    w = history_window(ws.store)
    mine = [e for e in w["ops"] if e["actor"] == "human:gh:alice" and e["batch"] == "t-1"]
    assert mine, [e["batch"] for e in w["ops"]]
    inverse = [b for e in reversed(mine) for b in e["inverse"]]
    L.submit(cd, client_name("alice"), [Submission(cid=f"u{i}", kind=b["kind"], target=b["target"], payload=b["payload"], batch="u-t-1")
                                        for i, b in enumerate(inverse)])
    undo = ws.drain_inbox()
    ws.close()
    assert [o.conflict for o in undo] == [None] * len(inverse), [(o.kind, o.conflict) for o in undo]


def test_H7_the_hub_page_cannot_be_framed(tmp_path):
    """app.ts trusts a message whose `source` is `window.parent` (a VS Code
    webview needs that). On the hub `window.parent` is whoever frames the tab;
    the shell's CSP has no `frame-ancestors` and no X-Frame-Options is sent, so
    a same-site page (lax cookies travel) can frame the hub and post `history`
    into it."""
    cd = workspace(tmp_path)
    c = TestClient(build_app(str(cd)))
    r = c.get("/")
    csp = SHELL.lower()
    assert "frame-ancestors" in csp or r.headers.get("x-frame-options", "").upper() in ("DENY", "SAMEORIGIN"), \
        (SHELL[:400], dict(r.headers))


def test_H8_a_contributor_cannot_cite_a_directive_they_did_not_realize(tmp_path):
    """`handle_message` copies `caused_by` from the browser into the submission
    and the daemon stamps it as recorded. `caused_by` is how the ledger says
    'this op is what the hand-off produced' (loops/realize.settle reads it on
    bind ops; the provenance card shows it). A suggester's text op citing a
    directive id is a forged provenance link, accepted without a look."""
    cd, ws = hub_ws(tmp_path)
    t = ws.store.blocks["p1"].text()
    auth = AuthContext(SessionStore(), resolver=FakeResolver({"alice": "read"}), oauth=FakeOAuth())
    c = TestClient(build_app(str(cd), auth=auth), follow_redirects=False)
    login(c)
    c.post("/api/message", headers=H, json={"kind": "submit", "subs": [
        {"cid": "f1", "kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
         "payload": {"after": list(t.last_live_ref()), "text": " forged"}, "batch": "b", "caused_by": "s:forged-directive"}]})
    ops = ws.drain_inbox()
    ws.close()
    assert ops and ops[0].caused_by in (None, ""), ops[0].caused_by


# ── C. fixtures for the timeline fold (vitest §T) ─────────────────────────────

def _page(store: Store) -> list[list]:
    return [[b.id, b.kind, b.level if b.kind == "heading" else 0, b.text().live_text()] for b in store.ordered() if b.kind != "comment"]


def _prefix_pages(store: Store, window: dict) -> list[list[list]]:
    """The page right after each window entry, from a prefix replay of the store's
    own ops — the truth the reader's fold must reproduce."""
    idx = {o.id: i for i, o in enumerate(store.ops)}
    out = []
    for e in window["ops"]:
        s2 = Store()
        for op in store.ops[: idx[e["id"]] + 1]:
            if op.conflict is None:
                apply(s2, _wire(op))
        out.append(_page(s2))
    return out


def _tree(ws: Workspace, n_features: int = 3, paras: int = 2) -> None:
    after = "START"
    for f in range(n_features):
        h = f"h{f}"
        ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": h}, "payload": {"kind": "heading", "level": 1, "after": after}},
                   {"kind": K.TEXT_INSERT, "target": {"bid": h, "path": "text"}, "payload": {"after": "START", "text": f"Feature {f}"}}],
                  actor="human:local")
        after = h
        for p in range(paras):
            pid = f"p{f}{p}"
            ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": pid}, "payload": {"kind": "paragraph", "after": after}},
                       {"kind": K.TEXT_INSERT, "target": {"bid": pid, "path": "text"}, "payload": {"after": "START", "text": f"para {f}.{p} words here"}}],
                      actor="human:local")
            after = pid


def _random_ops(ws: Workspace, rng: random.Random, steps: int) -> None:
    last_batch: str | None = None
    n = 0
    for _ in range(steps):
        live = [b for b in ws.store.ordered() if b.kind != "comment"]
        paras = [b for b in live if b.kind == "paragraph"]
        heads = [b for b in live if b.kind == "heading"]
        g = rng.random()
        b = None
        if g < 0.3 and paras:
            p = rng.choice(paras); t = p.text()
            at = list(t.last_live_ref()) if t.live_text() and rng.random() < 0.5 else "START"
            if at != "START" and rng.random() < 0.5:
                at = list(t.ref_at(rng.randrange(len(t.live_text()))))
            body = [{"kind": K.TEXT_INSERT, "target": {"bid": p.id, "path": "text"}, "payload": {"after": at, "text": f"w{n} "}}]
        elif g < 0.45 and paras:
            p = rng.choice(paras); s = p.text().live_text()
            if len(s) < 2:
                continue
            a = rng.randrange(len(s) - 1); z = rng.randrange(a, len(s))
            body = [{"kind": K.TEXT_DELETE, "target": {"bid": p.id, "path": "text"}, "payload": {"from": list(p.text().ref_at(a)), "to": list(p.text().ref_at(z))}}]
        elif g < 0.58 and paras:
            p = rng.choice(paras); s = p.text().live_text()
            off = rng.randrange(len(s) + 1)
            at = "START" if off == 0 else list(p.text().ref_at(off - 1))
            body = [{"kind": K.BLOCK_SPLIT, "target": {"bid": p.id, "path": "text"}, "payload": {"at": at, "new": f"n{n}"}}]
        elif g < 0.7:
            pairs = [(live[i], live[i + 1]) for i in range(len(live) - 1) if live[i].kind == "paragraph" and live[i + 1].kind == "paragraph"]
            if not pairs:
                continue
            a, z = rng.choice(pairs)
            body = [{"kind": K.BLOCK_MERGE, "target": {"into": a.id, "bid": z.id, "path": "text"}, "payload": {}}]
        elif g < 0.8 and len(heads) > 1:
            h = rng.choice(heads)
            others = [x for x in live if x.id not in ws.store.dominated_ids(h.id)]
            after = rng.choice(others).id if others and rng.random() < 0.7 else "START"
            body = [{"kind": K.BLOCK_MOVE, "target": {"bid": h.id}, "payload": {"after": after}}]
        elif g < 0.9 and len(paras) > 1:
            body = [{"kind": K.BLOCK_DELETE, "target": {"bid": rng.choice(paras).id}, "payload": {}}]
        elif last_batch:
            ops = [o for o in ws.store.ops if o.batch == last_batch and o.conflict is None]
            inv = [x for o in reversed(ops) for x in o.inverse]
            if not inv:
                continue
            ws.submit(inv, actor="human:local", batch=f"u-{last_batch}")
            last_batch = None
            n += 1
            continue
        else:
            continue
        n += 1
        last_batch = f"b{n}"
        ws.submit(body, actor="human:local", batch=last_batch)


def _write_timeline_fixture(tmp_path, name: str, build, limit: int | None = None) -> dict:
    cd = tmp_path / f"{name}.codoc"
    ws = Workspace(cd)
    build(ws)
    publish(cd, ws.store, root_dir=str(tmp_path))
    w = history_window(ws.store, limit) if limit else json.loads((cd / "history.json").read_text())
    fixture = {"history": w, "projection": json.loads((cd / "projection.json").read_text()),
               "pages": _prefix_pages(ws.store, w), "now": _page(ws.store)}
    (FIX / "timeline").mkdir(parents=True, exist_ok=True)
    (FIX / "timeline" / f"{name}.json").write_text(json.dumps(fixture))
    ws.close()
    return fixture


def test_T1_fixture_scripted_delete_merge_move_undo(tmp_path):
    def build(ws):
        _tree(ws, 3, 2)
        ws.submit([{"kind": K.BLOCK_DELETE, "target": {"bid": "p01"}, "payload": {}}], actor="human:local", batch="b-del")
        ws.submit([{"kind": K.BLOCK_MERGE, "target": {"into": "p10", "bid": "p11", "path": "text"}, "payload": {}}], actor="human:local", batch="b-merge")
        ws.submit([{"kind": K.BLOCK_MOVE, "target": {"bid": "h2"}, "payload": {"after": "START"}}], actor="human:local", batch="b-move")
        # undo the delete: the recorded inverse
        dele = [o for o in ws.store.ops if o.batch == "b-del"][0]
        ws.submit(list(dele.inverse), actor="human:local", batch="u-b-del")
        ws.submit([{"kind": K.BLOCK_DELETE, "target": {"bid": "h1"}, "payload": {}}], actor="human:local", batch="b-delh")
        t = ws.store.blocks["p20"].text()
        ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p20", "path": "text"}, "payload": {"after": list(t.last_live_ref()), "text": " tail"}}], actor="human:local")
    f = _write_timeline_fixture(tmp_path, "scripted", build)
    assert len(f["pages"]) == len(f["history"]["ops"])


@pytest.mark.parametrize("seed_n", [1, 2, 3, 4])
def test_T2_fixture_random(tmp_path, seed_n):
    rng = random.Random(seed_n)
    f = _write_timeline_fixture(tmp_path, f"random{seed_n}", lambda ws: (_tree(ws, 3, 2), _random_ops(ws, rng, 40)))
    assert f["pages"]


def test_T3_fixture_window_entering_mid_history(tmp_path):
    """A window that begins after the tree existed: `before`, `dead` and `ranks`
    entering it all carry weight."""
    rng = random.Random(9)
    f = _write_timeline_fixture(tmp_path, "midwindow", lambda ws: (_tree(ws, 3, 2), _random_ops(ws, rng, 40)), limit=15)
    assert f["history"]["start"] > 0


# ── D. the round trip: the real editor's ops through the real applier ──────────

def _base_ws(cd: Path) -> Workspace:
    ws = Workspace(cd)
    ws.submit([
        {"kind": K.BLOCK_ADD, "target": {"bid": "h1"}, "payload": {"kind": "heading", "level": 1, "after": "START"}},
        {"kind": K.TEXT_INSERT, "target": {"bid": "h1", "path": "text"}, "payload": {"after": "START", "text": "Client"}},
        {"kind": K.BLOCK_ADD, "target": {"bid": "p1"}, "payload": {"kind": "paragraph", "after": "h1"}},
        {"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": "START", "text": "Retries once before giving up."}},
        {"kind": K.BLOCK_ADD, "target": {"bid": "p2"}, "payload": {"kind": "paragraph", "after": "p1"}},
        {"kind": K.TEXT_INSERT, "target": {"bid": "p2", "path": "text"}, "payload": {"after": "START", "text": "Second paragraph here."}},
        {"kind": K.BLOCK_ADD, "target": {"bid": "h2"}, "payload": {"kind": "heading", "level": 1, "after": "p2"}},
        {"kind": K.TEXT_INSERT, "target": {"bid": "h2", "path": "text"}, "payload": {"after": "START", "text": "Server"}},
        {"kind": K.BLOCK_ADD, "target": {"bid": "p3"}, "payload": {"kind": "paragraph", "after": "h2"}},
        {"kind": K.TEXT_INSERT, "target": {"bid": "p3", "path": "text"}, "payload": {"after": "START", "text": "Listens on a port."}},
        {"kind": K.BLOCK_ADD, "target": {"bid": "h3"}, "payload": {"kind": "heading", "level": 2, "after": "p3"}},
        {"kind": K.TEXT_INSERT, "target": {"bid": "h3", "path": "text"}, "payload": {"after": "START", "text": "Sub"}},
        {"kind": K.BLOCK_ADD, "target": {"bid": "p4"}, "payload": {"kind": "paragraph", "after": "h3"}},
        {"kind": K.TEXT_INSERT, "target": {"bid": "p4", "path": "text"}, "payload": {"after": "START", "text": "A child feature."}},
        {"kind": K.BLOCK_ADD, "target": {"bid": "h4"}, "payload": {"kind": "heading", "level": 1, "after": "p4"}},
        {"kind": K.TEXT_INSERT, "target": {"bid": "h4", "path": "text"}, "payload": {"after": "START", "text": "Tail"}},
        {"kind": K.BLOCK_ADD, "target": {"bid": "p5"}, "payload": {"kind": "paragraph", "after": "h4"}},
        {"kind": K.TEXT_INSERT, "target": {"bid": "p5", "path": "text"}, "payload": {"after": "START", "text": "Last words."}},
    ], actor="human:local")
    return ws


def test_R0_write_the_round_trip_base(tmp_path):
    base = FIX / "roundtrip"
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True)
    cd = tmp_path / ".codoc"
    ws = _base_ws(cd)
    publish(cd, ws.store, root_dir=str(tmp_path))
    ws.close()
    shutil.copytree(cd, base / "base.codoc")
    shutil.copy(cd / "projection.json", base / "projection.json")
    (base / "out").mkdir()


def _out_files():
    d = FIX / "roundtrip" / "out"
    return sorted(d.glob("*.json")) if d.exists() else []


@pytest.mark.parametrize("path", _out_files() or [pytest.param(None, marks=pytest.mark.skip(reason="run the vitest fuzz first"))])
def test_R1_the_real_editors_ops_replay_to_the_page_it_shows(tmp_path, path):
    """The vitest fuzz drove the real editor from the base projection, flushed
    every submission in order, and wrote what its page finally read. Draining
    those submissions through the real applier must produce the same blocks,
    kinds, levels and words, and every ack must be clean — anything else is the
    page and the store saying different things."""
    rec = json.loads(Path(path).read_text())
    cd = tmp_path / ".codoc"
    shutil.copytree(FIX / "roundtrip" / "base.codoc", cd)
    L.submit(cd, "ide", [Submission(cid=s["cid"], kind=s["kind"], target=s["target"], payload=s["payload"],
                                    batch=s.get("batch") or "", plan=s.get("plan")) for s in rec["subs"]])
    ws = Workspace(cd)
    ops = ws.drain_inbox()
    acks = [json.loads(l) for l in (L.inbox_dir(cd) / "ide.ack.jsonl").read_text().splitlines()]
    got = _page(ws.store)
    ws.close()
    bad = [(a["cid"], a["conflict"]) for a in acks if a.get("conflict")]
    want = rec["expect"]
    assert bad == [], f"seed {rec['seed']}: refused {bad}\n gestures: {rec['log']}"
    assert got == want, f"seed {rec['seed']}\n gestures: {rec['log']}\n store: {got}\n page:  {want}"
