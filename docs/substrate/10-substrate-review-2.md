# Review of the second layer: ingest, derive, export, substrate, loops (implementation 1)

Scope: `codoc/calculus/{inline_md,ingest,derive,export}.py`, `codoc/substrate/*`,
`codoc/graph/{graph,flows}.py`, `codoc/loops/*` and their tests on branch `substrate`
(worktree `CodeNav-substrate`, commit `7d21199`), read against `01-calculus.md` rev 2
§4/§6/§7, `02-workflows.md` §1, `06-cut-list.md`, and `08-kernel-review-1.md`. The 63
shipped tests pass. Every §A item was run and confirmed; the probe file is
`scratchpad/probe_substrate2.py` beside this review (35 probes; each asserts the
*observed* wrong behaviour so it passes today, and the line marked `BUG` is the one to
flip). The §A fixes from the kernel review that this layer leans on (`locate_char`
prefers a live holder, `by_id`, `rank_head`, two-phase verdict handlers) were re-read and
hold.

Verdict up front. The shape is right and small: `ingest` is genuinely the one diff,
`derive` is pure over the store, the daemon is one applier with one inbox vocabulary,
and `loops/` is a fraction of `loop_a`/`loop_b` with the same decision table. Four
things are not yet true that the layer's own docstrings promise, and each is a class:

1. **I6 does not survive `Workspace.rebuild`.** Two canonical payloads are not accepted
   back by their handler (`plan.accept` drops the wire `subset`; `text.mark` drops a
   restored mark's `actor`/`op`), and the rebuild path *quarantines ops the log already
   holds* and then writes new ops on top of the resulting state. The store on disk and
   the store in memory can differ after every restart, and the test that would catch it
   (`Workspace` round trip after a subset accept, after an undo, after a skewed stamp)
   does not exist.
2. **`ingest` is not the fixed point §9 promises, on marks or on the last word.** It
   never turns a mark off, never widens one that survives by identity, and its
   tokenizer glues trailing whitespace to the word, so appending after the last word
   deletes and re-authors that word, and a loop append after a human's bolded last word
   is *refused*.
3. **`ink`, `history`, `_queued` and `settle` read the wrong key.** `ink` merges runs
   across the hand-off frontier so unsent words read as sent; `history` keys on the
   client's hint block so an op can belong to no feature; `settle` marks a request
   realized on any reflect pass that touches the feature, including a person's hand
   edit unrelated to the request.
4. **Authority is not what §7.3 says.** `Roles.policy` lets `agent:*` accept plans,
   hand off and mark features realized, and lets a `suggest` actor tag ops with
   *someone else's* open plan so they land when the maintainer accepts that plan.

Everything else is a first-pass list. Complexity is not urgent at study scale but
`publish` is O(features × ops) per filesystem event (258 ms at 1,500 features /
13,500 ops, measured), and it runs on every batch including `no-op`.

---

## A. Correctness bugs

Each item has a pasteable test using the helpers in `tests/calculus/test_apply.py`,
`tests/calculus/test_ingest.py` (`run_ingest`), `tests/calculus/test_derive_export.py`
(`build`) and `tests/loops/test_reflect.py` (`seed`, `chunk`). `T(s, bid)` is
`s.blocks[bid].text().live_text()`. Assertions state the observed behaviour; flip the
`BUG` line for the regression test.

### A1. `ink` merges runs across the hand-off frontier: unsent words read as sent

`ink` iterates `attribution(t)` with `merge=True`, which joins adjacent segments by
`(actor, plan)` and keeps the *first* segment's `op`. A person who typed before the
hand-off and again right after it gets one run whose `op` is inside the frontier, so
the new words are staged `sent`, `phase` never says `edited`, and the directive's
"Changed since" line quotes words the person has not sent.

```python
def test_ink_reports_unsent_words_as_sent_when_adjacent_to_sent_ones():
    m, s = Mint(), Store()
    heading(m, s, "h1", "Client", actor="loop")
    ins = paragraph(m, s, "p1", "Retries once.", after="h1", actor="loop")
    a = apply(s, m.op(K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": [ins.id, 12], "text": " A"}))
    apply(s, m.op(K.FEATURE_HANDOFF, {"fid": "h1"}, {}))
    apply(s, m.op(K.BIND_ATTACH, {"fid": "h1"}, {"file": "c.py", "symbol": "C"}))
    apply(s, m.op(K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": [a.id, 1], "text": " B"}))  # unsent
    assert [(c.stage, c.text) for c in ink(s, "h1")] == [("sent", " A B")]   # BUG: " B" is unsent
    assert phase(s, "h1") == "queued"                                         # BUG: edited
    assert 'added: " A B"' in directive(s, "h1")                              # BUG
```

Fix: in `ink`, walk segments (`attribution(t, merge=False)`) and merge by
`(actor, plan, stage)`; or give `attribution` a `key` callable and pass the stage in.
Same latent fault in `plan_claims` (a plan's run merged with an accepted plan's run on
the same actor is impossible today only because `plan` is in the merge key).

### A2. `plan.accept` with `subset` is not replayable: rebuild accepts everything (I6)

`_h_plan_accept`'s canonical payload is `{accepted, rejected, deleted_blocks, **record}`;
the wire `subset` is dropped. `_wire()` claims "the canonical payload is a superset of
the wire payload for every kind". It is not for this one: on replay `subset is None`
and the handler accepts the whole plan. The kernel review's A6 put `subset` into
*reopen's* payload; the accept itself was missed. `tests/calculus/test_apply.replay`
would show it too, but no subset accept is replayed anywhere.

```python
def test_subset_accept_replays_as_accept_all(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "p1"}, "payload": {"kind": "paragraph", "after": START}}], actor="human:local")
    ins = ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": START, "text": "base"}}], actor="human:local")[0]
    pid = ws.open_plan(actor="agent:x")
    i1 = ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": [ins.id, 3], "text": " A"}}], actor="agent:x", plan=pid)[0]
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": [i1.id, 1], "text": " B"}}], actor="agent:x", plan=pid)
    acc = ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {"subset": [i1.id]}}], actor="human:local")[0]
    assert T(ws.store, "p1") == "base A" and "subset" not in acc.payload
    ws.close()
    ws2 = Workspace(tmp_path / ".codoc")
    assert T(ws2.store, "p1") == "base A B"                                   # BUG: I6
```

Fix (apply.py, one line): `canonical = {"subset": subset, "accepted": accepted, ...}`
(store the wire subset as given, `None` when absent; the closure is already in
`accepted`).

### A3. A restored mark's `actor`/`op` are not replayable (I6)

`_h_text_mark` honours `payload["actor"]`/`payload["op"]` on the way in (so an undo
restores a displaced mark with its original placer) but its canonical payload does
not carry them. Rebuilding the log re-applies the undo with `actor = op.actor`,
`placer = op.id`, so `Mark.op` (and `Mark.actor` when the undoer differs from the
original author) change on every restart. `state_fingerprint` includes both.

```python
def test_restored_mark_placer_is_not_replayable(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "p1"}, "payload": {"kind": "paragraph", "after": START}}], actor="human:local")
    ins = ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": START, "text": "the client"}}], actor="human:local")[0]
    ws.submit([{"kind": K.TEXT_MARK, "target": {"bid": "p1", "path": "text"},
                "payload": {"kind": LINK, "attrs": {"href": "https://a"}, "start": [ins.id, 0], "end": [ins.id, 9], "on": True}}], actor="human:local")
    disp = ws.submit([{"kind": K.TEXT_MARK, "target": {"bid": "p1", "path": "text"},
                       "payload": {"kind": LINK, "attrs": {"href": "https://b"}, "start": [ins.id, 4], "end": [ins.id, 9], "on": True}}], actor="loop")[0]
    ws.submit([dict(b) for b in disp.inverse], actor="human:local")           # undo
    ws.close(); ws2 = Workspace(tmp_path / ".codoc")
    assert sorted(m.op for m in ws.store.blocks["p1"].text().marks) != \
           sorted(m.op for m in ws2.store.blocks["p1"].text().marks)          # BUG: I6
    assert ws.store.state_fingerprint() != ws2.store.state_fingerprint()
```

Fix: add `actor`/`op` to the canonical when they were given (`if "actor" in
op.payload: canonical["actor"] = actor`, same for `op`). The kernel review's D item
about restricting who may set them to `owner` still applies.

### A4. Rebuild quarantines committed ops, then writes on top of the resulting state

`Workspace._replay_one` calls `clock.observe(op.at)` and drops the op into
`quarantined` on `SkewError`. That check belongs at *arrival* (spec §7.2, "an op whose
`at` is more than ten minutes ahead of the applier's wall clock"); at rebuild the op is
already the truth on disk. A collaborator's fast clock, or an NTP correction backwards
on this machine, makes the daemon rebuild a store the log does not describe, open a new
session on it, and record `unknown_anchor` on every later op that names the quarantined
characters. Two machines then diverge permanently on the same log set.

```python
def test_rebuild_quarantines_a_committed_op_and_then_writes_on_top(tmp_path):
    from codoc.substrate.clock import fmt
    ws = Workspace(tmp_path / ".codoc")
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "p1"}, "payload": {"kind": "paragraph", "after": START}}], actor="human:local")
    ws.clock.wall += 60 * 60 * 1000 - 1                                        # a peer's fast clock stamped this
    ins = ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": START, "text": "hello"}}], actor="human:local")[0]
    ws.close(); ws2 = Workspace(tmp_path / ".codoc")
    assert [o.id for o in ws2.quarantined] == [ins.id] and T(ws2.store, "p1") == ""
    nxt = ws2.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": [ins.id, 4], "text": "!"}}], actor="human:local")[0]
    assert nxt.conflict == "unknown_anchor"                                   # BUG
```

Fix: replay applies everything (`observe` without the skew raise, or `observe(...,
strict=False)`); the skew check moves to `drain_inbox`/`submit` for ops that arrive
with their own `at` (today none do: the daemon stamps), and to `read_session` as a
*report* (`LogProblem`) rather than a skip. Also: the same `SkewError` applies to a
snapshot's ops.

### A5. `ingest` is not a fixed point on marks

Three faults, one cause: step 5 only asks "does an existing mark of this kind/attrs
cover a survivor of the new span"; it never compares the *extent*, and never emits
`on=False`.

(a) A bold the new text no longer has stays.

```python
def test_ingest_keeps_a_bold_the_new_text_no_longer_has():
    m, s = Mint(), Store()
    heading(m, s, "h1", "C"); ins = paragraph(m, s, "p1", "alpha beta", after="h1")
    apply(s, m.op(K.TEXT_MARK, {"bid": "p1", "path": "text"}, {"kind": BOLD, "attrs": {}, "start": [ins.id, 0], "end": [ins.id, 4], "on": True}))
    res, applied = run_ingest(s, Mint("h", "human:a"), "p1", "alpha **beta**", actor="human:a")
    assert markdown(s.blocks["p1"].text()) == "**alpha** **beta**"            # BUG: "alpha **beta**"
```

(b) A bold that survives by identity is not widened to the new span.

```python
def test_ingest_does_not_widen_a_bold_that_survives_by_identity():
    m, s = Mint(), Store()
    heading(m, s, "h1", "C"); ins = paragraph(m, s, "p1", "Retries once via x.", after="h1")
    apply(s, m.op(K.TEXT_MARK, {"bid": "p1", "path": "text"}, {"kind": BOLD, "attrs": {}, "start": [ins.id, 8], "end": [ins.id, 11], "on": True}))
    res, _ = run_ingest(s, Mint("h", "human:a"), "p1", "**Retries once** via x.", actor="human:a")
    assert markdown(s.blocks["p1"].text()) == "Retries **once** via x."      # BUG
```

(c) The tokenizer glues trailing whitespace to the word (`\S+\s*`), so `"once"` at
the end of a field and `"once "` in the middle are different tokens. Appending a word
therefore deletes and re-mints the person's last word as the loop's, and if that word
was bolded by a person the whole call is *refused*.

```python
def test_appending_after_a_bolded_last_word_is_refused_for_the_loop():
    m, s = Mint(), Store()
    heading(m, s, "h1", "C"); ins = paragraph(m, s, "p1", "Retries once", after="h1")
    apply(s, m.op(K.TEXT_MARK, {"bid": "p1", "path": "text"}, {"kind": BOLD, "attrs": {}, "start": [ins.id, 8], "end": [ins.id, 11], "on": True}))
    res, _ = run_ingest(s, Mint("l", "loop"), "p1", "Retries **once** more")
    assert res.refused == "would_drop_human_bold"                             # BUG: nothing was dropped
    m2, s2 = Mint(), Store(); heading(m2, s2, "h1", "C"); paragraph(m2, s2, "p1", "one two", after="h1")
    res2, _ = run_ingest(s2, Mint("l", "loop"), "p1", "one two three")
    assert res2.deleted == 3                                                  # BUG: "two" re-authored
```

Fix. For (c): tokens are `\S+` and `\s+` separately, and `SequenceMatcher(isjunk=
str.isspace, ...)`: junk tokens are never match *anchors* but are extended into a
neighbouring match, which is exactly "whitespace never survives on its own" without
gluing it to the word. For (a)/(b): after the diff, compute each existing mark's
surviving live span in new-text offsets; a new span that shares a survivor with it and
has the same extent is kept; a different extent emits `on=True` over the new span (bold
merges; link/cite displace and the inverse restores) and `on=False` over the pieces of
the old span the new text does not cover; an existing mark with survivors but no new
span emits `on=False` over its survivors. Then the §9 property `ingest(new) ⇒ markdown
== new` can be a real test (E1).

### A6. `inline_md` cannot round-trip bold overlapping a link or cite

`render`'s docstring says "only bold can overlap either". Partial overlap produces
unbalanced markup because the re-opened inner span is never pushed back on the stack;
full nesting produces well-formed markdown that `parse` (a flat regex alternation)
reads as a *link whose label contains literal asterisks*. The export→import→export test
passes on such a document because the stars are re-rendered verbatim, while the store
has gained six characters and lost a mark.

```python
def test_bold_inside_cite_survives_export_only_as_literal_stars():
    assert render("abc", [Span(LINK, {"href": "u"}, 0, 2), Span(BOLD, {}, 1, 3)]) == "[a**b****](u)c**"   # BUG
    m, s = Mint(), Store()
    heading(m, s, "b-000000000001", "T"); ins = paragraph(m, s, "b-000000000002", "see the client now", after="b-000000000001")
    apply(s, m.op(K.TEXT_MARK, {"bid": "b-000000000002", "path": "text"},
                  {"kind": CITE, "attrs": {"file": "c.py", "symbol": "C"}, "start": [ins.id, 4], "end": [ins.id, 13], "on": True}))
    apply(s, m.op(K.TEXT_MARK, {"bid": "b-000000000002", "path": "text"}, {"kind": BOLD, "attrs": {}, "start": [ins.id, 8], "end": [ins.id, 13], "on": True}))
    md1 = export_markdown(s); s2 = Store(); build(s2, Mint("t"), import_markdown(md1))
    assert export_markdown(s2) == md1                                        # textually a fixed point…
    p2 = [b for b in s2.ordered() if b.kind == "paragraph"][0]
    assert p2.text().live_text() == "see the **client** now"                 # BUG: …but the stars are now characters
    assert [mk.kind for mk in p2.text().marks] == [CITE]                       # BUG: and the bold is gone
```

Fix: parse in two passes (links/cites first, then bold *inside labels and outside*,
offsets mapped back), and render by splitting bold at link/cite boundaries so every
bold run is either wholly inside a label or wholly outside (a bold that straddles a
link edge becomes two bolds in the output, which parses back to two marks that
`set_mark` merges). Also escape the three token starts in plain text on render
(`\*\*`, `\[`, a leading `\#`) and unescape on parse; see A7.

### A7. `export` is not injective: paragraphs that look like structure import as structure

`export_markdown` writes paragraph text raw; `import_markdown` matches `_HEADING` /
`_FENCE` on any line. A paragraph whose text begins with `# … {#b-…}` comes back as a
heading with that id (a duplicate id if it names a real one); literal `**x**` typed as
characters comes back as a mark. `_HEADING`'s `\s*` also strips a title's trailing
whitespace and `flush_para` strips a paragraph's leading/trailing whitespace, so those
are not fixed points either.

```python
def test_literal_markdown_in_prose_is_not_escaped_on_export():
    m, s = Mint(), Store()
    heading(m, s, "b-000000000001", "T")
    paragraph(m, s, "b-000000000002", "# Not a heading {#b-000000000009}", after="b-000000000001")
    paragraph(m, s, "b-000000000003", "type **stars** to bold", after="b-000000000002")
    bodies = import_markdown(export_markdown(s))
    adds = [(b["kind"], b["payload"].get("kind")) for b in bodies if b["kind"] == K.BLOCK_ADD]
    assert adds[1] == (K.BLOCK_ADD, "heading")                                # BUG
```

Fix: `render` escapes (`\*` when two stars would form a token, `\[` before a `](`
target, `\#` at line start when followed by `{#`); `parse` honours the escapes; `import`
does not strip. Or accept the loss and say so in the module docstring and in `04` §3
("the export is a fixed point for text that contains none of the three forms").

### A8. `Roles.policy`: agents may write verdicts, hand-offs and `feature.realized`

Spec §7.3 and the comment on `ops.VERDICTS`: "the shipped policy denies it to every
`agent:*` actor". `Roles.role` maps `agent:*` to `machine`, and `policy` returns `None`
for `machine`.

```python
def test_agent_can_accept_plans_and_mark_features_realized(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "h1"}, "payload": {"kind": "heading", "level": 1, "after": START}}], actor="human:local")
    pid = ws.open_plan(actor="loop")
    assert ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor="agent:claude-code")[0].conflict is None   # BUG
    assert ws.submit([{"kind": K.FEATURE_REALIZED, "target": {"fid": "h1"}, "payload": {}}], actor="agent:claude-code")[0].conflict is None   # BUG
```

And a `suggest` actor may tag ops with *any* open plan, so the contributor's words land
when the maintainer accepts the loop's (or another contributor's) plan:

```python
def test_suggest_role_can_ride_someone_elses_open_plan(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "p1"}, "payload": {"kind": "paragraph", "after": START}}], actor="human:local")
    pid = ws.open_plan(actor="loop", note="the loop's repair")
    op = ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": START, "text": "smuggled"}}],
                   actor="human:contrib", plan=pid)[0]
    assert op.conflict is None and op.id in ws.store.plans[pid].ops           # BUG
```

Fix: `Policy` becomes `Callable[[Store, Op], str | None]` (apply.py passes the store);
`Roles.policy` denies `op.kind in K.VERDICTS` to `machine` unless `actor == "loop"` and
`kind == FEATURE_REALIZED` (the daemon's own settle), and for `suggest` requires
`store.plans[op.plan].actor == op.actor` (or `op.kind == PLAN_OPEN`). `Roles.role`
also matches `startswith("import")` which catches `importer-x`; use `== "import"`.

### A9. `read_inbox` advances past a torn trailing line; the op is lost with no ack

`read_inbox` reads to EOF and sets `end = offset + len(data)`. A line the writer has
not finished (Python's buffered text writer flushes at 8 KB boundaries mid-line, so
`submit`'s "one small write per line" is not one syscall per line) fails `json.loads`
and is skipped, and the completed line is never re-read. The comment says "the client
resubmits (idempotent by cid)", but the client has no signal: no ack arrives for a
line the daemon never saw.

```python
def test_torn_inbox_line_is_skipped_and_never_reread(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "p1"}, "payload": {"kind": "paragraph", "after": START}}], actor="human:local")
    d = L.inbox_dir(tmp_path / ".codoc"); d.mkdir(parents=True, exist_ok=True)
    line = json.dumps(Submission(cid="c1", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"}, payload={"after": START, "text": "X"}).__dict__)
    (d / "ide.jsonl").write_text(line[:20]); assert ws.drain_inbox({"ide": "human:local"}) == []
    with (d / "ide.jsonl").open("a") as fh: fh.write(line[20:] + "\n")
    assert ws.drain_inbox({"ide": "human:local"}) == [] and T(ws.store, "p1") == ""   # BUG
```

A submission with `"target": null` also raises `TypeError` out of `_mint` after the
offset was advanced, losing every later line of that read:

```python
def test_null_target_submission_crashes_the_drain_and_loses_the_batch(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "p1"}, "payload": {"kind": "paragraph", "after": START}}], actor="human:local")
    d = L.inbox_dir(tmp_path / ".codoc"); d.mkdir(parents=True, exist_ok=True)
    good = json.dumps({"cid": "c2", "kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": START, "text": "X"}})
    (d / "ide.jsonl").write_text(json.dumps({"cid": "c1", "kind": K.TEXT_INSERT, "target": None, "payload": {}}) + "\n" + good + "\n")
    with pytest.raises(TypeError): ws.drain_inbox({"ide": "human:local"})   # BUG
    assert ws.drain_inbox({"ide": "human:local"}) == []                       # BUG: c2 gone
```

Fix: `end = offset + data.rfind(b"\n") + 1` (only consume complete lines; an unterminated
tail is re-read next drain); `Submission` validation in `read_inbox` (`kind` a string,
`target`/`payload` dicts, `cid` a string) with a rejection ack `{"cid", "error"}` so the
client learns; advance `_inbox_offsets[client]` *per line* after the ack, not per read.
Use `open(..., buffering=1)` or `os.write` in `submit` so a line is one write.

### A10. A rejected `add_node` plan leaves its bindings on a dead heading (I8 zombie)

`_add_plan` attaches bindings on the trunk with the comment "rejecting the plan deletes
the node and its bindings with it". Reject kills the empty blocks; nothing detaches.
The chunk is then "bound" to a dead feature: the coverage net skips it (`key in
store.bindings`), `select_context` skips the dead owner silently, and the LLM is told a
dead `current_feature_id` on the next change.

```python
def test_rejecting_an_add_plan_orphans_its_trunk_bindings(tmp_path):
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    propose = lambda c, s, t, **kw: [{"kind": "add_node", "parent_id": "h1", "title": "Storage", "description": "Saves.", "bindings": [["s.py", "s.py::save"]]}]
    r = reflect(ws, ChangeSet(added=[chunk("s.py", "s.py::save", "f9", "t9")]), Graph(), propose=propose, index_keys={("s.py", "s.py::save")})
    pid = r.plans[0]; fid = ws.store.plans[pid].target
    ws.submit([{"kind": K.PLAN_REJECT, "target": {"pid": pid}, "payload": {}}], actor="human:local")
    assert not ws.store.blocks[fid].live and ws.store.bindings[("s.py", "s.py::save")].feature == fid   # BUG
```

Fix: either `bind.attach` becomes `PLANNABLE` (its inverse is `detach`; reject applies
it; the kernel's `_undo_plan_op` gains a `BIND_ATTACH` branch), or `plan.reject` for
`kind=add` records and detaches the target's bindings the way `plan.accept(delete)`
should (kernel review B4). The first is cleaner: the attach *is* part of the proposal.

### A11. `settle` realizes a request on any reflect that touches the feature

`_causes` cites the latest hand-off on every loop op for a queued feature, and `settle`
records `feature.realized` when any `bind.*` op carries that citation. A person fixing
a typo in bound code by hand, with no agent session, marks their own pending request
implemented. `test_handoff_queues_directive_and_reflect_under_it_realizes` enshrines
this: it never opens an epoch.

```python
def test_a_hand_edit_to_code_marks_a_queued_request_realized(tmp_path):
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    t = ws.store.blocks["p1"].text()
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": list(t.last_live_ref()), "text": " Retry five times."}}], actor="human:local")
    handoff(ws, "h1", actor="human:local", asked="retry five times")
    r = reflect(ws, ChangeSet(modified=[chunk("c.py", "c.py::send", "f2", "t1")]), Graph(), propose=lambda *a, **k: [])
    settle(ws, r.caused, sha="typo")
    assert phase(ws.store, "h1") == "synced"                                  # BUG
```

Spec §3: realized is emitted "when a reflect batch cites the hand-off in `caused_by` and
the session that ran it has closed its epoch". Fix: `Daemon.reflect` passes
`under_epoch: bool` (the pass was triggered by an epoch close or stale recovery, or the
epoch's `touched` names the feature) and `reflect()` only cites `caused_by` then;
`settle` is called from the epoch-close branch only. The opposite gap also exists: a
directive whose implementation changes no bound symbol's fingerprint never settles and
stays queued forever (B7).

### A12. `history` keys on the client's hint block; an op can belong to no feature

`tb = o.target.get("bid") or ... or o.payload.get("bid")` short-circuits on the wire
hint. After a merge moved the characters, an insert sent with the stale hint lands in
block `a` (canonical `payload.bid == "a"`) but `history` files it under the dead
block `b`, which no live feature dominates.

```python
def test_history_loses_an_op_whose_hint_block_is_dead():
    m, s = Mint(), Store()
    heading(m, s, "h1", "One"); paragraph(m, s, "a", "aaa", after="h1")
    heading(m, s, "h2", "Two", after="a"); b = paragraph(m, s, "b", "bbb", after="h2")
    apply(s, m.op(K.BLOCK_MOVE, {"bid": "b"}, {"after": "a"}))
    apply(s, m.op(K.BLOCK_MERGE, {"into": "a", "bid": "b"}, {}))
    ins = apply(s, m.op(K.TEXT_INSERT, {"bid": "b", "path": "text"}, {"after": [b.id, 2], "text": "!"}))
    assert ins.payload["bid"] == "a"
    assert ins not in history(s, "h1") and ins not in history(s, "h2")       # BUG
```

Fix: for `text.*` ops read `o.payload["bid"]` first (the canonical); `_drifted` and
`_op_touches` already check both, `history` should too. Better: a per-block op index
(C1) that `record` fills from the canonical.

### A13. `_mark_text` returns the first same-kind span, so two Focus bolds read alike

`Focus:` is rendered by re-finding the mark's text through `live_spans` by `(kind,
attrs)`; every bold has `attrs == {}`.

```python
def test_two_human_bolds_yield_the_same_focus_text():
    m, s = Mint(), Store()
    heading(m, s, "h1", "C"); ins = paragraph(m, s, "p1", "Retries once and logs.", after="h1")
    apply(s, m.op(K.TEXT_MARK, {"bid": "p1", "path": "text"}, {"kind": BOLD, "attrs": {}, "start": [ins.id, 8], "end": [ins.id, 11], "on": True}))
    apply(s, m.op(K.TEXT_MARK, {"bid": "p1", "path": "text"}, {"kind": BOLD, "attrs": {}, "start": [ins.id, 17], "end": [ins.id, 20], "on": True}))
    apply(s, m.op(K.FEATURE_HANDOFF, {"fid": "h1"}, {}))
    assert 'Focus: "once"; "once"' in directive(s, "h1")                      # BUG: "once"; "logs"
```

Fix: take the text of the mark itself: the live characters between `locate(m.start)`
and `locate(m.end)` (`_mark_live_refs` in ingest.py already does this; move it to
`text.py` as `Text.mark_text(m)` and use it in both places).

### A14. `_amend` opens a plan per new paragraph, and an empty plan on a held no-op

For a held feature `_amend` opens a plan *before* any ingest runs; if the model's text
equals the current text the plan stays open with zero ops and is counted in
`status.json` and `projection["plans"]` forever. For a non-held feature each entry
without an id opens its own plan (`p = plan or ws.open_plan(...)` with `plan` never
assigned), and the `omitted` step opens a third.

```python
def test_amend_with_unchanged_text_on_a_held_feature_opens_an_empty_plan(tmp_path):
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    t = ws.store.blocks["p1"].text()
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": list(t.last_live_ref()), "text": " Mine."}}], actor="human:local")
    same = lambda c, s, t, **kw: [{"kind": "amend", "feature_id": "h1", "blocks": [{"id": "p1", "text": T(ws.store, "p1")}]}]
    r = reflect(ws, ChangeSet(modified=[chunk("c.py", "c.py::send", "f3", "t1")]), Graph(), propose=same)
    p = ws.store.plans[r.plans[0]]
    assert p.status == "open" and p.ops == []                                 # BUG

def test_one_amend_opens_a_plan_per_new_paragraph(tmp_path):
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    two = lambda c, s, t, **kw: [{"kind": "amend", "feature_id": "h1", "blocks": [{"id": "p1", "text": T(ws.store, "p1")}, {"text": "New one."}, {"text": "New two."}]}]
    r = reflect(ws, ChangeSet(modified=[chunk("c.py", "c.py::send", "f3", "t1")]), Graph(), propose=two)
    assert len(r.plans) == 2 and len({ws.store.plans[p].target for p in r.plans}) == 1   # BUG: two verdicts for one repair
```

Fix: compute the op bodies for the whole amend first (title ingest, each block ingest,
new paragraphs, omitted strikes) with `Workspace.ingest` split into `plan_ingest`
(pure: returns `Ingest`) and `commit(bodies, plan)`; open *one* plan lazily on the first
body that needs one, and none if nothing does.

### A15. `_amend` strikes every paragraph the model did not list

An amend whose `blocks` names only the paragraph it changed (the natural thing for a
model to do) puts every other paragraph of the feature under `suggest_del`.

```python
def test_amend_listing_only_the_changed_block_strikes_every_other_paragraph(tmp_path):
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "p2"}, "payload": {"kind": "paragraph", "after": "p1"}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "p2", "path": "text"}, "payload": {"after": START, "text": "Still true."}}], actor="loop")
    one = lambda c, s, t, **kw: [{"kind": "amend", "feature_id": "h1", "blocks": [{"id": "p1", "text": "Retries twice before giving up."}]}]
    r = reflect(ws, ChangeSet(modified=[chunk("c.py", "c.py::send", "f3", "t1")]), Graph(), propose=one)
    assert [c.stage for c in plan_claims(ws.store, "h1")] == ["removed"]      # p2 struck, unasked
```

This is a prompt-contract risk rather than a code fault, but the code is the only
place it can be made safe: require an explicit `{"id": …, "delete": true}` for a
strike and treat omission as "unchanged". The prompt in `agent/tree_update.py` is not
under review; whichever contract wins must be in one place.

### A16. `_valid_binds` lets the model attach a symbol the index just removed

`touched_keys = fp.keys() | removed keys`; `_valid_binds` accepts any key in it.

```python
def test_model_may_attach_a_symbol_that_no_longer_exists(tmp_path):
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "h2"}, "payload": {"kind": "heading", "level": 1, "after": "p1"}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "h2", "path": "text"}, "payload": {"after": START, "text": "Other"}}], actor="loop")
    propose = lambda c, s, t, **kw: [{"kind": "attach", "feature_id": "h2", "bindings": [["c.py", "c.py::send"]]}]
    cs = ChangeSet(removed=[chunk("c.py", "c.py::send", "f1", "t1")], added=[chunk("n.py", "n.py::x", "fz", "tz")], rows=[])
    reflect(ws, cs, Graph(), propose=propose, allow_delete=False)
    assert ws.store.bindings[("c.py", "c.py::send")].feature == "h2"         # BUG
```

Fix: `touched_keys = set(fp)` (added + modified only). And `index_keys=None` when
`cs.rows` is empty means "accept any key the model names" — it should mean "refuse
every attach", since the loop cannot verify it. The `or None` on line 76 of reflect.py
is the bug: use the empty set.

### A17. `Workspace.ingest` on a dead block records two conflict ops in the truth

`Workspace.ingest` checks `block is None` but not `block.live`, so a repair on a block
deleted between the LLM's read and its answer writes `dead_block` conflicts into the
session log (they are the truth forever) instead of refusing.

```python
def test_ingest_on_a_dead_block_logs_conflict_ops(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "p1"}, "payload": {"kind": "paragraph", "after": START}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": START, "text": "old"}},
               {"kind": K.BLOCK_DELETE, "target": {"bid": "p1"}, "payload": {}}], actor="human:local")
    res, ops = ws.ingest("p1", "text", base_text="old", new_md="new", actor="loop")
    assert res.refused is None and [o.conflict for o in ops] == ["dead_block", "dead_block"]   # BUG
```

Fix: `if not block.live: return Ingest(refused="dead_block"), []`. Same for a non-Text
`path` (`block.text(path)` raises `KeyError` uncaught).

### A18. `_queued` and `realize._covered` are two definitions of one fact

`_queued` tests one session of the hand-off frontier (the one with the largest `seq`,
which is not "the latest" across sessions) against `realized`; `queued()` uses
`_covered` (all sessions) for phantoms. They agree only because `feature.realized`
defaults `covers` to the whole hand-off frontier; a client-supplied partial `covers`
splits them.

```python
def test_queued_and_covered_disagree_on_a_partial_realization():
    a, b, s = Mint("a", "human:a"), Mint("b", "human:a"), Store()
    heading(a, s, "h1", "C", actor="loop"); ins = paragraph(a, s, "p1", "x", after="h1", actor="loop")
    apply(s, a.op(K.BIND_ATTACH, {"fid": "h1"}, {"file": "c.py", "symbol": "C"}))
    apply(s, b.op(K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": [ins.id, 0], "text": "y"}))
    apply(s, a.op(K.FEATURE_HANDOFF, {"fid": "h1"}, {}))                      # {a:5, b:1}
    w = s.watermarks("h1")
    apply(s, a.op(K.FEATURE_REALIZED, {"fid": "h1"}, {"covers": {"a": 5}}))
    assert _covered(w.handoff, w.realized) is False and _queued(s, "h1", w) is False   # BUG
    assert phase(s, "h1") == "synced" and [c.stage for c in ink(s, "h1")] == ["sent"]
```

Fix: delete `_queued`/`_max_op`; `queued = w.handoff and not covered(w.handoff,
w.realized)` with `covered` in `ops.py` next to `frontier_union`. Spec §3 defines
queued work as the set difference of two frontiers; that is `covered`.

### A19. Smaller, confirmed

- **`ground` with no hand-off paints every loop character green.** `since` defaults to
  `w.handoff == {}`, so a bootstrapped tree's every word is a `code/added` claim in
  `projection.json` (`test_A17` in the probe file). Default to "nothing" when there is no
  hand-off, or to the per-viewer cursor the cut list names (§2 "Catch up").
- **`written_by` is per feature, not per block** (`test_A19`). One human character in
  any paragraph puts every paragraph of the feature under `GATE_HUMAN`; spec §4.7 says
  "a block whose last live author is a person". Also "newest by `at`" counts an open
  plan's segments, so an agent's pending proposal makes the feature "agent"-written.
- **`export` commits open-plan text as prose** (`test_A20`): `tree.codoc` is tracked and
  renders `markdown(..., "live")`, so an un-vetted proposal enters git as fact. Render
  the export in `rejected` mode (a plan is not the document until accepted); `04` §3
  should say which.
- **`projection.json` never says `realizing`** (`test_A21`): `Daemon.process_batch` calls
  `publish(...)` without `realizing=`, so the projection's phases ignore the lease that
  `write_status` preserves. Read the lease (`realizing_is_fresh`) and pass the queued
  set as `realizing` to `projection`.
- **`_new_marks` includes `on=False` ops**: an unbold's `added` pieces become Focus
  lines. Filter `o.payload.get("on", True)`. It also ignores the hand-off frontier
  (spec: "the ops between the frontiers"), which is consistent with "plus the person's
  ink" but inconsistent with `_human_changes`, which reports `sent` only.
- **`_cites` builds `Edit only:` from every cite in the feature**, live or dead, inside a
  comment or not. Spec: "`cite` marks inside open comment spans". Until comments exist
  this must emit nothing, or the agent is scoped to whatever the description happens to
  cite rather than to the feature's binding set.
- **`Refused` is raised and never caught** (`_ref_for`). It is unreachable today (every
  new-text offset is in an `equal` or `insert` hunk), so either delete the exception or
  map it to `res.refused`.
- **`reflect`'s `except (KeyError, TypeError, ValueError): continue`** around the
  raw-op loop swallows a half-built plan (`_add_plan` after `open_plan`) as "success":
  the plan stays open with a heading and no title. Catch per helper *before* the first
  submit, or record `res.refused.append((kind, repr(e)))`.
- **`_relocations` rename pass** counts uniqueness among *bound* removed chunks only
  (`removed = [r for r in cs.removed if … in removed_owner]`), so an unbound removed
  chunk with the same `types_hash` no longer blocks the pairing the way v1's pass 3
  (`rem_rem_count` over all `cs.removed`) did. Count shapes over all of `cs.removed`.
- **`Graph.update`**: `_add` on an existing key calls `_remove`, which never drops the
  key from `_by_file`, so `_by_file` grows stale keys (harmless with the `in _edges`
  guard, but `save()` is fine only because it serialises `_edges`). `rebuild` calling
  `self.__init__` works but reads as a mistake; build a new instance.
- **`Daemon.process_batch` rotates the session on a `HEAD` change but does not
  rebuild.** After `git checkout other-branch` the files under `.codoc/log/` are a
  different set, the in-memory store is not, and the new session log is written against
  a state the disk logs do not describe (I6). `04` §1 says the daemon "closes on every
  change of HEAD"; the code must also `ws.rebuild()` there (and re-derive
  `_inbox_offsets` is unaffected). The `.codoc` directory is in `SKIP_DIRS`, so a
  `git pull` that adds a peer's session file is also never noticed until restart.

---

## B. Spec deviations

Which side should move is stated per item.

- **B1. Ingest step 1 is "equals the current live text", not "equals the text at
  `base`".** The reconstruct-by-un-apply is not implemented; a stale LLM answer is
  refused as `stale_base` instead of demoted to a plan (step 4 then fires only when the
  caller passes the current text with an old frontier, as `_amend` never does). **Spec
  changes** to the simpler rule the code has: "`base_text` must equal the field's
  current live text; the caller re-reads otherwise. `base` is recorded on the ops."
  Then delete `demote_to_plan="edited_since_base"` and its test, since the held-feature
  check in `_amend` is what actually implements row 13.
- **B2. `ingest` records `base={}` on its ops.** `Workspace.ingest` computes `base` for
  the diff but mints ops with the caller's `base` argument (`None` → `{}`). Spec §2:
  "authoritative for `ingest`". **Code changes**: pass the computed frontier.
- **B3. `phase` puts `realizing` before `edited`.** Spec §6 lists `synced · edited ·
  queued · realizing …` with "doc-wins ordering"; the code checks the lease first, so a
  person's unsent edit is hidden for the lease's lifetime (up to 300 s). **Code changes**
  (`PHASES` order and the first `if`).
- **B4. `drifted` is defined differently.** Spec: "`bind.refresh` or `detach` on f beyond
  realized". Code: the newest of {binding change, prose edit by anyone, realized} is a
  binding change. The code's rule is the usable one (the spec's leaves every
  never-handed-off feature drifted forever after the first refresh). **Spec changes.**
  Also state that `bind.attach` (relocation) does not count.
- **B5. `history` does not "say so".** Spec: ops on blocks the feature no longer
  dominates "are omitted, and it says so". Code omits silently. Also conflicted ops
  (including `authority` refusals) are hidden. **Code changes**: return
  `(ops, omitted_count)` or include a synthetic marker; show conflicts with their code.
- **B6. `feature.realized` authority** (A8): spec §7.3 and `ops.py` say the shipped
  policy denies it to agents. **Code changes.**
- **B7. Realization evidence.** Spec §3: realized when a reflect batch cites the hand-off
  and the epoch closed. Code: when a `bind.*` op cites it, epoch or not (A11), and never
  otherwise, so a request whose implementation leaves every bound fingerprint unchanged
  (a config change, a new file the model does not attach) is queued forever. **Both
  change**: spec names the escape hatch (a `feature.realized` the *owner* may write from
  the CLI/IDE, "mark done"); code gates `caused_by` on the epoch.
- **B8. One actor per session file.** Spec §7.1 and `04` §2: "one actor per session
  file … a file whose ops carry more than one actor … is rejected at load". The code's
  session is one *daemon* lifetime carrying every actor (human, loop, agent), and
  `read_session` does not check actors. The code's model is the right one (the daemon is
  the only applier; the actor is stamped per op). **Spec changes**; the hub's
  validation then checks roles per op, not actors per file.
- **B9. Directive `Edit only:`** (A19): spec scopes it to cites inside open comments.
  **Code changes** (emit nothing until comments exist).
- **B10. Export read mode** (A19): spec §6 does not say; `04` §3 does not say. **Spec
  states `rejected`**; code follows.
- **B11. Suggest role and plan ownership** (A8): spec §7.3 "`suggest` may write
  plan-tagged ops" does not say *whose* plan. **Spec adds "of its own plans"**; code
  enforces.
- **B12. Snapshot ops and skew** (A4): spec §7.2 quarantines at arrival; code at
  rebuild. **Code changes.**
- **B13. `deleted_fraction` and the gate live in different places.** Spec §4.7 says the
  *call* records the fraction and the gate opens a plan "instead of landing on the
  trunk". Code: `ingest()` records it, `Workspace.ingest` gates, `_amend` picks the
  threshold from `written_by` per feature (A19). Fine as a split, but the threshold
  must be per block (`written_by(store, bid)` over that block's live segments, plan
  segments excluded).

---

## C. Complexity

Measured on a synthetic store (1,500 features, one paragraph each, one binding each,
four refresh rounds; 13,500 ops):

| call | cost | scales with |
|---|---|---|
| `_drifted(f)` | 6.0 ms | ops |
| `phase(f)` × 1,500 | 46 ms | features × ops |
| `history(f)` | 3.9 ms | ops |
| `publish()` | 258 ms | features × ops, computed **four times** (projection phases, `write_status` phases, `queued()` in `write_status`, `queued()` in `render_queue`) |

`publish` runs on every debounced filesystem batch including `no-op`, and
`select_context` calls `phase` for every heading (`all_titles`) on every reflect. At
the study's scale (25 features) none of this is visible; at a real repository (500 to
3,000 features, tens of thousands of ops after a month) the daemon spends most of each
batch re-deriving what did not change.

Every O(ops) scan has the same shape: "the newest op of kind K on feature f", or "ops
on block b". Three indexes maintained in `Store.record` remove all of them:

```python
class Store:
    ops_by_block: dict[str, list[int]]      # bid -> positions of ops whose canonical bid/into/fid is bid
    last_by_fid: dict[str, dict[str, int]]  # fid -> {"bind": pos, "prose": pos, "realized": pos, "handoff": pos}
    plans_on: dict[str, set[str]]           # bid -> open plan ids with an op on this block (drop on verdict)
```

`record(op, touched, structural)` already receives `touched` (the canonical block set);
append `pos` to `ops_by_block[b]` for each `b in touched`, and for `bind.*` /
`feature.*` update `last_by_fid[fid][...]`; for `text.*` set
`last_by_fid[feature_of(bid)]["prose"] = pos` (one `feature_of` per text op, O(distance)
after the kernel's C4). `apply` appends `op.plan` to `plans_on[b]` for each touched
block, and the verdict handlers remove the plan from every block it listed (walk
`plan.ops`). Then:

- `_drifted(f)`: `d = last_by_fid[f]; return d["bind"] > max(d["prose"], d["realized"])`. O(1).
- `history(f)`: `sorted(set().union(*(ops_by_block[b] for b in body_ids(f))))`. O(result).
- `open_plans_on(f)`: `set().union(*(plans_on[b] for b in body_ids(f)))`. O(blocks).
- `_new_marks(f)`: `ops_by_block[b]` filtered by kind. O(ops on f).
- `_causes`: `last_by_fid` gives the latest hand-off; `queued` is `covered` (A18). O(features).
- `phase(f)`: O(blocks of f) from `ink` alone; cache per `(fid, block_head max, structural_head, watermark head)` and reuse across the four `publish` callers.
- `publish`: compute `phases` once, pass into `write_projection`, `write_status`,
  `write_queue`; skip the whole thing when `store.pos == last_published_pos` and the
  lease state is unchanged (`Daemon` keeps `last_published`).

Also: `_acked_cids` re-reads every ack file on every drain (O(total acks ever)); load once
into `self._acked` at startup and add to it in `drain_inbox`. `SessionWriter.append`
fsyncs per op (an ingest is 5 to 30 ops); fsync once per `submit`/`ingest`/`drain`
batch. `realize.withdraw` scans `store.ops` for an id `store.by_id` holds.
`_graph_context`'s `for op in reversed(store.ops)` stops at five hits but walks all ops
when the seed features are quiet; use `ops_by_block`.

---

## D. Cleanliness

- **Three copies of "live span of a mark"**: `derive.live_spans` + `_first_live`,
  `derive._suggest_spans` (a filtered copy of `live_spans` that keeps `SUGGEST_DEL`
  instead of dropping it), `ingest._mark_live_refs` + `_mark_covers`. One method on
  `Text`: `live_span(m) -> (lo, hi) | None` in live offsets, and `mark_text(m)`. Then
  `live_spans(text, kinds=...)` is a comprehension and A13 disappears.
- **The `"view"` sentinel** in `derive.markdown` is written on a clone and read nowhere
  (`ground` and `_human_changes` iterate the store's own `Text`, which can never hold
  it). Delete the two `!= "view"` checks; `markdown`'s clone should be `text.clone()`
  (the intermediate `Text([s for s in text.segs], list(text.marks))` shares the `Seg`
  objects for one statement and then copies them; it is correct and pointless).
- **Raw LLM op dicts.** `reflect` dispatches on `raw.get("kind")` with `.get` chains and
  a blanket `except`. A `TypedDict` per kind (`Attach`, `AddNode`, `Amend`, `MoveNode`,
  `RetireNode`) with one `parse_raw(dict) -> RawOp | None` that validates shape *before*
  any submit makes the half-built-plan class (A19) impossible and gives `propose` a
  stated contract. The `propose` injection signature `(changes, subtree, titles,
  repo_name=, config=, doc_language=) -> list[dict]` should be a `Protocol` in
  `loops/context.py` so tests and `agent/tree_update.py` agree by type.
- **`reflect` should return the ops it wrote.** `ReflectResult` carries counters and plan
  ids; `settle`, `divergent` and the tests then rediscover the ops by scanning
  `store.ops` for `caused_by`. Collect every `Op` `ws.submit`/`ws.ingest` returned into
  `res.ops`; `settle(ws, res)` reads `res.ops` and `daemon.reflect` stops passing
  `res.caused` around.
- **Two ingest paths in `Workspace.ingest` and `run_ingest` (test helper)** resolve
  `$ins` differently (the helper resolves before applying any insert and relies on
  op-id predictability). Give `Workspace` a pure `bodies_for(ingest_result, plan) ->
  list[dict]` step and have the test call it.
- **`Workspace.submit` applies a batch op-by-op after a conflict.** §3.1 makes the
  batch the undo unit; a batch whose third op conflicts leaves two applied. Decide:
  either "stop at the first conflict and record the rest as `conflict='batch_aborted'`"
  or say in the docstring that batches are not atomic. `_delete_plan` and `_add_plan`
  currently depend on the latter.
- **Dead or misnamed**: `derive._blocks_of` is `store.feature_body`; `derive.Run.op`
  is the first segment's op after merging (misleading; A1); `publish.SYNCED…PROPOSED`
  duplicate `derive.PHASES`; `realize._covered` duplicates what `derive._queued` should
  be; `Ingest.demote_to_plan` is both an input flag and an output reason; `Refused` is
  never caught (A19); `inline_md.Span.attrs` is a mutable dict on a frozen dataclass.
- **`Roles.role`'s machine test** hard-codes four prefixes; `Actor` in the spec is a
  closed grammar, so parse it once (`actor_kind(actor) -> "human" | "agent" | "loop" |
  "translator" | "import"`) in `ids.py` and use it in `Roles`, `ingest._MACHINE_ACTORS`,
  `derive.HUMAN/LOOP` and `context.written_by`.
- **`daemon.identity`** maps inbox file names to actors; the file name is the identity,
  so any local process that writes `ide.jsonl` is `human:local`. Fine for the local
  trust model; the docstring should say so, and the hub must stamp from its session
  cookie *before* the daemon ever sees the file.

---

## E. Test gaps

§9's properties against what exists in this layer:

| property | status |
|---|---|
| `ingest(new)` ⇒ `markdown(text) == new`, marks by identity | not tested as a property; A5 shows it is false |
| `export → import → export` fixed point | one hand-written document; passes on inputs that break the store (A6, A7) |
| `replay(log) == store` through `Workspace` | one round trip with four ops; no verdicts, no undo, no skew |
| authority | one `suggest` denial; nothing on `machine` (A8), nothing on plan ownership |
| the two loops' watermarks | one linear story; nothing adversarial (A1, A11, A18) |

Three tests worth writing first.

**T1. Ingest is a fixed point, fuzzed.** Catches A5(a)(b)(c) and any future tokenizer
change. Marks are placed by a person so the machine-refusal rule is exercised too.

```python
def test_ingest_is_a_fixed_point_on_text_and_marks():
    rng = random.Random(11)
    words = ["alpha", "beta", "gamma", "delta", "once", "twice", "client", "retries"]
    for trial in range(200):
        m, s = Mint(), Store()
        heading(m, s, "h1", "T")
        base_words = [rng.choice(words) for _ in range(rng.randint(1, 8))]
        ins = paragraph(m, s, "p1", " ".join(base_words), after="h1")
        # a person bolds one word and cites another
        t = s.blocks["p1"].text()
        for kind, attrs in ((BOLD, {}), (CITE, {"file": "c.py", "symbol": "C"})):
            w = rng.choice(base_words); lo = t.live_text().index(w)
            apply(s, m.op(K.TEXT_MARK, {"bid": "p1", "path": "text"},
                          {"kind": kind, "attrs": attrs, "start": list(t.ref_at(lo)), "end": list(t.ref_at(lo + len(w) - 1)), "on": True}))
        # the new text: drop, insert, append, move, and re-mark arbitrarily
        new_words = [w for w in base_words if rng.random() > 0.3] + [rng.choice(words) for _ in range(rng.randint(0, 3))]
        rng.shuffle(new_words)
        new_md = " ".join((f"**{w}**" if rng.random() < 0.3 else w) for w in new_words) or "x"
        actor = rng.choice(["human:a", "loop"])
        res, applied = run_ingest(s, Mint("i", actor), "p1", new_md, actor=actor)
        if res.refused:                       # only a machine may be refused, only for a dying human mark
            assert actor == "loop" and res.refused.startswith("would_drop_human_"), (trial, res.refused)
            continue
        assert all(o.conflict is None for o in applied), (trial, [o.conflict for o in applied])
        assert markdown(s.blocks["p1"].text()) == new_md, trial
        # identity: every surviving base word keeps its base characters
        t2 = s.blocks["p1"].text()
        for seg in t2.segs:
            if seg.live and seg.op == ins.id:
                assert seg.chars in " ".join(base_words)
```

**T2. `Workspace` rebuild is the store, through verdicts, undo and skew.** Catches A2,
A3, A4 and any future canonical form a handler does not accept back. Reuses the
kernel soup's generator once it is extracted (`_random_op`), run through
`ws.submit` so every op passes `_wire` on the way back.

```python
def test_workspace_rebuild_equals_live_store(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    rng = random.Random(3)
    ws.clock.wall += 5 * 60 * 1000                                  # inside MAX_SKEW: must still replay
    for step in range(300):
        body = _random_body(rng, ws.store, step)                    # the soup generator, on bodies
        actor = rng.choice(["human:local", "loop", "agent:x"])
        got = ws.submit([body], actor=actor)[0]
        if got.conflict is None and rng.random() < 0.2 and got.inverse:
            ws.submit([dict(b) for b in got.inverse], actor="human:local")   # undo as the person's op
        if step % 50 == 0:
            pid = ws.open_plan(actor="agent:x")
            ids = [o.id for o in ws.submit([_random_text_insert(rng, ws.store)], actor="agent:x", plan=pid)]
            ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {"subset": ids[:1]}}], actor="human:local")
    ws.close()
    ws2 = Workspace(tmp_path / ".codoc")
    assert ws2.problems == [] and ws2.quarantined == []
    assert ws2.store.state_fingerprint() == ws.store.state_fingerprint()
```

**T3. The watermarks under an adversary.** Catches A1, A11, A18 and pins the doc-wins
order (B3).

```python
def test_watermarks_under_interleaved_actors(tmp_path):
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    t = ws.store.blocks["p1"].text()
    a = ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": list(t.last_live_ref()), "text": " A"}}], actor="human:local")[0]
    ho = handoff(ws, "h1", actor="human:local", asked="do A")
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": [a.id, 1], "text": " B"}}], actor="human:local")
    assert [(c.stage, c.text) for c in ink(ws.store, "h1")] == [("sent", " A"), ("unsent", " B")]
    assert phase(ws.store, "h1") == "edited"                        # doc wins over queued
    assert phase(ws.store, "h1", realizing={"h1"}) == "edited"      # and over the lease (B3)
    # A hand edit with no session must not settle the request.
    r = reflect(ws, ChangeSet(modified=[chunk("c.py", "c.py::send", "f2", "t1")]), Graph(), propose=lambda *a, **k: [])
    assert settle(ws, r, sha="x") == []                             # after A11: reflect() returns ops; settle reads them
    # The agent's session closes; its reflect cites the hand-off; realized covers the whole frontier.
    d = Daemon(tmp_path, changeset=lambda *a: ChangeSet(modified=[chunk("c.py", "c.py::send", "f3", "t1")]), propose=lambda *a, **k: [])
    ...open an epoch via write_activity, close it, process_batch, then:
    w = ws.store.watermarks("h1")
    assert covered(w.handoff, w.realized) and phase(ws.store, "h1") == "edited"   # " B" is still unsent
```

Also cheap and worth adding: a `Roles` table test that enumerates `(role, kind, plan
owner) → allowed` for every kind in `K.KINDS`; an inbox test that writes lines through
a second process with `buffering` on; and `export_markdown(store) ==
export_markdown(replay)` inside T2.

---

## F. Ranked changes

1. **Make rebuild the store** (A2, A3, A4, A19 HEAD). `plan.accept` canonical keeps
   `subset`; `text.mark` canonical keeps `actor`/`op` when given; `_replay_one` never
   quarantines (skew is a `LogProblem` report); `process_batch` calls `ws.rebuild()` on a
   HEAD change before rotating. Then T2.
2. **Fix `ink`'s merge key** (A1): merge runs by `(actor, plan, stage)`; `directive`'s
   "Changed since" follows. Three lines and the first half of T3.
3. **One definition of queued** (A18): `covered(handoff, realized)` in `ops.py`; delete
   `_queued`, `_max_op`, `realize._covered`.
4. **Authority** (A8, B6, B11): `Policy(store, op)`; deny `VERDICTS` to `machine` except
   the loop's own `feature.realized`; `suggest` only on its own plans; `actor_kind()`.
5. **Ingest fixed point** (A5): `\S+`/`\s+` tokens with `isjunk=str.isspace`; extent
   comparison with `on=False`/widening emits; `Text.live_span(m)`. Then T1.
6. **Inline markdown** (A6, A7): two-pass parse, boundary-split render, escapes; the
   export test gains a store-equality assertion, not just a string one.
7. **Realization evidence** (A11, B7): `caused_by` only under a closed epoch;
   `reflect()` returns its ops; `settle(ws, res)`; an owner-writable "mark done".
8. **Inbox durability** (A9): consume only complete lines; validate `Submission`; ack
   rejections; per-line offset advance; unbuffered `submit`.
9. **Plan hygiene in `_amend`/`_add_plan`** (A10, A14, A15): `bind.attach` becomes
   `PLANNABLE` (reject detaches); one lazily-opened plan per amend; omission means
   unchanged, `delete: true` strikes; raw ops typed and validated before any submit.
10. **`history`/`_drifted`/`_new_marks` read the canonical `bid`** (A12) and then the
    indexes of §C (`ops_by_block`, `last_by_fid`, `plans_on`); `publish` computes phases
    once and skips unchanged heads.
11. **Small fixes**: `_mark_text` uses the mark's own span (A13); `touched_keys` excludes
    removed and `index_keys` empty means refuse (A16); `Workspace.ingest` refuses dead
    blocks and non-Text paths (A17); `written_by` per block; `ground` default when no
    hand-off; export in `rejected` mode; projection gets `realizing`; `_new_marks`
    filters `on`; `_cites` empty until comments; `_relocations` counts all removed
    shapes; `PHASES` order (B3).
12. **Spec edits** (B1, B4, B5, B8, B10, B12, B13): ingest base rule as implemented;
    drifted as implemented; history reports omissions and conflicts; one daemon per
    session file with many actors; export mode `rejected`; skew at arrival; per-block
    author gate.
13. **Cleanliness** (§D): one live-span helper, drop `"view"`, typed raw ops, `Protocol`
    for `propose`, `reflect` returns ops, batch atomicity decided and documented,
    `_acked` cached, one fsync per batch, `withdraw` via `by_id`.
