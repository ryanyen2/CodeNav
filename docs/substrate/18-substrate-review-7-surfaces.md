# Review 7: the two surfaces — the editor and the hub (implementation 6)

Scope: worktree `CodeNav-substrate` at `9012665` (clean), read against `09-editor.md`,
`docs/serve-deployment.md`, `docs/architecture.md` §6–7.1, and review 16 (§A, §C, its
"Applied" section). Files: `vscode-codoc/src/{calc,editor,host,hub,daemon,setup}/*`,
`codoc/serve/*`, `tests/serve`, `tests/loops/test_roundtrip.py`, `vscode-codoc/src/test/*`.
Every `file:line` is at `9012665`. Nothing in the worktree was modified.

Baseline: 139 vitest pass, `tsc --noEmit` and esbuild clean; `tests/serve` + `tests/loops`
green (the lead's numbers; not re-run here).

Probes are beside this file and assert the **correct** behaviour, so a failing probe is a
confirmed fault:

- `probe7_editor.test.ts` + `vitest.probe7.config.mjs` — 15 vitest probes over the real
  `mountEditor` (and, for E6/G3, the real `app.ts` shell) under the worktree's jsdom.
  From `vscode-codoc/`: `npx vitest run --config <scratchpad>/vitest.probe7.config.mjs`.
  **8 of 13 non-§R probes fail** (E1, E1c, E1d, E2, E3, E4, E5, E6); E1b, E5b, G1–G3 pass.
  Measurements are in `probe7_cost.txt`.
- `probe7_hub.py` — 9 hub/transport probes and the two round-trip drivers. From the
  worktree: `PYTHONPATH=. ../CodeNav/.venv/bin/python -m pytest <file> -q -p no:cacheprovider`.
  **6 of 9 hub probes fail** (H1–H6); H7–H9 pass; **both round trips pass**.
- The round trips (`test_R1`, `test_R2`; §R in the vitest file, driven by the Python side
  through `CODOC_P7_DIR`/`CODOC_P7_PHASE`): R1 is review 6's fuzz with four more gestures
  (a comment thread on a selection, replace-all through the Find widget, a pasted heading,
  Tab on a paragraph) — 24 seeds × 18 gestures, **817 submissions** of 8 kinds
  (`text.insert` 385, `text.delete` 212, `block.add` 75, `block.set` 58, `text.mark` 38,
  `block.delete` 22, `block.move` 15, `block.split` 12), **zero refusals, zero
  disagreements**, every comment's anchor mark on a live block. R2 is two actors and a plan:
  Bob (a maintainer) appends to `p1`, adds a paragraph after `p2`, prefixes `p5`, and an
  agent opens a plan proposing words in `p4`, all applied to the store *under* the editor's
  16 seeds × 12 gestures; phase B replays the gestures (seeded `Math.random`, so bids and
  batches match), adopts Bob's projection while every touched block is held, acks every
  submission with the daemon's real acks, then adopts the final projection: **zero refusals,
  zero shadow faults at any of the three points, and the page after the final projection is
  the store's page in 16/16 seeds.**

**Verdict.** The boundary that records gestures into ops is sound and stayed sound under
more pressure than round 6 put on it: 817 fuzzed submissions of eight kinds, including
comment threads anchored on unacked words and replace-all across many blocks, replay
through the real applier without a refusal or a disagreement, and a second actor's edits
plus an agent's plan landing under the person's own gestures merge to exactly the page the
store describes. What does not hold is the seam between **held blocks, acks and the
projection**: a projection that arrives while a block is held is skipped for that block and
never re-read when the ack lifts the hold, and the hub's feed delivers projection-then-acks
in every poll, so on the hub another actor's words in the paragraph you are typing in, the
agent's plan there, and a contributor's own strike-through do not appear until an unrelated
publish happens (A1). The round-6 tree-pane diff throws on any projection that changes a
heading's title or phase — every settle while typing in a heading — leaving that row missing
and skipping the toolbar, status and margin update for that projection (A2). The daemon's
ack-file trim silently disconnects every connected tail (hub `Feed` and `provider.ts` alike)
until the file regrows, so blocks stay held for the rest of the session (A3). A pasted
paragraph keeps decomposed text on the page while the shadow and store hold NFC (A4); a
comment being written is wiped by every arriving projection (A5); the History scrubber jumps
into the past when the window grows (A6); a drag drops the moved heading's marker until the
next transaction (A7). On the hub: a `suggest` login can resolve and rewrite a maintainer's
comment thread (A8), a hub that dies between `git commit` and its ledger write deletes the
agent's branch and pays for a fresh session (A9), `Glob`'s `pattern` is a path the sandbox
does not confine (A10), and a revoked collaborator recorded as `none` is a suggester to the
applier (A11). Cost: a keystroke at 1,500 features is 13 ms, 9 of which are ProseMirror's
per-child decoration lookup over two kept sets of 3,000 entries — quadratic in the number of
decorated top-level blocks, and independent of jsdom — and an adopt is still two full
decoration-set rebuilds (≈77 of 104 ms) around a whole-document replace that costs 0.5 ms.

## A. Correctness

Ranked. Each item names its probe; the decisive assertion is quoted.

### A1. A projection skipped for a held block is never re-read when the hold lifts (HIGH, two actors; the hub's normal order)

`editor.ts:471–529` `adopt` leaves every held block alone (`held.has(b.id) → nodeFor.set(b.id, cur)`,
`:501`), as §6 says. `editor.ts:246–269` `ack` then clears the hold and dispatches only
`decoKey`; nothing compares the released block's projected `head` with the node's. The
daemon acks during the drain and publishes afterwards (`daemon.py:109–152`), so on disk acks
precede the projection — but `push.py:83–103` `Feed.pending` appends the projection *first*
and the acks *last* in every poll, so on the hub the order is always projection, then acks:
the projection is skipped (held), the ack releases the block, and the page keeps the
shadow's word for it until some *unrelated* publish arrives while the person happens not to
be holding it again. With one actor the shadow equals the store and nothing shows. With two:

- **E1** — Bob's letter at the start of the paragraph Alice is typing in never appears
  while she types (each of her flushes republishes while she holds the block).
- **E1c** — an agent plan proposing words in that paragraph never shows, so the verdict
  buttons name a plan the page does not draw.
- **E1d** — a contributor's own Backspace, which the daemon turns into the plan's
  `suggest_del` (`workspace._as_proposal`), stays an absence on their page instead of a
  strike-through; the store says "still there, proposed removed", the page says "gone".

Pasteable (`probe7_editor.test.ts` E1; the projection's `frontier` covers the acked op, so
it is *known* to be newer than the op):

```ts
m.handle.adopt(proj([h, p1 /* 'Bonex', runs bob:1 | mine | h:5 */], { frontier: { h: 5, bob: 1 } }));
expect(texts(m.view.state.doc)[1]).toBe('onex');            // held: left alone — right
m.handle.ack([{ cid, op: 'h:5', conflict: null }]);
expect(texts(m.view.state.doc)[1]).toBe('Bonex');           // got 'onex'
```

E1b (control) passes: the same two messages in the IDE's order — ack, then projection — are
fine, which is why the IDE never shows this and the hub always can. `probe7_hub.py` H2 pins
the transport half: `assert kinds.index("acks") < kinds.index("projection")` — got
`['projection', 'status', 'acks']`.

Fix, in the editor (the transport reorder alone is not enough: an ack can also arrive after
a projection the daemon wrote later, e.g. through the IDE's two async file reads): in
`ack()`, remember the op ids the acks returned per released block; when a block's unacked
set empties, if every acked op `sess:seq` satisfies `seq <= projection.frontier[sess]` (the
projection already contains the op) and the block's projected `head` differs from the
node's, call `adopt(projection, realizing)` once — it replaces only the released blocks.
A projection whose frontier does *not* cover the op predates it and must not be re-adopted
(R2 phase B shows why: the page correctly waits for P3 there in 13/16 seeds). ~12 lines.
Also `push.py`: emit acks before the projection (1-line reorder), so the hub matches disk
order and the IDE.

### A2. The tree pane throws on any projection that changes a heading's title or phase (HIGH, round-6 regression)

`app.ts:89–113` `renderTree` diffs rows against a `rows` map (review 6 §C). When a row's
key changes it removes the old row (`hit?.row.remove()`, `:106`) and then
`tree.insertBefore(hit.row, at)` (`:110`) — but `at` is the cursor into the live children,
and when it points at the row just removed (the common case: rows are walked in order)
`insertBefore` throws `NotFoundError`. The exception escapes the `message` listener, so for
that projection `renderToolbar`, `renderStatus`, `margin.update` and the History/undo
re-request (`app.ts:312–316`) never run, and the row is missing. A person typing in a heading
publishes a changed title every 350 ms; a hand-off changes a phase. Pasteable
(`probe7_editor.test.ts` E6, over the real `app.ts`):

```ts
post({ kind: 'projection', proj: renamed });    // h5's title changed, h7's phase → edited
expect(errors).toEqual([]);                    // got ["The child can not be found in the parent."]
expect(document.querySelectorAll('.codoc-row').length).toBe(1500);   // 1499 in G3
```

Fix: `if (at === hit.row) at = at.nextElementSibling;` before `hit.row.remove()` (or
`hit.row.replaceWith(row)` and `at = row.nextElementSibling`). 1–2 lines. Then wrap the
`projection` case so a render error cannot skip the ack/undo bookkeeping (or order
`renderTree` last).

### A3. The daemon's ack-file trim disconnects every connected tail (MEDIUM-HIGH, data visibility)

`log.py:366–386` `rotate_acks` rewrites `<client>.ack.jsonl` to its newest 2,000 lines at
every daemon start (`workspace.py:171`). `push.py:117–134` `Feed._acks` and
`provider.ts:171–189` `readFrom` tail by **byte offset**; when the file shrinks under them,
`seek(offset)` is past the end, the read is empty, and every ack written until the file
grows back past the old offset is never delivered — and the first one after that is read
mid-line and dropped as torn. An hour of typing is more than 2,000 acks; a daemon restart
(the extension's supervisor, `codoc watch` restarted by hand) with a panel or a hub tab open
then leaves every block edited in that window **held for the life of the page**, and ⌘Z
waiting on `inFlight` for ever (`app.ts:40–48, 263`). Pasteable (`probe7_hub.py` H1):

```python
feed = Feed(cd, client); feed.pending()          # 2,100 acks in the file, offset at the end
L.rotate_acks(cd, keep=2000)                     # the daemon restarted
L.ack(cd, client, "fresh")
assert "fresh" in got                            # got []
```

Fix: in both tails, `if size < offset: offset = 0` (the editor ignores cids it does not
know, so re-reading the kept 2,000 is harmless); 2 lines each. Or have `rotate_acks` not
truncate while a session may be open — but the tails cannot know that, so fix the tails.

### A4. A pasted paragraph keeps decomposed text on the page while the shadow and store hold NFC (MEDIUM, data boundary)

`commands.ts:355–410` `pasteBlocks`: `paras` are taken raw (`:357`), the new block's node
is built from the raw text (`:394`) and the op carries the raw text (`:402`); the shadow
NFCs (`Shadow.insert`, `:404`) and so does the applier. `composeNFC` (`editor.ts:432–468`)
skips the block-insert step because its range is not inside one textblock (`:457`), so the
new paragraph shows five scalars where the shadow and store have four, and every later
anchor in that block is off by one. The typed run and the first paragraph are composed
(review 6 A9's fix holds); the later paragraphs are the gap. Pasteable
(`probe7_editor.test.ts` E2):

```ts
paste(m, ['x', 'café ok']);  m.handle.flush();
expect(shadowFaults(m)).toEqual([]);   // got ["b-…: shadow \"café ok\" != doc \"café ok\""]
```

Fix: `paras.push(nfc(node.textBetween(…)))` at `:357` (1 line); the op then also carries
NFC, as `projection.ts:133` promises.

### A5. A comment being written is wiped by every arriving projection (MEDIUM, data loss)

`comments.ts:109–112` `Margin.update` → `render()` → `this.el.replaceChildren()` (`:122`)
rebuilds every card, including the draft's and each thread's reply `textarea`, whose
`value` lives only in the DOM. Every settle publishes a projection (another actor's, the
loop's, the agent's), and `app.ts:315` calls `margin.update` on each. Pasteable
(`probe7_editor.test.ts` E3):

```ts
draft.value = 'please split this sentence';  reply.value = 'agreed, and rename it';
margin.update(p);                                        // someone else typed elsewhere
expect(draft2?.value).toBe('please split this sentence');   // got ''
```

Fix: before `replaceChildren`, collect `textarea` values by card key (`draft`, thread id)
and restore them after; or only rebuild cards whose thread changed. ~6 lines.

### A6. The History scrubber jumps into the past when the window grows (MEDIUM-LOW)

`timeline.ts:36–42` `setWindow`: `if (!bar.hidden && value < w.ops.length) show(value)`.
With History on and the scrubber at "now" (`value == old length`), a new op makes
`value < new length` and the page flips to a past reconstruction the person did not ask for
— with the live editor hidden under it. Pasteable (`probe7_editor.test.ts` E4):

```ts
tl.setWindow(w2 /* one more op */, p);
expect(tl.past.hidden).toBe(true);        // got false: the past page is shown, the editor hidden
```

Fix: remember whether the scrubber was at now (`value >= old length`) and keep it there
(`value = new length`), else re-show. ~3 lines.

### A7. The paints are patched before the gesture is recorded, so a drag drops the moved heading's marker (MEDIUM-LOW, round-6 seam)

`editor.ts:381–395` `dispatchTransaction`: `applyTransaction` runs the kept-set plugin
(`decoPlugin.apply`, `:98–111`), which maps the set through the move and drops the widgets
inside the deleted range; only *then* does `record()` hold blocks (`repaint(bid)`) and the
structural `repaint()` at `:392` set `paintsAll` — for the *next* transaction. Until an ack
(≥350 ms + a round trip) or a keystroke, the moved heading has no marker and the moved
paragraph no ink. Pasteable (`probe7_editor.test.ts` E5):

```ts
m.handle.moveBlock('h2', 'START');
expect(m.view.dom.querySelectorAll('.codoc-marker').length).toBe(2);   // got 1
```

E5b (a split) passes: the mapped set keeps the surviving heading's marker. Fix: decide the
structural repaint inside `apply` from the transaction itself (`!sameStructure(tr.before,
tr.doc)` → full rebuild), and drop the post-apply `repaint()` at `:392`. 2 lines. (The
per-block `repaint(bid)` from `queue` cannot change a paint today: `inkPaints`'
`unacked.has(r.op)` compares a cid with an op id `session:seq` and is never true — see §D.)

### A8. A `suggest` login can resolve and rewrite a maintainer's comment thread (MEDIUM-LOW, hub authority — a design question)

`workspace.py:80–92` `is_comment_op` and `:138–156` `Roles.policy` let any actor write
untagged `block.set` / `text.delete` / `text.insert` inside any `comment` block ("a
contributor can edit inside another person's comment thread", the docstring says), and
`comments.ts:163–166` offers **Resolve** to every viewer. So a contributor can mark a
maintainer's request-for-work as resolved (the state `Build it` and the margin read) and
delete its words. Editing *inside* a thread should mean replying in it. Pasteable
(`probe7_hub.py` H3):

```python
alice submits block.set(status=resolved) on bob's c1, and text.delete of bob's body
assert [o.conflict for o in ops] == ["authority", "authority"]   # got [None, None]
```

Fix (core's file; flagged): `text.delete` / `block.set(status)` on a comment block whose
first message is not the actor's → refuse for a suggester; `block.add(comment)` and
`text.insert` into a block they minted stay free. ~6 lines. And hide Resolve for a
suggester on others' threads (`decides()`-style gate in `Margin.card`).

### A9. A hub that dies between `git commit` and the ledger write deletes the agent's branch (MEDIUM-LOW, hub restart)

`worker.py:198–212` writes `built:<branch>:<n>` only after `_publish` has failed; a hub
killed during the push (slow) or `gh pr create` leaves the commit on the branch and nothing
in the ledger. The next pass runs `git worktree add -b <branch>` (`:124`), which fails
because the branch exists, is classed transient-not-built (`:125`), and `_cleanup(…,
keep_branch=False)` (`:220`, `:232–243`) runs `git branch -D` on it: the agent's work is
deleted, and the pass after that pays for a fresh model session. Pasteable (`probe7_hub.py`
H4, a stateful fake `git`):

```python
assert deleted == []            # got [['git','branch','-D','codoc/realize-s-…']] (twice)
assert agent_runs == []         # the agent ran again for work already committed
```

Fix: record `built:<branch>:0` right after `git commit` succeeds (before the push), or on a
`worktree add -b` failure check `git rev-parse --verify <branch>` and take the resume path.
~5 lines. Related, not probed: a crash after `gh pr create` succeeded but before
`record_pr` makes every retry fail "already exists" until `MAX_ATTEMPTS`, then records
"refused" for a hand-off that has a PR; `gh pr view --head` on a create failure would tell.

### A10. `Glob`'s `pattern` is a path the sandbox does not confine (LOW-MEDIUM, security; names only)

`sandbox.py:142–149` reads `file_path` / `path`; `Glob` also takes its target in `pattern`,
and an absolute pattern lists files anywhere (`Read` of them is still refused by
`under_root`, so this leaks names, not contents — e.g. which key files exist under
`~/.ssh`). Pasteable (`probe7_hub.py` H5): `policy("Glob", {"pattern": "/Users/*/.config/gh/*"})[0] is False`
— got `True`. Fix: if `pattern` is absolute, `under_root(pattern's static prefix, root)`. 2 lines.

### A11. A revoked collaborator recorded as `none` is a suggester to the applier (LOW-MEDIUM, authority)

`auth.py:109–112` writes `none` to the tracked roles table on revocation (review 6 A4).
`workspace.py:138–156` `Roles.policy` knows owner, maintainer, machine and "everyone else"
— `none` is everyone else, so the record that says a person was removed grants exactly what
a contributor has: plans and comments. Their session is gone, but an inbox line of theirs
drained after the revocation, and a clone validating their pull request's logs
(`validate_logs`), are judged as a suggester's. Pasteable (`probe7_hub.py` H6):

```python
record_role(cd, "eve", NONE); eve submits plan.open + a comment block.add
assert all(o.conflict for o in ops)     # got [None, None]
```

Fix (core's file; flagged): `if role == NONE: return f"{op.actor} has no access"` at the top
of `policy`. 1 line.

### A12. Read-only findings (not probed)

- **`hub.ts:78–81`** re-arms the EventSource only when a POST succeeds after a 401. A
  person who signs in again in another tab and then only *reads* keeps a dead stream until
  they type. Poll `/api/whoami` while `signedOut` (review 6 A11's suggestion) or re-arm on
  `focus`. ~6 lines.
- **`comments.ts:159`** `Margin` submits a thread without `editor.flush()`; the anchor refs
  may be `$cid` refs of ops still in `pending` (the toolbar's `verdicts()` and
  `buildThread` flush first, `app.ts:210, 217`). The 350 ms settle makes it unlikely for a
  human; R1 flushes before composing and passes, so this is one line of consistency.
- **`realize_agent.py:37–38`** `DENIED_TOOLS` names `Task`, the old name of the subagent
  tool; today it is `Agent`. The hook denies it anyway (H8 asserts `Agent` → deny), so this
  is belt, not boundary — but the belt is mis-sized.
- **`hub.ts` / `app.ts`** on a hub restart every session is gone (in-memory
  `SessionStore`); every open tab's stream dies with a 401 and the words on screen wait for
  a re-login. Documented behaviour; a persisted session store would remove the cliff.

### A13. Confirmed correct (probed)

- **R1** — 24 seeds × 18 gestures, 817 submissions of 8 kinds drained by the real applier:
  every ack clean; blocks, kinds, levels, words and order identical to the page; every
  comment thread's anchor mark on a live block (provisional `$cid` anchors resolved by the
  drain). New this round: comment threads on unacked words, replace-all through the Find
  widget (one transaction, many steps, many blocks), a pasted heading (flattened, as §4
  says), Tab on a paragraph (no-op).
- **R2** — two actors and a plan: Bob's three edits and an agent's plan-tagged insert,
  applied under 16 seeds × 12 of the person's gestures; phase B adopts Bob's projection with
  every touched block held, acks with the real acks, adopts the final projection: zero
  refusals, zero shadow faults at each of three points, page == store 16/16. Held blocks
  the projection did not know (splits, pastes, deletions) kept their place and their words;
  Bob's new paragraph landed after `p2` even where `p2` had been merged away.
- E1b — ack then projection (the IDE's order) adopts the other actor's words.
- E5b — a split keeps the surviving heading's marker and gives the new block its handle.
- H7 — `set-language` on the hub preserves the roles table.
- H8 — the PreToolUse hook (SDK 0.2.148, `_hooks_to_internal_format` accepts its shape,
  `query()` is streaming internally so hooks fire for a string prompt) denies `Agent`,
  `mcp__*`, `Bash`, `WebFetch` (empty allowlist), a `Write` outside the bound files and a
  `Read` of `.codoc/config.json`; allows an in-scope `Edit` and `Read`; `setting_sources`
  is unset, so the worktree's own `.claude/settings.json`, `.mcp.json` and `CLAUDE.md` are
  not loaded. The GitHub token the resolver uses is captured at startup
  (`GithubAuthConfig`, frozen), so the per-run environment scrub cannot make a 10-minute
  recheck read `none` for a legitimate maintainer.
- H9 — a symlink in `media/` to a file outside it is 404; `..%2F` reaches the SPA shell,
  never the file.
- Round-6 probes re-run at `9012665`: all 15 non-cost editor probes pass (adoption holds
  nothing it inserts or removes; NFC composes the typed run only; the timeline's order and
  a gone heading's kind; F1–F4); the 8 hub probes H1–H8 pass (sandbox hook, push before
  `gh`, no re-run on transient, Grep secrets, demotion written, suggester undo,
  `frame-ancestors`, `caused_by` stripped).

## B. Review-6 items: what holds

| # | review-6 fix | status at `9012665` |
|---|---|---|
| A1 | sandbox as a `PreToolUse` hook; `disallowed_tools` | **holds** (H8, shipped test); `Glob.pattern` unconfined (**A10**); `Task` not `Agent` in the belt (A12) |
| A2 | `git push -u`; resume from the push; 5-attempt cap; `built:` ledger | **holds** for a failure the pass sees; a crash before the ledger write deletes the branch and re-runs the agent (**A9**) |
| A3 | `record()` ignores an empty `RECORDED` list | **holds** (probe6 A1–A4 pass; R2 adopts mid-edit cleanly) |
| A4 | re-checked role written to `config.json` | **holds** (probe6 H5); `none` is a suggester to the applier (**A11**) |
| A5 | code-point rank compare; kind/level in the window | **holds** (probe6 T, 6 fixtures); the scrubber moves on its own when the window grows (**A6**) |
| A6 | a plan's author may retract its characters | **holds** (probe6 H6) |
| A7 | `frame-ancestors 'none'` | **holds** |
| A8 | `is_secret_path` on Grep/Glob paths | **holds** for `path`; not for `Glob.pattern` (**A10**) |
| A9 | `composeNFC` composes the inserted run only | **holds** for typing and the first pasted paragraph (probe6 B1, R3); later pasted paragraphs stay decomposed (**A4**) |
| A10 | `caused_by` dropped for a person | **holds** (`_drain_one`, probe6 H8) |
| A11 | EventSource reopened on `codoc:signed-in` | holds when a POST follows; a read-only re-login keeps a dead stream (A12) |
| A11 | atomic `config.json` in `provider.ts` | **holds** |
| §C | paints set kept and patched; drag handles kept, rebuilt on `REPLACED`; tree rows diffed | keystroke 245 → 13 ms **holds**; the patch runs before the gesture is recorded (**A7**); the row diff throws on a changed row (**A2**); adopt still rebuilds both sets (§C) |
| §F 15 | the round trip as a shipped test | **holds**; R1/R2 here push it to 817 + two actors, still clean |

## C. Cost, measured

`probe7_editor.test.ts` G1–G3, jsdom (no layout), real ProseMirror and every real plugin,
1,500 features = 3,000 blocks, ~90-character paragraphs, every block carrying one human run
with no realized frontier (one `ink-unsent` paint per block — a tree full of unrealized
prose, the worst case for the paints set). `paintsFor`, `buildDecorations`,
`decorationsFor`, the drag plugin's `init`/`apply` are wrapped with `performance.now()`.
Second run of two (±15%).

| operation | review 6 "after" | now | inside it |
|---|---|---|---|
| mount, 3,000 blocks | 317 ms | 348 ms | paintsFor 4/3,000 calls, buildDecorations 49, handles() 39; the rest `docFrom` + jsdom |
| keystroke, median of 8 | 11.9 ms | 12.9 ms | `EditorState.apply` (all plugins) 2.0 — of which drag `apply` 0.9, `decorationsFor` 0.2; **`view.updateState` 8.9** |
| adopt, nothing changed | 44 ms | 62 ms | paintsFor 8/3,000, buildDecorations 47 (full: `repaint()` on every adopt) |
| adopt, one block changed | 114 ms | 104 ms | buildDecorations 45 + handles() rebuild on `REPLACED` 32 = **77 of 104**; the whole-doc `replaceWith` on a bare view is **0.5 ms** |
| adopt, one block inserted | — | 104 ms | as above |
| adopt, one changed while one held | — | 91 ms | as above |
| ack, one cid | 41 ms | **1.7 ms** | one `decorationsFor` of one block |
| ack, one refusal → re-adopt one block | — | 86 ms | the two rebuilds again |
| History on | — | 189 ms | buildDecorations 73 (author paints ×3,000) |
| selection move only | — | 0.11 ms | |
| app.ts, later projection, one block changed (G3) | 81 ms | 79 ms | adopt + tree diff + toolbar + margin |
| app.ts, one feature's phase changed (G3) | — | 117 ms | and it **throws** (A2) |

**Where the rest of a keystroke goes — not jsdom, ProseMirror.** G2 isolates it on a bare
view with one *kept, mapped* `DecorationSet` and no other plugin:

| blocks | bare keystroke | + one kept set of N widgets | + one kept set of N inline paints | + both |
|---|---|---|---|---|
| 750 | 0.20 ms | 0.43 | 0.43 | 0.79 |
| 1,500 | 0.24 ms | 1.32 | 1.22 | 2.52 |
| 3,000 | 0.47 ms | 4.35 | 4.37 | **9.77** |

Doubling the blocks triples-to-quadruples the cost: `DecorationSet.forChild` scans the
set's flat `children` array from the start for every top-level child on every view update
(`prosemirror-view`, `forChild`: `for (i…) if (children[i] >= offset) break`), so a
document of N decorated blocks pays ~N²/2 comparisons per set per transaction. Two sets of
3,000 → 9.8 ms, which is the 8.9 ms `updateState` above. This is pure JS and will be the
same in Chrome. Nothing in the round-6 patching touches it: the sets are already kept and
mapped; the cost is in *consulting* them.

What is worth doing next, in order:

1. **Drop the drag widgets** for a CSS `::before` handle on `[data-bid]` plus one
   `mousedown`/`dragstart` handler (review 6 §C already offered it). Zero decorations:
   keystroke ≈ 8 ms → ~4 ms at 1,500 features; adopt loses the 32 ms `handles()` rebuild;
   mount loses 39. ~30 lines, deletes 60.
2. **Repaint only what the projection changed** in `adopt`: the blocks it replaced or
   inserted, plus the headings whose `phases[fid]` differ, plus everything when `claims`
   or `watermarks` differ for a feature — instead of `repaint()` (`editor.ts:475`). The
   plan already knows which blocks changed. ~15 lines; adopt ≈ 25 ms.
3. After 1 and 2 the paints set is the only per-child cost, proportional to *decorated*
   blocks — a synced tree has few. A fully-inked 1,500-feature tree still pays ~4 ms a
   keystroke; that is ProseMirror's floor short of fewer decorations, and acceptable.
4. Per-block adoption (review 6 asked): the whole-doc replace is 0.5 ms. No.

## D. Cleanliness

- `claims.ts:39` `const provisional = unacked.has(r.op)`: `r.op` is `session:seq`
  (`ids.op_id`), a cid is `ide-<tab>-<ms>-<n>`; never equal, so the `provisional` branch and
  the `unacked` parameter through `paintsFor`/`paintMap`/`unackedOps` are dead, and the
  per-block `repaint(bid)` from `queue`/`ack` repaints for nothing that can change. Either
  make the daemon's ack carry the op id into the runs' identity (it does: `shadow.resolve`)
  and paint provisional refs from the *shadow*, or delete the parameter. §7 of the spec
  ("unacked local ops are unsent by definition") is currently satisfied only by watermarks.
- `editor.ts:392` a `repaint()` that takes effect one transaction late by construction (A7).
- `app.ts:106–110` the row diff's cursor is invalidated by its own removal (A2).
- `comments.ts:122` `replaceChildren` on every update (A5); `:159` no flush before a
  thread's ops (A12).
- `push.py:117–134` and `provider.ts:171–189` two byte-offset tails with no truncation
  check, beside a `rotate_acks` that truncates (A3).
- `push.py:85–102` projection before acks, the reverse of the disk order the daemon writes (A1).
- `worker.py:198–212` the `built:` record is the last thing written, after everything that
  can be slow (A9).
- `realize_agent.py:37` `Task` in `DENIED_TOOLS`; the tool is `Agent` now.
- `sandbox.py:13–14` docstring "every path-taking tool (Read, Edit, Write, Glob, Grep) stays
  inside the worktree" — `Glob.pattern` does not (A10).
- `auth.py:3–8` "not a collaborator → none (denied)" — denied at the door, a suggester past
  it (A11).
- `timeline.ts:40` keeps the scrubber's *index* across windows, not its *meaning* (A6).
- `tests/serve/test_hub.py::test_feed_sends_a_snapshot_then_only_changes_and_this_viewers_acks`
  never trims the ack file and never checks the order of kinds within one poll; a test of
  `Feed` across a `rotate_acks` (H1's shape) and of the kinds order (H2's) would have caught
  both. `editor-dom.test.ts` has no test that drives `app.ts` with a *second* projection
  whose heading changed (E6's shape); `tests/loops/test_roundtrip.py` has one actor.

## E. Spec ↔ code

| spec | says | code |
|---|---|---|
| `09-editor.md` §6 | a held block is left alone; otherwise its node is replaced when `head` changed | true per projection; nothing re-reads a skipped projection when the hold lifts (A1). The spec has no rule for it; it needs one (frontier covers the acked op → adopt) |
| `architecture.md` §7.1 | "one SSE feed carries the projection, status, their acks and the timeline window" | the feed carries projection, status, acks — in that order, the reverse of disk (A1); the window comes back as the reply to a `history` POST (`app.py:175–177`) |
| `09-editor.md` §5 | "On the hub the browser posts submissions to `/api/submit`" | `/api/message` with `{kind: 'submit'}` (`app.py:130`, `hub.ts:18`) |
| `app.ts:1–7` header, review 6 §C | every later projection "re-renders the tree rows and the toolbar counts" | a changed row throws; the toolbar, status and margin are skipped for that projection (A2) |
| `09-editor.md` §5 | the host "tails `inbox/ide.ack.jsonl` and forwards acks" | by byte offset, across a daemon that truncates it (A3) |
| `projection.ts:133–137` | "the editor counts and sends NFC" | `pasteBlocks` sends and shows the later paragraphs raw (A4) |
| `09-editor.md` §7 | the scrubber "renders `history.json`'s snapshots read-only"; §7 "Dragging back replaces the page" | the page is replaced without a drag when the window grows under a scrubber parked at now (A6) |
| `09-editor.md` §7 | ink: "unacked local ops are unsent by definition" | unacked ops are invisible to `inkPaints` (cid ≠ op id); the unsent/sent distinction comes from watermarks only (§D) |
| `09-editor.md` §7 | "Catch up: a viewer-local read cursor (`localStorage`) over loop batches" | not built (no reference in `src/editor`) |
| `09-editor.md` §8 | kept: "the walkthrough overlay, the placeholder" | not built |
| `09-editor.md` §9 | `recorder-plugin.ts`, `adopt-plugin.ts`, `keymap.ts`, `toolbar.ts` | `editor.ts` holds recording, adoption and the keymap; `app.ts` the toolbar. A plan, but the doc says "module plan", not "as built" |
| `serve-deployment.md` "Building a hand-off" | "a git or gh failure is not a refusal … the worker comes back to it" | true when the pass sees the failure; a crash before the ledger write deletes the built branch (A9) |
| `serve-deployment.md`, `sandbox.py` docstring | every path-taking tool confined to the worktree | `Glob.pattern` unconfined — names, not contents (A10) |
| `auth.py` header, `serve-deployment.md` | "anyone else is denied" | the door denies; the applier and `validate_logs` treat a recorded `none` as `suggest` (A11) |
| `workspace.py` `Roles` docstring | "a contributor can edit inside another person's comment thread; the log says who did" | as written — and the UI offers Resolve to them; whether *resolving* another's request is "editing inside" is the question (A8) |
| `realize_agent.py:13–16` | the agent is offered `HUB_ALLOWED_TOOLS` and "NO Bash" | true; the belt names `Task`, the tool is `Agent` (A12) |

## F. Ranked changes

1. **Re-adopt a released block when the projection already contains the acked op** (A1).
   `editor.ts` `ack()`: per released block, if every acked `sess:seq` is within
   `projection.frontier[sess]` and the projected `head` differs from the node's, `adopt(projection,
   realizing)` once. ~12 lines. And `push.py` `pending()`: acks before the projection, 1 line.
   Probes: E1, E1c, E1d, H2. Add the frontier rule to `09-editor.md` §6.
2. **Fix the tree-pane cursor** (A2). `app.ts:106`: advance `at` past a row before removing
   it (or `replaceWith`). 2 lines; then make the `projection` case's bookkeeping not depend
   on a render succeeding. Probe: E6.
3. **Tails notice truncation** (A3). `push.py` `_acks` and `provider.ts` `readFrom`: `size <
   offset → offset = 0`. 4 lines. Probe: H1.
4. **NFC the pasted paragraphs** (A4). `commands.ts:357`. 1 line. Probe: E2.
5. **Keep textarea drafts across `Margin.render`** (A5). ~6 lines. Probe: E3.
6. **Decide the structural repaint inside the plugin's `apply`** (A7). 2 lines; delete
   `editor.ts:392`. Probe: E5. Then either paint provisional refs from the shadow or delete
   the dead `unacked` parameter (§D).
7. **The scrubber stays at now** (A6). `timeline.ts:36–42`, ~3 lines. Probe: E4.
8. **Record `built:` right after `git commit`** (A9), or take the resume path when the
   branch exists. ~5 lines. Probe: H4.
9. **Confine `Glob.pattern`** (A10). `sandbox.py`, 2 lines. Probe: H5.
10. **`none` is denied by the policy** (A11; core, flagged). 1 line. Probe: H6.
11. **A suggester may not resolve or rewrite another's thread** (A8; core, flagged), and the
    margin hides Resolve for them. ~8 lines. Probe: H3.
12. **CSS drag handles, zero decorations; repaint only what an adopt changed** (§C). ~45
    lines, deletes ~60; keystroke ≈ 4 ms and adopt ≈ 25 ms at 1,500 features.
13. Small: `Agent` in `DENIED_TOOLS`; flush before a thread's ops; re-arm the stream on a
    read-only re-login; correct the sandbox and auth docstrings; `/api/submit` → `/api/message`
    in `09-editor.md`; mark Catch up and the walkthrough overlay as not built or build them.
14. Keep R2 beside R1 in `tests/loops/test_roundtrip.py`: two actors and a plan arriving
    mid-edit is the case the single-actor trip cannot see, and it costs three seconds.

---

## Applied (2026-09-18)

Every ranked change is in, across six commits (`c86b211`..`58d0e7e`). 12 of the 13
non-§R editor probes pass, 10 of the 11 hub probes, and both round trips — which
now live in the suite (333 Python tests, 147 TypeScript, 3 skipped without node).

**The seam, not the parts.** §A1 is the round's one HIGH that was not a slip:
`adopt` correctly leaves a held block alone, `ack` correctly lifts the hold, and
nothing joined them — so on the hub, whose feed sent the projection first,
another actor's words in the paragraph you were typing in never arrived. The
rule that closes it is the frontier, which was already on the wire: a projection
whose frontier covers every op the acks named ALREADY CONTAINS this block's
gesture, so its word is newer than the page's; one that does not predates the
gesture and re-reading it would undo the typing. It is now in `09-editor.md` §6,
because a rule the spec does not state is a rule the next round re-derives.

**Cost.** §C's two recommendations, measured at 1,500 features (jsdom; this
machine varies ±40% between runs, so these are ratios within one run):

| | review | now |
|---|---|---|
| keystroke, median of 8 | 12.9 ms | **6.1** |
| mount, 3,000 blocks | 348 | **249** |
| adopt, nothing changed | 62 | **2.8** |
| ack, one refusal → re-adopt | 86 | **5** |
| adopt, one block replaced | 104 | ~95 |

The drag handles are a CSS `::before` now: ProseMirror consults a decoration set
once per top-level child per view update, so three thousand widgets cost ~N²/2
comparisons per transaction for a mark that never depends on what a block says.
`adopt` asks which blocks a projection can have repainted instead of repainting
all of them. The last row did not move, and the reason is worth recording: an
adoption replaces the whole document in one step, and mapping the paint set
through that measured **five times worse** than rebuilding it, so `REPLACED`
rebuilds. Per-block adoption is the only lever left there and §C is right that
the replace itself is 0.5 ms — but with the widgets gone the rebuild, not the
replace, is what it would save. Left for a round that needs it.

Two of the ranked changes were made differently:

- **§F 6** offered "paint provisional refs from the shadow, or delete the dead
  `unacked` parameter". Deleted — and `09-editor.md` §7's "unacked local ops are
  unsent by definition" went with it, because there is no such case to paint: a
  run reaches the projection only once its op is applied, and the block is
  unheld by then. The watermarks are the whole distinction.
- **§F 11** asked that a suggester not resolve or rewrite another's thread. The
  rule shipped is about their OWN words rather than about threads: adding a
  comment is always free (a reply IS a new comment), writing into one they
  minted is free, and deleting somebody else's words or resolving a thread they
  did not start is not. Resolving is the sharp case — it is what `Build it` and
  the margin read — but the same principle covers the delete.

The two probes that still fail assert an implementation the fix removed:

- **E5b** counts `.codoc-handle` elements. There are none; the handle is CSS.
  Its other half — the split's new paragraph keeping its neighbours' marker —
  passes and is in the suite.
- **H4** asserts `git branch -D` is never run on a built branch. It is not run
  before the push any more, which was the fault; it runs after the PR is open,
  where the work is on the remote and `_cleanup` is doing its job. The suite's
  version asserts the order instead.

`docs/substrate/probes/` holds this round's probes and `perf7.py`; earlier
rounds' are deleted (their properties are in the suite). Round 7's core review
is `17-…`.
