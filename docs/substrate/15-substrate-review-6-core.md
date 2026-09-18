# Review 6: the Python core (calculus, substrate, loops, kinds, MCP, CLI)

Scope: worktree `CodeNav-substrate`, branch `substrate`, HEAD `de8fd4b`, clean. Read
against `01-calculus.md` (rev 2), `02-workflows.md`, `03-extensions.md`,
`04-version-control.md`, reviews 08/10–14 and their probes. Every `file:line` is at
`de8fd4b`. Nothing in the worktree was modified. Baseline: 284 shipped tests pass, 1
skipped.

Probes: `probe6_core.py` beside this file — 26 probes, each asserting the CORRECT
behaviour, so a failing probe is a confirmed fault. **20 fail, 6 pass** (the 6 are the
confirmations in §A12 and P6f). Run from the worktree:

```
PYTHONPATH=. ../CodeNav/.venv/bin/python -m pytest <scratchpad>/probe6_core.py -q -p no:cacheprovider
```

`perf6.py` beside it is `perf5.py` plus the review-6 rows (§C). Re-run of
`docs/substrate/probe5_core.py`: 28 pass, 2 fail, both stale expectations (P4b's ground
truth reports a dead block's stored text where the window now deliberately reports `""`;
P6b is the A12 reading the spec now states in `01` §6).

## Verdict

Review 5's applied changes hold where they were probed: snapshots keep an open plan
decidable and an accepted delete plan present, `nothing_queued` does not refuse the
snapshot's own `feature.realized`, two clones inserting after one character both land,
`Wire` turns a missing key into `missing:<key>`, doc wins over the realizing lease, and
the window un-applies a verdict's kills by identity after a split (§A12). But the
plan-mark change traded one wrong matching rule for another: **un-applying by placer
misses every mark `set_mark` records under a different placer** — a merge keeps the
neighbour's op, a displacement's outer pieces keep the displaced mark's op, a partial
`off`'s pieces keep the original's — so a plan that widens a person's bold, cites inside
their link, or unbolds a word cannot be rejected, and the export shows the proposal while
it is open (`****Retries** once**.`, `[[Ret](u)rie[s once](u)](u).`; A1, six probes). Two
of those cases were correct under the by-value rule review 5 replaced. Beside it, three
things the specs promise and the code does not do: **the agent's MCP write surface has no
gate** — `codoc_reflect amend` replaces a person's paragraph on the trunk of a *queued*
feature and drops their bold, while its own docstring promises the plan (A2); **a hub
contributor cannot delete a character** — the daemon wraps their inserts in a plan and
refuses their `text.delete` as `authority`, and a batch that lands nothing leaves an
empty plan for the maintainer to judge (A3); and **a snapshot keeps the state but not the
history the views read** — after a fold a phantom can never settle (A7 returns), a
queued feature's directive loses its `Focus:` and `removed:` lines, and `_emptied`
forgets a feature once had code (A4). Below those: `ink()` counts a margin comment as
unsent ink and turns a synced feature `edited` (A5, against `01` §6's own sentence);
`lower` reports the edges the LOOP lifted as "the author drew that edge" (A6); a reopen
re-binds code to a dead heading and a `marks_back` entry for an unknown block escapes
`apply` as a `KeyError` (A7, A8); `codoc init` on a repository with no indexable file
produces a workspace no later command recognises (A9); the timeline has no rank for a
verdict's un-move (A10); and ack rotation bounds `01` §7.4's idempotency to the last
2,000 acks (A11, by design and unstated).

Cost (§C): a cold publish with 20 open plans is still 2.5× one without (891 vs 359 ms
here) because `_unapply_plan_marks` and `plan_claims` walk every open plan's mark op
**per field**; a snapshot rebuild is no faster than a full one (613 vs 606 ms).

---

## A. Correctness

Ranked. Each item names its probe and quotes the decisive assertion. `T(s, bid)` is
`s.blocks[bid].text().live_text()`; `seed(ws)` is review 5's `h1 "Client"` /
`p1 "Retries once."` bound to `c.py::send` (`r = refs(ws, "p1")`, so `r[0..6]` is
"Retries", `r[8..11]` is "once", `r[12]` is the full stop).

### A1. Plan marks are un-applied by placer, and `set_mark` records marks under other placers (HIGH; partly a regression of review 5 A4)

`Text.set_mark` (`text.py:375-431`) records three shapes whose `added` marks do NOT carry
the toggling op's id:

- **merge** (`:401-417`): an `on` that overlaps or abuts a same-kind mark removes it and
  re-adds one wider mark with `keep_op = m.op` — the *neighbour's* placer ("widening a
  person's cite by one word does not make it the loop's cite"). Right for the trunk;
  under a plan it means the plan's op placed nothing that `marks_of(op)` can find.
- **displacement** (`:393-400`): a `cite`/`link` `on` over the other kind cuts the old
  mark into outer `pieces` (`_cut_out`, placer = the old mark's op) and records them in
  `added`.
- **partial off** (`:424-431`): the same `pieces`, same placer, in `added`.

`_undo_plan_op` (`apply.py:650-669`) does `remove_marks(op=o.id)` then puts `removed`
back; `_unapply_plan_marks` (`derive.py:82-94`) does the same on the clone. Neither
looks at `added` any more. Six probes:

- **P1a** — person bolds "Retries"; plan bolds "Retries once"; reject:
  ```python
  assert bold_chars(t) == before          # got: bold over 0..11 AND 0..6, both op = the person's
  ```
- **P1b** — the same while open, rejected mode: `markdown(t,"rejected") == "**Retries** once."`
  → got `'****Retries** once**.'`; `block_entry` spans `[(0,7)]` → got two bold spans.
- **P1c** — plan A bolds "Retries", plan B bolds " once" (adjacent → merged, placer A);
  reject B: A's bold should remain alone → got the merged 0..11 plus A's 0..6.
- **P1d** — person links "Retries once"; plan cites "rie"; reject: marks should equal the
  one link → got three links (two pieces + the whole).
- **P1e** — the same while open, rejected mode → `'[[Ret](https://x.test/)rie[s once](https://x.test/)](https://x.test/).'`
  — nested links the parser cannot read back; `import(export(s)) == s` (`01` §9) fails.
- **P1f** — person bolds "Retries once"; plan unbolds "rie"; reject → three bolds (two
  pieces + the whole); rejected mode while open → `'****Retries** once**.'`.

P1d and P1f were correct under review 4's by-value rule; review 5's A4 fix (needed for
growth and for two plans on one span) removed the `added` matching that handled them.

Fix (one rule, both call sites): a plan mark op is undone by removing every live mark
that either has `op == o.id` (the growth case, and split pieces) **or** matches an
`added` entry on `(kind, attrs, start, op)` — start and placer, not end, so a grown mark
still matches and a merged mark (whose `added` entry carries the neighbour's op and the
merged start) is removed whole — then putting `removed` back under the existing
`_rejected_plan_mark` guard. ~10 lines in `apply._undo_plan_op`, ~8 in
`derive._unapply_plan_marks`; P1a–P1f into `tests/calculus/test_plan_marks.py`. The
same matching belongs in `plan_claims` (`derive.py:251-261`), which today draws the
whole merged range as "marked" when the plan proposed only the extension.

### A2. The agent's MCP write surface has no amend gate (HIGH; code contradicts its own contract)

`tools.reflect` (`mcp/tools.py:206-253`) sends every `amend` block straight to
`client.ingest` with no plan (`:246-250`); `client.ingest` (`client.py:109-128`) runs
`ws.ingest(plan=None)` daemonless or an `ingest` submission the drain applies with
`plan=None`; nobody reads `deleted_fraction` or the feature's phase. The gate
(`GATE_HUMAN`/`GATE_MACHINE`, `HELD`) exists only in `reflect._amend`
(`reflect.py:468-540`), the loop's own LLM pass. And `ingest`'s human-mark refusal
(`ingest.py:140`) applies to `REWRITERS = {loop, translator}` (`ids.py:79`) — the agent
is not one.

- **P3c** — `seed` (person), hand off (queued), then daemonless
  `tools.reflect(amend p1 → "Completely different words, nothing kept.")`:
  ```python
  assert not trunk   # got: Seg(actor='agent:claude-code', plan=None, chars='Completely different words, nothing kept')
  ```
  `01` §4 rule 8: "While a feature has unsent human ink or is queued, a loop amend on any
  of its blocks becomes a plan regardless of size (the document wins)". The tool's own
  docstring (`server.py:74-76`): "small repairs land, rewrites and repairs on a feature
  with pending intent become a plan"; `tools.py:10-12` the same.
- **P3d** — person bolds "Retries"; agent amends to "Retries twice." → the bold is gone,
  no plan (`markdown(t) == 'Retries twice.'`). `01` §4 rule 5 protects it from the loop
  and a translator only.

Fix: move the gate to where every machine write passes — `Workspace.ingest` (or
`commit_ingest`): for `actor_kind(actor) in MACHINE_KINDS` with `plan is None`, compute
the ingest with `trunk=True`; if it is refused `would_drop_human_*`, or
`deleted_fraction` exceeds the block's gate, or `phase(feature_of(bid)) in HELD`, open a
plan for that actor and commit under it (the `_amend` logic, hoisted). Add `agent` to
`REWRITERS` (or rename the set: it means "machines that rewrite prose on their own").
~25 lines; `_amend` then shrinks. P3c/P3d into `tests/mcp` (there is no such directory —
`codoc/mcp` has no test of its own; `test_kinds.py:79` is the only caller).

### A3. A hub contributor cannot delete a character; a batch that lands nothing leaves an empty plan (HIGH for the hub flow)

`_suggester_plan` (`workspace.py:313-336`) wraps a suggester's untagged op in a plan only
when `kind in K.PLANNABLE` (`:320`); `text.delete` is not plannable (`ops.py:55`, by
design: a plan proposes, its deletions are `suggest_del`). So the op reaches the policy
untagged and `Roles.policy` (`:114`) refuses it:

- **P3a** — `hub-alice` submits `text.delete` over "Retries":
  ```python
  assert [o.conflict for o in ops if o.kind != K.PLAN_OPEN] == [None]    # got ['authority']
  ```
  Every Backspace, every typed-over selection (delete + insert: the insert lands as a
  plan, the delete is refused, the page shows the replacement and the ack reloads the old
  words) fails for a contributor. `02` §3.6: "a contributor's edit on the trunk is
  applied as a plan with that actor". `as_plan_bodies` (`ingest.py:226-239`) already
  turns a delete into the plan's `suggest_del`; the drain's ingest path gets that for
  free, the drain's op path does not.
- **P3b** — a suggester's `ingest` with `new_md == base_text`, and a batch whose only op
  is an unknown-anchor insert: both leave `Plan(actor='human:gh:alice', status='open', ops=[])`
  — the plan is opened (`:330`) before anything lands. Review 5 A9 closed exactly this
  for `translate`.

Fix: in `_drain_one`, for a suggester's `text.delete {from,to}` rewrite the body to
`text.mark suggest_del` under their plan (the range form; a `segments` form — an undo —
can stay refused); open the plan lazily, i.e. after the first op that applies, or reject
it when the batch ends with `ops == []`. ~15 lines. `block.merge`/`block.delete` from a
suggester stay impossible (a plan may not contain them) — the hub UI should say so
rather than reload the paragraph.

### A4. A snapshot keeps the state and drops the history three views read (MEDIUM-HIGH)

`snapshot_ops` (`snapshot.py:42-94`) emits blocks, bindings, plans (+ archived ops for
OPEN plans only), and one hand-off/realized per watermark. Three derived views read
`store.ops`/`ops_on`/`by_id` for facts the fold throws away:

- **P2a — a phantom can never settle after a fold.** `_phantom_done`
  (`realize.py:102-118`) reads the delete's `detached` from `ops_on([fid])`; the
  snapshot's `block.add {dead}` has no `detached`. Delete a bound feature, hand off with
  `delete_code`, `codoc snapshot`, the agent removes the code, the pass runs under the
  epoch: `assert R.queued(ws.store) == []` → got `['h1']`. Review 5 A7 returns for any
  hand-off that crosses a fold.
- **P2b — the directive of a queued feature changes across a fold.** `_new_marks`
  (`derive.py:436-445`, Focus) and `_human_changes` (`:418-433`, `removed:` via
  `_op(store, s.dead)`) read ops. Person bolds "Retries", deletes "once", hands off;
  before the fold the directive has `Focus: "Retries"` and `removed: "once"`; after:
  ```
  - - removed: "once"
  - Focus: "Retries"
  ```
  `01` §7.5 says the fold keeps "the frontiers (with the words the person asked for)";
  the request's own content is not the words asked for alone.
- **P2c — `_emptied` forgets.** `reflect.py:313` asks for any `bind.attach` in
  `ops_on([h.id])`; after a fold only current bindings exist: `_emptied == ['h1']` → `[]`.
  A feature that lost its code while held (so no plan was opened) is never proposed for
  deletion after a snapshot.

Also unprobed but the same cause: `ground()` (`derive.py:197-227`) reads `_op(store,
s.dead)` for the killer and `store.position` for "new", so every red/green ground clears
at a fold; `_drifted` resets to `synced`; provenance `caused_by` chains point at ids no
longer in `by_id`.

Fix, smallest that keeps the invariants honest: the snapshot must **refuse or carry**
work in flight. Either (a) refuse to fold while any feature is queued or has unsent ink
(`queued(store)` non-empty, or any `phase == edited`) and while any binding was detached
since the last delete-plan verdict — one check, a printed reason; or (b) archive, as it
already does for open plans, every op beyond each feature's `realized` frontier (they
are exactly what the directive, `ground` and `_phantom_done` read) and load them into
`by_id`/`ops_by_block` at position −1. (a) is ~8 lines and makes `04` §5 true by
construction; (b) is ~30 and keeps `codoc snapshot` always available. Either way,
`_emptied` should read state: "once had code" can be a per-feature flag the applier sets
on `bind.attach` and the snapshot carries (1 field on `Watermarks` or a set on `Store`).

### A5. `ink()` counts a margin comment as the feature's unsent ink (MEDIUM)

`ink` (`derive.py:171-194`) walks `store.feature_body(fid)` with no `in_flow` filter;
`_human_changes` (`:424`) and `written_by` (`context.py:55`) filter, `ink` does not.
`phase()` (`:294`) reads `ink` first.

- **P7a** — loop-written, synced feature; the person adds a `comment` block (body
  "why once?"):
  ```python
  assert not [k for k in claims if k.bid == c.bid]      # got a Claim(channel='human', stage='unsent') on the comment block
  assert phase(ws.store, "h1") != "edited"               # got 'edited'
  ```
  `01` §6 `ink(f)`: "Only blocks in the reading flow count — a margin comment is a
  request about the prose, not the prose (`written_by` and the directive agree)". So a
  note with no build behind it turns the row blue and the status bar to "1 feature with
  unsent edits — hand off to build" until it is handed off. `ground` and `plan_claims`
  walk `feature_body` the same way (a plan on a comment body would paint the card).

Fix: `if not in_flow(b.kind): continue` in `ink` (1 line), and the same in `ground` and
`plan_claims` (2 lines); P7a into `tests/calculus`.

### A6. `lower` reports the loop's own lift as the author's drawing (MEDIUM)

`_kind_lines` (`derive.py:372-403`) reconstructs `old` with `_at_realized(text,
w.realized)` (`:406-415`), which drops every segment minted since the realized frontier
whatever its actor. A diagram the loop lifted from the code after the frontier (or on a
feature never realized — frontier `{}`) is therefore all "new":

- **P5c** — loop-written feature, loop adds a diagram, `lift_pass` fills `send --> retry`
  from the graph, the person hands off with "make it retry twice":
  ```
  Add a dependency or call from `send` to `retry` (the author drew that edge).
  ```
  The edge exists in the code; the agent is told to add it. The `consult` line is right
  beside it, so the prompt contradicts itself.

Fix: `old` for `lower` should exclude only what a *person* changed since the frontier —
drop human segments minted after it and revive segments a human killed after it (the
`ink`/`_human_changes` reading), keeping the loop's. ~6 lines in `_at_realized`
(take `store` and check `s.actor`/the killer's actor). Add P5c to `tests/kinds`; the
shipped `test_the_directive_speaks_through_the_kind_codecs` adds the diagram as
`human:local`, which is why it passes.

### A7. `plan.reopen` re-binds code to a dead heading (MEDIUM-LOW)

`_h_plan_reopen`'s `binds` loop (`apply.py:878-885`) restores `Binding(entry["fid"],…)`
without asking whether the heading is live; `_reattach` (`:461-468`, used for `detached`)
at least checks the block exists, and `_h_block_restore` (`:508-509`) filters by `alive`.

- **P4a** — plan `bind.attach d.py::x` on `h1`; reject (binding removed, `binds`
  recorded); the person deletes `h1`; the person undoes the reject (`plan.reopen`):
  ```python
  assert not owners or ws.store.blocks["h1"].live   # got Binding(feature='h1', file='d.py', …) on a dead h1
  ```
  `01` §3 `block.delete`: "A dead heading owns no code". The binding then shows in
  `by_feature`, `queued`/`planned` logic and `_emptied`, on a heading no view renders.

Fix: skip a `binds` entry whose `fid` is not live (record it as not restored). 2 lines.

### A8. A `marks_back` entry for an unknown block escapes `apply` (LOW-MEDIUM)

`_h_plan_reopen` checks the blocks under `killed`/`struck`/`marks`/`sets`/`blocks`/
`deleted_blocks`/`moved` (`apply.py:825-830`) but not `marks_back`, then does
`store.blocks[entry["bid"]]` (`:851`). `apply` catches `Conflict`, `AnchorError`,
`MarkError`, `RankError`, `ValueError`, `TypeError` (`:86-95`) — not `KeyError`, by the
`Wire` design ("this code's mistake … has to surface").

- **P4b**: `apply raised KeyError('no-such-block') instead of recording a conflict`.

Reachable from a merged branch's log (a reopen recorded against a block this clone's
replay refused as `duplicate_block`), where it kills the rebuild rather than recording a
conflict. Fix: add `marks_back` to the pre-check (1 line). Consider, separately, whether
a `KeyError` at *replay* should become `conflict="internal:<key>"` with a printed problem
rather than a dead daemon — the log is the truth and `04` §2 says nothing in it is
refused.

### A9. `codoc init` on a repository with no indexable file leaves a workspace no command recognises (LOW-MEDIUM; regression of review 5 A14)

`SessionWriter` creates `log/` and the file on the first `append` (`log.py:200-207`);
`_bootstrap` with no rows publishes and returns (`main.py:120-123`) without writing an
op; `_require` (`main.py:33-38`) checks `(cd / "log").is_dir()`.

- **P5a**: `_require` raises `Exit(1)` — "No codoc workspace … run `codoc init` first" —
  right after `codoc init` succeeded. Same for a repo whose only sources are in an
  unsupported language, and for `import-v1` of an empty database.

Fix: `_require` should test what `init` writes unconditionally (`cd / ".gitignore"` or
`cd.is_dir()`), or `init` should `mkdir log/`. 1 line.

### A10. The timeline has no rank for a verdict's un-move (LOW)

`_rank_rows` (`window.py:209-217`) reads displaced ranks from `block.move` only;
`plan.reject` (`_undo_plan_op` `moved`, `apply.py:715-728`) and `plan.reopen` (`:872-877`)
re-rank too. **P4c**: plan moves `h2` to the top, reject restores it; a window holding
only the reject reports `ranks == {}` for it, so the page one notch before "now"
renders in the order the reject produced. Fix: `_rank_rows` also reads `moved` (and a
reopen's `moved`, whose rows are the ranks the reject displaced) — ~4 lines.

### A11. Ack rotation bounds idempotency to the newest 2,000 acks (LOW, design; unstated)

`rotate_acks` (`log.py:366-380`, `ACK_KEEP = 2000`) trims each ack file at session open;
`_acked` is what `drain_inbox` dedups on (`workspace.py:302`). **P5b** (with `keep=1` to
make the bound visible): a client that resubmits a cid older than the kept window after a
daemon restart lands it twice (`'Retries once. X X'`). `01` §7.4 states the rule without
a bound. The offsets file makes a *replay of the same inbox line* impossible; only a
client that *re-appends* an old cid is affected — say so in `01` §7.4 and in the
`rotate_acks` docstring, or key the dedup on `(cid → op)` persisted with the offsets.

### A12. Confirmed fine (probed, no fault)

- **P6a** — a handed-off and realized feature folds with no conflicted op, equal
  `state_fingerprint`, `queued == []`: the snapshot's `feature.handoff` then
  `feature.realized` order satisfies `nothing_queued`.
- **P6b** — a suggester's insert through the inbox rides in a plan of theirs; rejected
  mode hides it; the rebuild's fingerprint equals the live one.
- **P6c** — two clones inserting after the same character both land after the merge,
  no conflicts.
- **P6d** — `Wire`: a submission missing `after` / `kind` is `missing:after` /
  `missing:kind`, not a traceback.
- **P6e** — doc wins: a person typing during a build reads `edited`, not `realizing`.
- **P6f** — a `suggest_del` whose characters a split moved is struck in the right block
  and the window agrees with a prefix replay (review 5 A5 holds).
- By reading: `plan_mark_ops` memo key `(store.pos, plans)` is sound (every op moves
  `pos`); `Wire` never leaks past `apply` (every out-of-apply `payload[...]` is on a
  submission or a canonical record); the daemon's HEAD check runs before any op of a
  batch, so no op lands in a rotated-away session; `prune_index` is O(ops of the field);
  the `nothing_queued` refusal cannot wedge a snapshot (P6a).

## B. Reviews 2–5: what holds in this scope

| # | change | status at `de8fd4b` |
|---|---|---|
| R5 A1 | snapshot carries archived plan ops; dead blocks keep their killer; delete-plan `plan.open` skips the live check | holds (shipped tests, P6a). **New**: what it does not carry (A4) |
| R5 A2 | `add_block` de-duplicates ranks | holds (shipped test) |
| R5 A3 | `serve/validate.py`, `codoc validate-logs`, `rebuild` signal | present; hub side is the other reviewer's |
| R5 A4 | plan marks un-applied by placer | holds for growth and two plans on one span (shipped tests); **regressed** displacement and partial off, and never covered merge (A1) |
| R5 A5 | window resolves segments in the clones | holds (P6f, shipped fuzz) |
| R5 A6 | `consult`/`lower` in `directive()` | wired; `lower`'s `old` is by frontier not by actor (A6) |
| R5 A7 | phantom settles from `detached` | holds live; not across a fold (A4/P2a) |
| R5 A8 | `feature.realized` with nothing queued conflicts | holds; the snapshot order is compatible (P6a) |
| R5 A9–A11 | translate/bootstrap/`_emptied` | hold; `_emptied` reads the log, so not across a fold (A4/P2c) |
| R5 A12 | moved paragraph = ink | spec now says so; probe5 P6b fails by design |
| R5 A13 | no-op ingest acked as status | holds |
| R5 A14 | lazy session file | holds; **new** A9 |
| R5 A15 | `written_by` skips out-of-flow | holds; `ink`/`ground`/`plan_claims` do not (A5) |
| R5 §C | `plan_mark_ops` memoized | holds; the per-field loop over it remains (§C) |
| R5 §D | `read_offsets`/ack rotation; one identity function; dead code | done; A11 is the bound rotation introduces |
| R4/R3 | ingest fixed point, plan marks are proposals, bindings die with heading, inbox totality, torn tail, `$cid` chains, replay honours recorded authority | hold (shipped suites, probe5 re-run 28/30) |

## C. Complexity, measured

`perf6.py` (= `perf5.py` + rows below), 1,500 features / 13,500 ops, Python 3.12. This
machine was shared with several other reviewer processes during the run, so the
absolute numbers are 2–3× review 5's (its cold publish was 287 ms; here 359); the ratios
are the reliable part.

| operation | ms |
|---|---|
| `text.insert` (one op) | 0.1 |
| `block.split` + `block.merge` | 0.3 |
| `Publisher.publish` cold, no plans | 359 |
| `Publisher.publish` warm after one insert | 104 |
| `Publisher.publish` **cold, 20 open plans × 10 mark ops** | **891** (review 5: 707 after its memo; 2,434 before) |
| — of which `projection()` (entries + claims) | 493 (161 with no plans) |
| `_unapply_plan_marks` on ONE untouched field (200 mark ops) | 0.1 |
| `plan_claims(h1400)` | 0.1 |
| `phase(h700)` / `ground(h700)` | 0.1 / 0.0 |
| `history_window(200)`, 10 rejects of 10-mark plans inside | 9.1 |
| `Workspace()` rebuild from disk, 13.7k ops | 606–824 |
| `write_snapshot` | 461 |
| `Workspace()` rebuild from a snapshot alone (3,000 `block.add` + texts) | **613** |
| log 6.2 MB (449 B/op); snapshot 2.2 MB | |

- **Open plans still cost O(fields × open-plan mark ops) per cold publish.** The memo
  (`store.py:105`, `derive.py:64-79`) removed the list *computation*; `_unapply_plan_marks`
  (`derive.py:87`, called per field from `block_entry` and `export_block`) still iterates
  all 200 ops per field, and `plan_claims` (`derive.py:251-252`) does `for o in mark_ops:
  if o.payload.get("bid") != b.id: continue` per field. 0.1 ms × 3,000 fields ≈ the 330 ms
  gap. Fix: memoize `plan_mark_ops` **grouped by `(bid, path)`** (one dict, same key) and
  index into it; ~8 lines; cold publish with plans returns to ≈ no-plans cost. A rebuild
  on `HEAD` change pays the cold price, so this is felt on every checkout.
- **A snapshot does not make rebuild faster.** 3,000 snapshot ops replay in the time
  13,700 ordinary ops do (~200 µs per `block.add` with texts vs ~45 µs/op): `Text.from_json`
  + `index_text` + `add_block` per block dominate. Not wrong, but `04` §5's motivation
  ("compactable") buys disk (2.2 vs 6.2 MB), not start-up time. Profile before promising
  otherwise.
- `Publisher` warm floor is ~70–110 ms here (review 5: 33): `_feature_keys` over 1,500
  features, `history_window`, the export string compare, `write_queue`, `write_status`.
  Unchanged since review 5; fine.
- `apply` handlers: `_detach_dead` (`apply.py:448-458`) scans **all** bindings per killed
  heading — O(B) per delete, 1,500 bindings ≈ 0.1 ms; `_h_plan_accept` is O(plan ops ×
  holders). `Text.locate` is linear in segments and `set_mark` calls it O(marks) times;
  a paragraph edited into hundreds of segments with dozens of marks is the first place
  this would show. Nothing here is O(log) any more (`prune_index` fixed).

## D. Cleanliness

- **Dead code** (no caller in `codoc/`): `Text.is_live`, `Text.anchor_before`
  (`text.py:164-166, 179-180`); `rank.ordinal_keys` and `_encode` (`rank.py:82-110`,
  only each other); `Kind.view`, `Kind.text_view` (`kinds/__init__.py:55, 59-60`) —
  declared "what an LLM reads when the block is context"; `context.feature_entry` shows
  paragraphs only, so a diagram is never context; `Kind.write_back` (`:56`,
  `builtins.py:190`) — `tools.write_block` never calls it; the image kind's "an agent's
  reply names an asset" path does not exist. Review 5's `blame_summary`, `body_range`,
  `dominated`, `withdraw` are gone.
- **Names / docstrings that overstate**: `snapshot.py:25` and `01` §7.5 "`state_fingerprint`
  is equal across a fold" is true and is the wrong invariant — the *views* are not (A4);
  `window.py:25-26` "`ranks` … the order the page had" (A10); `Roles.policy`'s
  docstring says a suggester writes "ops tagged with a plan the same actor opened" while
  the drain is what tags them, and only inserts (A3); `mcp/tools.py:10-12` and
  `server.py:74-76` promise a gate that is not there (A2); `ingest.REWRITERS` is named
  for what it lists, not for the rule it enforces (`ids.py:76-79` says "the two that
  rewrite prose on their own" — an agent's `amend` is a third); `derive._at_realized`
  says "as the last realization read it" and drops the loop's later lift too (A6).
- **Duplicated tables**: `HELD = ("edited", "queued", "planned")` in `reflect.py:54` and
  `kinds/lift.py:15`; `LOOP = "loop"` in `derive.py:20`, `reflect.py:50`,
  `bootstrap.py:32`; `_title_from_file` in `reflect.py:385` and `bootstrap.py:268`;
  `seed()` helpers in three test modules (fine for tests). `Roles.from_config`'s
  `human:import` (`workspace.py:84`) vs `01` §2's grammar (`import`) — `01` §7.3 names
  `human:import`, §2 does not.
- `is_comment_op` (`workspace.py:40-52`) lets a suggester edit or delete inside *anyone's*
  comment body and toggle anyone's comment anchor off (`text.mark comment on=False` is a
  comment op by kind). Probably fine on a hub with GitHub identities in the log; say so.
- `_suggester_plan` keys plans by `(actor, batch)` (`:324`): the editor's batch is one
  settle, so a contributor typing a sentence over four settles opens four plans, four
  verdicts. Reuse the actor's newest open plan on the same target block instead (~3
  lines) — a UX decision, but the code comment ("a plan per keystroke would be a verdict
  per keystroke") suggests the intent was one per edit, not one per 350 ms.
- `comment_lower` (`builtins.py:202-214`) ignores `old`, so every open thread is in
  every later directive until a person resolves it (`comments.ts:85` is the only
  resolver). Intended? Then `03` §4 should say a thread is re-issued each hand-off until
  resolved.
- `rotate_acks(keep=0)` keeps everything (`lines[-0:]`); harmless, never called so.
- `main.py:441-486`: `serve` is defined after `if __name__ == "__main__": app()`; works
  under the console script, confusing to read.
- `probe5`'s `P4b` and `P6b` in `docs/substrate/` now fail for stale reasons; either
  mark them or move the two settled decisions into the shipped suite (dead block reads
  `""`; moved paragraph is ink) so the probe file is not a false alarm.

## E. Spec ↔ code

| spec | says | code | which moves |
|---|---|---|---|
| `01` §3 `plan.reject` | "Marks are removed by the op that placed them" | true only when the toggling op is the placer; `set_mark` records merges/pieces under other placers (A1) | code (match `added` by start+placer) and spec (say `added` is the record) |
| `01` §6 read modes, `01` §9 `export→import→export` | rejected mode = document without open plans; export round-trips | nested links / doubled bold while a displacing or widening plan is open (P1b, P1e, P1f) | code |
| `01` §4 rules 5, 7–8; `server.py:74-76`; `tools.py:10-12` | machine rewrites are gated; the document wins while queued | agent `amend` lands on the trunk ungated; `REWRITERS` excludes agents (A2) | code; spec should name the agent in rule 7 |
| `02` §3.6 | a contributor's trunk edit is applied as a plan with that actor | inserts/marks/adds yes; `text.delete` refused `authority` (A3) | code |
| `01` §7.5, `04` §5 | the fold keeps "the frontiers (with the words the person asked for)"; `state_fingerprint` equal | fingerprint equal; directive, phantom settle, `_emptied`, `ground`, `_drifted` change across the fold (A4) | code (refuse or carry), spec (state which views survive a fold) |
| `01` §6 `ink(f)` | "Only blocks in the reading flow count" | `ink`, `ground`, `plan_claims` walk out-of-flow blocks (A5) | code |
| `03` §5.1 `lower` | "the change since the last realization … with `old` being the block's content as the realization read it (by identity)" | `old` excludes the loop's own later lift, so the lift reads as the author's change (A6) | code; spec should say "what a person changed" |
| `01` §3 `block.delete` | "A dead heading owns no code" | `plan.reopen` re-binds to a dead heading (A7) | code |
| `01` §5 | apply is total | `KeyError` from `marks_back` escapes (A8) | code |
| `01` §7.4 | a resubmitted client id is not applied twice | bounded to the newest `ACK_KEEP` acks (A11) | spec (state the bound) or code |
| `01` §6 `timeline`, `window.py:25` | the order the page had | verdict un-moves carry no ranks (A10) | code |
| `04` §4 | a `post-commit` hook appends a `commit(sha)` marker op | no such op kind (`ops.py` has 20); no hook | spec (delete) or code |
| `01` §7.6, `04` §6.1 | `privacy.prompts = text \| hash`; `init` defaults to `hash` on a public remote | nothing reads `privacy` anywhere in `codoc/` | spec (mark unbuilt) or code |
| `01` §6 `attribution` | "snapped to `doclang.tokens` boundaries for display only" | `attribution()` does not snap | spec or editor |
| `01` §6 `projection` | per block `{… segs, marks, aria}` | `runs`, `spans`, `suggest_del`; no `aria`, no per-block `lang` | spec |
| `01` §6 `directive` | "`Edit only:` returns with comments (a `cite` inside an open comment span)" | comments exist as a kind; no `Edit only:` line (`comment_lower` has no scope) | code or spec |
| `04` §1 | `config.json # doc language, kinds enabled` | no `kinds` key is read | spec |
| `01` §7.5, `04` §5 | "the archive stays readable for the timeline/scrubbing" | nothing reads `log/archive/`; the window reads `store.ops` | spec (or a reader) |
| `01` §2 vs §7.3 | Actor grammar lists `import`; §7.3 and `Roles.from_config` name `human:import` | both exist (`import_v1.py:47`, `snapshot.py:39`) | spec §2: add `human:import` |
| `02` §3.3, §5 | "Accept hands the feature off" | `codoc accept` does when the plan has a target; an `add` plan with `target=None` (several nodes) is accepted and not queued (`main.py:233-237`) | code (hand off each added heading) or spec |
| `03` §1 | `text_view`, `write_back`, `export`/`import_` codecs | `text_view`/`write_back` declared, never called; export is one generic JSON fence (§5.1 says so) | spec §1: mark as not built |

## F. Ranked changes

1. **Un-apply a plan's mark op by placer OR by `(kind, attrs, start, op)` of its `added`
   entries** in `apply._undo_plan_op` and `derive._unapply_plan_marks`; use the same
   match in `plan_claims`. P1a–P1f into `tests/calculus/test_plan_marks.py`. ~20 lines.
2. **Put the amend gate where every machine write passes** (`Workspace.ingest` for
   `MACHINE_KINDS` with no plan: refusal → plan; `deleted_fraction` > gate → plan;
   feature `HELD` → plan); add `agent` to `REWRITERS`; shrink `reflect._amend`
   accordingly. P3c/P3d as `tests/mcp/test_tools.py`. ~25 lines.
3. **Suggesters: rewrite a range `text.delete` to `suggest_del` under their plan; open the
   plan lazily / reject an empty one** in `_drain_one`. P3a/P3b into
   `tests/substrate/test_workspace.py`. ~15 lines.
4. **Snapshots refuse (or carry) work in flight**: refuse while anything is queued or
   edited, or archive every op beyond each realized frontier the way open plans' ops are
   archived; make "once had code" state the snapshot carries. P2a–P2c into
   `tests/substrate/test_review5.py`'s neighbour. ~8 lines (refuse) / ~30 (carry).
5. **`in_flow` filter in `ink`, `ground`, `plan_claims`** (A5). 3 lines; P7a.
6. **`_at_realized` keeps machine segments** so `lower` speaks only for the person (A6).
   ~6 lines; P5c into `tests/kinds`.
7. **Group `plan_mark_ops` by `(bid, path)` in the memo** (§C): cold publish with plans
   back to the no-plans cost. ~8 lines.
8. **`plan.reopen`: skip `binds` on a dead heading; pre-check `marks_back` blocks** (A7,
   A8). 3 lines; P4a/P4b into `tests/calculus/test_apply_properties.py`.
9. **`_require` tests what `init` writes unconditionally** (A9). 1 line; P5a.
10. **`_rank_rows` reads a verdict's `moved`** (A10). ~4 lines; P4c into `test_window.py`.
11. **State the ack-rotation bound** in `01` §7.4 and `rotate_acks` (A11), or persist the
    cid→op map with the offsets. 2 lines of prose or ~10 of code.
12. **Spec edits** (§E): rule 7 names the agent; `03` §5.1 says "what a person changed";
    delete `commit(sha)` and `privacy` or mark them unbuilt; `projection` fields;
    `attribution` snapping; `Edit only:`; `human:import` in §2; what survives a fold.
13. **Delete the dead code** (§D) and fix the four overstating docstrings; dedupe `HELD`
    / `LOOP` / `_title_from_file`; decide the suggester plan-per-batch question.

---

## Applied (2026-09-18)

Every ranked change is in; 24 of the 26 probes pass, and the properties behind
them are in the shipped suite (303 Python tests). The two that remain:

- **P4c** asserts the *window* alone reports the order a page had. It reports
  what each op displaced; a block that never moved keeps today's rank, which the
  reader has from the projection and folds together (`calc/window.shapeAt`).
  The substantive half — a verdict's un-move carried no ranks at all — is fixed
  and pinned by `test_the_window_carries_the_order_a_verdict_undid`.
- **P5b** is the ack-rotation bound (A11). The bound is now stated in `01` §7.4
  and in `rotate_acks`, which is the review's own first option: what is left is
  a client that re-appends an id it sent more than 2,000 acks ago, and since
  the editor mints ids under a per-page random prefix, a page that restarts
  never collides with one.

Two judgements differ from the review:

- **A4** offered "refuse a fold while work is in flight" or "carry it". Carrying
  it is what shipped: refusing would take `codoc snapshot` away exactly when a
  team is busy, and the same mechanism already existed for an open plan's ops.
- **§D** proposed reusing the actor's newest open plan "on the same target
  block" for a suggester. That is what shipped, and it also replaced the batch
  key for the machine gate, so an agent's multi-block amend is one proposal too.

`docs/substrate/probe5_core.py`'s `P4b` and `P6b` now fail for stale reasons —
both are decisions this review's predecessor settled: a dead block reads as `""`
in the window, and a paragraph moved between features is the destination's ink
(`01` §6). The shipped suite carries both.
