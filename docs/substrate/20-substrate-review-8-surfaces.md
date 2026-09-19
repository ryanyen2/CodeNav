# Review 8: the two surfaces — the editor and the hub (implementation 7)

Scope: worktree `CodeNav-substrate` at `18855c0` (clean), read against `09-editor.md`,
`docs/serve-deployment.md`, `docs/architecture.md` §6–7.1, and review 18 (§A, §C, its
"Applied" section). Files: `vscode-codoc/src/{calc,editor,host,hub,daemon,setup}/*`,
`codoc/serve/*`, `tests/serve`, `tests/loops/test_roundtrip.py`. Every `file:line` is at
`18855c0`. Nothing in the worktree was modified.

Baseline re-run here: `tests/loops/test_roundtrip.py` R1 + R2 **pass** (7 s). The lead's
147/2 vitest and 333/1 pytest numbers were not re-run.

Probes are in the scratchpad and assert the **correct** behaviour, so a failing probe is a
confirmed fault and a passing one a confirmed property:

- `probe8_editor.test.ts`, `probe8_app.test.ts`, `probe8_hubshell.test.ts` +
  `vitest.probe8.config.mjs` — 28 vitest probes over the real `mountEditor`, the real
  `app.ts` message shell and the real `hub.ts` (fetch/EventSource stubbed), under the
  worktree's jsdom. From `vscode-codoc/`:
  `npx vitest run --config <scratchpad>/vitest.probe8.config.mjs`.
  **11 fail** (A1–A4, B1–B3, C1–C2, D1, S1); **17 pass** (A5–A6, B4, C3, D2, E1–E2, G1,
  S2–S5, G3, hub shell H1–H4). Output: `probe8_results_ts.txt`; cost: `probe8_cost.txt`
  (run 3) and `probe8_cost_run2.txt`.
- `probe8_hub.py` — 11 pytest probes over the hub, the transport and two applier contracts
  the editor leans on. From the worktree:
  `PYTHONPATH=. ../CodeNav/.venv/bin/python -m pytest <scratchpad>/probe8_hub.py -q -p no:cacheprovider`.
  **5 fail** (H1, H2, H3, H6, H9); **6 pass** (H4, H5, H7, H8, H10, H11). Output:
  `probe8_results_py.txt`.

**Verdict.** Round 7's frontier rule is right and is in the wrong place. It lives in
`ack()` (`editor.ts:295–307`), where it decides which released blocks to re-read — but the
re-read is done by calling `adopt()`, and `adopt()` itself still holds only blocks with
*unacked* ops (`:545`). So any block that has just been released, whose last op the current
projection does *not* cover, is replaced by that older projection the next time `adopt()`
runs for any reason: a conflict on a different block in the same ack message (A1), the same
projection delivered twice — VS Code's watcher fires twice per atomic write (A2) — or two
acks coalesced into one message when the projection covers one and not the other (A3). In
each case the person's own typed letter vanishes from the page until the next publish, which
is exactly the symptom round 7 closed in the other direction. The closed form is three lines
in `adopt()` (hold a block whose last acked op the projection's frontier does not contain)
plus one line refusing a projection whose `head` is lower than the one adopted (A4, S1 —
the IDE's two async reads can finish out of order); `caughtUp` is then deleted. Second: the
CSS drag handle round 7 shipped does not work in a browser. ProseMirror's own `dragstart`
handler runs after `drag.ts`'s and calls `dataTransfer.clearData()`, which erases the block
id the plugin just set (C1); with any words selected elsewhere, ProseMirror instead moves
*those words* to the drop point (C2). Nothing in the suite drives the DOM path — the two
drag tests call `handle.moveBlock` directly. Third: `adoptDirty` (round 7's item 5) is
correct for what a projection changes but the paint map's block→feature index (`paintFid`)
is rebuilt only on a full repaint, so the paragraph every **Enter** creates is never inked
blue (B1), and a paragraph dragged under another feature keeps the old feature's hand-off
colour for the life of the page (B2). One line — a structural change rebuilds the map — fixes
both. On the hub: a revoked collaborator's open SSE stream keeps delivering the tree (the
session is checked once, at connect — H3); the sandbox's `pattern` check, added for `Glob`,
also reads `Grep`'s regex, so an agent that greps for `/api/message` or any slash-leading
literal is refused (H1); a relative `../..` glob still escapes (H2). Cost is where round 7
left it (keystroke 6.1–6.4 ms; adopt-one-block 61 ms), and the per-block adoption round 7
deferred is now measurable: the same one-block replacement as an in-place transaction on the
same view costs 5.8–6.1 ms — an 8–10× reduction on the row that fires on every other
actor's settle — and the steps for it are already computed by `adoptionPlan` and thrown away.

## A. Correctness

Ranked. Each item names its probe; the decisive assertion is quoted.

### A1. The frontier rule guards `ack()`, not `adopt()`: a released block is re-read from a projection that predates its op (HIGH, two actors)

`ack()` collects `released` (block → the ops its acks named, `editor.ts:254, :262`) and
`caughtUp` (`:295–307`) re-adopts only blocks whose ops are all inside
`projection.frontier`. Correct — but the re-adoption, and every other adoption, goes through
`adopt()`, whose hold set is `unacked.size > 0` only (`:545`), and whose replacement rule is
`cur.attrs.head !== b.head` (`:574`). A block whose unacked set has just emptied is unheld
there whatever the frontier says. Three ways the same projection reaches `adopt()` after such
a release:

- **A1 — a conflict in the same message.** `refused.size` takes the early path
  (`:269–274`): `adopt(projection, realizing, refused)` with the co-acked clean block now
  unheld and `head` differing → replaced by words that lack its op.
  ```ts
  m.handle.adopt(bob);                                   // frontier {bob:1}; p1, p2 held
  m.handle.ack([{ cid: cidA, op: 'h:5', conflict: 'unknown_anchor' }, { cid: cidB, op: 'h:6', conflict: null }]);
  expect(texts(m.view.state.doc)[2]).toBe('twoy');       // got 'Btwo': my 'y' is gone until the next publish
  ```
- **A2 — the same projection delivered twice.** `provider.ts:87–92` posts on every
  `projection.json` change event; VS Code's `FileSystemWatcher` commonly fires twice for an
  atomic rename write, and `app.ts:332` adopts each. The first is skipped for the held
  block, the ack releases it (frontier says: not covered, keep the page's word — right), the
  second replaces it. `expect('onex')` — got `'Bone'`.
- **A3 — two acks in one message, one covered and one not.** `stale = {p1}` →
  `adopt(projection, realizing, {p1})` → p2, unheld, `head` differs (Bob touched it) →
  replaced. `expect('twoy')` — got `'Btwo'`.

With one actor the shadow equals the store and nothing shows; R2 does not see it because its
phase B delivers every ack in one message *before* the covering P3 and never re-adopts P2.
The store is never wrong; the page loses the person's letter for one publish, and any op
they type meanwhile anchors on the older shadow (valid refs, so no refusal).

**Fix** (the rule moves to where the decision is made, and `caughtUp` goes):
```ts
const lastOp = new Map<string, string>();                 // block → newest op the daemon applied for this page
// ack(): if (a.op && !a.conflict) for (const bid of waiting) lastOp.set(bid, a.op);
// adopt(): held = unacked>0 ∪ { bid | lastOp.has(bid) && !inFrontier(lastOp.get(bid), next.frontier) } minus force
//          delete lastOp for blocks that leave the page (with shadows, :589)
// ack(): after resolving, `if (refused.size) …; else adopt(projection, realizing);` — adopt already replaces only heads that moved
```
~8 lines added, `caughtUp` (~15) deleted. Probes A1–A3 pin it; A5/A6 (below) must keep
passing. Add to `09-editor.md` §6: *the hold is "unacked, or acked past this projection's
frontier"* — one predicate, evaluated by adoption, not by acks.

### A2. The drag handle does not move blocks in a browser; with a selection it moves the selected words (HIGH, round-7 regression)

`drag.ts:50–56` sets the block id on `dataTransfer` in a `handleDOMEvents.dragstart` that
returns `false`. ProseMirror runs custom handlers first and, when one neither returns true
nor prevents default, runs its own: `prosemirror-view/dist/index.js:3710–3739`
`handlers.dragstart` builds a slice from the *selection* (empty or not), calls
`event.dataTransfer.clearData()` (`:3733`), sets `text/html`, and sets `view.dragging`. The
custom MIME is gone, so `dragover` (`drag.ts:58–65`) never targets, `drop` (`:67–78`) finds
no bid and returns false, and ProseMirror's `drop` runs instead. With an empty selection that
is a no-op: **the handle does nothing**. With words selected anywhere on the page, the
selection's slice is what `view.dragging` carries and ProseMirror's drop **moves those words
to the pointer** — a text change the recorder then records as the person's.

Pasteable (`probe8_editor.test.ts` C1/C2; mousedown at the handle strip, `dragstart` on
the block, `dragover`/`drop` on the target, a faithful `DataTransfer`):
```ts
const { dt } = dragBlock(m, 'p1', 'p2');
expect(dt.getData('application/x-codoc-bid')).toBe('p1');     // got ''
expect(ids(m.view.state.doc)).toEqual(['heading:h', 'paragraph:p2', 'paragraph:p1']);   // unchanged
expect(m.view.dragging).toBeNull();                          // C2: set, carrying the selected words
```
Round 7's E5 and the suite's `E6c` call `handle.moveBlock` — the API under the drop — never
the DOM path, which is why this shipped green. C3 (mousedown in the text, not the strip)
passes: nothing arms.

**Fix:** `dragstart` returns `true` when it armed the block (ProseMirror's handler is then
skipped; ours is the drag). 1 line. Also call `view.input.mouseDown?.done()` there or accept
the leftover `mousemove` listener until the next mousedown (harmless). Add C1/C2 to the
suite: the DOM path is the feature.

### A3. The paint map's block→feature index is rebuilt only on a full repaint, so Enter's new paragraph is never inked and a moved block keeps its old feature's colour (MEDIUM-HIGH)

`paintFid` (`editor.ts:133`) is filled only in `paintMap`'s `paintsAll` branch (`:149–161`).
Round 7 made the paints plugin rebuild the *set* on any structural change (`:109`) but the
*map* still takes the else-branch (`:163`): `for (bid of paintsDirty) if (paintFid.has(bid))
paintsOf(bid, paintFid.get(bid))`. Two consequences:

- **B1.** A block born by Enter has no `paintFid` entry. When its projection arrives,
  `adoptDirty` marks it moved (`:574`), `paintMap` skips it (`paintFid.has(nid)` is false),
  and it is never painted: the person's words in every paragraph they start with Enter are
  not blue. `expect(paintsIn(m, nid)).toEqual(['codoc-ink-unsent'])` — got `[]`.
- **B2.** A paragraph dragged under another heading keeps `paintFid = old heading`, so
  `watermarksFor` and `claims` come from the wrong feature, at the gesture and after the
  confirming projection (same page order → `adoptDirty` returns `moved`, not null).
  `expect(paintsIn(m, 'p1')).toEqual(['codoc-ink-unsent'])` after the move — got
  `['codoc-ink-sent']` (the old feature's hand-off).

B4 passes: another actor's heading arriving above a paragraph reorders the page →
`adoptDirty` returns null → full rebuild → right. Only the person's own gestures are wrong.

**Fix:** a structural change rebuilds the map: in the plugin's `apply`, before `paintMap`,
`if (tr.docChanged && !sameStructure(tr.before, tr.doc)) paintsAll = true;` (the set is
rebuilt in that case already; the map costs `paintsFor` × N ≈ 4–6 ms at 3,000 blocks, on a
gesture, not a keystroke). 1–2 lines. Probes B1, B2.

### A4. A projection older than the one adopted is adopted (MEDIUM, IDE)

`adopt()` has no monotonicity check; `adoptionPlan` compares per-block `head`s in both
directions. `provider.ts:63–66` `postProjection` is `await readJson(...)` per watcher event
with no serialisation; two events, two reads, and the older can resolve second. The page then
moves back one publish (A4 on `mountEditor`, S1 through the real `app.ts` shell):
```ts
post({ kind: 'projection', proj: newer });   // head 20
post({ kind: 'projection', proj: older });   // head 9, resolved second
expect(textOf('p1')).toBe('Retries twice, then gives up.');   // got 'Retries once.'
```
`projection.head` is `store.pos` (`derive.py:584`), monotonic. **Fix:** first line of
`adopt`: `if (next.head < projection.head && !force.size) return;` 1 line. (`drainAcks`
has the same re-entrancy, `:76–84`; a duplicate ack is idempotent, so no probe.)

### A5. A revoked collaborator's open stream keeps carrying the tree (MEDIUM, hub authority)

`app.py:116–128` checks `session(request)` once and hands `event_source` only
`request.is_disconnected`; `push.py:150–159` polls until disconnect. `auth.session`
(`auth.py:97–122`) re-asks GitHub every ten minutes *per request* — a viewer who only reads
makes no requests, so nothing ever re-asks, and even when a POST does and `record_role(NONE)`
is written, the stream is not told. `serve-deployment.md`: "taking someone off the repository
reaches the tab they already have open" — their writes, yes; their view of every later
projection, no, for as long as the EventSource (which the browser reconnects on its own —
and a reconnect *is* re-checked) stays up. Pasteable (`probe8_hub.py` H3):
```python
record_role(cd, "eve", NONE); edit_and_publish(...)
nxt = await gen.__anext__()          # expected StopAsyncIteration; got a 'projection'
```
**Fix:** pass `still_allowed=lambda: session(request) is not None` into `event_source` and
return when it turns false (checked each poll; `session()` is a dict lookup until the recheck
is due). ~4 lines.

### A6. `Grep`'s regex is read as a path (MEDIUM, the agent cannot search for a route)

Round 7 confined `Glob.pattern` (`sandbox.py:157–161`), but the branch reads `pattern` for
every tool that names no `file_path`/`path`. `Grep`'s `pattern` is the regex. A search for
`/api/message`, `/healthz`, `/usr/bin/env` — any literal starting with `/` — is refused as
"outside the worktree", and a feature whose directive names a route is un-buildable on the
hub. Pasteable (`probe8_hub.py` H1):
`can_use_tool("Grep", {"pattern": "/api/message"}, scope=["a.py"], root=wt)[0]` — got
`False`. **Fix:** `if tool_name == "Glob" and not target:` 1 line. Probe H1.

### A7. A relative glob that climbs out of the worktree is not confined (LOW-MEDIUM, names only)

The same branch checks only `/` and `~` prefixes; `../../*` is resolved by the tool against
its cwd. `probe8_hub.py` H2: `can_use_tool("Glob", {"pattern": "../../*"}, root=wt)[0]` —
got `True`. **Fix:** `_static_prefix` of a relative pattern joined to `root`, then
`under_root`. 2 lines.

### A8. The scrubber holds an index into a sliding window (LOW-MEDIUM)

`history_window` is `store.ops[-200:]` (`window.py:47`, `publish.py:39`), so once a tree has
200 ops every publish slides it by one. `timeline.ts:36–47` keeps `range.value` across
windows; round 7's fix keeps "at now" at now, but a scrubber parked on a past moment now
shows the *next* moment each time anyone edits. `WindowEntry.id` exists to pin it.
Pasteable (`probe8_editor.test.ts` D1):
```ts
range.value = '1'; ... setWindow(slid)     // the same three moments, one op later
expect(caption.textContent).toContain('00:00:01');   // got '…00:00:02'
```
**Fix:** remember the entry id at the shown position; on `setWindow`, find it in the new
window and re-show there (fall back to now if it slid out). ~5 lines.

### A9. One `/api/message` carries any number of submissions (LOW)

`app.py:178–186` appends every entry of `subs`; the rate limiter (`:139`) counts requests.
5,000 inserts in one POST → `200 {"submitted": 5000}` (`probe8_hub.py` H9), each drained,
each opening a plan for a suggester. **Fix:** cap `len(subs)` (a settle is a few ops; 200 is
generous) and the body size. 2 lines.

### A10. Read-only findings (not probed)

- **`app.ts:332`** `editor.adopt(m.proj, realizing)` runs outside `paint()`; a throw there
  still skips the three panes, and `projection = m.proj` (`:307`) has already advanced. Wrap
  it, or order it last.
- **`push.py:81`** the replay offset is `size − bytes(last N complete lines)`; when the file
  ends in a half-written line the offset lands that many bytes into the oldest replayed line,
  which is then dropped as torn (`probe8_hub.py` H6: three acks, a torn tail → replayed
  `['gap1','gap2']`). With `replay=200` the lost one is the 200th-newest, which the page
  already has — an off-by-torn-tail, not a loss. 1 line: subtract the torn tail too.
- **`comments.ts:196`** and **`workspace.py:128–149`** agree: the margin offers Resolve to
  the thread's starter, `resolveOps` sets `status` on every block of the thread, and the
  applier judges each by the *thread's* minter (H7 passes with a maintainer's reply in the
  thread; E1/E2 pass). The one asymmetry is cosmetic: `threadsOf` reads `status` from the
  first block only, so a reply block left `open` by a refusal would not show.
- **`drag.ts:23`** `HANDLE_TOP = 0, HANDLE_HEIGHT = 22` "must match app.css"; the CSS strip is
  `top: 0.35em; height: 16px` (`app.css:95`). The hit strip is taller than the drawn handle;
  harmless, the comment overstates.

### A11. Confirmed correct (probed)

- **A5** — a block born by Enter (held under the split's cid via `payload.new` and
  `bornOrGone`) is re-read from the first projection covering the split; both heads move
  `→ 5`; the caret stays where Enter put it; no shadow fault.
- **A6** — a batch acked across two messages: nothing until the last ack, then the block is
  re-read with Bob's letter in it (the frontier is per session and monotonic, so covering the
  last op covers the batch).
- **B4** — another actor's heading arriving above a paragraph repaints it as the new
  feature's.
- **C3** — a mousedown in the text arms nothing; a `dragstart` from there carries no bid.
- **D2** — at now, the page stays at now when the window grows (round 7 A6).
- **E1/E2** — Resolve shown to a `suggest` starter after a maintainer replied, hidden on a
  maintainer's thread; Resolve emits `block.set(status)` per block of the thread.
- **S2** — a `projection` from another origin (not the host frame) is ignored.
- **S3** — `role: suggest` hides Hand off / Accept all / Build now; owner shows them.
- **S4** — ⌘Z asks for the window, waits while a batch's acks are outstanding, asks again
  when they land, and submits the newest batch's inverse as `u-<batch>` (the real `app.ts`
  `inFlight`/`retire`/`applyUndo` path).
- **S5** — one projection that renames, reorders and removes headings, then another that
  moves and inserts: rows once each, in order, nothing thrown (round 7 A2's cursor fix, under
  a harder sequence).
- **Hub shell H1–H4** — the stream opens once and re-posts its events as host messages;
  a submit goes to `/api/message` with the CSRF header; a 401 is a notice; **focus after a
  sign-in elsewhere closes the dead stream and opens a new one** (round 7 item 7); a stream
  error while signed in asks `whoami` once and stays quiet.
- **H4/H5** — the feed keeps delivering after `rotate_acks`; acks precede the projection.
- **H7/H8** — the comment rule and `none` as the applier sees them.
- **H10** — a `plan.accept` of an insert-only plan *does* move the block's head (the accept
  untags the block's segments and records it touched), so `adoptDirty`'s reliance on `head`
  for verdicts holds today; B3 stays a pinned contract (below).
- **H11** — typing into a block another actor deleted is refused `dead_block` with the op
  id on the ack, so `caughtUp`'s "projection no longer has the block" case is the conflict
  path, not a silent stale page.
- **R1/R2** re-run at `18855c0`: pass.

## B. Round 7: what holds

| # | round-7 change | status at `18855c0` |
|---|---|---|
| 1 | a released block is re-read when the frontier covers its ops | **holds in `ack()`** (A5, A6, round 7's E1 in the suite); **escapes in `adopt()`**: a released block whose op the projection does not cover is replaced on the next adoption of that projection — conflict in the same message, duplicate delivery, coalesced acks (**A1**) |
| 2 | feed sends acks before the projection; both tails reset on truncation | **holds** (H4, H5; `provider.ts:180` read) |
| 3 | `app.ts` steps the row cursor off before removing; `paint()` per pane | **holds** (S5 under rename+reorder+remove+insert); `editor.adopt` itself is outside `paint()` (A10) |
| 4 | CSS drag handles; `drag.ts` is two DOM events | the plugin is as described; **it does not work in a browser** — ProseMirror's `dragstart` clears the payload and, with a selection, takes the drag as a text move (**A2**) |
| 5 | `adoptDirty` repaints only what a projection can have changed | **holds for what a projection changes** (B4, `adopt nothing changed` 2.8 ms); **the block→feature map is not rebuilt on the person's structural gestures**: Enter's paragraph never inked, a moved block keeps its old feature's colour (**A3**) |
| 6 | `inkPaints` lost `unacked` | **holds** (`claims.ts:152–153`, no parameter; the docstring explains why) |
| 7 | drafts survive `Margin.render`; scrubber holds a position; `pasteBlocks` NFCs; margin flushes; hub re-arms on focus | drafts: holds (suite E3); scrubber: holds **at now** (D2), **slides in the past** (**A8**); NFC: `commands.ts:363–364` read; flush: `app.ts:318–325` read; focus: **holds** (hub shell H3) |
| 8 | `none` denied; suggester comment rule; `Glob.pattern` confined; branch resume | `none`: holds (H8); comments: holds and matches the margin (H7, E1, E2); `pattern`: **over-reaches to `Grep`** (**A6**) and **misses relative `..`** (**A7**); resume: not re-probed (suite) |
| 9 | R2 in `test_roundtrip.py` | **holds**, passes here; it cannot see A1 because phase B never re-adopts P2 after the acks |

## C. Cost, measured

`probe8_editor.test.ts` G1 (+ `probe8_app.test.ts` G3), jsdom, real ProseMirror and every
real plugin, 1,500 features = 3,000 blocks, every block with one unrealized human run (one
`ink-unsent` paint each). Two runs; **this machine varies ±40% between runs, so read the
ratios within a column**. Round 7's column is its `probe7_cost.txt`.

| operation | round 7 | run 2 | run 3 | inside it |
|---|---|---|---|---|
| mount, 3,000 blocks | 265 ms | 252 | 246 | buildDecorations 49; paintsFor 4.5/3,000 |
| keystroke, median of 8 | 6.2 | **6.1** | 6.4 | `EditorState.apply` 1.5–1.8 (of which `structure()` ×4 ≈ 0.8); `view.updateState` 4.3–4.4 |
| adopt, nothing changed | 2.9 | 2.8 | 3.6 | no paint work |
| adopt, one block changed | 62.7 | **61.4** | 60.9 | buildDecorations 45–49 of it (the `REPLACED` rebuild) |
| **one block replaced in place** (a per-block adoption's transaction, same view) | — | **5.8** | 6.1 | mapped set + one block's DOM |
| adopt, one block inserted | 61.9 | 50.1 | 61.5 | buildDecorations 35–38, paintsFor 3,001 |
| adopt, one changed while one held | 51.2 | 46.9 | 47.4 | buildDecorations 36 |
| ack, one cid (one block repainted in the kept set) | 1.3 | 1.3 | 2.1 | one `decorationsFor` |
| ack, one refusal → re-adopt one block | 46.6 | 46.2 | 45.7 | the rebuild again |
| adopt, only `plans` changed | — | 1.7 | 1.8 | `adoptDirty` fast path |
| History on | 181.5 | 174 | 180 | author paints ×3,000 |
| selection move only | 0.13 | 0.12 | 0.12 | |
| app.ts, later projection, one block changed (G3) | 36 (Applied) / 79 (review) | 76 | — | adopt + tree diff + toolbar + margin |
| app.ts, one feature's phase changed (G3) | 89 | 81 | — | |

Nothing moved since round 7's Applied numbers (within noise). The keystroke's remaining
`EditorState.apply` ≈ 1.5 ms is mostly `structure()` (`editor.ts:648–652`, 0.21 ms per
call, called four times per doc-changing transaction: twice in `filterTransaction`, twice in
the paints plugin); caching the string on the state, or comparing child ids without joining,
takes ~0.6 ms off every keystroke — small, and free.

**The next thing worth doing, with the measurement behind it: per-block adoption.** Round 7
rejected mapping the paint set through a whole-document replace (5× worse than rebuilding)
and left per-block adoption "for a round that needs it". Measured on the same view: the
whole-document adoption of one changed block is **61 ms**, of which the `REPLACED` rebuild
is 45–49; the *same* replacement as one in-place `replaceWith` of that block's node — the
transaction a per-block adoption would dispatch — is **5.8–6.1 ms**, with the paint set
mapped, plus 1.3–2.1 ms to repaint that one block through the ack path. **~8–10× on the
row that fires on every other actor's 350 ms settle**, which on the hub with two live
writers is most adoptions. Inserts and reorders would still rebuild (the structure changed),
so `adopt, one block inserted` stays ~50–60 ms; those are rarer. The steps are already
computed: `adoptionPlan` (`adopt.ts:77–96`) returns `replace | insert | remove | move` per
block and `adopt()` reads only `plan.length` (`editor.ts:546–547`), then rebuilds the whole
document anyway (`:591–593`). Applying those steps as ProseMirror steps — `replaceWith` per
replaced block, `insert`/`delete` for the rest — is ~30 lines, deletes the `order`/`nodeFor`
rebuild (~25), and makes `REPLACED` unnecessary for the replace-only case. Do A1 first: the
hold predicate is what per-block adoption must respect.

## D. Cleanliness

- `editor.ts:295–307` `caughtUp`: the frontier test lives beside `ack()` while the hold set
  it should be part of lives in `adopt()` (`:545`). One predicate in one place (A1).
- `editor.ts:546–547` `adoptionPlan(...)` is computed and only its `.length` is read; the
  steps it returns are what per-block adoption needs (§C).
- `editor.ts:133, :163` `paintFid` is a cache with no invalidation on the gestures that
  change what it caches (A3).
- `editor.ts:648–652` `structure()` joins 3,000 strings, four times per keystroke (§C).
- `editor.ts:611` `scalarCaret` is indented as a top-level function inside `mountEditor`.
- `commands.ts:28–35` `REPLACED` carries two docstrings, one above the other, saying the same
  thing.
- `drag.ts:8–16` says what a decoration would cost and why the handle is CSS; nothing says
  the plugin's `dragstart` must own the event or ProseMirror's will. `drag.ts:23` "must match
  app.css" — the numbers do not (A10).
- `sandbox.py:152–156` the comment says "`pattern` too: a Glob takes its target there"; the
  code applies it to every tool (A6). `:13–16` the module docstring now claims `Glob.pattern`
  is confined; relative patterns are not (A7).
- `push.py:81` the replay offset assumes a newline-terminated file (A10).
- `app.py:118` `events()` names its authority once; `push.py:150` `event_source` has no
  parameter for it (A5).
- `timeline.ts:41` "at now is a position the person chose, not a number" — for now, yes; a
  past position is still a number into a window that slides (A8).
- Tests: `editor-dom.test.ts:350` `E6c: drop (drag handle) …` calls `handle.moveBlock`, not
  the drop; no test dispatches `dragstart`/`drop` (A2). No test adopts the same projection
  twice around an ack, or mixes a refusal with a clean ack (A1). No test in `tests/serve`
  reads the stream past a role change (A5).
- `probe8_editor.test.ts` B3 fails as written but the daemon cannot produce its projection
  today (H10): the accept touches the block. It is a pinned contract — `adoptDirty` assumes
  every verdict moves the head of the blocks whose paints it changes — worth one comment at
  `editor.ts:506–512`, not a fix.

## E. Spec ↔ code

| spec | says | code | move |
|---|---|---|---|
| `09-editor.md` §6 | a projection whose frontier does not cover the op "must not be re-read; adopting it would undo the person's typing" | true for the adoption the ack triggers; false for the next adoption of that projection by any other path (A1) | code: the predicate into `adopt()`; spec: state the hold as one predicate |
| `09-editor.md` §6 | "a block with unacked local ops is left alone" | that is the whole hold set (`:545`); acked-but-uncovered is not in it | code |
| `09-editor.md` §8, `drag.ts:8` | drag handles are a CSS `::before`, "two DOM events" | the events are there; ProseMirror's own `dragstart` erases the payload; the handle does nothing / moves selected text (A2) | code |
| `09-editor.md` §7 | "The scrubber holds a POSITION, not an index: parked at now it stays at now when the window grows" | true at now; in the past it holds an index into `ops[-200:]` (A8) | code (pin by `WindowEntry.id`), then the spec sentence is true |
| `09-editor.md` §7 | ink: "runs with a human actor whose op is outside the realized frontier" | drawn from a block→feature index that is stale after Enter or a move (A3) | code |
| `serve-deployment.md` | "taking someone off the repository reaches the tab they already have open" | their next write, yes; their open stream keeps carrying projections (A5) | code; or the spec says "their next write" |
| `sandbox.py:13–16` docstring, `serve-deployment.md` | every path-taking tool confined; `Glob`'s `pattern` "is a path too" | a `Grep` regex is treated as one (A6); a relative `..` glob is not (A7) | code |
| `architecture.md` §7.1 | "one SSE feed carries the projection, status, their acks and the timeline window" | projection, status, acks; the window is the reply to a `history` POST (`app.py:175–177`) | spec (unchanged from round 7's E) |
| `09-editor.md` §9 | `adopt.ts` "projection → per-block replacement plan" | the plan is computed and discarded; adoption replaces the whole document (`editor.ts:591`) | code (§C) or spec ("whole-document replace guided by per-block holds") |
| `09-editor.md` §7 | Catch up "not built" | still not built (`src/editor` has no cursor) | agree |
| `09-editor.md` §8 | kept: "the placeholder", "the palette" | `find.ts:72,79` has input placeholders; no editor placeholder and no palette module | spec: mark not built or build |

## F. Ranked changes

1. **The hold predicate into `adopt()`; delete `caughtUp`** (A1). `lastOp` per block set
   at ack; `adopt` holds `unacked > 0 ∪ !inFrontier(lastOp, next.frontier)`; `ack()` calls
   `adopt(projection, realizing)` after resolving. ~8 lines added, ~15 deleted. Probes
   A1–A3 fail today; A5, A6 and the suite's E1/E7/E8 must keep passing. Spec §6: one
   sentence.
2. **`dragstart` returns `true` when armed** (A2). 1 line; add C1/C2 to the suite (the DOM
   path).
3. **A structural change rebuilds the paint map** (A3). `paintsAll = true` when
   `!sameStructure(tr.before, tr.doc)` in the plugin's `apply`, 1–2 lines. Probes B1, B2.
4. **Refuse an older projection** (A4/S1). `if (next.head < projection.head && !force.size)
   return;` 1 line.
5. **The stream re-asks its authority** (A5). `event_source(..., still_allowed=…)`, return
   when false. ~4 lines. Probe H3.
6. **`pattern` is a path for `Glob` only, relative included** (A6, A7). 3 lines. Probes
   H1, H2.
7. **Pin the scrubber by entry id** (A8). ~5 lines. Probe D1.
8. **Cap `subs` per message** (A9). 2 lines. Probe H9.
9. **Per-block adoption** (§C): apply `adoptionPlan`'s steps as ProseMirror steps; rebuild
   only when the structure changed. ~30 lines, deletes ~25; adopt-one-block 61 → ~8 ms.
   After 1, because the hold set is what it must respect.
10. Small: wrap `editor.adopt` in `paint()`; the replay offset minus the torn tail;
    `structure()` once per transaction; the two `REPLACED` docstrings; `scalarCaret`'s
    indentation; the `drag.ts` geometry comment; the sandbox docstrings; §8's placeholder and
    palette; a comment at `adoptDirty` naming the head-moves-on-verdict contract (B3/H10).

## G. The design against its own literature

Four mechanisms on the surfaces have now been patched more than once. For each: what the
patches were, which system solved it in closed form, and whether taking that form deletes
code or adds it.

**1. Adoption and held blocks (rounds 5, 6, 7, and A1 here).** The patches: hold what a
gesture names; hold what it created or removed (round 6 A3); don't hold what adoption itself
moved (round 6); re-read a released block when the frontier covers it (round 7); and now, the
same test in `adopt()`. Each patch answers "is the page or the projection newer for this
block?" with a new special case. The closed form is **ShareDB's / Quill's client document**
(and Google Docs' before it, from Wave): a per-document `version`, exactly one `inflightOp`,
a `pendingOps` list, and one rule — an incoming server op is applied only after being
transformed against `inflight` and `pending`; a server op at a version the client already
has is discarded. ProseMirror's own `prosemirror-collab` is the same shape with rebasing
instead of transform: `receiveTransaction(state, steps, clientIDs)` rebases the client's
unconfirmed steps over the received ones and the `version` says what is new. codoc cannot
adopt the transform half — the store, not the client, is the authority and text ops are
already identity-addressed, so there is nothing to transform — but it can adopt the *version
half*, and that is what A1's fix is: the page's version of a block is the newest op the
daemon applied for it (`lastOp`), the projection's version is its `frontier`, and a block is
adopted iff `frontier ⊇ lastOp` and the block has nothing in flight. Stated that way,
`caughtUp` (15 lines), the "held means the page is right" paragraph in §6, and the
frontier special case all collapse into one predicate. **Deletes code.** The CRDT bindings
(y-prosemirror, automerge-prosemirror) go further — no acks, no holds, because concurrent
edits to one block merge by character identity — but codoc's calculus already has character
identity in the store; what it lacks is a local replica, and building one (Eg-walker's
critical version, as round 7's core review noted) is the round after this one, not this one.

**2. The paints and their cost (rounds 6, 7, and A3 here).** The patches: keep and map the
set; patch per block on ack; rebuild on `REPLACED`; drag handles to CSS; `adoptDirty`; and
now the map's index. Each keeps a cache (`paintsOut`, `paintFid`, the `DecorationSet`) and
hand-invalidates it at the call sites that turned out to matter. The closed form is
**CodeMirror 6's decoration model**: decorations are a `RangeSet` derived by a `ViewPlugin`
from `view.visibleRanges` on every update, so the set is *never larger than the viewport*
and *never cached across a structural change* — invalidation is not a question because the
derivation is cheap enough to run each time. ProseMirror has no viewport facet, but the same
move is available: build paints only for blocks whose DOM intersects the scroll box (`view.dom`
children have `getBoundingClientRect`), which at 3,000 blocks is ~30 blocks, and rebuild
that set on every transaction. That deletes `paintsOut`, `paintFid`, `paintsDirty`,
`paintsAll`, `repaint`, `adoptDirty`, `markDirty` and the `REPLACED` branch (~90 lines) for
one function of ~25, and makes A3 impossible rather than patched. Round 7's measurement that
the per-child lookup is ~N² in *decorated* blocks also goes away, since N becomes the
viewport. **Deletes code.** The alternative — keep the caches and add the invalidation rule
in F.3 — is the one-line fix; it is the third invalidation rule added to this cache.

**3. The ack protocol (rounds 6, 7, and A4/A10 here).** The patches: acks by byte-offset
tail; reset on truncation; acks before the projection in the feed; replay the last 200 on
reconnect; and now, an offset that assumes a trailing newline and a projection read that can
overtake another. Two independent tails of two files with no shared sequence number are the
problem. **Notion's** transport and **Figma's multiplayer** both send *one* ordered stream
per client in which the server's acknowledgement of a client's op and the server's own
changes are interleaved by the server, each carrying the server sequence, so "did this
snapshot come before or after my op" is a comparison of two integers and duplicates are
dropped by sequence. codoc has the integer already — `projection.head` is `store.pos`, and
an ack's `op` has a `position` — so the minimal form is: the IDE's provider and the hub's
feed emit one message stream in disk order (the feed does; the provider's two async readers
do not), the editor drops any projection with `head` ≤ the last adopted, and the ack tail is
keyed by the last delivered cid rather than a byte count. That deletes `sizeOf`/`readFrom`'s
offset arithmetic and the replay offset math (~25 lines) for a sequence compare. **Deletes
code**; the feed's ordering is already right.

**4. The margin's layout (v1's slide, round 7's draft-preserving render, and here).** The
patches: don't move the prose for a card; stack by a floor; keep textarea values across
`replaceChildren`; keep focus. Every one is a consequence of rebuilding the DOM from scratch
per update. **Google Docs' comment gutter** (and Medium's, and every keyed-list renderer) keeps
one element per thread keyed by id, updates the ones whose data changed, and lays out by
solving the one-dimensional constraint "each card at its anchor's y, none overlapping, the
active card pinned and the others pushed away". Keying the cards deletes `drafts()`, the
focus save/restore and the `ta.value = ''` guard in `post` (~25 lines) and makes A5 of round
7 unreproducible by construction; the layout function already exists (`layout()`), it only
lacks the "push the others away from the active card" pass, which is ~10 lines if wanted.
**Deletes code.**

What all four share is the finding round 7's core review made about the calculus: each
surface mechanism was fixed by adding a rule at the place the last fault appeared, and the
next fault appeared at the place the rule did not reach. The forms above are each *one*
rule evaluated in *one* place — a version compare in adoption, a derivation over the
viewport, a sequence on one stream, a keyed list — and each is smaller than what it replaces.

---

## Applied (2026-09-18)

Every ranked change is in, across three commits (`27359e3`..`182618e`); the suite is
**159 TypeScript tests, 347 Python, 3 skipped**, and all 11 hub probes pass.

**§A1 was a fault in something round 7 shipped, and the report is right about why:** the
frontier rule was in `ack()`, which decides which released blocks to re-read, while the
re-reading is `adopt()`, whose hold set was "unacked" alone. One predicate now — *held is
unacked, or acked past this projection's frontier* — evaluated where the decision is
made, and `caughtUp` is deleted. **§A2 is the same shape and worse:** the CSS drag handle
shipped last round did nothing in a browser, because ProseMirror's own `dragstart` runs
after a custom handler that returns false, clears the payload and takes the drag for the
selection. It shipped green because nothing drove the DOM path; three tests do now.

**§C's per-block adoption is in, with its measurement.** Round 7 rejected mapping the
paint set through a whole-document replace (5× worse) and left this "for a round that
needs it"; round 8 measured what that costs, and an adoption whose every step is a
`replace` now replaces in place:

| operation | review | now |
|---|---|---|
| adopt, one block changed | 61 ms | **14** |
| adopt, one changed while one held | 47 | **12** |
| ack, one refusal → re-adopt | 46 | **10** |
| keystroke, median of 8 | 6.1–6.4 | 8.0 (same, within this machine's noise) |

Anything structural still rebuilds, which is what the rebuild is for.

Two items were made differently:

- **§F 5** asked for `still_allowed` from the app. The generator also asks the tracked
  roles table itself, because that is what a revocation WRITES and a caller can forget
  to pass a callback.
- **§F 6** separated the two `pattern` rules rather than narrowing one: a Glob's pattern
  is a place (absolute or `..`-climbing, both confined), a Grep's is a regex and is not a
  path at all.

**B3 still fails, and should:** it asserts that a projection in which only `plans`
changed re-paints, which `adoptDirty` does not do — deliberately, resting on a contract
the probe itself names (a verdict touches every block the plan's ops landed in, so those
heads move; `probe8_hub` H10 pins that today). The contract is now written at
`adoptDirty`, which is what was missing.

### §G's four closed forms: three in, one measured and rejected

**G1 (the version compare) is A1's fix**, and the report is right that it collapses
`caughtUp`, the special case and the §6 paragraph into one predicate. **G3 (one sequence
per stream)** is in as far as it goes without a transport rewrite: the feed is in disk
order, the editor drops a projection whose `head` it has passed, the tails notice
truncation, and the replay offset counts from the last complete line. **G4 (keyed cards)**
is in: one element per thread, rebuilt only when the thread changed, which deletes
`drafts()`, the focus save/restore and the `ta.value = ''` guard, and makes round 7's A5
unreproducible rather than fixed.

**G2 (CodeMirror 6's viewport-derived decorations) was built and reverted, on the
measurement.** The form is right and the deletion is real — `paintsOut`, `paintFid`,
`paintsDirty`, `paintsAll`, `repaint`, `adoptDirty`, `markDirty` and the `REPLACED` branch
all go for one function — but it rests on the derivation being bounded, and the way to
bound it here is not available. CodeMirror does not measure blocks: it computes its
viewport from `scrollTop` and a height oracle it maintains, never touching a DOM rect per
block. ProseMirror has no such oracle, so `onScreen()` has to ask each block for its
rect — and 3,000 `getBoundingClientRect` calls per transaction cost **150× what painting
all 3,000 blocks costs**: a keystroke went from 7 ms to 1,230 ms, and every other row with
it. (It is not a jsdom artifact; per-element rects force layout in a browser too, which is
why CM6 avoids them.)

So the honest version of §G2 is: the closed form is a viewport-derived derivation **plus a
height oracle**, and the oracle is a substantial addition, not a deletion. Round 8 keeps
the cache and the invalidation rule F.3 asked for — knowing, and now recording, that it is
the third rule on that cache. The measurement is the thing to carry forward: whoever
reaches for this next should build the oracle first and re-measure, not bound the
derivation by asking the DOM.
