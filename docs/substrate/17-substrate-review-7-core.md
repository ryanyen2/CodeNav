# Review 7: the Python core (calculus, substrate, loops, kinds, MCP, CLI)

Scope: worktree `CodeNav-substrate`, branch `substrate`, HEAD `9012665`, clean. Read
against `01-calculus.md` (rev 2 + the review-6 edits), `02-workflows.md`,
`03-extensions.md`, `04-version-control.md`, reviews 08/10–16 and their probes, and the
two founding brainstorms (`docs/brainstorms/2026-09-17-*`). Every `file:line` is at
`9012665`. Nothing in the worktree was modified. Baseline: 306 shipped tests pass, 1
skipped.

Probes: `probe7_core.py` beside this file — 26 probes, each asserting the CORRECT
behaviour, so a failing probe is a confirmed fault. **20 fail, 6 pass** (the 6 are the
confirmations in §A9). Run from the worktree:

```
PYTHONPATH=. ../CodeNav/.venv/bin/python -m pytest <scratchpad>/probe7_core.py -q -p no:cacheprovider
```

`perf7.py` beside it is perf6's rows re-run at `9012665` plus scaling rows (§C);
its output is `perf7.out`.

## Verdict

Round 6 closed what it named: a plan's widening, displacing and partial-off marks are
rejectable and hidden in the rejected mode, the agent's MCP amend is gated, a
contributor's Backspace over trunk words becomes a `suggest_del`, the directive and the
phantom survive a fold, `_emptied` reads state, and the window carries liveness and
ranks (§A9, §B). But the round's two largest changes each carry a fault of the same
shape as the one they fixed, and the fault is at the seam the round itself created.
**The snapshot now carries every unsettled feature's ops and drops their order**:
`Store.remember` files each one at position −1 and `ops_on` hands them back grouped by
block in set-iteration order, so after one fold 20 of 40 `drifted` features read
`synced` (which 20 depends on the hash of two block ids), `ground` paints nothing it
painted before, and the second fold writes the archive out of apply order and adds the
first fold's own ops to it (A1, six probes). The fold also compacts nothing in the
common case: after any reflect pass every feature is `drifted`, so the snapshot is the
whole log plus the state — 8.3 MB replacing a 6.0 MB log (§C). **The plan-mark match
on (kind, attrs, start, placer) is not closed under the ops that move marks**: an Enter
inside a plan's widening leaves its extension bold after the reject and in the export,
and two plans proposing one bold share one placer, so rejecting the first empties the
second before its verdict (A2). Beside those, the drain's `suggest_del` rewrite meets the
surfaces round's own-plan-text rule at the wrong seam: a contributor who types a word and
backspaces over it gets their own words struck under their own proposal, while
`text.restore` lets them revive words the maintainer deleted (A3). The gate that moved
into `Workspace.ingest` for "every machine" also caught the one machine writer that is
not intent: the loop's diagram lift now opens a plan when the code graph moves enough,
which is the branch-for-the-always-on-channel the founding brainstorm ruled out; the
same round's `_at_realized` keeps an accepted agent plan's diagram edge out of the
directive; and the agent's multi-block amend is N proposals, not the one the Applied
note claims (A4). Two clones that each `codoc snapshot` lose one clone's features on
merge with no problem reported (A5).

Cost (§C): apply and the per-field views are what the spec says. Cold publish with 20
open plans is 534 ms against 306 without; the 2× is no longer the mark-op walk (0.0 ms
per field) but a `Text.clone()` of every field in two places whenever any plan is open.
`_feature_keys`, `body_ids` and `feature_of` are O(n) each from list slicing, so a
publish has an O(n²) term (9.4 → 40.8 ms for 1,500 → 3,000 features) that is 3% today
and dominant at ten thousand.

---

## A. Correctness

Ranked. Each item names its probe and quotes the decisive assertion. `T(s, bid)` is the
live text; `seed(ws)` is review 5's `h1 "Client"` / `p1 "Retries once."` bound to
`c.py::send` (`r = refs(ws, "p1")`, `r[0..6]` "Retries", `r[8..11]` "once").

### A1. The snapshot keeps the unsettled ops and loses their order (HIGH; regression of review 6 A4's fix)

Review 6 A4 offered "refuse or carry"; carry shipped. `snapshot_ops` archives every
applied op of every non-`synced` feature (`snapshot.py:93-104`, `_unrealized`
`:120-133`); `Workspace.rebuild` hands each to `Store.remember` (`workspace.py:204-208`),
which files it in `by_id` at **position −1** (`store.py:349-360`); `ops_on` returns
`archived_on[b]` for each `b` in the *set* it is handed, concatenated, then the applied
ops (`store.py:362-374`). Three readers decide by that order or by position:

- **P2g** — `seed(loop)` then a `bind.refresh`; truth order `[add h1, ins h1, add p1,
  ins p1, attach, refresh]`; after a fold:
  ```
  truth [0, 1, 2, 3, 4, 5]
  got   [2, 3, 0, 1, 4, 5]        # p1's ops, then h1's: set order of {"h1", "p1"}
  ```
- **P2h** — forty loop-written features, each refreshed after its prose, all `drifted`;
  after one fold:
  ```python
  assert after == before   # before {'drifted': 40} after {'drifted': 20, 'synced': 20}
  ```
  `_drifted` (`derive.py:384-398`) compares the enumerate index of the newest binding op
  with the newest prose op; whichever block's list `ops_on` emits last wins. Which
  twenty flip depends on `hash("h17") < hash("p17")`, i.e. on `PYTHONHASHSEED`: two
  clones rebuilding the same snapshot disagree on phase.
- **P2b** — a realized feature the loop then repaired on the trunk, then a refresh
  (`drifted`, so archived): `ground` before `[('added', ', then stops')]`, after `[]`.
  `ground` measures "since" by `store.position` (`derive.py:257-263`); every remembered
  op is −1, so no folded loop op is ever newer than the person's last touch. 01 §7.5
  now says the fold archives these ops *so that* `ground` answers the same.
- **P2c** — the second fold. `snapshot_ops` sorts the archive by `store.position.get(id,
  0)` (`:103`), which is −1 for everything the first fold carried, so the stable sort
  keeps the order the open-plan loop and `ops_on` visited them in:
  `order_of_original=[5, 0, 1, 2, 3]` at fold 2 (the plan's insert first; op 4, the
  `plan.open`, is dropped because `remember` indexes no block for it). And `_unrealized`
  includes the previous snapshot's own `block.add`/`bind.attach` ops, so the archive
  grows by one copy of the state per fold: 6 → 8 → 10 archived lines over three folds
  of a six-op log; 13,500 → 8,856 + 4,500 at 1,500 features (§C).
  ```
  the second fold archived the FIRST fold's own ops: ['snap-…:1', 'snap-…:2', 'snap-…:3']
  ```
- **P2a** (LOW, spec-consistent, user-wrong) — the same feature *without* the refresh is
  `synced`, so nothing is archived and the green the person has not caught up on is
  gone after the fold. 02 §2 gives them "Catch up / Keep" to clear it; the fold does it
  for them. Either `_unrealized` also archives a feature whose `ground` is non-empty, or
  01 §6 says `ground` clears at a fold.

Fix, in order of leverage. (1) `remember(op, k)` files the k-th archived op at position
`-(N - k)` so folded ops are ordered among themselves and all older than anything
applied; `ops_on` sorts `old` by position (~6 lines). (2) `snapshot_ops` sorts by that
position too and excludes ops whose `actor == SNAPSHOT_ACTOR` (or whose session has the
`snap-` prefix) from `_unrealized`, so a fold never re-archives a fold (~3 lines). (3)
`ground`'s `since` and `new` then work unchanged. Probes P2b/P2c/P2g/P2h into
`tests/substrate/test_review5.py`'s neighbour. The deeper alternative is in §G (Eg-walker's
critical version): fold only up to the last frontier at which every feature was
`synced`, and keep everything after it as ordinary log, which deletes `archived`,
`remember` and `archived_on` outright.

### A2. Plan marks are matched by start and placer; a split moves the start and a merge shares the placer (HIGH; the third rule in three rounds)

`Text.marks_placed_by` (`text.py:450-460`) finds a live mark by `m.op == op` or by
`(kind, attrs, start, op) ∈ placed_keys(added)`. `_undo_plan_op` (`apply.py:653-678`)
and `_unapply_plan_marks` (`derive.py:89-107`) both use it; `_mark_targets`
(`apply.py:748-759`) finds the fields to look in by `_mark_holder`, which needs *both*
ends of the recorded mark in one Text.

- **P1a** — the person bolds "Retries"; the plan widens to "Retries once" (recorded
  `added`: bold, start r0, placer = the person); the person presses Enter after "Retries
  o"; reject:
  ```python
  assert markdown(tail) == "nce."     # got '**nce**.' — the tail piece starts at r9 under the person's placer
  ```
  `_mark_targets` finds no field at all (the recorded end r11 is in the other block), so
  nothing is removed anywhere; the head keeps the widened piece too and the person's
  original is put back beside it.
- **P1b** — the same while open: `markdown(tail, "rejected") == "nce."` → got
  `'**nce**.'`; the export shows the proposal.
- **P1c** — plan A bolds "Retries"; plan B (another actor) bolds "Retries" too:
  `set_mark` merges B's into A's and keeps A's placer, so B's `added` names A's mark.
  Reject A removes the mark whole; B is still open and offered; accept B:
  ```python
  assert bold_chars(t) == set(r[0:7])    # got set(): 'plan B was accepted and its bold is gone'
  ```
  A verdict on one proposal decided another.

Review 4 matched by value, review 5 by placer, review 6 by record; each handled the
previous round's cases and missed the next's, because the mark a toggle leaves is
mutated afterwards by ops the record cannot foresee (merge, split, another plan). The
rule that is closed is the one the literature brief already named (§G): a plan's mark
toggle is a **proposal record, not a mutation of the trunk marks**. Concretely: under a
plan, `text.mark` places a non-growing `suggest_mark{plan, kind, attrs, on}` mark and
touches the trunk marks not at all; the projection draws it (as `plan_claims` already
does from `added`); the rejected mode ignores it; `plan.accept` applies the recorded
toggle then, as an ordinary trunk `set_mark` under the plan's actor, and `plan.reject`
removes the record. That deletes `marks_placed_by`, `placed_keys`, `_placed`,
`_unapply_plan_marks`' body, `_mark_targets`, `_mark_holder`, both `_rejected_*mark`
and the `marks`/`marks_back` entries (~130 lines) for ~60. The minimal patch instead —
a `via: OpId` on `Mark` that pieces and merges copy, matched alongside `op` — is ~12
lines and fixes P1a/P1b but not P1c. P1a–P1c into `tests/calculus/test_plan_marks.py`.

### A3. The suggester's Backspace and the suggester's undo meet at the wrong seam (HIGH for the hub flow; seam of review 6 A3 and surfaces A6)

Round 6 rewrote a suggester's range `text.delete` into their plan's `suggest_del`
(`workspace.py:400-414`, `_as_proposal`); the surfaces round let a suggester's
`text.delete`/`text.restore` *by segments* pass the policy when every character named
was minted by an open plan of theirs (`workspace.py:52-67`, `_own_plan_text`). The
editor's Backspace is the range form (`vscode-codoc/src/calc/recorder.ts:83,198`).

- **P3a** — `hub-alice` inserts " More." (a plan opens), then backspaces over it:
  ```python
  assert T(ws.store, "p1") == "Retries once."
  # got 'Retries once. More.' with a suggest_del{plan: alice's} over " More"
  ```
  Her own words stand struck under her own proposal, and the maintainer is asked to
  accept the deletion of a suggestion. Typing and correcting a sentence on the hub
  leaves every correction as a strike.
- **P3b** — alice inserts " More." (plan); the maintainer deletes it on the trunk
  (01 §3: "an ordinary delete: the plan sees fewer of its words survive"); alice sends
  `text.restore {segments: [[ins, 0, 6]]}`:
  ```python
  assert T(ws.store, "p1") == "Retries once."   # got 'Retries once. More.', ('text.restore', None)
  ```
  A suggester overrode an owner's op. `_own_plan_text`'s docstring: "touches nothing of
  anyone else's"; 01 §7.3 gives `suggest` no undo of a trunk op.

Fix: `_as_proposal` resolves the range in the store and, when every live character in it
was minted by an open plan of this actor, leaves it a `text.delete` (the policy then
needs the same range check, or the drain converts it to the segments form). `_own_plan_text`
drops `text.restore`, or admits it only with `by` naming an op of the actor's own. ~15
lines; P3a/P3b into `tests/substrate/test_workspace.py`.

### A4. The gate for "every machine" caught the machine that is not intent, and its plan bookkeeping is per block (MEDIUM-HIGH; seams of review 6 A2/A6/§D)

- **P4a** — `lift_pass` writes a diagram's re-derived source through `write_block` →
  `Workspace.ingest(actor="loop")` (`lift.py:38-44`, `write.py:106-113`), which now runs
  `_must_propose` (`workspace.py:334-346`). When the bound code moves enough that the
  new source deletes more than half the old one (four symbols leave, one arrives):
  ```python
  assert open_plans_of(ws.store, "loop") == []   # got [('edit', 'a larger repair')]
  ```
  and the rejected mode (the export, the directive's `consult`) shows the *old* diagram.
  03 §5.1: the lift "writes the delta with `actor=loop` on the trunk"; `lift.py:1-8`: "a
  lift is attribution, like a binding refresh, never intent". The next lift finds the
  live content equal (the plan's words are live) and writes nothing, so the plan is the
  only path to the current diagram.
- **P4b** — `_at_realized` (`derive.py:482-495`) now keeps every non-human segment. An
  agent proposes a diagram edge under a plan, the person accepts and hands off (02 §3.3:
  a plan is a request to build); the directive:
  ```
  Diagram of this feature's code (mermaid):
  flowchart TB
    send
    retry
    send --> retry
  ```
  and no "Add a dependency or call from `send` to `retry`": `old` already contains the
  accepted segments, so `lower` sees no change. The accepted proposal is the one case
  where machine segments beyond the frontier *are* the request.
- **P4c** — `tools.reflect` sends one `ingest` per block with no batch
  (`tools.py:247-250`); `Workspace.ingest` keys `_plan_for` on `batch or bid`
  (`:322`); a queued feature's two-paragraph amend:
  ```python
  assert len(plans) == 1     # got 2: one plan per paragraph, both 'the document has pending intent'
  ```
  01 §4 rule 8: "One amend is one plan"; review 6's Applied note: "an agent's multi-block
  amend is one proposal too" — it is not, on either the daemonless or the inbox path.
- **P4d** — `_plan_for` reuses "an open plan of theirs on the same block" whatever its
  kind (`workspace.py:427-430`). An agent that proposed `retire_node h1` (a `delete`
  plan targeting `h1`) and then repairs the title through `codoc_reflect`:
  ```python
  assert riders and "delete" not in riders    # got {'delete'}
  ```
  Accepting the deletion accepts the title; rejecting it drops the repair.

Fix: `Workspace.ingest(..., gate=True)`; `lift_pass` passes `gate=False` (or
`_must_propose` returns None when the block's kind declares a `lift`: derived content is
attribution). `_at_realized` drops, beyond the frontier, human segments *and* segments
whose plan is accepted, and revives kills by those. `tools.reflect` mints one batch id
for the whole amend and passes it through `client.ingest` (add a `batch` field to the
`ingest` submission; `_drain_one` already forwards `s.batch`). `_plan_for` reuses only
`kind == "edit"`. ~25 lines; P4a–P4d into `tests/kinds`, `tests/mcp`.

### A5. Two clones that each snapshot lose one clone's work on merge, silently (MEDIUM)

`rebuild` takes `list_snapshots(...)[-1]` (`workspace.py:198-203`), reads no other
snapshot, and reads nothing under `log/archive/` (`log.py:154-158`). **P2d**: clones A
and B fork from one base; A adds "Alpha", B adds "Beta"; each runs `codoc snapshot`;
git merges by adding both snapshot files and both archives:
```python
assert {"Alpha", "Beta"} <= titles or m.problems     # got ({'Beta', 'Client'}, [])
```
04 §5 promises "a branch forked before the snapshot merges correctly"; a branch that
*also* snapshotted is the ordinary case for two long-lived checkouts, and nothing says
only one party may fold. The hub refuses a PR that carries a snapshot; a local merge has
no door. Fix: `rebuild` reports a `LogProblem` when more than one snapshot file is in
`log/` and, until a fresh fold, also replays `log/archive/*` sessions outside the chosen
frontier (~12 lines); 04 §5 states that snapshots are taken on the integration branch
only. P2d into `tests/substrate`.

### A6. Three smaller fold faults (MEDIUM-LOW)

- **P2e** — `snapshot_ops` stamps the fold's `plan.accept`/`plan.reject`/
  `feature.handoff`/`feature.realized` with `actor="import"` (`snapshot.py:46,86-88,
  105-116`), a `machine`, for which those kinds are refused. `refused_verdicts`
  (`serve/validate.py:94-103`, printed by `codoc rebuild`) counts them:
  ```python
  assert refused_verdicts(ws.store, ws.roles) == {}     # got {'import': 3}
  ```
  The signal that is meant to catch a forged log fires on every rebuild after a fold.
  Fix: skip ops of the snapshot session (2 lines), or stamp the verdicts with the actor
  that gave them (the plan's status has no recorded actor; the hand-off op does).
- **P2f** — a person undoes a hand-off that was already realized (the one sanctioned I9
  regression); the feature keeps `realized` with an empty `handoff`; the fold emits
  `feature.realized` alone and `nothing_queued` refuses it:
  ```python
  assert not [o for o in ws.store.ops if o.conflict]    # got [('feature.realized', 'nothing_queued')]
  ```
  and `state_fingerprint` differs (the realized frontier is gone). 01 §7.5:
  "`state_fingerprint` is equal across a fold". Fix: emit a `feature.handoff
  {covers: realized}` before it when `handoff` is empty (3 lines), or decide that
  undoing a realized hand-off is itself refused.
- **P2a** — see A1's last bullet.

### A7. Two daemonless writers publish two different trees (LOW-MEDIUM, design)

`client.py:37-57` makes any process the applier when no daemon runs and keeps its
`Workspace` for the life of the process (the MCP server); a second process (`codoc
handoff`, `codoc accept`, a hub without a daemon) does the same. **P6a**: two
`Workspace(cd)` each insert and each `publish`:
```python
assert " A." in text and " B." in text     # projection.json: 'Retries once. B.'
```
Neither store is wrong at replay (two session files merge by HLC on the next rebuild),
but 01 §1's "one applier" is enforced only by a pidfile the daemon owns; two daemonless
appliers are silent about each other and the derived files describe whichever wrote
last. Fix: the daemonless applier takes the same pidfile (~10 lines in `_applier`) and
the second process falls back to the inbox with a clear error when the ack never comes.

### A8. Applier and window edges (LOW)

- **P5a** — `_rank_rows` and `_shape_rows` read a verdict's `moved`/`sets` as "the rank
  and shape it displaced" (`window.py:220-252`). A reject records what *it* displaced;
  `plan.reopen` copies that record and *applies* it (`apply.py:885-900`), so a reopen's
  rows are the values it set. A window holding only the reopen:
  ```python
  assert w["ranks"].get("h2") == before_reopen   # 'X'; window says 'F' (the rank after)
  ```
  Reopen reaches the log only through undo, so this is rare. Fix: `_h_plan_reopen`
  records `restored: [[bid, prev_rank, prev_level]]` and `sets_prev`, and the window
  reads those for reopen (~8 lines).
- **P5b** — `_h_bind_attach` adds to `bound_once` (`apply.py:534`) before reading the
  payload keys it may conflict on (`:536`):
  ```python
  assert _emptied(ws.store) == []     # got ['h1'] after a 'missing:symbol' attach
  ```
  A feature that never had code is proposed for deletion as one that lost it. Move the
  line after the key reads (1 line). `_h_bind_attach` is the one handler that mutates
  before it has resolved everything (the two-phase rule in `apply.py:4-8`).

### A9. Confirmed fine (probed, no fault)

- **P8a** — `replay(log) == store` after the drain's `suggest_del` rewrite (I6 holds for
  the rewritten op).
- **P8b** — a plan's bold grown by the person typing at its edge is still rejected whole
  (`marks_placed_by` by `op`; review 5 A4 holds).
- **P8c** — reject of a widening plan bold then undo (`plan.reopen`) restores the widened
  mark exactly (`apply ∘ inverse = identity` for the review-6 path).
- **P8d** — two clones, a loop plan open in one and the person typing after the same
  anchor in the other: both land, no conflict, the rejected mode shows only the person's
  words, the reject kills only the plan's.
- **P8e** — a widening plan bold is rejectable after a fold (the archived op's record is
  read).
- **P8f** — the gate: a small agent rewrite of machine prose lands on the trunk; a large
  one becomes the agent's plan.
- By reading: `_withdraw_if_empty` cannot loop (it rejects only `open` plans with no
  ops); `_as_proposal` leaves the `segments` form alone, so a suggester's *undo* still
  passes through `_own_plan_text` as the surfaces round intended; the `_placed` cache is
  safe (a recorded op never changes); `Publisher` memo keys still cover every input the
  views read (a hand-off touches `fid`, a plan op moves `plans_head`, a fold calls
  `invalidate`).

## B. Round 6: what holds at `9012665`

| # | round-6 change | status |
|---|---|---|
| A1 | `marks_placed_by` by (kind, attrs, start, placer) | holds for widen / displace / partial-off / growth (shipped, P8b/P8c/P8e); **not under a split, not for two plans on one span** (A2) |
| A2 | gate in `Workspace.ingest` for every machine; `REWRITERS` has `agent` | holds for the agent (shipped `tests/mcp`, P8f); **catches the lift** (A4/P4a); multi-block = N plans (A4/P4c) |
| A3 | drain rewrites a suggester's range delete; withdraws empty plans | holds for trunk words (shipped); **wrong over the suggester's own words** (A3/P3a); I6 holds (P8a) |
| A4 | snapshots archive unsettled features' ops; `remember`/`archived_on` | directive, phantom, `_emptied` hold (shipped); **order lost, phase flips, ground clears, archive grows** (A1); merge of two folds loses work (A5); `import` verdicts trip the signal (A6) |
| A5 | `prose_blocks` in `ink`/`ground`/`plan_claims` | holds (shipped) |
| A6 | `_at_realized` keeps machine segments | holds for the loop's lift (shipped); **wrong for an accepted agent plan** (A4/P4b) |
| A7/A8 | reopen skips dead `binds`; `marks_back` pre-check | hold (shipped) |
| A9 | `_require` on the directory | holds (shipped) |
| A10 | window carries `dead`/`live`, `ranks`, `shape` | holds for move + reject; **reopen rows are post-op** (A8/P5a) |
| A11 | ack bound stated | holds |
| §C | `_placed` cache | per-field walk is 0.0 ms; the 2× is now the clone (§C) |
| §D | `_plan_for` reuses the actor's open plan on the block | holds (shipped); **reuses a `delete` plan** (A4/P4d) |
| §D | dead code, `HELD`/`LOOP` dedupe | done; `Text.remove_marks` and `Block.is_heading` are now the dead ones (§D) |
| surfaces A6 | `_own_plan_text` | holds for the undo; **over-permits `text.restore`** (A3/P3b) |
| R5/R4/R3 | ingest fixed point, plan words, bindings die with the heading, inbox totality, torn tail, `$cid` chains, recorded authority, two-clone inserts | hold (shipped suites; P8d) |

## C. Complexity, measured

`perf7.py`, Python 3.12, this machine idle (review 6's numbers were taken on a shared
machine; its cold publish without plans was 359 ms, here 306).

| operation (N = 1,500 features, 13.5k ops) | ms |
|---|---|
| `text.insert` (one op) | 0.1 |
| `block.split` + `block.merge` | 0.2 |
| `Publisher.publish` cold, no plans | 306 |
| `Publisher.publish` warm after one insert | 36 |
| `Publisher.publish` **cold, 20 open plans × 10 marks** | **534** (review 6: 539; review 5: 2,434 → 707) |
| — of which `projection()` (entries + claims) | 324 |
| — of which `export()` | 181 |
| — of which `phases()` | 63 |
| `_unapply_plan_marks` on one untouched field / `block_entry` / `plan_claims` | 0.0 / 0.0 / 0.0 |
| `Publisher.publish` cold, **+200 open plans on deleted headings** | **840** |
| `history_window(200)` / with 10 rejects inside | 2.4 / 6.3 |
| `Publisher._feature_keys` (all headings) | 9.4 |
| `store.feature_of(p)` for every paragraph / `body_ids(h)` for every heading | 8.1 / 8.2 |
| `Workspace()` rebuild, no snapshot | 437 |
| `write_snapshot` | 465 |
| `Workspace()` rebuild from the snapshot alone | **243** |
| **N = 3,000** (18k ops): `_feature_keys` / `feature_of` all / `body_ids` all | **40.8 / 35.5 / 35.8** (4.3×, 4.4×, 4.4× for 2× N) |
| N = 3,000: `Publisher.publish` cold, no plans | 758 (2.5× for 2× N) |

- **What is O(what).** `apply` is O(segments + marks of the touched blocks) as 01 §5
  says (0.1–0.2 ms). The per-field views are O(field) with a constant per open-plan
  mark op (`_placed` + `isdisjoint`, ~0.5 µs). `phase`/`ground`/`ink` are O(ops on the
  feature). A cold publish is O(fields) for the entries and export **plus O(n²)** from
  `dominated_ids`, `body_ids`, `feature_of` and `live_neighbours_of`, each of which
  slices `self.order` (`store.py:204,220,245,260`) — an O(n) list copy per call, called
  once per heading in `_feature_keys` and once per block by `lift_pass` and
  `_must_propose`. At 1,500 features it is 3% of the publish; the 4.3× scaling says it
  is ~450 ms alone at 10,000 features, more than everything else together. Fix: iterate
  by index (`for j in range(i + 1, len(self.order))`), ~8 lines, makes them O(range).
- **Open plans still double a cold publish, for a new reason.** The mark-op walk is
  free now; the 228 ms gap is `text.clone()` of *every* field in `markdown(mode=
  "rejected")` (`derive.py:52`) and again in `block_entry` (`derive.py:593`) whenever
  `open_ids` is non-empty — 3,000 fields × 2 clones × ~40 µs. Guard both with the
  `_placed`/`named` check already computed (clone only a field some open plan's mark op
  names): ~6 lines, cold publish with plans returns to the no-plans cost. The +200
  undecidable plans (target heading deleted; `open_plans` never offers them, nothing
  ever closes them) add 300 ms: `plan_mark_ops` walks them per field for the life of the
  tree. Auto-reject a plan whose target dies, the way `_withdraw_if_empty` closes an
  empty one (~4 lines in `_h_block_delete`'s caller or the drain).
- **The snapshot is faster to rebuild and no smaller.** 243 vs 437 ms (review 6 found
  no gain; the `_placed`-era code paths differ). But after a reflect pass every feature
  is `drifted` (its newest op is a `bind.refresh`), so `_unrealized` archives the whole
  log: **8.3 MB snapshot (4,500 state ops + 13,500 archived) replacing a 6.0 MB log**;
  the second fold 6.5 MB (8,856 archived + the first fold's 4,500 state ops). 04 §5's
  "compactable" is false in the state a tree is in most of the time. A1's fix changes
  the contents; the size needs either the critical-version fold (§G) or `_unrealized`
  keeping only the ops the three readers need (`text.*` beyond the frontier, `bind.*`,
  the delete, the hand-off) rather than every op.
- `Publisher` warm floor 36 ms; `history_window` 2–6 ms; rebuild ~32 µs/op. Fine.

## D. Cleanliness

- **Dead code** (no caller in `codoc/` or `tests/`): `Text.remove_marks`
  (`text.py:436-445`, superseded by `marks_placed_by`), `Block.is_heading`
  (`doc.py:69-71`).
- **Two copies of one function**: `apply._rejected_plan_mark` (`apply.py:740-745`) and
  `derive._rejected_mark` (`derive.py:123-126`) are identical.
- **Three copies of one gate.** 01 §4 rule 7: the gate "lives at the one boundary all of
  them pass, `Workspace.ingest`, not in the loop that happens to have been written
  first". The loop's copy is still there (`reflect._amend`, `reflect.py:459-531`, its
  own `held`/`need_plan`/`GATE_*` logic committing through `commit_ingest`), and
  `translate` has a third (`translate.py:171-187`, human → plan). They already differ:
  `_amend` re-plans per block when the trunk refuses; `_must_propose` only when
  `deleted_fraction` or phase says so; `translate` ignores the fraction.
- **Names and docstrings that overstate**: `snapshot.py:25-31` and 01 §7.5 "answer the
  same before and after a fold" (A1); `Store.remember` "older than anything this store
  applied" is true and hides that the archived ops have no order among themselves;
  `Store.ops_on` "oldest first" (P2g); `lift.py:4-6` "on the trunk" (P4a);
  `_own_plan_text` "touches nothing of anyone else's" (P3b); `_plan_for` "One per edit"
  (P4c: one per block for the agent); `window.py:25-27` "the rank each op displaced"
  (P5a); 04 §5 "compactable" (§C); review 6 Applied "an agent's multi-block amend is one
  proposal too" (P4c). `Workspace._suggest_plans` holds the machine gate's plans too.
- `snapshot_ops` emits a decided plan's verdict as `actor="import"` (`snapshot.py:86-88`)
  where `plan.open` gets `actor=p.actor`; the hand-off likewise loses the person who
  gave it (A6/P2e). `Plan` records no verdict actor, so this is the format's gap, not the
  snapshot's alone.
- `_placed` (`derive.py:109-120`) stores its cache as a dynamic attribute on `Op`
  (`o._placed_cache`). Works because `Op` is an unslotted dataclass; a `field(default=
  None, repr=False, compare=False)` would say so.
- `docs/substrate/` now carries `perf3/5/6.py` and `probe_substrate2/3/3b/3c/4.py`,
  `probe5_*`, `probe6_*`, `editor.probe.test.ts`: eleven probe files whose properties are
  in the shipped suite or stale (review 6 noted two stale probe5 expectations). Move the
  live ones to a `probes/` directory and delete the superseded.
- `Roles.policy`'s docstring now describes the drain's tagging and the rewrite (good);
  it does not mention `_own_plan_text`'s untagged delete/restore, which is the exception
  a reader most needs (A3).
- 01 §3's paragraph on mark matching ("widening a mark keeps the neighbour's placer, and
  displacing one keeps the displaced mark's on the pieces") documents the mechanism
  rather than the rule; after A2's fix it should say the rule.

## E. Spec ↔ code

| spec | says | code | which moves |
|---|---|---|---|
| 01 §7.5, 04 §5 | the fold archives unsettled features' ops so `ground`, the directive and phantoms "answer the same" | `ground` empties; phase flips `drifted → synced` for some features, hash-dependent; the second fold reorders and grows the archive (A1) | code |
| 01 §7.5 | "`state_fingerprint` is equal across a fold" | not after an undone realized hand-off (P2f) | code (or refuse that undo) |
| 04 §5 | "compactable"; "a branch forked before the snapshot merges correctly" | snapshot ≥ log when features are drifted; two folded clones lose work silently (A5) | code + spec (one folding party) |
| 01 §3 `plan.reject` | marks "matched on kind, attrs, start and placer" | the rule itself fails under split and shared placer (A2) | code (proposal record) + spec |
| 01 §6 read modes, §9 | `rejected` is the document without open plans; export round-trips | `**nce**.` from an open plan after a split (P1b) | code |
| 01 §7.3 | `suggest` writes "`plan.open`, ops tagged with a plan the same actor opened, and comments" | untagged `text.delete`/`text.restore` by segments pass when the chars are the actor's plan's (P3b) | spec (state the exception) + code (drop `restore`) |
| 02 §3.6 | a contributor's trunk edit "is applied as a plan with that actor" | their edit *of their own proposal* is too (P3a) | code |
| 03 §5.1, `lift.py` | the lift writes "with `actor=loop` on the trunk" | gated into a plan past 50% (P4a) | code |
| 01 §4 rule 8, review 6 Applied | "One amend is one plan"; the agent's multi-block amend is one proposal | one plan per block through `codoc_reflect` (P4c) | code |
| 01 §4 rule 7 | the gate lives at `Workspace.ingest`, "not in the loop" | `reflect._amend` and `translate` keep their own (§D) | code |
| 02 §3.3, 03 §5.1 | an accepted plan is a request to build; `lower` speaks from what changed since the realization | an accepted agent diagram edge is in `old` (P4b) | code |
| 01 §7.3, `codoc rebuild` | the refused-today count is the forged-log signal | fires on every fold's `import` verdicts (P2e) | code |
| 01 §1 | "One applier" | two daemonless processes both apply and publish (P6a) | code (lock) or spec |
| 01 §6 `timeline`, `window.py:25` | `ranks`: "the rank each op displaced" | a reopen's rows are the ranks it set (P5a) | code |
| 01 §5 | two-phase handlers | `bind_attach` mutates `bound_once` before resolving (P5b) | code |
| 02 §3.2 | phase reads "`divergent` when the agent's reflect touched features the hand-off did not name" | no `divergent` in `PHASES` (`derive.py:362`) | spec (delete) or code |
| 02 §2 | paste carries `copied_from` in the op meta; select-all Delete "refused only if it would empty the whole tree" | neither exists in `codoc/` | spec |
| 04 §3 | merge driver `codoc export --merge %O %A %B %P` | no `export` command in `cli/main.py` | spec (mark unbuilt) or code |
| 03 §4 | a comment is "out of the reading flow (`rank=null`)" | comments have ranks; `live_neighbours_of` skips them | spec |
| 01 §6 `ground` | "since the person last engaged" | clears at a fold for a `synced` feature (P2a) | spec or code |
| 01 §3 `plan.open` | "an open plan with no ops is harmless and `plan.reject` closes it" | the workspace now auto-rejects it (`_withdraw_if_empty`) | spec (say so) |

## F. Ranked changes

1. **Order the archive.** `Store.remember` takes a sequence number and files folded
   ops at `-(N - k)`; `ops_on` sorts `old` by position; `snapshot_ops` sorts by position
   and excludes the snapshot session's own ops from `_unrealized`. P2b/P2c/P2g/P2h into
   `tests/substrate`. ~15 lines. Then decide (§G) whether to replace `archived` with a
   critical-version fold.
2. **Plan marks as proposal records** (`suggest_mark{plan, kind, attrs, on}`, applied at
   accept): deletes `marks_placed_by`/`_placed`/`_unapply_plan_marks`/`_mark_targets`/
   `_mark_holder`/both `_rejected_*mark`/`marks_back`. ~60 new for ~130 removed; P1a–P1c
   into `test_plan_marks.py`. (Minimal alternative: `Mark.via`, ~12 lines, P1a/P1b only.)
3. **The suggester's own words**: `_as_proposal` keeps a range delete over the actor's
   own open-plan characters as a delete; `_own_plan_text` drops `text.restore` (or
   requires `by` of their own). ~15 lines; P3a/P3b.
4. **Gate scope and bookkeeping**: `Workspace.ingest(gate=)` with the lift ungated;
   `_at_realized` treats accepted-plan segments as the person's request; one batch id
   per `codoc_reflect` amend (add `batch` to the `ingest` submission); `_plan_for` reuses
   `edit` plans only. ~25 lines; P4a–P4d.
5. **Two snapshots**: `rebuild` reports a problem and replays archived sessions outside
   the chosen frontier when `log/` holds more than one snapshot. ~12 lines; P2d. 04 §5:
   fold on the integration branch only.
6. **Fold edges**: skip snapshot-session ops in `refused_verdicts` (2 lines); emit a
   hand-off before a lone `realized` (3 lines). P2e/P2f.
7. **Cost**: index loops instead of slices in `dominated_ids`/`body_ids`/`feature_of`/
   `live_neighbours_of` (~8 lines); clone a field in `markdown`/`block_entry` only when an
   open plan's mark op names it (~6 lines); auto-reject a plan whose target block dies
   (~4 lines). `_unrealized` keeps only what the readers read (~6 lines), or item 1's
   deeper option.
8. **One applier**: the daemonless `_applier` takes the pidfile; a second process uses
   the inbox or fails loudly. ~10 lines; P6a.
9. **Reopen rows and `bound_once`**: `_h_plan_reopen` records `restored`/`sets_prev`;
   `_rank_rows`/`_shape_rows` read them; move `bound_once.add` below the key reads. ~9
   lines; P5a/P5b.
10. **Cleanliness**: delete `Text.remove_marks`, `Block.is_heading`; one
    `_rejected_mark`; fold `reflect._amend`'s gate into `Workspace.ingest` (it needs the
    "one plan for the whole repair" batch that item 4 adds anyway); fix the nine
    overstating docstrings (§D); spec edits (§E: `divergent`, `copied_from`, the merge
    driver, `rank=null`, the `suggest` exception, auto-withdrawal); move the probe files.

## G. The design against its own literature

Read: `docs/brainstorms/2026-09-17-substrate-literature.md` and
`2026-09-17-codoc-substrate-redesign.md`. What the built core holds, what it should
adopt, and what it has quietly reintroduced.

**Holds.**

- *Etherpad, one value per key per character* (lit §2B): `Seg` carries one `actor` and
  one `plan` (`text.py:60-67`); "blue ink on green ground is impossible" is the
  encoding, and the amend gate reads `block_written_by` from runs (`derive.py:179-194`),
  not from a diff. "A diff may draw, never decide" holds everywhere but `ingest`, which
  01 §4 names as the one boundary.
- *Peritext's non-growing anchors* (lit §2A): `GROWS_AFTER = {bold}` (`text.py:41`); a
  plan's insertion is a segment, so typing at its edge is the person's (P8b shows the
  plan's bold growing over it is still rejectable).
- *Baseline, "retraction can fail, and failure is the signal"* and *Grove, "represent,
  do not resolve"* (lit §1): `unknown_anchor`, `range_spans_blocks`, `duplicate_block`
  are recorded conflicts (`apply.py:86-95`); nothing is nearest-matched. `stale_base`
  is Denicek's drop-on-conflict confined to derived amends, as the brainstorm said.
- *Kleppmann's move-op* (lit §2A): `block.move` on a stable id with an ancestor check
  (`apply.py:435-437`) and "their move stands" by `rank_head` (`:731`) — except for an
  archived move, where the code comment admits the rule is dropped (`:725-728`); after
  A1's fix the position is known and the rule can return.
- *Upwelling, grouping keystrokes into units* (lit §2A): batches; `_plan_for` gives a
  contributor one plan per edit across settles (shipped test). Not for the machine
  (P4c).
- *CoAuthor's event-source column* (lit §4): `actor` on every op, `caused_by` to the
  hand-off; `attribution()` records per character and `merge=True` displays runs, which
  is Hwang's "record fine, show coarse".
- *Dang, diegetic vs non-diegetic*: bold → `Focus:`, link → `Consult:`, a comment through
  its own `lower` (`derive.py:430-441,448-479`).
- *Mozannar/Jakesch, a proposal must be cheap to leave pending and never fade*: nothing
  forces a verdict; the plan tag stays as provenance after accept (`plan_claims`
  "accepted"). Eg-walker's "persist the log and the text, rebuild the rest" is the
  store-as-cache (`store.py:1-4`).

**Should adopt.**

- *Google Docs' `suggestedTextStyleChanges`* (lit §2A, "take"). The brief took the
  id-arrays idea for insertions and deletions (the `plan` tag, `suggest_del{plan}`) and
  not for style: a plan-tagged `text.mark` mutates the trunk's marks and is un-applied
  by a record (`apply.py:653-678`, `derive.py:89-107`, `text.py:450-460`). Docs keeps a
  style suggestion *beside* the style, keyed by suggestion id, and applies it at accept.
  Four rounds of matching rules (by value, by placer, by record, and A2's cases) are the
  cost of not doing that; the proposal-record design in A2 is the literature's answer.
- *Patchwork, "the branch workflow is too heavy for always-on assistance"* (lit §2A/§4;
  brainstorm rule 2, "Branches only for discrete proposals, never for the loop"). The
  round-6 gate on `Workspace.ingest` makes the lift — the most always-on writer there is,
  re-run after every reflect pass — open a plan when the code moves (P4a,
  `lift.py:38-44` → `workspace.py:334-346`). The lift is attribution; gate it out.
- *Eg-walker's critical version* (lit §2A). Eg-walker persists the event graph and the
  text and rebuilds its merge structure "from the most recent critical version" — a
  point with no concurrent events. codoc's fold is the same construction and now
  persists a *derived* subset of the log (`archived`) with its order lost (A1) and its
  size larger than the log (§C). The construction the paper describes is: fold up to the
  latest frontier at which every feature was `synced` and nothing was in flight, and
  keep everything after it as ordinary session log. That removes `archived`,
  `remember`, `archived_on`, position −1 and the second-fold growth in one move, and it
  is what review 6's "refuse while in flight" should have been: not refuse, fold less.
- *Grove, "arbitrarily losing content is not acceptable"* against A5: `rebuild` picking
  `snapshots[-1]` and ignoring the other is exactly the silent resolution Grove
  refuses. Represent it (a problem) until someone re-folds.
- *Semantic Commit's revision-free impact step* (lit §4, "take"): the tree is an intent
  spec; the one place a person sees "which features does this touch" before anything is
  rewritten is the LLM prompt's `impacted` list (`reflect.py:167-177`), which no surface
  shows. A `codoc status`/MCP read of `impacted_features` for the changed files would be
  the step the study found users want. Optional; not a fault.

**Quietly reintroduced, or kept against the brief.**

- *Baseline's warning on op-alphabet growth* (lit §1 and Q5 objection 1). The alphabet is
  20 kinds, but the payload *dialects* are the rule table in disguise: `text.delete`
  range vs `segments`; `block.move` vs `{restore}`; `feature.handoff` vs `{restore,
  handoff}` vs `{reinstate}`; `block.split {new_takes}`; `block.add {texts, dead,
  bound_once, rank}` for snapshots; `plan.open {ops, opened_by}`; `plan.reopen`'s seven
  record keys. Each is a branch in `apply` *and* in the window's `touched`/`unapply`/
  `_liveness`/`_rank_rows`/`_shape_rows` *and* in the snapshot — P5a is a dialect the
  window mishandles, P2f one the snapshot emits wrong. Count dialects, not kinds, when
  holding the line.
- *"`preserved_ratio` as a decision" was to be deleted* (brainstorm, "What this
  deletes"); `deleted_fraction > GATE_HUMAN|GATE_MACHINE` (`derive.py:26-27`) is the same
  threshold decision with an exact numerator, now in three places (§D). Wordcraft
  Writers Workshop motivates a gate; the brief's claim that it "stops being a similarity
  ratio" is not true and the spec should say what it is.
- *The move-as-delete-plus-insert warning* (Webstrates): `ingest` re-authors a moved
  sentence (01 §4, "Known and accepted"); fine, and stated.
- *Heuristics kept by design*: `_relocations` (fingerprint/types-hash matching,
  `reflect.py:329-360`), `diagram_lower._freeform`, `translate`'s language detection.
  All decide something from a comparison; all are outside the calculus and named. Only
  `lift_pass`'s `current != new` (`lift.py:38-40`) decides *inside* the write path, and
  it is idempotence, not attribution.
- *Undecidable plans*: `open_plans` hides a plan whose target died (Boxer's "a proposal
  you can see but cannot grab" is avoided) but nothing closes it, so `plan_mark_ops`
  walks it per field for ever (§C). Mozannar wants pending to be cheap; this makes
  pending free for the person and costly for the publish. Close it when its target dies.

---

## Applied (2026-09-18)

Every ranked change is in, across ten commits (`7eb491d`..`55b17a2`); 24 of the 26
probes pass and the properties behind them are in the shipped suite (327 Python
tests, 139 TypeScript, 1 skipped). `probes/perf7.py` re-run after the work:

| | review | now |
|---|---|---|
| `Publisher.publish` cold, no plans | 306 | **234** |
| `Publisher.publish` cold, 20 open plans × 10 marks | 534 | **260** |
| `Publisher._feature_keys` (all headings) | 9.4 | **3.5** |
| `feature_of` every paragraph / `body_ids` every heading | 8.1 / 8.2 | **1.7 / 2.1** |
| N = 3,000: the same three | 40.8 / 35.5 / 35.8 | **9.6 / 4.5 / 5.8** |
| `write_snapshot` on a wholly drifted tree | 465 (8.3 MB) | **21 (writes nothing)** |

**§G is why this round's two HIGH faults were the previous round's fixes.** Both
patched a seam rather than adopting a closed form the literature already had, and
both closed forms *delete* machinery:

- **A1**, the fold. Eg-walker's **critical version**: fold only up to the last
  point at which nothing was in flight, and leave the rest an ordinary log. Two
  rounds of "carry the unsettled ops too" produced an archive that grew per fold
  and had no order among itself. `Store.remember`, `archived_on`, `ops_on`'s memo
  and `SessionFile.archived` are gone; `derive.reads_its_ops` names the three
  reasons a feature is not finished, and `settled_prefix` stops at the oldest op
  any of them was written by.
- **A2**, plan marks. Google Docs' **`suggestedTextStyleChanges`**: a plan's mark
  toggle is a record BESIDE the trunk's marks, applied only at accept. Three
  rounds of matching rules (by value, by placer, by the op's own `added`) each
  closed the last round's cases and missed the next, because a split moves a
  recorded start and a merge shares a placer. `Text.marks_placed_by`,
  `placed_keys`, `_attr_key`, `derive._unapply_plan_marks`, `_placed`,
  `_rejected_mark`, `plan_mark_ops`, `_minus`, `apply._mark_targets`,
  `_rejected_plan_mark` and the `marks_back` record are gone — about 130 lines for
  about 60, and with them the per-field walk that made open plans cost 8× a cold
  publish.

Two of the ranked changes were made differently, and one was not made:

- **A8/P5a** proposed `restored` + `sets_prev` on the reopen. It records one
  `undid: {moved, sets}` instead, because the reason it cannot reuse `moved` is
  that `moved` is what replay applies (I6), and one field named for that is
  clearer than two named for their shapes.
- **§D's "three copies of one gate"** is now one copy and one *different rule*.
  `reflect._amend` asks `Workspace.must_propose` per block (it still decides over
  the whole amend, because one amend is one plan). `translate` keeps its own
  question — a translation replaces every word by design, so the fraction says
  nothing and the question is whose words they were — and gained the one clause
  it does share: while a feature has pending intent, the document wins.
- **§C's "auto-reject a plan whose target dies"** was tried and reverted. It looks
  like the same fix as `_withdraw_if_empty` and is not: a delete is undoable, and
  restoring the heading has to bring its proposals back with it
  (`test_a_heading_deleted_under_a_paragraph_plan_and_restored_keeps_the_plan_decidable`).
  The ~50 ms such plans cost a cold publish is the price of that. What WAS wrong
  is that `read_status` and `read_tree` counted them, so a person saw a proposal
  count they could not clear.

The two probes that still fail are both probes whose premise the fix removed:

- **P2c** reads `snap.archived`. After A1 a snapshot archives no ops at all — the
  unsettled ones are simply not folded — so there is nothing to be out of order.
- **P6a** opens two raw `Workspace(cd)` objects. The lock is at the `client`
  boundary, which is where the MCP server, the CLI and the hub all go; a second
  raw `Workspace` on one checkout is the thing "one applier" forbids, not a case
  to make safe. `test_a_second_daemonless_writer_does_not_lose_the_firsts_work`
  drives the real path, through a real second process.
