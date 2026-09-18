# Review of the review-2 fixes: calculus, substrate, loops (implementation 2)

Scope: worktree `CodeNav-substrate`, commit `77f7613` ("Substrate review 2: …"), read
against `01-calculus.md` (rev 2 + edits), `04-version-control.md`, `09-editor.md`,
and `10-substrate-review-2.md`. The 202 shipped tests pass (5.3 s). Every §A item
below was run; the probes are beside this file:

- `probe_substrate3.py` (P1–P15, P17 draft), `probe_substrate3b.py` (P17, P20–P25),
  `probe_substrate3c.py` (P26 I6 fuzz, P27). Each asserts the *correct* behaviour, so
  a failing probe is a confirmed fault; run from the worktree with `PYTHONPATH=.`.
- `perf3.py` (the 1,500-feature / 13,500-op store, built through `Workspace`).

**Verdict.** All thirteen of review-2's ranked changes were made; ten hold, two are
partial (F6 inline markdown, F13 cleanliness), and the headline one (F5) does not hold. `ingest` is still not a fixed point: the new
mark logic handles a bold that grows *at its end*, and nothing else — any insert whose
anchor sits *inside* an existing mark (a bold over two words, a link label, a mark
whose end character is dead) is swallowed by that mark, for every kind, and the new
fuzz test cannot see it because its base marks are single words (A1; 118 of 300
trials in a fuzz with multi-word marks). The second class is a semantic hole the fix
for A14/A15 opened wider: a plan-tagged `text.mark` is *applied*, not proposed — a
held amend or an MCP `propose` that unbolds a person's word removes the bold from the
live document and from `tree.codoc` while the plan is still open (A2). Third, the
`caused_by` gate for A11 was drawn one notch too tight: a request that was built
while the person kept typing (the exact doc-wins scenario the `PHASES` comment
defends) never settles (A3). Below those: deleted headings keep their bindings on
every path (A5, spec says otherwise), `translate` tombstones an open plan's words
(A6), one malformed `ingest` submission bricks a client's inbox across restarts (A7),
and the publish-skip key misses a checkout that changed the tree without changing the
op count (A8). I6 through `Workspace` now holds under verdicts, undo, redo, plan
marks and plan attaches (P26, six seeds, 220 steps each); the `ops_by_block` index
agrees with a brute-force scan and survives rebuild except for one no-op shape (A14).
`publish` is unchanged in absolute cost (238 ms vs 258 ms) because the per-op scans
it replaced were never the bulk of it; it now runs only when `pos` changes, which is
every batch that did anything.

---

## A. Correctness

Ranked. `T(s, bid)` is `s.blocks[bid].text().live_text()`; `md_of` is
`derive.markdown(block.text())`; `mark(m, s, bid, ins, lo, hi, kind, attrs)` applies a
`text.mark on` over `[ins.id, lo]..[ins.id, hi]`; helpers as in `tests/`.

### A1. `ingest` is not a fixed point: an insert inside a mark is captured by it (HIGH)

`Text.insert` (text.py:320-326) places the new segment right after the anchor in
sequence order, so it lands inside `[m.start, m.end]` whenever the anchor is a
character of `m` other than its last live one, or when `m.end` is a *dead* character
that follows the anchor. Every kind then covers the insert. `ingest` only emits an
`off` for the `GROWS_AFTER` case `anchor == m.end` (ingest.py:161-166); the shrink
step (155-158) looks at *surviving base* characters only, and the widen step (172)
skips a span already under a same mark. The commit message's "markdown(field) ==
new_md after every call (fuzzed)" is true only for single-word marks with no dead
tail, which is what `test_ingest_is_a_fixed_point_on_text_and_marks_fuzzed` generates.

```python
def test_insert_inside_a_live_bold_is_not_unbolded():
    m, s = Mint(), Store(); heading(m, s, "h1", "C")
    ins = paragraph(m, s, "p1", "alpha beta", after="h1")
    mark(m, s, "p1", ins, 0, 9)                                        # **alpha beta**
    res, applied = run_ingest(s, Mint("h", "human:a"), "p1", "**alpha** gamma **beta**", actor="human:a")
    assert res.refused is None and all(o.conflict is None for o in applied)
    assert md_of(s, "p1") == "**alpha** gamma **beta**"                # got: **alpha** **gamma beta**

def test_insert_inside_a_live_link_is_not_unlinked():
    ... mark(m, s, "p1", ins, 0, 9, LINK, {"href": "https://u"})
    run_ingest(s, Mint("h", "human:a"), "p1", "[alpha](https://u) gamma [beta](https://u)", actor="human:a")
    assert md_of(s, "p1") == "[alpha](https://u) gamma [beta](https://u)"   # got: [alpha](https://u) [gamma beta](https://u)

def test_append_after_a_bold_whose_end_char_is_dead_is_bolded():
    ... mark(m, s, "p1", ins, 0, 9)
    apply(s, m.op(K.TEXT_DELETE, {"bid": "p1", "path": "text"}, {"from": [ins.id, 5], "to": [ins.id, 9]}))
    assert md_of(s, "p1") == "**alpha**"
    run_ingest(s, Mint("l", "loop"), "p1", "**alpha** gamma")
    assert md_of(s, "p1") == "**alpha** gamma"                           # got: **alpha gamma**
```

Fuzz (P1d: one multi-word mark of a random kind, optional dead tail, one word
inserted anywhere, per-word marking preserved): 118/300 trials differ, all this class.

Fix (ingest.py:161-166, generalise): for each existing mark `m` and each insert
`(anchor, s, e)`, if `anchor` lies inside `m` in sequence order — `m.kind in
GROWS_AFTER and anchor == m.end`, or `locate(m.start) <= locate(anchor) < locate(m.end)`
for any kind — emit `off` over the inserted offsets not in `cover[key(m)]`. The dead-end
case is the second condition with a dead `m.end`. Then move the multi-word fuzz into
the suite; the single-word one is not a test of this property.

### A2. A plan-tagged `text.mark` is applied, not proposed: a proposal removes a person's bold (HIGH)

`markdown(mode="rejected")` (derive.py:47-51) hides open-plan *segments* and open-plan
`suggest_del` marks and nothing else. A plan op that turns a mark off removes it from
`Text.marks` at apply, so the live view, the `rejected` view, the export (export.py:41
`MODE = "rejected"`), the directive and the LLM's `feature_entry` all lose it until a
verdict; a plan op that turns a mark on shows it everywhere as fact. Two paths reach
it today: `_amend` on a held feature passes `plan=sentinel` so the human-mark refusal
is off (reflect.py:459-467) and `ingest` then emits `off` bodies that `commit_ingest`
tags with the plan; MCP `propose` with an `amend` calls `client.ingest(..., plan=pid)`
(mcp/tools.py:349-350) with the same effect. `PLANNABLE`'s comment (ops.py:50-54) says
"a plan proposes and never destroys"; a mark-off under a plan destroys.

```python
def test_held_amend_drops_a_human_bold_from_the_live_doc_and_the_export(tmp_path):
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    ins = [o for o in ws.store.ops if o.kind == K.TEXT_INSERT and o.payload["bid"] == "p1"][0]
    ws.submit([{"kind": K.TEXT_MARK, "target": {"bid": "p1", "path": "text"},
                "payload": {"kind": BOLD, "attrs": {}, "start": [ins.id, 8], "end": [ins.id, 11], "on": True}}], actor="human:local")
    t = ws.store.blocks["p1"].text()
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                "payload": {"after": list(t.last_live_ref()), "text": " Mine."}}], actor="human:local")   # unsent → held
    unbold = lambda c, s, t, **kw: [{"kind": "amend", "feature_id": "h1", "blocks": [{"id": "p1", "text": "Retries once before giving up. Mine."}]}]
    r = reflect(ws, ChangeSet(modified=[chunk("c.py", "c.py::send", "f3", "t1")]), Graph(), propose=unbold)
    assert ws.store.plans[r.plans[0]].status == "open"
    assert md_of(ws.store, "p1") == "Retries **once** before giving up. Mine."   # got: bold gone
    assert "**once**" in export_markdown(ws.store)                                # got: gone from git too

def test_a_plan_bold_on_trunk_words_enters_the_export():
    m, s = Mint(), Store(); heading(m, s, "b-000000000001", "T")
    ins = paragraph(m, s, "b-000000000002", "alpha beta", after="b-000000000001")
    apply(s, m.op(K.PLAN_OPEN, {"pid": "p-1"}, {"kind": "edit"}, actor="loop"))
    apply(s, m.op(K.TEXT_MARK, {"bid": "b-000000000002", "path": "text"},
                  {"kind": BOLD, "attrs": {}, "start": [ins.id, 0], "end": [ins.id, 4], "on": True}, actor="loop", plan="p-1"))
    assert "**alpha**" not in export_markdown(s)                                   # got: exported as fact
```

Two consistent designs; pick one and write it into `01` §2/§3. (i) *Plans cannot
touch marks*: `as_plan_bodies` drops `text.mark` bodies, `apply` refuses
`text.mark` with `plan` set (`PLANNABLE -= {TEXT_MARK}` except `suggest_del`), and
`ingest` under a plan is no longer a fixed point on marks (say so). (ii) *Plan marks
are proposals*: `markdown(rejected)` un-applies every open plan's `text.mark` op
(walk `plan.ops`; remove its `added`, re-add its `removed` on the clone), `accepted`
mode leaves them, `plan_claims` reports them, and `_amend` keeps the fixed point. (ii)
is the one that matches "three read modes over one document"; it is ~15 lines in
`derive.markdown` because the payload already records `added`/`removed`.

### A3. A request built while the person kept typing never settles (HIGH)

`_causes` (reflect.py:301-307) cites a hand-off only when `phase(store, fid) in
("queued", "planned")`. `phase` returns `edited` first, so a feature with unsent ink
is never cited even though its hand-off frontier is uncovered, and `settle`
(realize.py:96-100) therefore records nothing. The realized frontier should advance
to the hand-off frontier regardless of later ink; the ink is beyond that frontier by
construction and stays `unsent`.

```python
def test_request_built_while_the_person_kept_typing_never_settles(tmp_path):
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    t = ws.store.blocks["p1"].text()
    a = ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                    "payload": {"after": list(t.last_live_ref()), "text": " Do A."}}], actor="human:local")[0]
    handoff(ws, "h1", actor="human:local", asked="do A")
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                "payload": {"after": [a.id, 5], "text": " And B later."}}], actor="human:local")   # typed during the build
    r = reflect(ws, ChangeSet(modified=[chunk("c.py", "c.py::send", "f2", "t1")]), Graph(),
                propose=lambda *a, **k: [], under_epoch=True)
    done = settle(ws, r, sha="built")
    w = ws.store.watermarks("h1")
    assert len(done) == 1 and covered(w.handoff, w.realized)      # got: 0, still queued
    assert phase(ws.store, "h1") == "edited"
```

Fix: `_causes` cites when `w.handoffs and not covered(w.handoff, w.realized)`; drop the
`phase` call (it also makes `_causes` O(features × ink) for no reason).

### A4. `inline_md` is not a fixed point on `]` in a label, `*` at a bold edge, `\` before a link (MEDIUM)

`_escapes` (inline_md.py:123-135) escapes `*` only next to another *plain* `*`, `[`
only where the plain text itself parses as a link, and `#`/`` ` `` only at offset 0. It
never escapes `]` inside a label, a `*` adjacent to a `**` the renderer emits, or the
`\` that ends up immediately before an emitted `[` (the `(?<!\\)` lookbehind then
refuses the link). Fuzz over the alphabet `ab*[]()#\`\\ ` with one random span: 330 of
2,000 trials differ; every failure involves a span (plain text alone round-trips). The
export→import fixed-point test passes on such stores only because the stars/brackets
are re-rendered verbatim while the store has gained characters and lost a mark, which
is review-2 A6's failure mode again.

```python
def test_render_parse_edges():
    assert parse(render("*a", [Span(BOLD, {}, 1, 2)])) == ("*a", [Span(BOLD, {}, 1, 2)])          # got bold over "*a"
    assert parse(render("a*b", [Span(BOLD, {}, 2, 3)])) == ("a*b", [Span(BOLD, {}, 2, 3)])       # got bold over "*b"
    assert parse(render("a]b", [Span(LINK, {"href": "https://u"}, 0, 3)])) == ("a]b", [Span(LINK, {"href": "https://u"}, 0, 3)])  # got plain "[a]b](https://u)"
    assert parse(render("\\a]", [Span(CITE, {"file": "c.py", "symbol": "C"}, 1, 3)]))[1]           # got []
```

Fix: escape `]` inside labels (the label regex already accepts `\\.`); in `render`,
escape a plain `*` that is adjacent to a bold boundary it is about to emit (the
renderer knows `opens`/`closes`); make the lookbehind escape-aware (`(?<!(?<!\\)\\)`
or a small hand parser); keep P2d as a suite test.

### A5. A deleted heading keeps its bindings, on every path (MEDIUM-HIGH)

`01` §3: "Bindings of a deleted heading are detached by ops in the same batch,
`caused_by` the delete." Nothing emits them: `_h_block_delete` (apply.py:411-430)
and the delete branch of `_h_plan_accept` (689-694) kill blocks only, `_delete_plan`
(reflect.py:439-444) strikes prose only, and no gesture in `codoc/` emits a detach
(`grep BLOCK_DELETE` finds only `snapshot.py`). After an accepted `retire_node` or a
person's delete, the symbol stays bound to a dead feature: `owner_of` returns it, the
coverage net skips the chunk (`key in store.bindings`), and the next reflect tells the
model `current_feature_id` is a dead node. Review-2 A10 fixed the *add* side; the
delete side was kernel-review B4 and is still open.

```python
def test_accepting_a_retire_plan_leaves_bindings_on_the_dead_heading(tmp_path):
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    retire = lambda c, s, t, **kw: [{"kind": "retire_node", "feature_id": "h1", "rationale": "gone"}]
    r = reflect(ws, ChangeSet(modified=[chunk("c.py", "c.py::send", "f2", "t1")]), Graph(), propose=retire)
    ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": r.plans[0]}, "payload": {}}], actor="human:local")
    assert not ws.store.blocks["h1"].live
    assert ("c.py", "c.py::send") not in ws.store.bindings          # got: still bound to h1

def test_a_persons_delete_of_a_bound_heading_leaves_bindings_behind(tmp_path):
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    ws.submit([{"kind": K.BLOCK_DELETE, "target": {"bid": "h1"}, "payload": {}}], actor="human:local")
    assert ("c.py", "c.py::send") not in ws.store.bindings          # got: still bound
```

Fix: do it in the applier, once. `_h_block_delete` and the accept-delete branch
record `detached: [binding…]` for every killed heading and delete them; the inverse
(`block.restore`, `plan.reopen`) re-attaches from the record. The phantom queue entry
(`queued()` line 62) already works from watermarks, not bindings, so nothing else
moves. Update `01` §3 to say the applier does it (a "same batch" rule with no
implementer is a bug generator).

### A6. `translate` tombstones an open plan's words on the trunk (MEDIUM)

`_feature_text` shows the model the `rejected` text (translate.py:65-66) but ingests
with `base_text = live_text()` (149, 155). With an open loop plan on the block the live
text holds the plan's words and the translation does not, so the diff deletes them as
the translator's own trunk deletes; the plan stays `open` over dead characters, and
words under a `suggest_del` are translated as if kept. `_has_translator_plan` skips
only the translator's own plans.

```python
def test_translate_tombstones_an_open_plans_words(tmp_path):
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    pid = ws.open_plan(actor="loop", kind="edit", target="h1")
    t = ws.store.blocks["p1"].text()
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                "payload": {"after": list(t.last_live_ref()), "text": " Maybe."}}], actor="loop", plan=pid)
    propose = lambda payload, lang, **kw: {p["id"]: ("客户端", "放弃前重试一次。") for p in payload}
    translate_tree(ws, tmp_path / ".codoc", language=resolve("zh-Hans"), propose=propose)
    plan_words = [s for s in ws.store.blocks["p1"].text().segs if s.plan == pid]
    assert all(s.live for s in plan_words)                            # got: dead, by a translator op
```

Fix: skip a feature with any open plan (`open_plans_on(store, fid)`), the way it
already skips translator plans; two lines at translate.py:93.

### A7. One malformed `ingest` submission bricks a client's inbox, across restarts (MEDIUM-HIGH)

`_validate` (log.py:243-258) checks that `target` is an object, not that an `ingest`
submission's `target` has a `bid`. `drain_inbox` then does `s.target["bid"]`
(workspace.py:242) outside `apply`, the `KeyError` escapes, the per-line offset is not
advanced (256 runs after the line), and every later line of that client waits behind
it. `Daemon.run` catches and prints; on restart `_inbox_offsets` starts at 0, acked
cids are skipped, the poison line is not acked, and it raises again. Review-2 A9's
`target: null` case is fixed; this is the same class one field deeper, and the hub
writes these files from strangers' input.

```python
def test_ingest_submission_without_bid_poisons_every_later_line(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "p1"}, "payload": {"kind": PARAGRAPH, "after": START}}], actor="human:local")
    L.submit(tmp_path / ".codoc", "ide", [
        Submission(cid="c1", kind="ingest", target={}, payload={"base_text": "", "new_md": "x"}),
        Submission(cid="c2", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"}, payload={"after": START, "text": "ok"})])
    ws.drain_inbox({"ide": "human:local"})                            # got: KeyError
    assert T(ws.store, "p1") == "ok"
    ws.close(); ws2 = Workspace(tmp_path / ".codoc")
    ws2.drain_inbox({"ide": "human:local"})                           # got: KeyError again (P27)
```

Fix: `_validate` checks `kind == "ingest" → target.bid is a str, payload.base_text /
new_md are str`; and `drain_inbox` wraps the per-line body in `try/except Exception`
that acks `{"cid", "error": repr(e)}` and advances, so no line can ever block the
next. The applier's "total" property (every op recorded) should hold for submissions
too.

### A8. The publish-skip key misses a rebuild that changed the tree but not `pos` (MEDIUM)

`process_batch` rebuilds on a HEAD change (daemon.py:97-102) and then skips publish
when `(store.pos, lease)` equals the last key (143-148). A checkout to a branch whose
logs hold the same number of ops (two branches forked from one commit, each with one
session of equal length; or the same branch after a `git reset` that swapped a session
file) leaves `projection.json`, `tree.codoc` and `realize.md` describing the old
tree until the next op.

```python
def test_rebuild_with_equal_pos_publishes_stale_files(tmp_path):
    root = tmp_path / "repo"; (root / ".codoc").mkdir(parents=True)
    d = Daemon(root, changeset=lambda *a: ChangeSet(), propose=lambda *a, **k: [], printer=lambda *_: None)
    seed(d.ws); d.process_batch([])
    other = tmp_path / "other" / ".codoc"; ws = Workspace(other)
    ws.submit([... five ops building "Server" / "Listens forever." / a binding ...], actor="loop"); ws.close()
    d.ws.close(); shutil.rmtree(d.codoc_dir / "log"); shutil.copytree(other / "log", d.codoc_dir / "log")
    d.state.head = "other-branch"; d.process_batch([])
    assert T(d.ws.store, "p1") == "Listens forever."                                   # the store is right
    assert "Listens forever." in (d.codoc_dir / "tree.codoc").read_text()             # got: the old tree
```

Fix: `self._published = None` in the HEAD-change branch, or key on
`tuple(sorted(store.frontier.items()))` instead of `pos`. One line.

### A9. Deleting a heading under an open plan leaves a zombie proposal (MEDIUM)

`open_plans_on` and `write_status` count plans by `status == "open"`; a plan whose
target heading a person deleted is invisible (no live block carries it) and
undecidable from the editor, yet `status.json` says "1 proposal(s) awaiting your
verdict" and `projection["plans"]` lists it, forever. Same for an `add` plan whose
proposed heading the person deletes instead of rejecting.

```python
def test_deleting_a_heading_under_an_open_plan_leaves_a_zombie_proposal(tmp_path):
    ... d = Daemon(root, ...); seed(d.ws)
    pid = d.ws.open_plan(actor="loop", kind="edit", target="h1")
    d.ws.submit([{"kind": K.TEXT_INSERT, ... " Maybe."}], actor="loop", plan=pid)
    d.ws.submit([{"kind": K.BLOCK_DELETE, "target": {"bid": "h1"}, "payload": {}}], actor="human:local")
    d.process_batch([])
    st = json.loads((d.codoc_dir / "status.json").read_text())
    assert json.loads((d.codoc_dir / "projection.json").read_text())["blocks"] == []
    assert st["state"] != "proposed", st                              # got: proposed, "1 proposal(s) awaiting your verdict"
```

Fix: the delete gesture (and the applier's `block.delete` of a heading, if A5 goes into
the applier) rejects open plans whose target or ops live in the killed blocks, in the
same batch; `block.restore` reopens them via the recorded inverses. Cheaper stopgap:
`write_status`/`projection` count only plans whose target block is live.

### A10. I6 depends on an input the log does not carry: the roles table (MEDIUM, design)

`apply` runs `store.policy` at replay (apply.py:57-60) and `Workspace` builds the table
from a constructor argument that nothing persists (workspace.py:83). Two clones with
different tables rebuild different stores from the same logs; today they agree only
because no code path constructs a non-default `Roles`. `04` §6's "a forged
`plan.accept` … is applied as a recorded conflict on rebuild" needs the *maintainer's*
table on every clone, which means the table must be in the tracked `config.json`.

```python
def test_replay_with_a_different_roles_table_changes_the_store(tmp_path):
    ws = Workspace(tmp_path / ".codoc", roles=Roles({"human:local": "owner", "human:alice": "maintainer"}))
    seed(ws); pid = ws.open_plan(actor="loop")
    ws.submit([{"kind": K.TEXT_INSERT, ... " Proposed."}], actor="loop", plan=pid)
    assert ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor="human:alice")[0].conflict is None
    ws.close(); ws2 = Workspace(tmp_path / ".codoc")                  # a clone with the shipped table
    assert ws2.store.state_fingerprint() == ws.store.state_fingerprint()   # got: plan open vs accepted
```

Fix: `Roles.load(codoc_dir)` from `config.json` `roles: {actor: role}` (tracked), used
by `Workspace` when no table is passed; `01` §7.3 says where roles live. The
alternative (trust the recorded `conflict == "authority"` at replay) makes the log
self-certifying and the hub's validation moot; the config route is the one `04` §6
already assumes.

### A11. Provisional `$cid` anchors die at a drain boundary (MEDIUM, spec-conformant)

`minted` is local to one `drain_inbox` call (workspace.py:228). A submission that
anchors to a `$cid` acked in a *previous* drain fails as `bad_anchor`. `01` §7.4 and
`09-editor.md`:64 say "in the same drain", so code and spec agree — but the editor
cannot see drain boundaries (the daemon drains per debounced filesystem batch), so a
settle typed before the previous settle's ack arrived is lost at a boundary the client
did not choose.

```python
def test_provisional_anchor_across_two_drains(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "p1"}, "payload": {"kind": PARAGRAPH, "after": START}}], actor="human:local")
    L.submit(tmp_path / ".codoc", "ide", [Submission(cid="c1", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"}, payload={"after": START, "text": "ab"})])
    ws.drain_inbox({"ide": "human:local"})
    L.submit(tmp_path / ".codoc", "ide", [Submission(cid="c2", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"}, payload={"after": ["$cid", "c1", 1], "text": "c"})])
    assert ws.drain_inbox({"ide": "human:local"})[0].conflict is None   # got: bad_anchor
```

Fix: `self._minted: dict[client, dict[cid, op]]`, seeded from the ack files at
startup (`acked_cids` already reads them; keep the `op` field too). Spec: "against
every ack it has issued to that client".

### A12. A torn trailing line drops the whole session, silently (MEDIUM)

`read_session` breaks on the first unreadable line and then sets `ops = []`
(log.py:88-105). `SessionWriter.append` writes and flushes per op; a crash mid-write
leaves a torn last line, and on restart every op of that session vanishes from the
store while the file stays on disk (and in git). The daemon then writes a new session
on top of the diminished state and never prints `ws.problems` (`Daemon.__init__` does
not look; only `codoc rebuild` does, main.py:315). The docstring's reason ("a partial
read would put the store in a state no log describes") does not apply to a torn
*tail*: the prefix is a state the daemon was in.

```python
def test_torn_last_line_loses_every_op_of_the_session(tmp_path):
    ws = Workspace(tmp_path / ".codoc"); seed(ws); ws.close()
    p = L.list_sessions(tmp_path / ".codoc")[0]
    p.write_bytes(p.read_bytes() + b'{"id": "' + ws.session.encode() + b':6", "at": "0000')   # crash mid-write of op 6
    ws2 = Workspace(tmp_path / ".codoc")
    assert "h1" in ws2.store.blocks                                     # got: {} — five good ops gone
```

Fix: when the only problem is an unreadable *last* line, keep the prefix, report it,
and have the daemon truncate the tail at startup (so every future rebuild agrees);
a problem anywhere else still rejects the file. `Daemon.__init__` prints `problems`.

### A13. Verdicts are in no feature's history (LOW-MEDIUM)

`record` indexes an applied op under `touched` and a conflicted one under a
`bid/fid/into` hint (store.py:296-304). A `plan.accept` that only confirms inserts
touches no block (`_h_plan_accept` adds to `touched` only when it undoes or strikes),
and a refused verdict's target is a `pid`. So the one event a reader most wants in
`history` — the verdict — is absent unless it happened to strike something, and
`B5`'s "conflicted ops included with their code" misses every `authority` refusal on
a verdict.

```python
def test_an_accept_that_only_confirms_inserts_is_in_nobodys_history(tmp_path):
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    pid = ws.open_plan(actor="loop", kind="edit", target="h1")
    ws.submit([{"kind": K.TEXT_INSERT, ... " Proposed."}], actor="loop", plan=pid)
    acc = ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor="human:local")[0]
    assert acc in history(ws.store, "h1")                              # got: absent

def test_a_refused_verdict_is_in_no_history(tmp_path):
    ... bad = ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor="agent:x")[0]
    assert bad.conflict == "authority" and bad in history(ws.store, "h1")   # got: absent
```

Fix: verdict handlers add the plan's `target` (and the blocks of its ops) to `touched`
even when nothing changed; `record`'s hint list gains `pid → store.plans[pid].target`.

### A14. `ops_by_block` differs between the live store and its rebuild for a no-op segment delete (LOW)

A `text.delete` given `segments` that are all dead (a person's undo of an insert
someone else already deleted) touches its holders on the live path (apply.py:142-146,
`work` from the requested segments) but records `segments: []`; on replay the empty
list yields no holders, `touched` is empty, and the op leaves the index. The
fingerprint is equal; `history` is not.

```python
def test_an_undo_that_kills_nothing_is_indexed_live_but_not_after_rebuild(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    ins = ws.submit([{block.add p1}, {text.insert "abc"}], actor="human:local")[1]
    ws.submit([{"kind": K.TEXT_DELETE, ..., "payload": {"from": [ins.id, 0], "to": [ins.id, 2]}}], actor="loop")
    undo = ws.submit([dict(b) for b in ins.inverse], actor="human:local")[0]     # kills nothing
    live_index = {k: list(v) for k, v in ws.store.ops_by_block.items()}
    ws.close(); ws2 = Workspace(tmp_path / ".codoc")
    assert {k: list(v) for k, v in ws2.store.ops_by_block.items()} == live_index   # got: p1 lost position 3
```

Fix: canonical `bid`/`path` for the segments branch should be the *holder* found
(`work[0]`), and replay of an empty `segments` should still touch that block (or:
record `touched` in the canonical as `blocks: [...]` for every text op, which also
fixes `text.restore`). Otherwise the P6b differential (300 random steps) holds:
`history` equals a brute-force scan over the recorded `touched`, and the index
survives rebuild.

### A15. `plan.reject` deletes a binding the plan only refreshed (LOW)

`_h_bind_attach` on the feature's own key records `displaced: None` plus
`prev_fingerprint` (apply.py:471-475); `_undo_plan_op`'s `BIND_ATTACH` branch
(618-631) reads only `displaced` and deletes the binding. Reachable only by a
plan-tagged attach on an already-owned key (no codoc path does it today; an MCP
client could).

```python
def test_plan_reject_deletes_a_binding_the_plan_only_refreshed():
    m, s = Mint(), Store(); heading(m, s, "h1", "C")
    apply(s, m.op(K.BIND_ATTACH, {"fid": "h1"}, {"file": "c.py", "symbol": "C", "fingerprint": "f1"}))
    apply(s, m.op(K.PLAN_OPEN, {"pid": "p-1"}, {"kind": "edit", "target": "h1"}, actor="agent:x"))
    apply(s, m.op(K.BIND_ATTACH, {"fid": "h1"}, {"file": "c.py", "symbol": "C", "fingerprint": "f2"}, actor="agent:x", plan="p-1"))
    apply(s, m.op(K.PLAN_REJECT, {"pid": "p-1"}, {}))
    assert s.bindings[("c.py", "C")].fingerprint == "f1"               # got: KeyError, binding gone
```

Fix: in `_undo_plan_op`, `if "prev_fingerprint" in o.payload: restore the hashes`
before the `displaced` test.

### A16. `set_mark` loses the existing placer when a displacement precedes the merge (LOW)

`keep_actor, keep_op = m.actor, m.op` runs only `if not removed` (text.py:405-409), and
`removed` is already non-empty when the same call displaced an exclusive mark. A loop
link that widens a person's link *and* displaces a cite becomes the loop's link.

```python
def test_merged_link_keeps_the_existing_placer_even_when_it_also_displaces():
    ... human = mark(..., 0, 4, LINK, {"href": "https://u"}, actor="human:a")
    mark(..., 11, 15, CITE, {"file": "c.py", "symbol": "C"}, actor="human:a")
    mark(..., 5, 15, LINK, {"href": "https://u"}, actor="loop")
    links = [mk for mk in s.blocks["p1"].text().marks if mk.kind == LINK]
    assert [(mk.actor, mk.op) for mk in links] == [("human:a", human.id)]   # got: ("loop", ext.id)
```

Fix: track `merged_first: bool` separately from `removed`.

### A17. `machine` may write into a stranger's open plan (LOW, design)

`Roles.policy` restricts plan ownership for `suggest` only (workspace.py:66-73);
`agent:*` may tag ops with `human:contrib`'s plan, which the maintainer then accepts
as the contributor's proposal. The docstring says "the trunk and plans", so this is
as designed; state in `01` §7.3 whether an agent may write into a plan it did not open
(I would say: only its own and the loop's).

```python
def test_machine_may_ride_a_strangers_plan(tmp_path):
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    pid = ws.open_plan(actor="human:contrib", kind="edit", target="h1")
    op = ws.submit([{"kind": K.TEXT_INSERT, ... " Agent words."}], actor="agent:claude-code", plan=pid)[0]
    assert op.conflict == "authority"                                   # got: applied
```

### A18. Confirmed fine (probed, no fault)

- **I6 under plan marks/attaches/verdicts/undo/redo** (P26): six seeds × 220 random
  steps with plan-tagged inserts, bold/link toggles on and off, plan `bind.attach`
  (displacing and same-feature), subset accepts, rejects, `plan.reopen` and redo:
  fingerprint, export and `ops_by_block` equal after rebuild. `marks_back` and the
  plannable attach hold.
- **Two plans on one block** (P23): B anchored after A's inserted character; accept B,
  reject A → A's words die by identity, B's stay, `rejected` view correct.
- **Undo of an accept after later typing, then reject** (P21): the person's words
  typed inside the accepted span survive the reject; honest.
- **Export/import of a store whose heading is plan-only but whose paragraph is trunk**
  (P22): the paragraph re-imports under the previous heading. Not a bug; a documented
  limit of the `rejected` export (see §E).

---

## B. Review-2 items: what holds

| item | status | evidence |
|---|---|---|
| A1 ink merge key | fixed | derive.py:141-164 merges by `(actor, stage)` per segment; test |
| A2 subset replay | fixed | apply.py:696 stores `subset`; T2 + P26 |
| A3 restored mark placer | fixed | apply.py:204-207; test |
| A4 rebuild quarantine | fixed | clock.py:37-43 no raise; workspace.py:121-126 |
| A5 ingest fixed point | **partially** | tokens/junk, shrink, widen, end-growth fixed; inserts *inside* a mark not (§A1) |
| A6 bold ⨯ link render | fixed for nesting; **not** for `]`/edge `*`/`\` (§A4) | inline_md.py:97-120 |
| A7 export escapes | fixed for plain text; same residue as A6 | `_escapes` |
| A8 authority | fixed | workspace.py:58-73; test; residue §A17 (machine in others' plans) |
| A9 inbox durability | fixed for torn/`target:null`; **new hole** one field deeper (§A7) | log.py:212-258 |
| A10 rejected add plan | fixed | `BIND_ATTACH ∈ PLANNABLE`; `_undo_plan_op`; residue §A15 |
| A11 settle on hand edit | fixed, **over-tightened** (§A3) | reflect.py:115, 301-307 |
| A12 history hint block | fixed | `ops_by_block` from `touched`; residue §A13/§A14 |
| A13 Focus per mark | fixed | derive.py:352-360 by placing op |
| A14 plan per paragraph / empty plan | fixed | reflect.py:447-514, one lazy plan; test |
| A15 strike unlisted | fixed | omission = unchanged, `delete: true` strikes |
| A16 attach removed key | fixed | `touched_keys = set(fp)`; `index_keys` gone |
| A17 dead block ingest | fixed | workspace.py:167-173 |
| A18 one queued fact | fixed | `covered` in ops.py:136; `_queued/_max_op` gone |
| A19 ground since | fixed | derive.py:175-178 |
| A19 written_by per block | fixed for the gate | `block_written_by`; feature-level kept for prompt/translate |
| A19 export rejected | fixed for segments, **not marks** (§A2) | export.py:41 |
| A19 projection realizing | fixed | daemon.py:146-147 |
| A19 `_new_marks` on | fixed | derive.py:344 |
| A19 `_cites` Edit only | fixed (removed) | — |
| A19 `Refused` | fixed (removed) | — |
| A19 reflect blanket except | fixed (validate first; the except remains, now mostly dead) | reflect.py:199-229 |
| A19 relocations count all | fixed | reflect.py:332 |
| A19 Graph `_by_file` | fixed | graph.py:45; `rebuild` still `__dict__.update` |
| A19 HEAD rebuild | fixed, **publish key not reset** (§A8) | daemon.py:97-102 |
| F1 rebuild is the store | holds (P26) | modulo §A10 (roles) and §A14 (index) |
| F2 ink | holds | |
| F3 queued | holds | |
| F4 authority | holds | |
| F5 ingest fixed point | **does not hold** (§A1) | |
| F6 inline markdown | partial (§A4) | |
| F7 realization evidence | holds, gate too tight (§A3); `codoc done` exists (main.py:227) | |
| F8 inbox | holds, one hole (§A7); `$cid` per drain (§A11) | |
| F9 plan hygiene | holds; delete side open (§A5, §A9) | |
| F10 indexes / publish once | holds; cost unchanged (§C) | |
| F11 small fixes | all in | |
| F12 spec edits | B1, B4, B5, B8, B10, B12, B13 in; residue in §E | |
| F13 cleanliness | ~half (§D) | |

---

## C. Complexity

Same synthetic store as review-2 (1,500 features, one paragraph and one binding each,
four refresh rounds; 13,500 ops), built through `Workspace` in 0.69 s.

| call | review-2 | now | note |
|---|---|---|---|
| `_drifted(f)` | 6.0 ms | 0.02 ms | `ops_on` |
| `history(f)` | 3.9 ms | 0.02 ms | |
| `ground(f)` | — | 0.03 ms | |
| `phase(f)` | — | 0.03 ms | |
| `phase` × 1,500 | 46 ms | 47 ms | dominated by `ink` (segment walk) + `open_plans_on` |
| `projection()` | — | 83 ms | runs/spans for 3,000 blocks |
| `export_markdown()` | — | 73 ms | `render` per block |
| `queued()` | — | 0.6 ms | |
| `publish()` | 258 ms | **238 ms** | phases once (47) + projection (83) + claims per feature + export (73) + queue + status |
| `Workspace()` rebuild from disk | — | 380 ms | now runs on every HEAD change (every commit) |

So the O(log) scans are gone but `publish` is unchanged, because it was never the
scans: it is rendering every block's runs, spans and Markdown from scratch. It now
runs only when `pos` changes, i.e. on every batch that applied an op — a person
typing pays 240 ms per debounced settle at 1,500 features, and the editor sees the
echo that much later. The spec's cache keys (`01` §6: per block on write; structural
head + block heads for export) are not implemented. The incremental version is
mechanical: cache `projection` entries and rendered export lines per `(bid,
block_head[bid])`, recompute only blocks whose head moved, and `phases` per
`(fid, max block_head of body, structural_head, watermark head)`. Rebuild at 380 ms
per commit is fine; `select_context`'s `phase` over every heading per reflect (47 ms)
is fine.

Remaining O(log): `realize.divergent` (realize.py:113, unused). `store.prune_index`
(store.py:244-254) walks every op in `char_index` per split/merge: O(distinct ops)
per structural op, which is O(log) in practice — measure before it matters, but it is
the one scan left on the apply path.

---

## D. Cleanliness

- **`_amend`'s `sentinel = "plan"`** (reflect.py:459): a fake plan id passed to a
  parameter typed `str | None` to flip one boolean in `ingest` (137). Give `ingest` /
  `plan_ingest` a `refuse_human_marks: bool` (or `trunk: bool`) and pass `not held`.
  The current shape also decides the refusal *before* the gate: a non-held amend that
  the per-block gate later demotes to a plan was computed with the refusal on, while a
  held one was not — two rules for one thing. With §A2 resolved, both cases should
  refuse nothing under a plan and the mark change should be a proposal.
- **`Workspace.ingest(gate=, plan_note=)` and `Ingest.demote_to_plan`** have no caller
  outside `tests/substrate/test_workspace.py` (grep `gate=`). `_amend` gates per
  block itself; `translate` and `client.ingest` never gate. Delete the path or route
  `_amend` through it — not both. `demote_to_plan` is still an output reason and an
  input-shaped field (review-2 D).
- **Two `MACHINE_KINDS`** with different sets: ingest.py:51 `{"loop", "translator"}`
  and workspace.py:33 `{"agent", "translator", "loop", "import"}`. One name, two
  meanings; call the first `REFUSING_KINDS` or put both in `ids.py`.
- **`derive._blocks_of`** (derive.py:133) is `store.feature_body` (review-2 D said so).
- **`"view"` sentinel** still written on the clone (derive.py:50, 55); harmless now that
  no reader tests for it, but `_op(store, "view")` returning `None` is what keeps
  `ground` honest — a comment or `by=None` would be clearer.
- **`realize.divergent`** (realize.py:108-121): no callers, scans `store.ops`. Delete.
- **`Daemon.identity`** (daemon.py:69) duplicates `client.CLIENT_IDENTITY`
  (client.py:25). One table, imported.
- **`Store.by_id` and `Store.position`** are two dicts keyed by op id for one fact
  (`ops[position[i]] is by_id[i]`). Keep `position`.
- **`store.record`** (295-298): `p = self.pos` then `p - 1` for the index and `p` for the
  heads; write `pos = len(self.ops) - 1` once.
- **`reflect`'s `except (KeyError, TypeError, ValueError)`** (reflect.py:228) is now
  shadowed by `validate_raw`; either delete it or keep it and drop the comment "a shape
  the validator did not foresee" in favour of a `refused` entry test.
- **`context.written_by`** docstring ("the strict amend gate then applies") lies: the
  gate is per block (`block_written_by`); the feature-level value feeds the prompt and
  `translate` only.
- **Module docstrings that overstate**: ingest.py:20-22 (fixed point; §A1),
  inline_md.py:15-17 (`parse(render)` for every state; §A4), export.py:19-20
  (`import(export(s))` rebuilds `s`; §A2, P22).
- **`projection["plans"]`** (derive.py:424) lists accepted plans forever; the editor
  wants open ones plus those with an accepted directive, which is `hold_detail.origin`
  territory, not an unbounded list.
- **Tests**: `test_roles_table_over_every_kind` line 327 asserts
  `(kind == PLAN_OPEN or kind in PLANNABLE or kind not in PLANNABLE)`, a tautology; it
  should assert that a suggester's own-plan verdict lands as `not_plannable` at
  `apply`, and that `machine` on a stranger's plan is whatever §A17 decides.
  `test_torn_and_malformed_inbox_lines` line 335 has a dead `_para(...) if False else`
  expression. The ingest fuzz (test_review2.py:210) needs multi-word marks and inner
  inserts (P1d) or it pins nothing about the mark logic it claims to.
- **`Graph.rebuild`** still `self.__dict__.update(fresh.__dict__)`.
- **`_h_plan_reopen`** (apply.py:788) reconstructs the verdict's `subset` from the
  reopen payload; fine, but `"subset": accepted if subset is not None else None` at
  698 records the *closure* under the name `subset` while `plan.accept` records the
  *wire* subset under the same name — two meanings of one key across two ops.

---

## E. Spec ↔ code

Which side moves is stated per item.

1. **§4 step 5 "the call is a fixed point"** — false (§A1). **Code moves.**
2. **§2 "a plan is a tag" / §3 `plan.open`** say nothing about plan-tagged `text.mark`;
   §4 step 5 "under a plan the person decides" assumes the mark is still there to decide
   on. **Spec adds the rule; code follows** (§A2, design (ii)).
3. **§3 `block.delete`: "Bindings of a deleted heading are detached by ops in the same
   batch, `caused_by` the delete."** No implementer anywhere (§A5). **Code moves** (the
   applier); spec then says "the applier detaches and records; the inverse re-attaches".
4. **§7.4 and `09-editor.md`:64 "$cid … in the same drain"** — spec = code (§A11), and
   the rule is unusable from the client. **Both move**: "against every ack issued to
   that client".
5. **§7.1/§7.3 + `04` §6: authority "checked per op at apply and again at replay"** with
   a table the spec locates "outside the calculus" and the code in a constructor
   argument (§A10). **Spec names the home (tracked `config.json`); code reads it.**
6. **§6 `history` "conflicted ops included with their code"** — a refused verdict and an
   applied no-touch verdict are absent (§A13). **Code moves.**
7. **§6 `export` "`import(export(s))` rebuilds `s`"** — false with any open plan (a block
   left out whole; a plan heading over a trunk paragraph, P22) and under §A4. **Spec
   says "for a store with no open plans, on text `render` can escape"; code fixes §A4.**
8. **§3 table `text.mark` wire `from, to`** — code and every client use `start, end`.
   **Spec moves.**
9. **§3 table `block.delete` canonical "the range … with ranks"** — code records `ids`
   and `killed`; ranks do not change. **Spec moves.**
10. **§6 `projection` (dead segments in the display window, `aria`) and `attribution(…,
    window)` snapped to `doclang.tokens`** — not implemented; `projection` emits live
    runs only. **Spec marks them planned, or code implements.**
11. **`04` §4 `commit(sha)` marker op** — no such op kind exists. **Spec marks planned.**
12. **`04` §1 "`cache/` SQLite store, index, lancedb"** — v2 has `cache/graph.json` and
    the cocoindex store; no SQLite. **Spec moves.**
13. **`04` §2 "a file that fails is reported and skipped, never partially applied"** —
    keep for a mid-file fault; a torn *tail* should keep the prefix (§A12). **Both move.**
14. **§7.2 "the hub … rejects a stamp far ahead of wall time"** — no code (hub out of
    scope here). Fine as a hub requirement; note it is unimplemented.
15. **§6 `phase` "`realizing` … read from `status.json`"** and the doc-wins order —
    matches (`PHASES`, daemon.py:143-147). **Agree.**
16. **§3 `feature.realized` "never to an agent"; §7.3 machine exception** — matches
    workspace.py:63. **Agree.** Residue §A17.
17. **§4 step 8 "one amend is one plan"** — matches. **Agree.**
18. **`09-editor.md`:110 "one debounced settle of typing is one batch"** — the daemon
    stamps `batch` from the submission's `batch` or mints one per submission
    (workspace.py:250); the editor must send the batch id itself. Consistent; no change.

---

## F. Ranked changes

1. **Ingest: an insert inside any mark gets an `off` over its uncovered characters**
   (§A1). Generalise ingest.py:161-166 to "anchor inside `m` in sequence order, any
   kind" (covers the dead-end case); keep `anchor == m.end` for `GROWS_AFTER`. Put P1d
   in the suite. ~10 lines.
2. **Plan marks are proposals** (§A2). `markdown(rejected)` un-applies every open plan's
   `text.mark` (its recorded `added`/`removed`); `plan_claims` reports them; `01` §2/§3
   gain the sentence; `_amend`/MCP `propose` stop needing the `sentinel`. ~20 lines.
3. **`_causes` cites every uncovered hand-off** (§A3): drop the `phase` filter. 3 lines.
4. **Bindings die with their heading, in the applier** (§A5), recorded and inverted;
   `01` §3 updated. ~25 lines; then §A9's zombie plans by rejecting open plans on the
   killed blocks in the same gesture (or count only live-target plans in status).
5. **Inbox totality** (§A7, §A11): validate `ingest` submissions; per-line
   `try/except → error ack + advance`; `_minted` per client seeded from acks; spec
   §7.4 reworded. ~25 lines.
6. **Publish key** (§A8): `self._published = None` on HEAD change. 1 line.
7. **Roles live in tracked config** (§A10): `Roles.load(codoc_dir)`; `01` §7.3 says so.
   Decide before the hub ships anything.
8. **Torn tail keeps the prefix; daemon prints problems** (§A12).
9. **`translate` skips features with any open plan** (§A6). 2 lines.
10. **`inline_md` escapes** (§A4): `]` in labels, `*` at a bold edge, escape-aware
    lookbehind; P2d into the suite.
11. **History carries verdicts** (§A13) and a no-op segment delete touches its holder
    on replay (§A14); `_undo_plan_op` restores a refreshed binding (§A15); `set_mark`
    keeps the merged placer through a displacement (§A16).
12. **Incremental `publish`** (§C): per-block cache keyed on `block_head`, phases keyed on
    the feature's heads. The only change in this list that is not small; schedule it
    for when a real repository is watched, not the study.
13. **Cleanliness** (§D): `refuse_human_marks` flag; one `MACHINE_KINDS`; delete
    `gate=`/`demote_to_plan` or use them; delete `divergent`, `_blocks_of`, `by_id`;
    one identity table; fix the three overstated docstrings and the two test defects;
    the spec rows in §E 8–12.
