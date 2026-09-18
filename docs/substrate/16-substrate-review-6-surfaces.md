# Review 6: the two surfaces — the editor and the hub (implementation 5)

Scope: worktree `CodeNav-substrate` at `de8fd4b` (clean), read against `09-editor.md`,
`docs/serve-deployment.md`, `docs/architecture.md` §6–7.1, review 14 (§A, its "Applied"
section) and review 12. Files: `vscode-codoc/src/{calc,editor,host,hub,daemon,setup}/*`,
`codoc/serve/*`, `tests/serve`, `vscode-codoc/src/test/*`. Every `file:line` is at
`de8fd4b`. Nothing in the worktree was modified.

Baseline: 139 vitest pass, `tsc --noEmit` and esbuild clean; `tests/serve` 21 pass.

Probes are beside this file and assert the **correct** behaviour, so a failing probe is a
confirmed fault:

- `probe6_hub.py` — 11 hub/daemon probes, 6 fixture writers for the timeline, and the
  round-trip consumer. From the worktree:
  `PYTHONPATH=. ../CodeNav/.venv/bin/python -m pytest <file> -q -p no:cacheprovider`
  (writes `probe6_fixtures/`), then the vitest file, then the same command with `-k R1`.
  **8 of 11 hub probes fail**; the round trip **passes 24/24**.
- `probe6_editor.test.ts` + `vitest.probe6.config.mjs` — 19 vitest probes over the real
  `mountEditor` under the worktree's own jsdom (the scratchpad's `node_modules/@tiptap` is
  a symlink into the worktree so the file can live outside it). From `vscode-codoc/`:
  `npx vitest run --config <scratchpad>/vitest.probe6.config.mjs`. **10 of 19 fail**
  (4 editor faults, 6 timeline fixtures). Measurements are in `probe6_cost.txt`; full
  output in `ts6.out`.
- The round trip (`R1` in both files): the real editor is driven from a projection the
  real daemon published, 24 seeds × 14 random gestures (typing, typing over selections,
  Backspace/Delete/Enter with and without selections, multi-paragraph paste, `## `,
  Tab/Shift-Tab, drag-move, Delete feature); every submission is flushed in order and
  drained through the real applier, and the store's blocks, kinds, levels, words and
  order are compared with what the page finally read. 359 submissions across
  8 op kinds (`text.insert` 150, `text.delete` 47, `block.set` 55, `block.delete` 33,
  `block.move` 30, `block.add` 23, `block.split` 17, `block.merge` 4), **zero refusals,
  zero disagreements**.

**Verdict.** The editing boundary itself is now sound: the recorder, the gesture commands
and the applier agree on every one of 359 fuzzed submissions replayed through the real
daemon, Enter/Backspace/Delete own their selections, `deleteSelection` and `deleteFeature`
refuse before they mutate, batches retire on acks, and the origin gate stands. What does
not hold is around that boundary. The hub's sandbox is not enforced at the live edge at
all: `_run_sandboxed_sdk` lists the five tools it guards in `allowed_tools`, and the
installed SDK (0.2.148) auto-approves a whole-tool entry *before* `can_use_tool` is
consulted — its own warning says so — so a hub-triggered agent may Read `~/.config/gh/hosts.yml`
and Write anywhere, and the six sandbox tests pass against a predicate nobody calls (A1).
The worker cannot open a pull request: it never pushes the branch and `gh pr create --head`
"explicitly skip[s] any … pushing behavior" (gh 2.96 help), so every hand-off is a
transient failure retried every 15 s, and each retry runs a fresh LLM session (A2).
In the editor, review 5's own fix introduced the largest fault here: the adoption
transaction is tagged `RECORDED` with an empty op list, and `record()` reads an empty
list as a gesture, so every block a projection inserts or removes is *held* under a cid
nobody submitted, for the life of the page — a plan paragraph never changes after its
verdict, another actor's later edits to a new paragraph never show, a restored block
never returns (A3). Demotion on GitHub reaches the session but not the tracked roles
table, so a maintainer demoted to `read` keeps deciding verdicts (A4). The History page
sorts base-62 ranks with `localeCompare`, so any tree with six or more appended blocks
renders every past page out of order, and a deleted heading is drawn as a paragraph (A5).
A contributor cannot undo their own typing (A6), the hub can be framed by a same-site page
(A7), and `Grep` reads the secret files `Read` may not (A8). Cost: a keystroke at 1,500
features is 110–250 ms in this harness, and the remainder review 5 attributed to
ProseMirror is 1 ms; the rest is two `DecorationSet` rebuilds of 3,000 decorations each,
one of them (the drag handles) on every selection change.

## A. Correctness

Ranked. Each item names its probe; the decisive assertion is quoted.

### A1. The live sandbox is never consulted for the tools it guards (HIGH, security)

`realize_agent.py:147–152`:

```python
options = ClaudeAgentOptions(cwd=worktree_path, allowed_tools=list(HUB_ALLOWED_TOOLS),
                             permission_mode="default", can_use_tool=can_use_tool)
```

`HUB_ALLOWED_TOOLS = (Read, Edit, Write, Glob, Grep, WebFetch)`. In the installed SDK a
whole-tool `allowed_tools` entry auto-approves that tool before the permission callback
runs; `claude_agent_sdk/types.py:1833–1863` computes exactly this and warns
(`CanUseToolShadowedWarning`): "can_use_tool will not be invoked for: Read, Edit, Write,
Glob, Grep, WebFetch … To gate every tool call, use a PreToolUse hook." The only tool the
policy ever sees is one it does not list — Bash, which it denies. So on a live hub
`under_root`, `is_secret_path`, `edit_allowed` and the consult gate are all dead code,
and review 5's A4/A5 fixes (and the six tests in `tests/serve/test_worker.py` that exercise
the predicate directly) verify a boundary the agent never meets. A directive can Read
`~/.config/gh/hosts.yml`, Write it into an in-scope file, and the post-run gate
(`out_of_scope_changes`, file names only) passes it into the PR.

Pasteable (`probe6_hub.py` H1; drives the real `_run_sandboxed_sdk` with `query` stubbed
and asks the SDK whether the callback is shadowed):

```python
opts = captured["options"]
shadow = _get_can_use_tool_shadowed_warning(opts.permission_mode, list(opts.allowed_tools))
assert "PreToolUse" in (opts.hooks or {}) or shadow is None
# got: "can_use_tool will not be invoked for: Read, Edit, Write, Glob, Grep, WebFetch. …"
```

Fix: install the policy as a `PreToolUse` hook (`hooks={"PreToolUse": [HookMatcher(hooks=[deny_unless_policy])]}`),
which runs regardless of allow rules; keep `allowed_tools` so the run does not prompt, and
add `disallowed_tools=["Bash", …]`. Also fail safe if the SDK build lacks `hooks`. ~15 lines.
`H1b`: `CODOC_GITHUB_CLIENT_SECRET` is not in `_GH_TOKEN_KEYS`; add it (1 line).

### A2. The worker never pushes, so no PR is ever opened — and each retry is an LLM run (HIGH)

`worker.py:119–124`: `git add -A`, `git commit`, then `gh pr create --base … --head <branch>`.
No `git push`. `gh pr create --help` (2.96.0, on this machine): "When the current branch
isn't fully pushed to a git remote, a prompt will ask where to push the branch … Use
`--head` to explicitly skip any forking or pushing behavior." The branch exists only in
the local worktree, `gh` fails, `realize_one` returns `transient=True`, the worker sets
`_unanswered`, and `process_ready` runs again next interval — creating the worktree again
and **running the sandboxed agent again** (`worker.py:109`), for ever, with no backoff
and no cap. Pasteable (H2, H3):

```python
res = realize_one(req, "/tmp/wt", run=record, agent=lambda *a, **k: ["c.py"])
assert any(a[:2] == ["git", "push"] for a in calls)              # got: no push at all
# H3: three passes with gh failing
assert len(agent_runs) <= 1                                      # got: 3
```

Fix: `run(["git", "push", "-u", "origin", branch], cwd=worktree)` before `pr_create_command`
(1 line, transient on failure); on a transient failure *after* the agent ran, keep the
branch (skip `_cleanup`'s `branch -D`) and retry from the push, or at least back off
(double the interval per retry, cap at a few attempts and then record the refusal with
its reason). ~15 lines.

### A3. Adoption holds every block it inserts or removes, for the life of the page (HIGH)

`editor.ts:477` tags the adoption transaction `setMeta(RECORDED, [])`; `editor.ts:155–156`:

```ts
const tagged = tr.getMeta(RECORDED) as OpBody[] | undefined;
if (tagged) { queue(tagged, …, bornOrGone(tr)); return; }        // [] is truthy
```

`queue([], [], holds)` runs `hold(bid, first || newCid())` (`:211`) for every block the
projection inserted or removed, so each of them acquires an unacked cid nobody submitted
and nobody will ever ack. A held block is left alone by every later adoption
(`adopt`: `held.has(b.id) → nodeFor.set(b.id, cur)`, `:460`), and a held block absent
from the page is never re-inserted (`:458`). Consequences, all until reload:

- **A1** — a paragraph another actor (or the loop, or the agent's plan) added never
  changes again: `['T','one','two']` stays when the projection says `'two more'`.
- **A3** — the agent's proposed paragraph keeps its gray `plan` mark after the maintainer
  accepts (the accepted projection cannot replace the node).
- **A2** — a paragraph the projection removed (someone's delete) and later restores
  (their undo) never comes back.

This is the review-5 A6 fix (`bornOrGone`) meeting the review-5 A1 fix (record what
applied): the adoption transaction is now recorded too. `composeNFC`'s appended
transaction has the same tag (`:425`) and the same path, harmlessly. Pasteable
(`probe6_editor.test.ts` A1):

```ts
m.handle.adopt(proj([h, p1, block('p2', 'paragraph', 'two', 1, 3)]));
m.handle.adopt(proj([h, p1, block('p2', 'paragraph', 'two more', 1, 7)]));
expect(texts(m.view.state.doc)).toEqual(['T', 'one', 'two more']);   // got ['T','one','two']
```

Fix: `if (tagged) { if (tagged.length) queue(…); return; }` — or tag adoption and the NFC
fix-up with their own meta and have `record` return on it. 2 lines. Then A4 in the probe
file (a split, acked, then edited by another actor) keeps passing, which is the case
review 5 fixed and must stay fixed.

### A4. Demotion reaches the session and nothing else (MEDIUM-HIGH, authority)

`auth.py:99–108` re-asks GitHub every ten minutes and rewrites `s.role`; removal
(`NONE`) deletes the session. Nothing rewrites the tracked `config.json`
(`record_role` is called only from `/auth/callback`, `app.py:103`). The daemon applies
every hub op under `Roles.load(cd)` (`workspace.py:73`), and the page's buttons come from
`status_message`, which reads the same file (`push.py:46–47`). So a maintainer demoted to
`read` still has their `plan.accept` applied and still sees Accept / Hand off. Pasteable
(H5):

```python
perms["alice"] = "read"
assert c.get("/api/whoami").json()["role"] == SUGGEST            # passes
assert read_roles(cd)["human:gh:alice"] == SUGGEST               # got 'maintainer'
```

Fix: in `build_app`'s `session()` closure, `before = s.role` … after `auth.session()`,
`if s.role != before: record_role(cd, s.login, s.role)`; or move the write into
`AuthContext.session` with `codoc_dir` on the context. ~4 lines. (On removal, consider
writing `none` too — `validate_logs` still reads the table.)

### A5. The past page is out of order, and a deleted heading is a paragraph (MEDIUM)

`timeline.ts:69`: `order = […].sort((a, b) => rankOf(a).localeCompare(rankOf(b)))`. Ranks
are base-62 in ASCII order (`rank.py`: `0-9A-Za-z`, "so Python's `<` and SQLite's
`ORDER BY` agree without a collation"). `localeCompare` collates letters case-insensitively
at the primary level, so `'a' < 'V'`. Appending blocks yields `V, W, X, Y, Z, a, b, …`
(`append_after`), so the sixth appended block and every one after it sort **above** the
first five on every past page. The prefix-replay fixtures show it at every position of
every random fixture; a code-point sort of the fold's own ranks reproduces the store's
order at every position (`probe6_hub.py` diagnostic and the `cpDetail` check in T), so
the fold (`shapeAt`) is right and the comparator is the whole fault. Second: a block
that is no longer live is rendered from `{ kind: 'paragraph', level: 0 }` (`timeline.ts:72`),
so a heading deleted inside the window is a paragraph on every page before its deletion
(T-scripted, p=6..: `got ["paragraph","Feature 1"] want ["heading","Feature 1"]`).

Fix: `(a, b) => rankOf(a) < rankOf(b) ? -1 : rankOf(a) > rankOf(b) ? 1 : 0` (1 line);
have the daemon record `kinds`/`levels` for every named block in the window (beside
`ranks`, ~4 lines in `window.py`) and read them in `Timeline.show` (~3 lines).

### A6. A contributor cannot undo their own typing (MEDIUM)

A suggester's keystrokes are wrapped in a plan of theirs (review 5 A2, correct). The
inverse the window records for a plan-tagged `text.insert` is a bare `text.delete`
(`window.py`/`apply.py`), which `app.ts` submits untagged; `_suggester_plan` does not tag
it (`text.delete ∉ PLANNABLE`), and `Roles.policy` refuses it: `authority`. So ⌘Z in a
contributor's browser is a refusal, a notice and a reload of the paragraph, every time.
Pasteable (H6):

```python
inverse = [b for e in reversed(mine) for b in e["inverse"]]   # the window's own inverse
undo = drain(alice submits inverse as batch "u-t-1")
assert [o.conflict for o in undo] == [None]                   # got ['authority']
```

Fix belongs to the policy (core's file, flagged to that reviewer): a `text.delete` whose
segments were all minted by ops tagged with a plan the same actor opened destroys
nothing of the trunk and should be allowed for a suggester (`Roles.policy`, ~8 lines).
Until then the page should not offer ⌘Z to a suggester (`decides()` gate in `undo()`).

### A7. The hub can be framed, and app.ts trusts `window.parent` (MEDIUM-LOW, security)

`app.ts:272` admits a message when `ev.source === window.parent` (a VS Code webview needs
this). On the hub, `window.parent` is whoever frames the tab. `SHELL`'s CSP
(`app.py:205`) has no `frame-ancestors` and no `X-Frame-Options` is sent, and the session
cookie is `samesite=lax`, which travels to a same-site frame. A page on a sibling
subdomain of the tunnel host frames the hub and posts `history`; with `pendingUndo`
set the page submits the attacker's ops under the viewer's session. Pasteable (H7):

```python
assert "frame-ancestors" in SHELL.lower() or r.headers.get("x-frame-options")   # got neither
```

Fix: add `frame-ancestors 'none';` to the CSP (1 line). Then the `window.parent` clause
is only ever satisfied inside VS Code.

### A8. `Grep` reads what `Read` may not (MEDIUM-LOW, security; moot until A1)

`sandbox.py:147–154`: `is_secret_path` is applied to `Read` only; `Grep` with
`path=<wt>/.env` (or the worktree's `.codoc/config.json`) returns matching lines.
Pasteable (H4): `policy("Grep", {"pattern": "KEY", "path": f"{wt}/.env"})[0] is False` — got
`True`. Fix: apply the check to `Grep`/`Glob` paths too (2 lines), and say in the
docstring and deploy doc that a pathless Grep sees every tracked file — the "repo's own
secrets" claim is only enforceable per path.

### A9. `composeNFC` recomposes text the store holds decomposed (MEDIUM-LOW)

The store records NFC per **insert**: `e` then U+0301 as two inserts is five scalars in
the store, the projection and the shadow. `composeNFC` (`editor.ts:406–426`) scans the
**whole document** whenever any transaction carries a decomposed run and composes every
text node, so a decomposed paste in one paragraph silently shortens another paragraph's
node by one scalar while its shadow keeps five — and records nothing. Pasteable (B1):

```ts
mount([…, block('p1', 'paragraph', 'café ok'), block('p2', 'paragraph', 'x')]);
select(p2, 1); typeText(m.view, 'é');
expect(shadowFaults(m)).toEqual([]);      // got: p1: shadow "café ok" != doc "café ok"
```

Fix: compose only the ranges the transactions inserted (map each step's `from/to`
through the later steps, or `replaceWith` on the text nodes those ranges touch), never
the whole document. ~8 lines.

### A10. A contributor's `caused_by` is taken at its word (LOW-MEDIUM, provenance)

`app.py:185` copies `caused_by` from the browser into the submission; `_drain_one` stamps
it. `caused_by` is the ledger's "this op is what the hand-off produced" (the provenance
card; `loops/realize.settle` reads it on bind ops). A suggester's text op citing a
directive id is a forged provenance link, recorded for ever. Pasteable (H8):
`assert ops[0].caused_by in (None, "")` — got `'s:forged-directive'`. Fix: drop
`caused_by` for hub clients in `handle_message` (1 line), or in `_drain_one` for any
non-machine actor.

### A11. Read-only findings (not probed: the browser is needed)

- **The event stream dies with the session and is never re-armed** (MEDIUM-LOW).
  `/api/events` answers an expired cookie with `401` JSON; per the EventSource spec a
  non-200 response *fails the connection* — `readyState` CLOSED, no reconnect. `hub.ts`
  says so once (`onerror → whoami → notice`) and leaves the page standing, as review 5
  intended. But when the person signs in again in another tab (the only way that keeps
  this page's words), POSTs succeed and acks, projections and status never arrive again:
  every block they type in is held for ever, ⌘Z waits for ever. Fix: while `signedOut`,
  poll `/api/whoami` every ~10 s and create a new `EventSource` when it is authenticated
  again. ~8 lines.
- **`provider.ts:125` writes `config.json` non-atomically** (LOW). The daemon reads it on
  every inbox pass; a torn read is `{}` → `Roles.from_config({})` → every hub actor is
  `suggest` for that pass and a maintainer's verdict arriving then is recorded `authority`
  for ever. The hub's path is atomic (`write_config`); the IDE's is not. tmp + rename, 2 lines.
- **`worker.py` has no backoff** (see A2): a persistent `gh` failure is an LLM session
  every 15 s per hand-off.
- **Pasting a heading flattens it** (LOW): `pasteBlocks` turns every block of the slice
  into a paragraph (`commands.ts:352`), so copying a section inside the editor pastes it
  as prose. A design gap rather than a fault; say so in §4 or carry the kind.
- **IME composition** was not driven (jsdom cannot fake `compositionstart/end` through
  ProseMirror's observer); the design (one insert at `compositionend`) is right by
  construction, and A9 is the decomposed-commit case.

### A12. Confirmed correct (probed)

- **The round trip** (R1, both files): 24 seeds × 14 gestures through the real view,
  359 submissions of 8 kinds drained by the real applier: every ack clean; blocks,
  kinds, levels, words and order identical to the page. This covers the whole §4 table
  including `## ` (28 times), Delete feature (32), drag (34), Tab (26), multi-paragraph
  paste over selections (35), Enter with selections (29).
- F1 — an ack for a cid this window never sent changes nothing (two-window safety).
- F2 — a comment anchor on unacked words is provisional by a cid that was submitted.
- F3 — a refused op re-adopts only its block; the other block's unsent words stay.
- F4 — Enter over a cross-paragraph selection is `text.delete, text.delete, block.merge,
  block.split` in one batch.
- The timeline **fold**: at every position of six fixtures (one scripted, four random,
  one whose window enters mid-history, 264 positions), the set of blocks and their words
  match a prefix replay of the store — deaths, births, merges, restores, undo. Only the
  order (A5) and a gone heading's kind (A5) are wrong.
- By reading: `RateLimiter()` now has defaults; `under_root` + the root strip are right
  as a predicate; `_suggester_plan` opens one plan per batch and `is_comment_op` lets a
  suggester comment, reply and resolve; `status.role` hides Accept / Hand off / Build for
  a suggester and `buildThread` refuses politely; the 202 reply is a notice; cids carry a
  per-page prefix; `retire()` clears `inFlight` on acks; `deleteSelection` checks every
  part before the first shadow moves; `deleteFeature` checks the range; the `Feed`
  stamps status and config; `set-language` validates the tag.

## B. Review-5 items: what holds

| # | review-5 fix | status at `de8fd4b` |
|---|---|---|
| A1 | record after apply; Enter owns its selection | **holds** (shipped E1–E3d, E6b; my F4; round trip) |
| A2 | daemon wraps a suggester's ops in a plan; comments allowed | **holds** (shipped test_hub) — but their undo is refused (**A6**) |
| A3 | `RateLimiter` defaults | **holds** |
| A4/A5 | root strip; every path tool under the worktree | right as a predicate; **never called at the live edge** (**A1**); Grep bypass (**A8**) |
| A6 | hold what an op names; held-unprojected block keeps its place | **holds** for gestures (E7–E9); the same code now holds every block adoption inserts or removes (**A3**) |
| A7 | paste position; gesture cids | **holds** (E4, E5, round trip) |
| A8 | NFC the document too | **holds** for the typed run (R3); over-reaches into other blocks (**A9**) |
| A9 | `deleteSelection` checks before mutating | **holds** (E6, round trip) |
| A10 | `deleteFeature` range check | **holds** (R2) |
| A11 | batches retire on acks | **holds** (E11) |
| A12 | timeline liveness + displaced rank | **holds** (264 positions vs prefix replay); order comparator and gone-block kind wrong (**A5**) |
| A13 | origin gate | **holds** for an opener (E10); a same-site frame passes as `window.parent` (**A7**) |
| A14 | transient → retry; `None` for did-not-run | **holds** (shipped tests); retry re-runs the agent unboundedly (**A2**) |
| A15 | recheck cadence | **holds** for removal; demotion not written to the table (**A4**) |
| A16 | language tag; 202 notice; per-page cid; 401 handling | hold; 401 leaves the stream dead (**A11**) |
| §C | keep the paint map | **holds**: `paintsFor` is 3–9 ms per full adopt, 0.1 ms per keystroke; the "rest is ProseMirror" judgement is wrong (**§C**) |

## C. Cost, measured

`probe6_editor.test.ts` G1–G3, jsdom (no layout), real ProseMirror and every real plugin,
1,500 features = 3,000 blocks, ~90-character paragraphs. `paintsFor`, `buildDecorations`
and the drag plugin's `decorations` prop are wrapped with `performance.now()` (transparent
otherwise). Two runs, because the harness varies ±2× with JIT/GC; the shape does not.

| operation | run 1 | run 2 | inside it |
|---|---|---|---|
| mount | 1218 ms | 1023 ms | paintsFor 9 ms (3,000 calls), buildDecorations 112, drag decorations 79–100; the rest is jsdom building 3,000 blocks |
| keystroke (`insertText`), median of 6 | 245 ms | 123 ms | buildDecorations ~60–167 per call, drag decorations ~50–160 per call (called on the selection change too) |
| keystroke without the drag plugin | — | 107 ms | buildDecorations ~95 |
| **bare ProseMirror keystroke, same doc, no plugins** | 1.4 ms | 0.9 ms | |
| adopt, nothing changed | 211 ms | 109 ms | paintsFor 3–7 ms (3,000 calls), buildDecorations 53–105, drag 44–79 |
| adopt, one block changed | 249 ms | 143 ms | as above; PM `replaceWith` of the whole doc ≈ 20–40 ms |
| adopt, one block inserted | 232 ms | 180 ms | |
| ack (one cid) | 182 ms | 186 ms | one `decoKey` redraw: buildDecorations 88–98, drag 75–83 |
| `buildDecorations`, no paints (G2) | 0.3–1.4 ms | | this is the number review 5 measured |
| `buildDecorations`, one ink paint per block (G2) | 37–61 ms | | |
| `DecorationSet.create`, 3,000 inline decorations, positions precomputed (G2) | 66 ms | | the floor: ProseMirror's own set construction |
| drag plugin `decorations` prop alone (G2) | 47–55 ms | | 3,000 widgets, rebuilt on **every** transaction including selection-only ones |
| app.ts, a later projection with one block changed (G3) | 349 ms | | adopt (~140) + `renderTree` 1,500 rows + toolbar + margin |

Where the rest goes: **not ProseMirror**. A bare keystroke on the same 3,000-block
document is 1 ms. The keystroke is two `DecorationSet.create` calls over ~3,000
decorations each — `buildDecorations` (one `ink-unsent` inline decoration per block here,
which is what a tree with unrealized human prose looks like) and the drag handles (3,000
widgets) — and an ack or an adopt pays the same two again. Review 5's 0.6 ms was
`buildDecorations` with an empty paint map (G2 reproduces 0.3–1.4 ms).

Is per-block adoption worth it? No, not first: adopt is 110–250 ms of which `paintsFor`
is 3–9 ms and the whole-doc `replaceWith` ~20–40 ms; the other ~150 ms is the same two
decoration rebuilds. The change that matters is to stop rebuilding decoration sets:

- `decoPlugin`: keep the `DecorationSet` in plugin state, `set.map(tr.mapping, tr.doc)`
  on every transaction, and on `docChanged`/`decoKey` rebuild only the blocks in
  `paintsDirty` (`set.remove(oldOnes).add(doc, newOnes)`); rebuild everything only when
  `paintsAll`. ~30 lines. Then a keystroke touches one block: ≈ 1–3 ms.
- `dragPlugin`: the widgets do not depend on the selection; keep them in plugin state and
  rebuild only when the block structure changed (`sameStructure` is already computed per
  transaction), else `map`. ~10 lines. Or drop the widgets for a CSS `::before` handle on
  `[data-bid]` and one `mousedown` handler — zero decorations.
- `app.ts`: `renderTree` rebuilds 1,500 rows per projection while the person types (every
  settle → publish → projection). Diff rows by `fid`/title/phase. ~20 lines.

After those, per-block adoption saves the ~20–40 ms `replaceWith` and can wait.

## D. Cleanliness

- `editor.ts:439` `byId` is built and never read.
- `commands.ts:147–148` a stale draft comment ("Headings split into heading + paragraph?
  No:") explains a branch that then does something else.
- `realize_agent.py` docstring says the predicate is what "the SDK PreToolUse/canUseTool
  hook calls"; it is wired as neither hook (A1). `sandbox.py`'s docstring claims Grep
  confinement of the repo's own secrets that only Read has (A8).
- `worker.py` has a `transient` flag and no memory of how often it was set.
- `hub.ts` `signedOut` is set and never re-armed into a new `EventSource` (A11).
- `timeline.ts:72` defaults a gone block's kind; `window.py` records ranks and liveness per
  block but not kind/level (A5).
- `provider.ts:125` non-atomic config write beside an atomic one in `app.py` (A11).
- `app.py:185` forwards `caused_by` and `plan` from the browser unchanged (A10; `plan` is
  checked by the policy, `caused_by` by nobody).
- `tests/serve/test_worker.py` tests the policy predicate six ways and never the wiring;
  a test that constructs the real options and asks the SDK's own shadow check (as H1 does)
  would have caught A1 at the moment `allowed_tools` was added.

## E. Spec ↔ code

| spec | says | code |
|---|---|---|
| `serve-deployment.md:65–73`, `architecture.md` §7.1, `sandbox.py` docstring | no shell; every path-taking tool confined to the worktree; edits confined; secrets unreadable | none of it reaches the agent: the policy is shadowed by `allowed_tools` (A1); Grep reads secret files (A8) |
| `serve-deployment.md:58–63` | each hand-off "gets one pull request … on its own git worktree and branch" | the branch is never pushed; `gh pr create --head` does not push; no PR is ever created (A2) |
| `serve-deployment.md:80–83` | a git/gh failure leaves the request unanswered and the worker "comes back to it on the next interval" | true, and each return re-runs the agent, unbounded (A2) |
| `serve-deployment.md:42–43`, `architecture.md` §7.1 | permission re-asked every ten minutes "so taking someone off the repository reaches the tab" | removal: yes; demotion: session only, not the roles table the daemon applies under (A4) |
| `09-editor.md` §6 | "blocks added, removed or re-ranked by the projection are inserted, removed or moved"; held = blocks with unacked local ops | every block adoption inserts or removes becomes held with a phantom cid and is never adopted again (A3) |
| `09-editor.md` §7 | "A past page shows the blocks that were on it, in the order they had" | blocks and words: yes; order by `localeCompare` over base-62 ranks (A5); a gone heading drawn as a paragraph (A5) |
| `09-editor.md` §4 | undo emits the inverses of the viewer's newest batch | a suggester's inverse is refused `authority` (A6) |
| `09-editor.md` §3 | shadow = the store's live characters | `composeNFC` composes a document node the store holds decomposed (A9) |
| `app.ts:266–272` comment | "our own messages come from this window … or, in a VS Code webview, from the frame that hosts it" | on the hub the hosting frame can be anyone's; no `frame-ancestors` (A7) |
| `hub.ts:24–27` comment | "leave the page standing … the next flush will carry them" | the flush goes; the stream that would answer it is closed for good (A11) |
| `02-workflows.md` §3.6 / review 5 A2 | contributor's edits ride as a plan of theirs | holds; provenance (`caused_by`) from the same contributor is unchecked (A10) |
| review 5 "Applied" §C | "the rest is ProseMirror's own view update … which per-block adoption would address" | bare ProseMirror is ~1 ms; the rest is two decoration-set rebuilds (§C) |

## F. Ranked changes

1. **Enforce the sandbox as a `PreToolUse` hook** (A1). `realize_agent._run_sandboxed_sdk`:
   `hooks={"PreToolUse": [HookMatcher(hooks=[policy_hook])]}`, `disallowed_tools=["Bash", "Task", …]`,
   fail safe when `hooks` is unsupported; scrub `CODOC_GITHUB_CLIENT_SECRET`. ~15 lines; H1, H1b.
2. **Push before `gh pr create`, and stop re-running the agent on a transient** (A2).
   `git push -u origin <branch>`; keep the branch on a post-agent transient and retry from
   the push; back off and cap. ~15 lines; H2, H3.
3. **`record()`: an empty `RECORDED` list is not a gesture** (A3). 2 lines; probe A1–A4.
4. **Write a re-checked role to `config.json`** (A4). ~4 lines in `app.py`'s `session()`; H5.
5. **Code-point compare for ranks; record kind/level of named blocks in the window** (A5).
   1 + ~7 lines; T fixtures.
6. **Let a plan's author retract the plan's own characters** (A6). ~8 lines in
   `Roles.policy` (core); until then gate `undo()` on `decides()`. H6.
7. **`frame-ancestors 'none'`** (A7). 1 line; H7.
8. **Apply `is_secret_path` to Grep/Glob paths; document the pathless case** (A8). 2 lines; H4.
9. **`composeNFC` composes only inserted ranges** (A9). ~8 lines; B1.
10. **Drop `caused_by` from hub submissions** (A10). 1 line; H8.
11. **Re-create the EventSource after a re-login** (A11). ~8 lines in `hub.ts`.
12. **Decoration sets in plugin state, mapped and patched per dirty block; drag widgets
    rebuilt only on structure change; diff the tree pane** (§C). ~60 lines; makes a
    keystroke at 1,500 features ~2 ms instead of 110–250.
13. **Atomic `config.json` write in `provider.ts`** (A11). 2 lines.
14. Small: delete `byId`; fix the `commands.ts:147` comment; correct the two sandbox
    docstrings; a test of the live SDK options (H1's shape) in `tests/serve`.
15. Keep the round trip (`R0`/`R1` + the vitest `R1`) in the suite: it is the one probe
    that says the page and the store agree, and it costs two seconds.

---

## Applied (2026-09-18)

Every ranked change is in. All 19 editor probes and all 39 hub probes pass,
including the 24 round-trip replays; the shipped suites are 306 Python and 139
TypeScript, `tsc` and esbuild clean.

The round trip is now a shipped test (`tests/loops/test_roundtrip.py` +
`R.` in `vscode-codoc/src/test/editor-dom.test.ts`): the Python half writes the
base projection, runs the editor fuzz under `CODOC_ROUNDTRIP`, and replays every
submission through the real applier. It skips when the extension's
`node_modules` is absent. §F item 15 was right that it is the one check saying
the page and the store agree.

Cost, re-measured with the review's own G1–G3 (same harness, same machine):

| operation | before | after |
|---|---|---|
| mount, 3,000 blocks | 1023–1218 ms | 317 ms |
| keystroke (median of 6) | 123–245 ms | 11.9 ms |
| adopt, nothing changed | 109–211 ms | 44 ms |
| adopt, one block changed | 143–249 ms | 114 ms |
| ack (one cid) | 182–186 ms | 41 ms |
| a later projection (app.ts, G3) | 349 ms | 81 ms |

Three changes, as §C prescribed: the paints `DecorationSet` is kept in plugin
state, mapped through each change and patched only where the paints moved; the
drag handles are kept too and rebuilt only when the set of blocks changes; and
the tree pane diffs its rows. One addition the review did not have: mapping
3,000 widgets through the adoption's whole-document replace costs more than
rebuilding them, so the adoption tags its transaction and the drag plugin
rebuilds on that tag — without it, `adopt` got *worse* (614 ms).

Two notes on the fixes themselves:

- **A6** (a contributor cannot undo their own typing) was flagged to the core
  reviewer as a policy change. It landed here: `Roles.policy` allows a delete or
  restore naming only characters an open plan of that actor's minted. Taking
  back words you proposed touches nothing of anyone else's.
- **A9** — composing only the inserted *ranges* was not enough, because a range
  lands inside a text node the transaction did not otherwise touch, and
  composing that node folds its neighbours. The unit is the inserted **run**,
  composed on its own, which is the unit the shadow and the store already use.
