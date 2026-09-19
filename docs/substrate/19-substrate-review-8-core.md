# Review 8: the Python core (calculus, substrate, loops, kinds, MCP, CLI)

Scope: worktree `CodeNav-substrate`, branch `substrate`, HEAD `18855c0`, clean. Read
against `01-calculus.md` (rev 2), `02-workflows.md`, `03-extensions.md`,
`04-version-control.md`, review 7 with its Applied note, reviews 5 and 6, and
`probes/probe7_core.py` / `perf7.py`. Every `file:line` is at `18855c0`. Nothing in
the worktree was modified. Baseline: 333 pass, 1 skipped.

Probes: `probe8_core.py` beside this file, 28 probes, each asserting the CORRECT
behaviour, so a failing probe is a confirmed fault and a passing one a confirmed
property. **17 fail, 11 pass** (the 11 are A2, G1 and the nine confirmations in §H).
Run from the worktree:

```
cd /Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate && \
PYTHONPATH=. ../CodeNav/.venv/bin/python -m pytest <scratchpad>/probe8_core.py -q -p no:cacheprovider
```

`perf8.py` beside it is perf7's rows re-run at `18855c0` plus this round's rows (§C);
its output is `perf8.out`.

## Verdict

Round 7's two closed forms hold where they were aimed: a plan's mark toggle is a record
beside the trunk (two plans on one bold are decided separately; a split under a
widening moves nothing the reject has to find), the fold archives nothing out of order,
`state_fingerprint` is equal across a partial fold, the reopen's `undid` is what the
window reads, and `decided_by` survives (§B, nine passing probes). What did not hold is
the *edge* of each closed form, and this round's faults sit there.

**The fold** (round 7 item 1) has four. `snapshot.state_at` builds the folded prefix with
a bare `apply` on a policy-less `Store`, so an op the applier REFUSED — a suggester's
untagged trunk insert, a forged verdict — is applied into the state the snapshot asserts
(A1; one probe, the words are on the trunk after the rebuild). The cut is decided by
`reads_its_ops` *at fold time*, but the directive of a hand-off given *after* the fold
reads back to the feature's realized frontier, which for a never-realized feature is the
beginning of time: a `removed:` line and a `Focus:` line the person made before the fold
are gone from the request they send after it (A3, A4). `codoc snapshot` takes no applier
lock and archives every session file the frontier covers whole, including the one a held
daemonless applier (the MCP server between calls) is still appending to; its next op
lands in `log/archive/` and no rebuild reads it (A7 — data loss, one probe). And
`codoc snapshot` crashes on the `None` that 04 §5 promises when nothing folds (A6).
Measured: with one drifted feature the cut is that feature's `block.add`; after any
reflect pass it is 0 of 13,521 ops. 04 §5 says so; §G says what would change it and
that it deletes code.

**Authority** (round 7 item 3) has three, all at the seam between the drain and the
policy. A suggester who withdraws proposed words and restores them *after* the verdict
puts them back live — tagged with an accepted plan (trunk prose nobody accepted) or a
rejected one (which the `rejected` read mode does not hide, so the export shows them)
(D1, D2). A typo corrected within one editor flush is struck under the contributor's own
proposal, because `_as_proposal` runs before provisional `$cid` anchors are resolved
(D3 — round 7's A3 fix holds only once the insert is acked). And the hub's door refuses a
contributor's honest log: the three own-words exceptions read `by_id`/`ops_on`, which
`validate._plans_view` does not carry (D4, shared with the surfaces round; the core's
policy is what depends on state the gate does not have).

**Plans** have three. `plan.accept` strikes before it applies the mark records, so a
`suggest_mark` proposing to turn a WHOLE mark off whose trunk mark the person has since
removed raises midway: the accept is recorded `mark_partial`, the strike stands, the plan
stays open and every later accept fails the same way (C1 — the two-phase rule in
`apply.py:4-8` broken in the one handler that mutates most). A `suggest_del` is a range
struck with a range delete, so a word the person typed inside it dies with the proposal
(C2). A plan-tagged `bind.attach` borrows a symbol on the live trunk and `reflect._emptied`
then proposes deleting its owner (C4). Two more: `client.read()` returns the held
applier's workspace with none of `applying()`'s catch-up, so the MCP agent's reads and
the `base_text` its `reflect` computes describe the tree before the person's
`codoc accept` (B1); and an op one clone applied that conflicts at merge is dropped with
no report (E1).

Cost (§C): apply and the views are what the spec says; a cold publish is linear in
features (213 → 473 ms for 1,500 → 3,000). One new term: `open_plans_on` iterates
*every* plan per feature, so `phases()` is O(features × plans) — 41 → 126 → 195 ms for
0 → 1,500 → 3,000 decided plans on 1,500 features.

---

## A. Correctness

Ranked. `T(s, bid)` is the live text; `seed(ws)` is review 5's `h1 "Client"` /
`p1 "Retries once."` bound to `c.py::send`; `r = refs(ws, "p1")`, `r[0..6]` "Retries",
`r[8..11]` "once". `A` is `human:gh:alice` with role `suggest`.

### A1. `state_at` re-applies ops the applier refused (HIGH)

`snapshot.state_at` (`snapshot.py:79-88`) replays `store.ops[:cut]` into a fresh
`Store()` with `apply(out, Op.from_json(...))` (`:87`). `apply` (`apply.py:59-97`) never
looks at a recorded `conflict`; `Workspace._replay_one` (`workspace.py:300-318`) does —
"if op.conflict: record, return" — which is what 01 §7.3 means by "replay honours what
the record says". So the folded prefix is not the state the log describes.

- **A1** — alice (suggest) sends an untagged `text.insert " Alice was here."` on the
  trunk: `conflict == "authority"`. A second feature is then created with an open plan
  (the cut lands at its `block.add`, so the first feature's history is in the prefix).
  Fold, reopen:
  ```python
  assert "Alice" not in T(ws2.store, "p1")   # got 'Retries once. Alice was here.'
  ```
  The same holds for every conflict kind in the prefix (`plan_not_open`,
  `unknown_anchor`, `range_spans_blocks`): each is re-run against the state it was
  refused in, and any that now succeeds is folded in. The authority case is the one that
  can be steered from outside: a stranger's refused op in a merged log becomes trunk text
  on the next `codoc snapshot`.

Fix: one `replay_recorded(store, op)` in `calculus/apply.py` (the body of
`_replay_one` minus the clock), called by `_replay_one`, `state_at` and
`tests/calculus/test_apply.replay` — ~10 lines, deletes ~8. Probe A1 into
`tests/substrate/test_review5.py`'s neighbour.

### A2. The fold decides for the hand-off the person has not given yet (HIGH for the product; design)

`settled_prefix` (`snapshot.py:50-76`) folds everything before the oldest op naming a
feature `reads_its_ops` flags *now*. The directive of a LATER hand-off reads ops back to
the feature's realized frontier: `_human_changes` (`derive.py:471-484`) resolves each
dead segment's killer through `by_id` (`:480`) and `_new_marks` (`:487-496`) reads the
person's `text.mark` ops (`:490`). A feature nobody has handed off is `synced` at fold
time, folds whole, and its human ops are gone.

- **A3** — the person deletes "once" (feature stays `synced`: `ink` is live segments
  only); fold; hand off:
  ```
  ### Client ⟨h1⟩

  Retries .

  Author asked: drop the retry          # no `removed: "once"`
  ```
- **A4** — the person bolds "Retries"; fold; hand off: no `Focus: "Retries"` line. The
  mark is still there, with its `actor` and `op`; the directive reads the op.
- **A5** (LOW, spec) — `history(f)` before: five ops by the person and the loop; after:
  three `block.add`/`bind.attach` by `import`. 01 §7.5 says "every derived view" is
  equal across a fold; `history` and `timeline` are derived views (01 §6).

Root cause: a `Seg` carries its minter's actor and clock, so `ink` survives the fold
(01 §7.5 says exactly this). A *deletion* carries only `dead: OpId`, and an `OpId` names
the SESSION, not the actor (one file, many actors — 01 §7.1), so the killer's identity
lives only on the op. Marks carry `actor` and `op`, so `Focus:` needs no op at all; the
code reads one anyway. Fix: `_new_marks` iterates the feature's live marks with a human
`actor` and `not in_frontier(m.op, realized)` (~8 lines, no op record); `Seg` gains
`dead_actor` written by `Text.delete/kill` (they already take `by`; add `by_actor`),
`_human_changes`, `ground`'s `removed` and `_authored`-for-kills read it (~15 lines
across `text.py`, `apply.py`, `window.py`). Then the directive reads state only, and the
"queued" clause of `reads_its_ops` is needed for nothing but `asked`, which the snapshot
already re-emits (`snapshot.py:136-143`). §G takes this further. Probes A3/A4 into
`tests/calculus/test_derive_export.py`; A5 is a spec edit (§E).

### A3. A fold archives the session a daemonless applier is still writing (HIGH: data loss; needs the MCP server to be the applier)

`write_snapshot` (`snapshot.py:185-191`) moves every session file whose ops the frontier
covers whole. `cli.snapshot` (`cli/main.py:381-396`) refuses only when a daemon runs and
opens a raw `Workspace(cd)` — no `applier.lock`. A daemonless applier holds its
`Workspace` for the life of the process (`client.py:53-58`) with an open `SessionWriter`
fd; its file is covered whole the moment its last op is settled, so the fold archives it;
the next `applying()` turn sees the log "grew by another hand" (the snapshot file),
rebuilds — and appends the new op to the file now under `log/archive/`, which no
rebuild reads (`log.py:147-151`).

- **A7** — `client.write` " A." (held), `codoc snapshot`'s exact steps, `client.write`
  " B.", fresh `Workspace`:
  ```python
  assert " B." in T(fresh.store, "p1")
  # got 'Retries once. A.'; log/ = [], archive/ = [<seed session>, <the held session>]
  ```

Fix: in `applying()` after the catch-up (`client.py:74-81`), `if ws.writer is not None
and ws.writer._fh is not None and not ws.writer.path.exists(): ws.rotate_session()` —
3 lines, closes the loss. Then `codoc snapshot`, `rebuild`, `translate` and `import-v1`
open their workspace through `client.applying()` so the lock has one door (~8 lines).
The closed form (§G d): a session file says when it was CLOSED and the fold archives
only closed files.

### A4. The held applier's reads are stale (MEDIUM-HIGH; the agent flow)

`client.applying()` catches a held workspace up "when the log grew by a hand that is not
ours" (`client.py:74-81`); `client.read()` (`:142-147`) returns the same held workspace
with no check. Every MCP read (`tools._open`, `tools.py:45-47`) and the `base_text` that
`tools.reflect` computes (`:254`) go through it.

- **B1** — this process writes " A." (held); a second process (a real subprocess through
  `client.write`) writes " B."; `client.read(cd)`:
  ```python
  assert " B." in T(client.read(cd).store, "p1")    # got 'Retries once. A.'; the log has both
  ```
  In the product: the agent (MCP, daemonless applier) reflects, the person runs
  `codoc accept` in a terminal, the agent's next `codoc_read_tree` does not show it and
  its next amend is `stale_base`.

Fix: factor the staleness check into `_caught_up(codoc_dir, ws)` and call it from both
`applying()` and `read()` (~8 lines). `_SEEN` is only written inside `applying()`, so
`read()` should compare and rebuild but not record.

### A5. A suggester revives withdrawn words after the verdict (MEDIUM-HIGH for the hub)

`_own_plan_text` (`workspace.py:88-102`) admits a `text.restore` when "the delete it
undoes" was the actor's (`:97-99`) and asks nothing about the words. The allowed delete
is the one round 7 added (own open-plan words). Sequence: propose, withdraw, wait for the
verdict, restore.

- **D1** — after the maintainer ACCEPTS: `text.restore {segments: [[ins,0,6]], by: d}`
  by alice → `conflict None`, `T == 'Retries once. More.'`; the segments carry
  `plan=<accepted>`, so `plan_claims` reports them `accepted` and the export prints them.
- **D2** — after the maintainer REJECTS: the same; `markdown(p1, "rejected")` is
  `'Retries once. More.'` — the rejected mode hides OPEN plans' words (`derive.py:64-70`)
  because a live segment of a rejected plan is a state the calculus assumes cannot exist.

Fix: the restore branch also requires `_own_plan_words(store, actor, {s[0] for s in
segments})` — the words must be from an open plan of theirs *now* (~3 lines); D1/D2 then
conflict `authority`. (An owner's own undo can produce D2's state too — delete a plan's
words on the trunk, reject the plan, undo the delete — which is their right; the read
modes should still not print a rejected plan's words: `markdown(rejected)` and
`export_block` hide segments whose plan is not `accepted`, ~2 lines.) §G (b) removes the
branch altogether.

### A6. A typo corrected within one flush is struck under the contributor's own proposal (MEDIUM; regression of round 7 A3 for the common case)

`_drain_one` (`workspace.py:560-587`) calls `_as_proposal` (`:567`) BEFORE
`_resolve_provisional` (`:580`). `_as_proposal` resolves the range through
`deleted_ops` (`:53-65`), whose `_ref(["$cid", "c1", 0])` raises, is caught, and returns
`set()`; `_own_plan_words(set())` is `False`; the delete becomes a `suggest_del`. The
editor batches keystrokes per 350 ms flush, so an insert and the Backspace over it arrive
together with `$cid` anchors exactly when a person corrects a typo.

- **D3** — one inbox batch: insert " Moer" (cid c1) + delete `["$cid","c1",0]..["$cid","c1",4]`:
  ```python
  assert T(ws.store, "p1") == "Retries once." and not t.proposals(kind=SUGGEST_DEL)
  # got 'Retries once. Moer' with a suggest_del under alice's plan
  ```
- **H3** (passes) — the same across two drains (insert acked first) is a real delete.

Fix: resolve provisional anchors first (`payload = _resolve_provisional(...)` above the
`_as_proposal` call, for `s.kind != "ingest"`) — 3 lines. §G (b) moves the check into the
applier, where anchors are always resolved.

### A7. `plan.accept` is not two-phase; a stale WHOLE-off record wedges the plan (MEDIUM)

`_h_plan_accept` (`apply.py:769-830`) undoes the rejected subset (`:791-793`), strikes
(`:794-802`), then applies the `suggest_mark` records through `Text.set_mark`
(`:806-816`). `set_mark` raises `MarkError` for a WHOLE kind turned off with no exact
match (`text.py:431-436`); `apply` maps it to `mark_partial` (`apply.py:90-91`) and
records the op — after the strike has mutated the text. The record is not consumed, so
every later accept fails identically; only a reject clears it.

- **C1** — the person's `comment{block: c1}` over "Retries"; a loop plan proposes turning
  it off AND strikes "once"; the person resolves the comment themselves; accept:
  ```python
  assert T(ws.store, "p1") == "Retries once."   # got ('mark_partial', 'Retries .') with the plan still open
  ```

Fix: a record whose mark is already gone is already satisfied — treat a `MarkError` on
a WHOLE-off record as a no-op for that record (~3 lines), or resolve every record against
the current marks before the first mutation (~10 lines). `apply.py:4-8`'s claim then holds
again for this handler. Probe into `tests/calculus/test_plan_marks.py`.

### A8. A strike kills the person's words typed inside it (MEDIUM; design)

A `suggest_del` is a `Mark` over `(start, end)`; accept strikes it with
`t.delete(m.start, m.end)` (`apply.py:798`), the range form, which kills every live
character between the ends. `text.delete` itself stores the explicit segments it covered
at apply (01 §3) precisely so that "nothing typed between them since" dies with them; the
proposal form does not.

- **C2** — plan strikes "once"; the person types "X" inside ("onXce"); accept:
  ```python
  assert T(ws.store, "p1") == "Retries X."     # got 'Retries .'
  ```

Fix: `_h_text_mark` records `segments` for a `suggest_del` at placement (what
`Text.delete` would have covered — `ops_between`'s neighbour, ~6 lines); accept kills by
identity (`t.kill(segments)`), the projection draws from the segments (~10 lines). Then a
split across a strike needs no "both ends in one Text" case either. 01 §3's "the
characters under its suggest_del marks" should say which (§E).

### A9. A plan's `bind.attach` borrows a symbol and the loop proposes deleting its owner (MEDIUM)

01 §3: plan ops apply live, tagged; `bind.attach` displaces the prior owner (I8).
`reflect._emptied` (`reflect.py:298-313`) lists any live heading in `bound_once` with no
binding and no delete plan; the next pass opens a `delete` plan for it (`:152-155`).

- **C4** — an agent plan adds `h2` and attaches `c.py::send` (h1's only symbol) under the
  plan:
  ```python
  assert "h1" not in _emptied(ws.store)        # got ['h1']
  ```
  Rejecting the agent's plan gives h1 its symbol back (`_undo_plan_op` BIND_ATTACH,
  `apply.py:707-727`) — after the loop has already asked whether to delete h1.

Fix: `_emptied` skips a feature whose lost symbol is currently held by a binding whose
attach op carries an open plan (`store.by_id[...]` of the attach is not indexed; simplest
is `borrowed = {o.payload["displaced"]["feature"] for open plan ops of kind bind.attach
with a displaced entry}`, ~6 lines).

### A10. An op one clone applied that conflicts at merge is silent (MEDIUM; design)

Two clones fork with plan P open. A accepts P. B, a moment later, writes into P (owner;
applied, logged). At merge B's op replays after the accept (`(at, session, seq)`,
`log.py:168-179`) and is recorded `plan_not_open`; B's own log says it applied; nothing
compares the two.

- **E1**:
  ```python
  assert " More." in T(merged.store, "p1") or merged.problems
  # got ('Retries once. Plan.', 'plan_not_open', [])
  ```
  When B rebuilds after the pull, B's own words vanish from B's tree too, with no report.
  The same shape for a paragraph deleted in A and typed into in B (`dead_block`).

Fix (minimum): `_replay_one` (`workspace.py:300-318`) appends a `LogProblem` when a record
with `conflict is None` replays with a conflict (~4 lines); `codoc rebuild` and the daemon
already print `problems`. Closed form in §G (f).

### A11. The hub's door refuses a contributor's honest log (MEDIUM; shared with surfaces)

`validate_logs` (`serve/validate.py:55-75`) asks `roles.policy(view, op)` with
`_plans_view` (`:109-119`), a `Store` holding only `plans`. The policy's own-words
exceptions read `store.by_id` (`_own_plan_words`, `workspace.py:81`; `_own_plan_text`
`:98`) and `store.ops_on` (`minted_by`, `:122`), so a suggester's allowed untagged
delete/restore/comment edit is "recorded as applied, but the roles table refuses it".

- **D4** — alice inserts (drained, auto-plan), then range-deletes her own words (a real
  delete, H3); `validate_logs([session], roles, store=ws.store)`:
  ```
  s-….jsonl:9: recorded as applied, but the roles table refuses it: suggest may only write plans
  ```
  Every PR from a contributor who corrected a word is refused at the door.

Fix: validate by replay — apply the PR's session files onto a throwaway copy of the
workspace's replay with the policy ON and report each op whose recorded outcome differs
(~15 lines; it is the same comparison as A10's). Or give `_plans_view` the store's
`by_id` and `ops_by_block` (~4 lines, keeps the coupling). §G (b) makes the policy a
function of `plans` again, which is what `_plans_view` was written for.

### A12. `codoc snapshot` crashes when nothing folds (LOW)

`cli/main.py:396` prints `path.name`; `write_snapshot` returns `None` when
`settled_prefix == 0` (`snapshot.py:161-163`), which 04 §5 documents as the ordinary
answer for a tree in flight.

- **A6** — a tree with an open plan on its first feature; `CliRunner().invoke(app,
  ["snapshot", ...])`: exit 1, `AttributeError("'NoneType' object has no attribute 'name'")`.

Fix: 1 line.

### A13. The inverse of a plan mark's inverse is refused (LOW)

A plan-tagged bold records a `suggest_mark` whose inverse is an untagged
`suggest_mark off` (`apply.py:240-242`); that applies. Its inverse is `suggest_mark on`
with the record's attrs (`:257-260`), untagged, which `_h_text_mark` refuses with
`suggest_needs_plan` (`:218-221`). `apply.py:9`: "the inverse of an inverse is the
original"; 01 §9: `apply ∘ inverse = identity` for every kind.

- **C3** — `redo.conflict == "suggest_needs_plan"`. For a suggester the drain re-tags
  the redo with their plan and it works; for an owner or maintainer there is no redo.

Fix: accept a `suggest_*` `on` without a plan tag when `attrs["plan"]` names an open
plan (the record already says whose it is), ~3 lines.

### A14. By reading, no probe

- The daemonless applier's `Roles` table is loaded once (`workspace.py:240`) and
  `applying()`'s rebuild keeps it; the daemon reloads it per inbox drain
  (`daemon.py:112`). A hub sign-in that changes a role reaches a long-lived MCP applier
  only at its next restart. LOW.
- `markdown(mode="accepted")` (`derive.py:71-80`) calls `set_mark` for each record and
  would raise `MarkError` on the C1 state; no product path uses `accepted` today
  (`kinds.plain` and the export use `rejected`).
- `Text.merge` rejoins WHOLE marks across the seam by `same` (`text.py:545-551`): two
  `suggest_del` records of one plan adjacent across a merged seam become one record under
  the head's op; harmless for both verdicts because they act per plan, but it is the kind
  of identity drift the record design was meant to end.

### A15. Confirmed fine (probed, no fault)

- **A2** — `state_fingerprint` equal across a PARTIAL fold (cut strictly inside the log).
- **G1** — the cut is exactly the oldest unsettled feature's `block.add` (documented in
  04 §5; the number is §C's).
- **H1–H9** — see §B.

## B. Round 7: what holds at `18855c0`

| # | round-7 change | status |
|---|---|---|
| 1 | fold = Eg-walker's critical version; `remember`/`archived_on` gone | order and fingerprint hold (A2, H9); **`state_at` re-applies refused ops (A1); a later hand-off's `removed:`/`Focus:` are gone (A3, A4); a held applier's live session is archived (A7); CLI crash on the documented `None` (A6)**; measured: any drifted feature pins the cut at its creation, 0% after a reflect pass (G1, §C) |
| 2 | plan mark toggle = `suggest_mark` record | holds for two plans on one span and for a split under a widening (H1, H2); **accept is not two-phase for a WHOLE-off record (C1); redo refused (C3)** |
| 3 | a contributor's own proposed words (`deleted_ops`, `_own_plan_words`, restore-by-own-delete) | holds for acked words and against a maintainer's delete (H3, H4); **revival after the verdict (D1, D2); provisional anchors (D3); the hub gate cannot evaluate the rule (D4)** |
| 4 | gate scope: `derived=True`, `must_propose`, one batch per amend, `_plan_for` edit-only | the lift stays on the trunk (H5); the batch and edit-only rules were not re-probed (shipped tests `tests/mcp`, `tests/substrate/test_workspace.py`) |
| 5 | `plan.reopen` records `undid`; `bound_once` after the payload read | hold (H6, H7); note the window's entry `ranks` are post-op, like its `blocks` — `window.py:25-27` describes the fold-back rows, not the entry (§D) |
| 6 | cost: index walks, plan-scan skips, answerable counts | hold (H8; §C: cold publish 242 ms, warm 32, `_feature_keys` 3.2); **new O(features × plans) term in `open_plans_on` (§C)** |
| 7 | `client.applying()` per call with rebuild | the write side holds (shipped subprocess test); **`read()` skips the catch-up (B1); a fold archives the held session (A7)** |
| 8 | `Plan.decided_by` | holds across a fold (H9) |

## C. Complexity, measured

`perf8.py`, Python 3.12, this machine. One run; ±40 % between runs on this machine, so
read the ratios within a row group, not the absolute numbers against review 7's.

| operation (N = 1,500 features unless stated) | ms |
|---|---|
| `text.insert` (one op) | 0.1 |
| `Publisher.publish` cold, no plans, drifted tree (13.5k ops) | 242 |
| `Publisher.publish` warm after one insert | 32 |
| `history_window(200)` | 2.3 |
| `phase` / `ground` / `open_plans_on` (h700), 0 plans | 0.0 / 0.0 / 0.0 |
| `settled_prefix`, every feature drifted → cut 0 of 13,521 (0 %) | 19.5 |
| `write_snapshot`, drifted tree (writes nothing) | 21 |
| `settled_prefix`, every feature synced → cut 7,500 of 7,500 | 25 |
| `Workspace()` rebuild, no snapshot / from the snapshot alone | 194 / **129** |
| `write_snapshot`, settled tree, folds whole: log 3.2 MB → snapshot **2.1 MB** | 191 |
| one drifted feature at index 750 → cut 3,750 of 7,501 (50 %); `state_at(cut)` replay | 113 |
| `write_snapshot`, half the tree folds | 237 |
| `Publisher.phases()` cold, 0 / 1,500 / 3,000 **decided** plans | **41 / 126 / 195** |
| `Publisher.publish` cold, 0 / 1,500 / 3,000 decided plans | 325 / 387 / 462 |
| `plan.accept` per verdict (10 marks), N = 1,500 / 3,000 | 2.7 / 3.2 |
| `plan.reject` per verdict, N = 1,500 / 3,000 | 0.3 / 0.2 |
| **N = 3,000**: `publish` cold / `_feature_keys` / `settled_prefix` | 473 / 7.9 / 35.5 (2.2× / 2.5× / 2.1× for 2× N) |

- **What is O(what).** `apply` is O(segments + marks of the touched blocks) as 01 §5 says.
  A cold publish is linear in features now (2.2× for 2× N; round 7's O(n²) slice is
  gone). `settled_prefix` is O(features × ops-per-feature) — it asks `reads_its_ops` of
  every heading before its walk — and linear (17 → 35.5 ms).
- **The new term: `open_plans_on` walks every plan per feature.** `derive.py:279` is
  `for p in store.plans.values()` inside a function `phase` calls once per feature per
  publish. Plans are never deleted (a decided plan is history), so this is
  O(features × all-plans-ever): +85 ms per 1,500 decided plans on 1,500 features,
  ~38 ns per pair; at 10,000 features and a year of proposals it is seconds per cold
  publish. Fix: keep `Store.open_by_target: dict[bid, set[pid]]` maintained where
  `plans_head` moves (`_h_plan_open/accept/reject/reopen`) and read it — ~8 lines, makes
  the term O(open plans on the feature). This is the next thing worth doing, with a
  measurement behind it.
- **The fold is free and folds nothing.** A wholly drifted tree costs 21 ms to *not*
  fold. A settled tree folds to 66 % of the log and rebuilds 1.5× faster. One drifted
  feature in the middle folds half. After every reflect pass every feature is drifted
  (its newest op is a `bind.refresh`), so in the tree's ordinary state the number is 0 %
  (04 §5 says so). §G (a) is the change that would make it fold in that state, and it
  deletes code.
- `plan.accept` is O(blocks) per verdict — `_proposed_marks` (`apply.py:744-754`) scans
  every block's fields for one plan's records (2.7 → 3.2 ms for 2× N). `mark_index` is
  already keyed by placing op and `plan.ops` names the ops; walking those instead is
  O(records). Fine today; note it.
- `Publisher` warm floor 32 ms; `history_window` 2.3 ms; rebuild ~26 µs/op. Fine.

## D. Cleanliness

- **Dead code**: `apply._mark_holder` (`apply.py:759-766`) has no caller (its last one,
  the mark-matching path, was deleted in round 7). `Store.to_json` (`store.py:361-369`)
  has no caller in `codoc/`. Residual blank lines where deleted functions stood:
  `apply.py:756-758`, `derive.py:83-84`, `text.py:142-144`.
- **Three copies of "replay a recorded op"**: `Workspace._replay_one`
  (`workspace.py:300-318`, honours `conflict`), `snapshot.state_at` (`:87`, does not —
  A1), `tests/calculus/test_apply.replay` (does not). One `apply.replay_recorded`.
- **Docstrings that overstate**: `apply.py:4-8` "a conflict discovered midway never
  leaves a partial change behind" (C1); `apply.py:9` "the inverse of an inverse is the
  original" (C3); `snapshot.py:29-30` and 01 §7.5 "so is every derived view" (A3–A5);
  `snapshot.py:57-58` "exact and linear" — conservative, not exact (the cut is at the
  feature's creation, not at the oldest op a reader reads; G1); `snapshot.py:56` "names
  the three" vs `derive.py:319` "Four do"; `client.py:12-19` says two daemonless writers
  rebuild "before writing" — reads do not (B1); `workspace.py:88-96` `_own_plan_text`
  "a restore undoing a delete of their own" — true, and it is not enough (D1); `window.py:
  25-27` "the rank each op displaced" describes the fold-back rows, while an entry's
  `ranks` are the ranks *after* its op, like its `blocks` (H6 reads them that way; say so).
- **The policy's hidden inputs.** `Roles.policy` reads `plans`, `by_id`, `blocks` and
  `ops_by_block` (through `_own_plan_words`, `minted_by`, `is_comment_op`);
  `validate._plans_view` provides `plans`. Nothing names the contract, and D4 is the
  result. Either state it on `policy` or (§G b) shrink the policy back to `plans`.
- **Two doors to the applier.** `client.applying()` takes `applier.lock`; `cli.snapshot`,
  `rebuild`, `translate`, `import-v1` and `_bootstrap` open raw `Workspace(cd)`. The
  `daemon_running` check guards the daemon only (A7).
- `_drain_one` (`workspace.py:560-587`) does `_as_proposal` → `_suggester_plan` →
  `_resolve_provisional`; the first needs the last (D3). Order them.
- `derive.reads_its_ops` and `snapshot.settled_prefix` are one decision in two files,
  and the reason each clause exists is spread across `directive`, `ground`, `_drifted`
  and the verdict handlers. §G (a) is the version with one reason.
- `open_plans_on` (`derive.py:277-282`) unions "target in body" with "ops on body carry
  the plan" per feature per call; with an index (§C) it is one lookup.

## E. Spec ↔ code

| spec | says | code | which moves |
|---|---|---|---|
| 01 §7.3, §7.5 | replay honours the recorded answer; a fold "re-expresses ops that were authorized" | `state_at` re-applies refused ops (A1) | code |
| 01 §7.5, `snapshot.py:29-30` | `state_fingerprint` equal across a fold "and so is every derived view" | `history` differs (A5); a later hand-off's directive differs (A3, A4) | spec (name the views: those that read state) + code (A2's fix) |
| 01 §6 `directive` | "Focus from `bold` marks a person turned on since the last realization (each by its own placing op)" | read from `text.mark` OPS (`derive.py:490`); the marks carry the op | code |
| 01 §7.5 | "a session file the fold covers WHOLE moves to `log/archive/`" | including one a live writer holds (A7) | code (rotate / archive only closed files) + spec (say "closed") |
| 04 §5 | "returns None rather than a file" | `codoc snapshot` crashes on it (A6) | code |
| 01 §1 | "a writer whose store the log has outgrown since its last turn rebuilds before it applies" | a reader does not (B1) | code + spec ("reads too") |
| 01 §7.3 | `suggest` may write "a `text.restore` undoing a `text.delete` of their own" | admits revival after the verdict (D1, D2) | spec ("…of their own open plan's words") + code |
| 02 §3.6, round 7 A3 | a contributor's Backspace over their own proposed words is a real delete | not within one flush (D3) | code |
| 01 §7.3, 04 §6 | the hub validates a PR's logs against the table | own-words ops refused at the door (D4) | code |
| 01 §3 `plan.accept` | "tombstones the characters under its `suggest_del` marks" | the range, including words typed inside since (C2) | spec (define "under": the characters covered when placed) + code |
| 01 §5, `apply.py:4-8` | apply is total; handlers resolve before mutating | accept strikes before it resolves the mark records (C1) | code |
| 01 §9 | `apply ∘ inverse = identity` for every kind | a plan mark's redo is `suggest_needs_plan` (C3) | code |
| 01 §3 | a plan's `bind.attach` displaces live; `reflect` proposes deleting emptied features | the borrowed symbol's owner is proposed for deletion (C4) | code |
| 01 §7.2, I6 | merge order `(at, session, seq)`; "nothing is refused at replay" | an op applied in one clone conflicts at merge, unreported (E1) | spec (say it can) + code (report) |
| 02 §2 | "empty a title by deleting characters → flagged" | 01 §6 `ink` = live segments; a delete-only edit is `synced` (F1) | spec (pick one; the directive already carries `removed:`) |
| 04 §5 | "everything before the oldest op any unfinished feature was written by" | = that feature's `block.add`; 0 % after a reflect pass (G1, §C) | spec is honest; §G (a) is the code change |
| 02 §3.2 `divergent`; 02 §2 `copied_from`, select-all refusal; 04 §3 `codoc export --merge` | (from round 7 §E) | still absent from `codoc/` | spec (mark unbuilt) |

## F. Ranked changes

1. **Replay honours the record everywhere.** `apply.replay_recorded(store, op)`; used by
   `_replay_one`, `state_at`, the test helper. ~10 lines, deletes ~8. Probe A1.
2. **The directive reads state.** `_new_marks` from marks; `Seg.dead_actor` written at
   `delete`/`kill`; `_human_changes`, `ground`, `_authored`-for-kills read it. ~25
   lines. Probes A3, A4. Then drop the "directive" clause from `reads_its_ops` (the
   hand-off op is re-emitted) and re-measure the fold.
3. **One applier, one door.** `applying()` rotates when its file is gone; `read()` catches
   up; `snapshot`/`rebuild`/`translate`/`import-v1` under `client.applying()`. ~15 lines.
   Probes A7, B1. (Or §G (d): a `closed` header and archive only closed files.)
4. **A contributor's own words, twice.** Restore requires own open-plan words (~3);
   resolve `$cid` anchors before `_as_proposal` (~3). Probes D1, D2, D3. (§G (b) deletes
   all four exceptions instead.)
5. **The verdict.** WHOLE-off record with no mark = satisfied (~3) — probe C1;
   `suggest_del` records `segments`, accept kills by identity (~15) — probe C2.
6. **`_emptied` ignores borrowed symbols.** ~6 lines. Probe C4.
7. **Report record/replay disagreement** in `_replay_one` (~4) — probe E1; `codoc
   snapshot` on `None` (~1) — probe A6.
8. **The hub's door validates by replay** (~15) — probe D4 (with the surfaces round).
9. **`open_by_target` index** so `phase` is O(open plans on the feature) (~8) — §C.
10. **Redo of a plan mark** (~3) — probe C3; dead code and the nine docstrings (§D); the
    spec rows in §E.

## G. The design against its own literature

Read: `docs/brainstorms/2026-09-17-substrate-literature.md`, review 7 §G and its Applied
note. Round 7 found that patching a seam twice produced the next fault and that the
literature's closed forms *delete* code; both shipped. This round's faults are, again,
where a closed form was adopted for one half of a mechanism and not the other half.

**(a) The fold, third round: the critical version was adopted; the per-character
metadata it depends on was not.** Eg-walker (Gentle & Kleppmann 2024) can rebuild "from
the most recent critical version" because what it persists — the event graph AND the
document with per-character origin — is enough for every later read; the critical
version is a *concurrency* boundary, not a "has anyone finished reading" boundary. Codoc
adopted the boundary and kept four readers that need op records: `directive` (killer's
actor, mark's placing op), `_drifted` (order of two events), `ground` (position of the
person's last op), open plans (the plan's ops). So `reads_its_ops` decides per feature,
`settled_prefix` stops at that feature's *creation*, and the measured fold is 0 % after
every reflect pass (§C) — and A3/A4 show the "has anyone finished reading" question is
unanswerable at fold time anyway, because the reader is a hand-off nobody has given yet.

The closed form is the one the design already chose for insertions and stated in 01 §7.5
("a segment carries its actor, its clock and its minting op through the fold"): put on
the *thing* what the reader needs. Automerge does exactly this — each element carries
its `succ` op ids, and an Automerge `OpId` is `counter@actor`, so the deleter's identity
survives compaction with the character; codoc's `OpId` is `session:seq` (one file, many
actors, 01 §7.1), which is why the killer's identity lives only on the op. Etherpad's
attribute pool is the same answer from the other tradition: every char carries author
and every attribute. Concretely: `Seg.dead_actor` (and the killer's `at`, which
`Text.delete` receives as `by` today — add `by_actor`, `by_at`), `Binding.refreshed_at`,
and per-feature `Watermarks.engaged_at` (HLC of the person's newest op on it, written in
`record`). Then `directive` needs no op, `_drifted` is `binding.refreshed_at > max(seg.at)`
with no op scan (deletes `derive.py:343-357`), `ground`'s `since` is an HLC compare
(deletes the `ops_on` sort), and `reads_its_ops` shrinks to "an open plan is on it".
Deletes: `_drifted`'s scan, `ground`'s position logic, three of `reads_its_ops`'s four
clauses, most of `settled_prefix`'s reason to exist (~70 lines) for three fields and ~15
lines. The measured consequence: a tree that is drifted everywhere — the ordinary state —
folds whole. A1's `state_at` also disappears in the common case (cut = whole log =
"return store").

**(b) The suggester's own words, third round: four special cases where Google Docs has
none.** Round 6 rewrote a suggester's range delete into `suggest_del`; round 7 let their
delete of own-plan words and restore of own-delete through untagged; this round finds the
restore reaching past the verdict (D1/D2), the range check running before anchors resolve
(D3), and the hub gate unable to evaluate any of it (D4). `Roles.policy` now has
`_withdrawing_own_empty_plan`, `_own_plan_text` (two branches), `_own_comment`, and each
reads store state beyond `plans`. In Google Docs' suggestion model a suggester's every
edit *is* a suggestion; deleting text you suggested is the suggestion getting smaller,
not a second act on the trunk, and there is no untagged op to authorize. The closed form:
**a suggester writes only plan-tagged ops and comments** (the policy 01 §7.3 stated before
round 7's exception), and `text.delete`/`text.restore` become PLANNABLE *when every
character they name was minted by the same plan* — checked in the handler, where anchors
are resolved and `by_id` is at hand, and recorded as an ordinary tagged kill/revive.
Under that rule D1/D2 close by construction (`plan_not_open` after the verdict), D3
closes because the check is after resolution, D4 closes because the policy is a function
of `plans` again — which is what `_plans_view` was built to carry. Deletes `deleted_ops`,
`_own_plan_words`, `_own_plan_text`, the own-words branch of `_as_proposal` and their
docstrings (~60 lines) for ~15 in `_h_text_delete`/`_h_text_restore` and one line in
`PLANNABLE`. The `_own_comment` rule stays (a comment is not a plan), and it is the one
exception the hub gate should be taught.

**(c) `suggest_del` is a range where `text.delete` is a set of characters.** Round 3
made `text.delete` store "the explicit live segments the range covered at apply" (01 §3)
precisely so an undo or a verdict never kills "anything typed between them since". The
proposal form was left as a `Mark` over two anchors and is struck with the range form
(C2). Google Docs keeps suggested deletions as per-character suggestion ids; Etherpad as
an attribute on the chars. Record `segments` on the `suggest_del` at placement and strike
by identity: ~15 lines, and it removes the class rather than the case (a split across a
strike, a merge, the person's insert inside).

**(d) The applier lock is a hand-rolled WAL, and it skips the reader's check.** SQLite's
WAL rule is that *every* connection, readers included, re-reads the wal-index at the
start of every transaction; `applying()` does this for writers (item 7) and `read()` does
not (B1). And a fold moving a file a connection holds open is the case journaling systems
avoid by never relocating a live file: git's `gc` packs only objects no process is
writing (`gc.pruneExpire`), and a WAL is checkpointed, never renamed under a reader. The
closed form here is a `closed` stamp in the session header written by
`SessionWriter.close()` (`log.py:219-223`), with `write_snapshot` archiving only closed
files (~6 lines): it deletes the "did my file move" question rather than answering it,
and it turns A7 from a rotate-and-hope into an invariant. Reads catching up is 8 lines
either way.

**(e) The verdict's two phases, and idempotent deletion.** `plan.accept` is the one
handler with three mutation stages and a rule table ("three refinements", 01 §3), and C1
is the stage that can fail after the others ran. In RGA/Yjs/Automerge deleting an
already-deleted element is a no-op by definition, and a style suggestion whose target is
already in the proposed state is satisfied. Adopt that: a WHOLE-off record with no
matching mark is *already done*, not a conflict. One rule (3 lines), and it removes the
only `MarkError` path in the verdict, which is what makes the handler two-phase again
without restructuring it.

**(f) Merge silence.** OT servers reject an operation that no longer transforms and tell
the client; Pijul and Darcs keep the losing patch as a visible conflict; Grove's rule is
"represent, do not resolve". Codoc records `plan_not_open` on B's op at merge (E1) and
tells nobody — the OT rejection without the message. The 4-line fix is the message (a
`LogProblem` when a record and its replay disagree). The design-level form, if wanted
later: an op tagged with a plan that has since been decided lands as a *new* plan of the
same actor (its words are still a proposal, just to a different question), which is
Google Docs' behaviour when you type into an accepted suggestion. ~10 lines in the drain
or the handler; not urgent.

**Holds, and worth saying.** The proposal-record design for marks (item 2) is the first
of the plan mechanisms to survive a round without a new matching rule: H1 and H2 pass,
and the only faults left are at the verdict's own edges (C1, C3), not in finding what to
undo. The critical-version fold is correct where it folds (A2, H9): what remains is that
it folds too little (a) and that one of its callers reads the log the wrong way (A1). The
gate lives in one place with one different rule for `translate`, as the Applied note
says. Etherpad's one-value-per-key-per-character, Peritext's non-growing anchors,
Baseline's "failure is the signal", Kleppmann's move-op with "their move stands" all hold
as round 7 found them; nothing this round found contradicts them, and (a)–(c) are each
the same principle applied to a place it had not yet reached.

---

## Applied (2026-09-18)

Every ranked change is in, across five commits (`df44e0b`..`6a8a20e`); the suite is
**347 Python tests, 159 TypeScript, 3 skipped**. Measured after (`probes/perf8.py`,
one run; this machine varies ±40% between runs):

| | review | now |
|---|---|---|
| `settled_prefix` on a fully drifted tree | **0 of 13,521 ops (0%)** | **13,521 of 13,521 (100%)** |
| `Publisher.phases()` COLD, 0 / 1,500 / 3,000 decided plans | 41 / 126 / 195 ms | **22 / 24 / 28** |
| `Publisher.publish` COLD, 3,000 decided plans | 306 | **236** |
| `Publisher.publish` COLD, drifted tree | 260 | 212 |

**§G was right twice, and the second time is the round's whole result.** §G(a) said
round 7 adopted Eg-walker's critical version for the fold's BOUNDARY and not the
per-character metadata it depends on, and that the closed form is the one 01 §7.5
already stated for insertions: **put on the thing what the reader needs**. Automerge's
element carries its `succ` op ids and its `OpId` is `counter@actor`, so the deleter's
identity survives compaction with the character; codoc's is `session:seq` — one file,
many actors — which is exactly why the killer's name lived only on the op. So:
`Seg.dead_actor` and `Seg.dead_at`, `Mark.at`, `Watermarks.code_at` and `realized_at`,
`Block.detached`. The directive's `removed:` and `Focus:` read the text and the marks,
`ground`'s "since" and `_drifted` are clock comparisons, a phantom's settle reads the
block — and `reads_its_ops` is one clause. The fold went from folding nothing after any
reflect pass to folding everything.

§G(b) said three rounds of special cases in the authority policy had a closed form with
none: in Google Docs a suggester's every edit IS the suggestion, so taking back words
you proposed is the suggestion getting smaller and there is no untagged op to authorize.
`text.delete` and `text.restore` are `PLANNABLE`, the applier holds them to the plan's
own characters, and D1/D2/D3/D4 close by construction — including the one the hub's door
could not evaluate, because the policy is a function of the plans again.

Three of the ranked changes were made differently:

- **§F 2** proposed `_new_marks` reading marks and `Seg.dead_actor`. Both shipped, and
  the fold's frontier had to be folded into the store's too — otherwise every watermark
  taken after a fold covered nothing, and A3's probe passed only because it did.
- **§F 3** offered "rotate when our file moved" or the `closed` stamp; the stamp shipped
  (§G d), because it deletes the question rather than answering it, and a crash then
  leaves a file in `log/` where it is still read.
- **§F 5**'s `suggest_del` segments are recorded in the mark's own `attrs` rather than a
  parallel structure, so the record travels with the thing it describes — the same move
  as the rest of the round.

§A5 (`history` differs across a fold) is a **spec edit, not a code change**: `history`
and the timeline are views of the LOG, and a fold is the log ending. 01 §7.5 and
`snapshot.py` now say "every derived view of the DOCUMENT" and name the exception.
§F1 was a question and the code moved: a person who only deleted words has pending
intent, so `phase` is `edited` (`ink` stays ink — 09-editor §7 draws removals not at all).

Four probes still fail, all on premises the fixes removed:

- **A5** as above.
- **D1/D2** submit the withdraw UNTAGGED, which is what round 7 allowed and round 8
  refuses outright; the property they assert (no revival after the verdict) holds by
  construction and is pinned through the real drain path in
  `test_a_contributor_cannot_revive_words_they_withdrew_once_the_plan_is_decided`.
- **G1** asserts the cut lands at the oldest unsettled feature's `block.add`, which was
  true when a drifted feature was unsettled.
