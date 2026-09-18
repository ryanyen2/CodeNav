# Review 5: the Python core (calculus, substrate, loops, kinds, MCP)

Scope: worktree `CodeNav-substrate`, branch `substrate`, HEAD `575667e` ("A proposal
to remove a mark leaves the mark on the page"), clean. Read against `01-calculus.md`
(rev 2), `02-workflows.md`, `03-extensions.md`, `04-version-control.md`; prior reviews
`08`, `10`–`12` and their probes. Every `file:line` is at `575667e`. The 246 shipped
tests pass (1 skipped).

Probes: `probe5_core.py` beside this file — 30 probes, each asserting the CORRECT
behaviour, so a failing probe is a confirmed fault. **22 fail, 8 pass.** Run from the
worktree:

```
PYTHONPATH=. ../CodeNav/.venv/bin/python -m pytest <scratchpad>/probe5_core.py -q -p no:cacheprovider
```

`perf5.py` beside it is the cost script (§C). Re-runs of the earlier probes:
`probe_substrate3*.py` 29 pass / 4 fail (all four explained in §B), `probe_substrate4.py`
20 pass / 5 fail (all five are editor or hub items outside this scope).

## Verdict

The applier itself is sound: 200 random steps over every op kind (verdicts, reopen,
hand-offs, realizations, binding ops, moves, deletes and restores, splits and merges,
level changes, a toggling realizing lease) never separated a warm `Publisher` from a
cold one (P3), the projection's span set agrees with the rejected-mode markdown by
character identity under a 12-seed fuzz (P2d), and a replay from disk reproduces every
recorded conflict and every recorded success whatever `config.json` says today (P1a,
P1b) — review 4's S3 is fixed. What is not sound is everything that leans on *where*
or *when* an op happened rather than on identity, and the two features that were
declared but never wired:

1. **Snapshots break four things at once** (§A1). After `codoc snapshot`, a verdict on
   a plan that was open across the fold acts on nothing: reject leaves the proposed
   words on the page as ordinary prose, accept leaves the struck words live (P1h, P1i)
   — because the verdict handlers look the plan's ops up in `store.by_id` and the fold
   moved them to the archive. An accepted `kind=delete` plan is lost outright (its
   `plan.open` conflicts on a dead target; P1g). A dead block's `dead` becomes the
   snapshot's own delete op, so `state_fingerprint` is NOT equal across a fold (P1f) and
   a `block.restore {by}` from a branch forked before the snapshot revives nothing (P1j).
   The spec's sentence "the plans (with the ops they owned, now archived, and their
   verdicts)" describes a list of ids, not what a verdict needs.
2. **Merging two clones that each appended a paragraph after the same block yields two
   live blocks with one rank**, and the next `block.add` after the first of them is
   refused as `malformed` (P1e). `add_block` trusts the recorded rank; only `set_rank`
   de-duplicates.
3. **Authority at arrival is now the whole story, and the door it points to does not
   exist.** With replay no longer re-asking the policy, a `plan.accept` line whose
   `conflict` field was stripped from a log file is honoured by every rebuild (P1d, by
   design). Both specs say the hub validates a pull request's session files before
   merging them; no module under `codoc/serve/` reads a session file (P1c). Until it
   does, a contributor's PR that adds `.codoc/log/<s>.jsonl` with a forged verdict rewrites
   the maintainer's tree on merge.
4. **Plan marks are proposals, but the un-apply matches marks by value.** Two plans on
   one span rejected in order resurrect the first plan's bold under the second's
   `removed` (P2a, P2b); typing at the end of a plan's bold (I2 growth) changes the live
   mark's `end`, and the recorded `added` no longer matches, so the plan's bold survives
   its own rejection and the export shows it while the plan is open (P2c).
5. **The timeline window resolves segment ops by block name, not identity.** A
   `text.restore`/`text.delete{segments}` whose characters a split has since moved is
   un-applied in the wrong clone, so earlier positions show text that was dead or hide
   text that was live (P4a; 2 of 40 fuzz seeds, P4b). Review 4's fuzz did not cover an
   undo after a split.
6. **The kind codecs are still declared and never called** (P6c). Review 4 recorded A10
   as "closed in the working tree"; its K4 probe passes because the diagram's source and
   the comment's body ride into the directive as raw human *ink* under "Changed since
   the last realization", not through `consult`/`lower`. Nothing in `codoc/` calls
   `Kind.lower`, `consult`, `text_view` or `write_back`; `LowerContext` has no consumer.
   `03-extensions.md` §5.1 remains untrue.

Below those: a phantom (deleted bound feature handed off with `delete_code`) can never
be settled, so the queue keeps it forever (P5c); `codoc done` on a feature with no
hand-off records a success that changes nothing (P5d); `translate` opens a plan before
its own refusal checks and leaves it empty and open (P5a); `bootstrap` mints a heading
with an empty title when the model's title has a newline (P5b); `_emptied` is
edge-triggered in disguise (P5e); a paragraph moved into another feature reads as that
feature's unsent ink (P6b); the daemonless and inbox paths give an agent two different
answers to the same no-op ingest (P6a); every read-only CLI command leaves a tracked
header-only log file behind (P1k).

Costs (§C): a warm publish is 33–43 ms at 1,500 features whether or not plans are open;
a cold one is 287 ms with no plans and **2.4 s with 20 open plans carrying 200 mark
ops**, because `plan_mark_ops` is recomputed per block per field. A rebuild on `HEAD`
change pays the cold price.

---

## A. Correctness

Ranked. Each item names its probe; the decisive assertions are quoted. `T(s, bid)` is
`s.blocks[bid].text().live_text()`; `seed(ws)` builds `h1 "Client"` / `p1 "Retries once."`
bound to `c.py::send`; `ins`/`mark`/`refs` are the obvious helpers in `probe5_core.py`.

### A1. Snapshots: verdicts act on archived ops, dead blocks change owner, an accepted delete plan is lost (HIGH)

Four faults with one cause: the snapshot restores *state* but the verdict and restore
handlers look up *ops*.

- **P1h / P1i — a plan open across the fold cannot be decided.** `snapshot_ops`
  (`snapshot.py:59-61`) emits `plan.open {ops: [...ids...]}`; `_h_plan_open`
  (`apply.py:565`) stores the ids. `_h_plan_accept` / `_h_plan_reject` then call
  `_plan_ops` (`apply.py:576-577`), which keeps only ids present in `store.by_id` — none of
  the archived ones. So `plan.reject` undoes nothing and `plan.accept` strikes nothing:

  ```python
  pid = ws.open_plan(actor="loop", kind="edit", target="p1")
  ins(ws, "p1", last, " Proposed words.", actor="loop", plan=pid)
  mark(ws, "p1", SUGGEST_DEL, r[0], r[6], actor="loop", plan=pid)   # strike "Retries"
  ws.close(); ws = Workspace(cd); write_snapshot(cd, ws.store, ws.clock); ws.close()
  ws2 = Workspace(cd)
  ws2.submit([{"kind": K.PLAN_REJECT, "target": {"pid": pid}, "payload": {}}], actor=H)
  assert T(ws2.store, "p1") == "Retries once."        # got: "Retries once. Proposed words."
  ```
  and for accept `assert T(...) == " once."` → got `"Retries once."`. User-visible: after
  a snapshot, rejecting a proposal leaves its words in the document as ordinary prose
  (the plan is `rejected`, so `plan_claims` no longer paints them gray), and accepting
  one leaves the words it proposed to remove.
- **P1g — an accepted `kind=delete` plan disappears.** Its target is dead; the
  snapshot's `plan.open` runs `_live_feature(target)` (`apply.py:559-562`) and conflicts
  with `dead_block`; the following `plan.accept` conflicts with `no_plan`. `pid in
  ws2.store.plans` → `False`. The snapshot file itself now carries two conflicted ops
  and `ws2.problems` is empty: nothing reports it.
- **P1f — `state_fingerprint` is not equal across a fold.** A dead block is re-created
  by `block.add` then killed by the snapshot's own `block.delete` (`snapshot.py:53-54`),
  so `Block.dead` changes from the original killer to `snap-…:4`; `to_json` includes
  `dead` (`doc.py:82-83`). Both the module docstring (`snapshot.py:16`) and `01` §7.5
  claim equality.
- **P1j — an undo from a branch forked before the fold restores nothing.**
  `_h_block_restore` revives only when `b.dead == by` (`apply.py:475`); after the fold
  `by` names an archived op. The restore applies without conflict and records
  `revived: []`. Text restores are unaffected (segments keep their own `dead`), which is
  why this was not seen: a whole-paragraph undo across a snapshot is the case.

Fix (one design): the snapshot must carry what verdicts and restores need, not a list
of ids. Either (a) emit the plan's *op records* in the snapshot as an `archived_ops`
section the replay loads into `by_id` (with `position` unset) so `_plan_ops`,
`_undo_plan_op` and `_touch_plan` find them; or (b) fold an open plan's *effects* (which
inserts are its, which marks) into the `texts` and make verdicts run over segment tags
(`Seg.plan`) and `Mark.attrs["plan"]` instead of the op list — the calculus already
tags both, so (b) removes the dependency on op records entirely. For dead blocks, emit
`block.add {…, dead: <killer>}` and let `_h_block_add` honour it (one line each side);
for plans with a dead target, let a snapshot-actor `plan.open` skip `_live_feature`.
Then P1f–P1j pass and the spec sentence becomes true. ~40 lines for (a), fewer for (b).

### A2. Two clones appending after one block: the merged tree has two blocks with one rank, and the next append is `malformed` (HIGH)

`_h_block_add` takes the recorded `rank` (`apply.py:249`) and `store.add_block`
inserts it unchecked (`store.py:108-113`); only `set_rank` (`:115-131`) avoids a taken
rank. Two sessions that each `block.add after p1` compute the same `rank_after(p1)`;
after `git merge` the rebuild holds `[('X','pa'), ('X','pb')]`. `rank_after('pa')` then
asks `R.between('X', 'X')`, `rank.py:40` raises `RankError`, `apply.py:81` records it
as `conflict="malformed"`:

```python
# probe P1e (two Workspaces on copies of one log dir, then the union of session files)
m = Workspace(a)
assert len({m.store.blocks[x].rank for x in ("pa", "pb")}) == 2     # got {'X'}
op = m.submit([block.add "pc" after "pa"], actor=H)[0]
assert op.conflict is None                                          # got 'malformed'
```
The same holds for `block.split`'s recorded rank (`apply.py:322`). `04` §2 promises
"every later op still applies". Fix: `add_block` de-duplicates like `set_rank` (take
the nearest free rank below; ranks are derived, position is the intent), ~6 lines; and
`rank_after`/`rank_before` should never see equal neighbours after that. Also map
`RankError` to its own conflict code rather than `malformed`.

### A3. Authority: the recorded answer is honoured, and nothing validates a foreign log (HIGH, design + spec↔code)

P1a and P1b pass: a demotion or promotion after the fact changes nothing on rebuild,
and `state_fingerprint` is equal across the role change (review 4 S3 fixed;
`workspace.py:136-154`). The consequence the spec accepts is P1d: strip `conflict` from
a refused `plan.accept` in a log file and the rebuild accepts the plan. Both `01` §7.3
and `04` §6 say this is fine because "the hub validates a pull request's session files
against its table before merging them". **No such code exists** (P1c: no module under
`codoc/serve/` calls `read_session` or anything named like a validator). So today the
chain is: a hub contributor opens a PR from the hub's worktree (the hub writes the
log, so that path is safe) — but any PR from any clone can add or edit
`.codoc/log/*.jsonl`, and the maintainer's next `codoc rebuild` after merging applies
whatever it says: verdicts by anyone, `feature.realized` that closes a queue, a
`block.delete` of the tree. Under the previous design the roles table would have
refused the verdicts at replay; that safety net is gone on purpose and its replacement
was not built. Two smaller points on the same surface:

- `Roles.load` (`workspace.py:58`) hard-codes `human:import` as owner; the snapshot
  actor is `import` (a machine, `snapshot.py:30`) and `import_v1` uses `human:import`
  for human-written nodes. Fine, but it is a third identity table (see §D).
- `Store.record` for a replayed conflict (`workspace.py:146-149`) records
  `inverse = []`; live conflicts also record `[]` (`apply.py:74-82`). Equal, good.

Fix: the validator the specs already describe — on the hub, before merging a PR, read
every added or changed `log/*.jsonl` with `L.read_session`, refuse any op whose
`conflict` is missing and whose `(actor, kind)` the table would refuse, refuse any
snapshot file, and refuse any op whose `at` is far ahead of wall time (`04` §2). ~40
lines in `serve/worker.py` or a new `serve/validate.py`. Locally, `codoc rebuild` could
print a one-line count of verdict ops per actor that the current table would refuse,
as a signal rather than a gate.

### A4. Plan marks: un-apply matches marks by value, so two plans or a growing bold defeat it (HIGH)

`_undo_plan_op` for `text.mark` (`apply.py:619-639`) and `_unapply_plan_marks`
(`derive.py:76-85`) both do `if mk in t.marks: remove` / `if mk not in t.marks: append`
with `Mark` dataclass equality over `(kind, attrs, start, end, actor, op)`. Three ways
that is wrong:

- **P2a — two plans on one span, rejected in order.** Plan A `bold on [a..g]`
  (`added=[MA]`); plan B `bold off [a..g]` (`removed=[MA]`). Reject A: `MA not in
  marks` → nothing. Reject B: puts `MA` back. The document is now bold under a mark
  whose `op` is a rejected plan's:
  ```python
  reject(a); reject(b)
  assert not bold_chars(t)            # got {('s:4',0) … ('s:4',6)} placer = A's op
  ```
- **P2b — the export while B is open.** Same state before B's verdict: rejected mode
  un-applies B's `removed` and shows `**Retries** once.` for a plan the person already
  rejected. `block_entry`'s spans do the same (they share the function).
- **P2c — typing at the end of a plan's bold.** `Text.insert` grows a `GROWS_AFTER`
  mark in place (`text.py:324-326`): the live mark is now `(a..typed)` and no longer
  equals the recorded `added` `(a..g)`. Rejected-mode markdown shows `**Retries!!**
  once.` while the plan is open; after `plan.reject` the bold stays, attributed to
  the plan's op:
  ```python
  mark(ws,"p1",BOLD,r[0],r[6],actor="loop",plan=a); ins(ws,"p1",list(r[6]),"!!")
  assert "**" not in markdown(t, "rejected", ws.store)     # got '**Retries!!** once.'
  reject(a); assert not bold_chars(t)                        # bold stays, op = a's
  ```

The un-apply must match by *placer*, not by value: a plan's mark is the mark whose
`op` is the plan's toggle op (`Text.marks_of(op)` already exists and is what
`plan.accept` uses for `suggest_del`, `apply.py:727-728`). For `removed`, restore the
mark only if the op that removed it is the one being undone AND no later op has
re-placed a same-kind mark over those characters (or, simpler and exact: make
`set_mark(off)` record the removed mark's identity and have the undo check that the
removing op is the current "last toucher" of that span). For the growth case, `added`
should be looked up by `op`, then removed whole (the grown tail was never bold in the
document without the plan — I2 says a person's typing at a proposal's edge is outside
it; the growth is the plan's mark growing, so it goes with the plan). ~25 lines across
`apply.py` and `derive.py`; put P2a–P2c in `tests/calculus`.

### A5. The timeline window un-applies segment ops in the wrong clone (MEDIUM-HIGH)

`_touched` (`window.py:62-65`) maps every text op to `(payload.bid or target.bid,
path)`. For `text.restore` the canonical `bid` is the *target* (`apply.py:179`), for a
segments-`text.delete` it is the first holder (`apply.py:154`) — neither is where the
characters are once a split has moved them. `_unapply` then revives/kills in that
clone (`window.py:97-99`), a no-op when the characters are elsewhere, and `after`
reports the wrong block.

- **P4a**: insert "abcdef"; delete it; undo (restore); split after "c"; undo the
  restore (a segments delete over two blocks). Window vs prefix replay:
  `('text.restore', 7, 'p1', 'Retries once.abc', 'Retries once.abcdef')`.
- **P4b**: 40-seed fuzz over inserts, range deletes, `suggest_del`, verdicts, splits
  (incl. `START`), merges, block deletes and undo through recorded inverses; 2 seeds
  disagree, both on a restore/segment-delete after a split (`('text.delete', 6, 'p1',
  'Retw0 ries once.', 'Retw0  once.')`). Text, split, merge, marks and verdicts alone
  are exact (review 4's W5b still holds; P4c's `born`/`ranks`/`batch_first` pass).

Fix: for `text.delete{segments}`, `text.restore` and the verdict entries, un-apply by
identity: for each segment's minting op, clone every live holder
(`store.holders(op)`) and apply `kill`/`revive` to *all* clones — both are identity-
scoped and no-ops where the characters are not. Compute `touched` from what actually
changed so `after` names the right blocks. ~15 lines in `window.py`; add P4a and the
fuzz to `tests/calculus/test_window.py`.

### A6. Kind codecs are declared and never called; review 4's K4 is a false positive (MEDIUM-HIGH)

`grep -rn '\.lower(old\|\.consult(\|\.view(\|write_back' codoc` finds only the
declarations (`kinds/__init__.py:52-60`) and `plain` in `lift.py:36`. `directive()`
(`derive.py:320-358`) renders heading + paragraphs only; `render_queue`
(`realize.py:68-78`) calls only `directive`. `LowerContext` (`kinds/__init__.py:40-43`)
has no consumer. So a drawn diagram edge, a `url`/`pdf` excerpt, a screenshot or a
comment reach the agent only when its Text happens to be the person's unsent ink, and
then as `added: "flowchart TB …"` under "Changed since the last realization" — which
is exactly what made review 4's K4 (`probe_substrate4.py:516-517`, asserts `"send" in
text`) pass. P6c asserts the codec's own phrase:

```python
add_block(ws, "diagram", {"source": "flowchart TB\n  send --> recv"}, after="h1", actor=H)
handoff(h1); q = render_queue(ws.store, root)
assert "Diagram of this feature's code" in q      # got: only 'added: "flowchart TB…"' ink
```
`03` §5.1 ("`lower` … run when a hand-off renders; `consult` … what the realizing agent
reads") is not implemented. Fix: in `directive()`, after the description, for each
kind block in the body emit `kind.consult(plain(block))` and, when the block's
non-Text content changed since the realized frontier, `kind.lower(old, new,
LowerContext(fid, bindings))` (old from the `block.set` inverses). ~25 lines; make K4
assert the codec phrase.

### A7. A deleted bound feature handed off with `delete_code` is queued forever (MEDIUM)

`02` §2: a bound feature's removal is "held until a hand-off with `delete_code`". The
hand-off works and the phantom renders (`realize.py:62-64`). The agent deletes the
code; the epoch closes; `reflect` sees `cs.removed` but `store.bindings.get(...)` is
`None` (the delete detached them, `apply.py:461`), so no `bind.detach` is written;
`settle` (`realize.py:102`) requires a `bind.*` op citing the hand-off and never fires:

```python
delete(h1); handoff(h1, delete_code=True)
reflect(ws, ChangeSet(removed=[ChunkRef("c.py","c.py::send",…)]), Graph(), propose=…, under_epoch=True)
R.settle(ws, res, …); assert R.queued(ws.store) == []       # got ['h1']
```
`codoc done` cannot reach it either: `_resolve_feature` (`main.py:176-185`) matches a
dead id only when pasted verbatim, and the phantom's directive shows its id — so the
escape hatch exists but only for someone who knows to paste `⟨b-…⟩`. Fix: `settle`
treats a phantom as realized when the pass ran under the epoch and the change set's
`removed` covers the symbols recorded in the delete's `detached` (the op is in
`history(fid)`); ~12 lines.

### A8. `codoc done` with no hand-off is a recorded no-op reported as success (LOW-MEDIUM)

`_h_feature_realized` (`apply.py:900`) defaults `covers` to `w.handoff`, which is `{}`
for a feature never handed off; the op applies, records `covers: {}`, changes nothing,
and the CLI prints "marked done" (P5d). Realization "settles a request" (`01` §3);
with none there is nothing to settle. Fix: conflict `nothing_queued` when
`covered(w.handoff, w.realized)` already holds. 3 lines.

### A9. `translate` opens the plan before its own refusals and leaves it empty (LOW-MEDIUM)

`translate.py:149` opens the plan for a human-written feature; `:152-153` then skips
on "paragraph count changed", and `:155-159` on a refused title ingest, without
closing it. The store now has an open `edit` plan with `ops=[]` on the feature; the
projection lists it in `plans`, `phase` says `proposed`, and the editor offers a
verdict on nothing (P5a: `[Plan(id=…, actor='translator:zh-Hans', status='open',
ops=[])]`). Also `translate.py:132`: on a batch failure every skipped feature is
reported with `payload[0]["title"]`, the first feature's title. Fix: run `_check` and
the paragraph-count test before `open_plan`; reject the plan if the title ingest is
refused. ~6 lines.

### A10. `bootstrap` silently mints a heading with an empty title (LOW-MEDIUM)

`_add_feature` (`bootstrap.py:145, 151`) ignores `ws.ingest`'s result. A model title
with a newline, or a hand-wrapped paragraph, is refused `multi_paragraph`, and the tree
gets a heading whose text is `""` and an empty paragraph block (P5b:
`Block(id='b-…', kind='heading', content={'text': Text(segs=[])})`). The export then
writes `#  {#b-…}`. Fix: collapse internal whitespace/newlines to one space before
ingest (titles and paragraphs alike), and count refusals in `BootstrapResult`. 4 lines.

### A11. `_emptied` is edge-triggered in disguise (LOW)

The docstring and the reflect comment (`reflect.py:148-152`) say "state-based: any
live feature that once had code and has none now". `_emptied` (`:299-311`) requires a
non-conflicted `bind.detach` on the heading. A feature whose only symbol was
re-attached to another feature (I8 displacement, `apply.py:513-514`) has none now and
is never proposed for deletion (P5e). Fix: "once had code" = any `bind.attach` on the
heading; 1 line.

### A12. A paragraph moved between features reads as the destination's unsent ink (LOW, design)

`ink(f)` (`derive.py:165-188`) is human live segments on `f`'s blocks beyond `f`'s
hand-off frontier. Move `p1` (realized under Client) under Server: Server is
`edited`, and its directive lists `Retries once.` as changed since the last
realization (P6b). The spec's own definition produces this; the question is whether
it is the intended reading (the description did change) or whether ink should be
segments *minted* after the frontier that are on `f`'s blocks *and were minted while
on them* — which the op log can answer (`text.insert`'s canonical `bid` vs the move).
Decide in `01` §6; if the current reading stands, say so there.

### A13. The same no-op ingest gets two answers (LOW)

Daemonless `client.ingest` returns `{refused: None, ops: []}`; through the inbox the
drain acks `conflict="unchanged"` (`workspace.py:280`) and `client.ingest` returns
`{refused: "unchanged"}` (P6a). An MCP agent sees a refusal in one deployment and a
success in the other. Pick one (the ack should carry `unchanged` as a status, not a
conflict). 2 lines.

### A14. Every read-only CLI command leaves a tracked header-only log file (LOW)

`Workspace.__init__` (`workspace.py:107`) opens a `SessionWriter`, which writes the
header line immediately (`log.py:185-187`). `codoc rebuild`, `codoc snapshot` and
`import-v1` open a writing workspace; three opens with no writes → three new
`log/s-*.jsonl` files (P1k: `4 == 1`). They are tracked, so each run dirties the
repository. Fix: create the file on the first `append`. 4 lines.

### A15. `written_by` counts an out-of-flow comment as the feature's prose (LOW, judgment)

`written_by` (`context.py:48-55`) walks `feature_body`, which includes `comment`
blocks (`in_flow=False`, never exported). A person's margin note on a loop-written
feature makes `written_by == "human"` (P5g), so `translate` opens a plan for prose
nobody but the loop wrote and the prompt's `written_by` says human. Exclude blocks
whose kind is not `in_flow`. 1 line.

### A16. Confirmed fine (probed, no fault)

- P1a/P1b: replay honours recorded successes and recorded conflicts under any later
  roles table; `state_fingerprint` equal. Review 4 S3 fixed.
- P2d: projection `spans` and rejected-mode marks agree by character identity (12
  seeds × 30 steps of person/plan bold/link/cite toggles, inserts, deletes, verdicts).
- P3: warm `Publisher` == cold on `projection.json`, `tree.codoc`, `history.json`,
  `realize.md` under 200 random steps of every op kind plus a toggling `realizing`
  set. The memo keys are tight; `structural_head` not advancing on a verdict that
  kills or revives blocks is covered because every feature key carries `plans_head`.
- P4c: `born` excludes a block revived by a merge inverse; `batch_first` points
  before the window when the window cuts a batch.
- P5f: an amend whose second block flips the gate rides as ONE plan with nothing on
  the trunk.
- P6d: a heading deleted under a paragraph plan hides the plan (`open_plans` filter),
  renders, and offers it again after `block.restore`.
- Provisional `$cid` anchors, torn-tail truncation, per-line inbox acks, `hub_actor`
  namespacing: hold (review 3/4 probes).

## B. Reviews 2–4: what holds in this scope

| # | change | status at `575667e` |
|---|---|---|
| R3 A1 | ingest fixed point inside any mark | holds (probe3 P1, P1b, P1d) |
| R3 A2 | plan marks are proposals | holds for export/directive; `block_entry` now un-applies too (575667e). **New**: A4 above (by-value matching) |
| R3 A3 | `_causes` cites every uncovered hand-off | holds |
| R3 A5 | bindings die with their heading | holds |
| R3 A6 | translate skips features with an open plan | holds; **new**: A9 (empty plan on refusal). probe3 P17 fails for the right reason (expects a translation that is now skipped) |
| R3 A7/A11 | inbox totality, `$cid` across drains | holds |
| R3 A8 | publish key on rebuild | holds (`invalidate()` on rotate) |
| R3 A9 | zombie proposal on a deleted heading | holds (P6d) |
| R3 A10 / R4 S3 | roles in config; replay without re-judging | **fixed** (P1a, P1b); consequence A3 |
| R3 A12 | torn tail keeps the prefix | holds |
| R3 A13–A16 | verdicts in history; no-op delete touches holder; refreshed binding; placer through displacement | hold |
| R3 A17 | machine rides a stranger's plan | spec now allows it (`01` §7.3); probe3b P25 is stale |
| R3 P3 (probe) | held amend keeps a human bold in the LIVE doc | fails by design since A2: live mode shows the toggle, projection/export undo it; review 4 accepted this |
| R4 W1–W5b | window: verdicts, restores, born blocks | hold on the committed tree; **new gap** A5 (segment ops after a split) |
| R4 A10/K4 | kind codecs reach the directive | **not fixed**; K4 passes on ink, not codecs (A6) |
| R4 A11/S1 | `human:local` login collision | holds (`hub_actor`, `gh:` prefix) |
| R4 A13/K3 | daemonless `client.write` opens one session | holds (`_OWNED`) |
| R4 F9 | translate counts skipped as "already" | fixed (`res.already = heads - wanted`) |
| R4 F13 | `demote_to_plan` dead field | gone; `MACHINE_KINDS`/`REWRITERS` are two tables with two meanings, fine |
| R4 A7 reader, A5/A6/A8/A9/C2, A3/A4 hub | editor / hub | out of scope; probe4 H1, H2, C2, P4, P5 still fail |
| R2 A1–A19 | | re-verified by review 3 §B; probe3c's I6 fuzz (P26, P27) passes; nothing regressed in my run |

## C. Complexity, measured

`perf5.py`, 1,500 features / 13,500 ops built through `Workspace` (0.6 s), Python 3.12,
M-series laptop:

| operation | ms |
|---|---|
| `text.insert` (one op) | 0.1 |
| `block.split` + `block.merge` (two ops) | 0.7 |
| `Publisher.publish` cold, no plans | 287 |
| `Publisher.publish` warm after one insert | 33 |
| `Publisher.publish` warm, nothing moved (`force`) | 33 |
| `history_window(200)` | 0.6 |
| bare `projection()` (no memo) | 84 |
| `Publisher.publish` **cold, 20 open plans × 10 mark ops** | **2,434** |
| — of which `projection()` (entries + claims) | 1,014 |
| — of which `phases()` | 50 |
| `Publisher.publish` warm (1 insert), 20 open plans | 36 |
| `Workspace()` rebuild from disk, read-only | 348 |
| log on disk | 6.2 MB (449 B/op) |

- **Open plans make a cold publish 8× slower** (287 → 2,434 ms). `block_entry`
  (`derive.py:450-452`) and `export_block` (`export.py:50, 63`) each call
  `_unapply_plan_marks` → `plan_mark_ops(store, open_ids)` (`derive.py:64-73`), which
  walks every open plan's op list and sorts, **per block per Text field**; `plan_claims`
  (`derive.py:244`) does it again per feature per field. That is O(blocks × open-plan
  ops) per cold publish. A rebuild on `HEAD` change (`daemon.py:104-106`) pays it. Fix:
  compute `plan_mark_ops` once per publish (or index mark ops by `(bid, path)` in
  `Store.record`), and pass it in. ~10 lines; expected cold cost back near 300 ms.
- Warm publish floor is ~33 ms regardless: `_feature_keys` over 1,500 features
  (`body_ids` walks each feature's body), `history_window`, the export string compare
  against the file (`publish.py:132`, a 300 KB read per publish), `write_queue`,
  `write_status`. Fine for now; the file compare could use mtime+size.
- `Store.prune_index` (`store.py:254-264`) iterates **every op in `char_index` and
  `mark_index`** on each split and merge: O(all ops), 0.3 ms each at 13.5k ops, so
  invisible today but linear in the log; `01` §5's "O(segments of the touched blocks)"
  is not literally true. Fix: iterate `text.ops()` of the block before the cut instead
  of the whole index. ~4 lines.
- `L.acked()` (`log.py:319-335`) reads every `*.ack.jsonl` at open and `_acked` /
  `_sent_text` (`workspace.py:99-100`) grow for the daemon's life; ack files are never
  truncated. A year of typing is a few MB of acks re-read on every daemon start —
  bounded but unbounded in principle. Rotate acks with the session.
- Rebuild from disk is 350 ms for 13.7k ops (≈25 µs/op); fine.

## D. Cleanliness

- **Dead code** (no caller in `codoc/`): `realize.withdraw` (the editor sends the
  inverse itself), `Text.remove_marks`, `derive.blame_summary`, `doc.body_range`,
  `doc.dominated`, `Kind.lower`, `Kind.consult` (only as a default), `Kind.text_view`,
  `Kind.write_back`, `LowerContext` (A6).
- **Three identity tables**: `log.CLIENT_IDENTITY` + `actor_for` (`hub-x` →
  `human:gh:x`, unknown → `None`), `client.write`'s fallback `f"human:{client}"`
  (`client.py:60, 107`: a daemonless `client="hub-alice"` is stamped `human:hub-alice`,
  a different actor from the daemon path), and `Roles.load`'s built-ins
  (`workspace.py:58`). One function.
- **Docstrings that overstate**: `snapshot.py:16` and `01` §7.5 ("`state_fingerprint`
  is equal across a fold", A1); `window.py:4-6` ("exact — a fuzz checks it against a
  prefix replay": the suite has two fixed cases and no fuzz; A5); `apply.py:1`
  ("O(touched blocks)", §C); `reflect.py:148-152` and `_emptied`'s docstring
  ("state-based", A11); `01` §7.1 line 370 says authority is checked "at apply and
  again at replay", contradicting §7.3 twenty lines later and the code.
- **Names that lie**: `_h_text_restore`'s canonical `bid` is the *target*, not a
  holder (`apply.py:179`), and `_h_text_delete`'s is the *first* holder (`:154`) —
  the window trusts both (A5). `Workspace(readonly=False)` is the default for commands
  that only read (A14). `apply()` maps any `KeyError` from a handler to
  `conflict="missing:…"` (`apply.py:79-80`): a programming error inside a handler
  becomes a recorded conflict instead of a traceback, which is how P1g's snapshot
  conflicts went unnoticed.
- `translate.py:132` reports every feature of a failed batch under the first
  feature's title (A9).
- `_h_feature_handoff`'s canonical always records `covers` even when defaulted — good;
  but `_h_block_add` accepts a client-supplied `rank` from the wire (`apply.py:249`),
  which only the snapshot and replay should be allowed to pin.
- `Roles` docstring says `suggest` is "everyone else"; `Roles.default` agrees; but the
  `machine` branch is decided by `actor_kind`, so an actor `unknown:x` is `suggest` —
  fine, just say so.
- `store.record`'s `plans_head` comment says a plan's "existence or status" moves it;
  it also moves on `plan.reopen`, good, but a `plan.open` for a plan that already
  exists (conflict) does not — also good; document.

## E. Spec ↔ code

| spec | says | code | which moves |
|---|---|---|---|
| `01` §7.3, `04` §6 | the hub validates a PR's session files before merging | nothing in `codoc/serve/` reads a log file (P1c) | code |
| `01` §7.1 l.370 | authority "checked per op at apply and again at replay" | recorded answer honoured at replay (`workspace.py:136-154`) | spec (delete the clause; §7.3 is right) |
| `01` §7.5, `04` §5 | `state_fingerprint` equal across a fold; a branch forked before the snapshot merges correctly | dead blocks change owner; `block.restore{by}` no-op (P1f, P1j) | code |
| `01` §7.5 | the snapshot carries "the plans (with the ops they owned, now archived, and their verdicts)" | ids only; verdicts after the fold act on nothing (P1h, P1i); an accepted delete plan is lost (P1g) | code, then spec says how |
| `04` §2 | after a merge "every later op still applies"; rebuild deterministic | deterministic, but two blocks share a rank and the next add is `malformed` (P1e) | code |
| `01` §3 `plan.open` / `plan.reject` | "Marks are removed by the op that placed them" | removed by value equality (A4) | code |
| `01` §6 `timeline` | un-apply exact per block | segment ops after a split un-apply in the wrong clone (A5) | code |
| `03` §5.1 | `lower` runs when a hand-off renders; `consult` is what the agent reads | never called (A6) | code |
| `02` §2 "Delete feature" | a bound feature's removal is held until a hand-off with `delete_code` | held, then never settled (A7) | code |
| `01` §3 `feature.realized` | "the owner may write `feature.realized` by hand (`codoc done`)" for a request | also for no request; records `covers: {}` (A8) | code (refuse) or spec |
| `02` §1 | `handoff.upto`, `realized.upto`, `feature.handoff(upto=head)` | the field is `covers` everywhere | spec |
| `02` §3.3, §5 | "Accept … hands the feature off in the same batch" | `codoc accept` (`main.py:216-224`) and `_h_plan_accept` send no hand-off; the editor may | spec should say who emits it, or the CLI should |
| `01` §6 `ink(f)` | human segments on `f`'s blocks beyond the frontier | as written; a moved paragraph becomes ink (A12) | decide in spec |
| `01` §5 | apply is O(segments of the touched blocks) | `prune_index` is O(all ops) per split/merge (§C) | code (cheap) or spec |
| `01` §2 Actor grammar | `"import"` is an actor | `Roles.load` also names `human:import`; `import_v1` uses it | spec: add it, or code: drop it |
| `01` §7.4 | "A resubmitted client id is acked with the same OpId" | yes; and a no-op `ingest` is acked as `conflict="unchanged"` though nothing conflicted (A13) | code |

## F. Ranked changes

1. **Snapshot carries what verdicts need** (A1): either load archived plan-op records
   into `by_id` from the snapshot, or make verdicts act on `Seg.plan` /
   `Mark.attrs.plan` tags; `block.add {dead}` honoured; snapshot `plan.open` skips
   the live-target check. P1f–P1j into `tests/substrate`. ~40 lines.
2. **`add_block` de-duplicates ranks; `RankError` gets its own conflict code** (A2).
   P1e into the suite. ~8 lines.
3. **Build the hub log validator the specs describe** (A3): refuse un-conflicted ops the
   table would deny, snapshot files, and far-future stamps, before merging a PR. ~40
   lines in `codoc/serve`. Until then, say in `04` §6 that it does not exist.
4. **Plan-mark un-apply by placer** (A4) in `apply._undo_plan_op` and
   `derive._unapply_plan_marks`; the grown tail goes with the plan. P2a–P2c into
   `tests/calculus`. ~25 lines.
5. **Window: segment ops by identity across all clones; `touched` from what changed**
   (A5). P4a + the fuzz into `test_window.py`. ~15 lines.
6. **Wire `consult` and `lower` into `directive()`** (A6); make K4 assert the codec
   phrase. ~25 lines.
7. **Settle a phantom from the delete's `detached` symbols** (A7). ~12 lines.
8. **`plan_mark_ops` once per publish** (§C): 8× on a cold publish with open plans.
   ~10 lines.
9. **`translate`: check before `open_plan`; reject on refusal** (A9). **`bootstrap`:
   normalize whitespace, count refusals** (A10). **`_emptied`: any prior attach**
   (A11). **`feature.realized` with nothing queued conflicts** (A8). ~15 lines total.
10. **Session file created on first append** (A14); ack files rotate with the session;
    one identity function (§D). ~15 lines.
11. **`written_by` skips out-of-flow kinds** (A15). 1 line. Decide A12 in `01` §6.
12. **`ingest` no-op acked as a status, not a conflict** (A13). 2 lines.
13. **Spec edits** (§E): delete `01` l.370's "again at replay"; `upto` → `covers` in
    `02`; say who emits the hand-off on accept; `prune_index` or the O() claim.
14. **Delete the dead code** in §D and fix the four overstated docstrings.
