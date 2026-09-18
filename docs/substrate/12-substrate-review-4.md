# Review 4: the editor and the timeline, block kinds, the hub (implementation 3)

Scope: worktree `CodeNav-substrate`, read at commit `b95f38f` ("The hub builds a
hand-off into a pull request") against `09-editor.md`, `03-extensions.md` §5.1,
`01-calculus.md` §3/§6/§7.4, `02-workflows.md`, `04-version-control.md`,
`architecture.md`, `serve-deployment.md`. Every `file:line` below is at `b95f38f`.

**The tree moved while this review ran.** By the time the probes were finished, HEAD
was `f43a3b8` (three commits) with a further 20 files uncommitted, touching
`window.py`, `worker.py`, `sandbox.py`, `push.py`, `auth.py`, `client.py`, `log.py`,
`kinds/*`. Each finding below therefore carries a status: **open** (the probe still
fails on the working tree as of writing) or **closed in the working tree** (the probe
passes there; the fix is unreviewed and uncommitted). Four shipped tests fail on that
working tree right now (`test_hub.py` ×3, `test_worker.py::test_the_sandbox_is_the_boundary`),
which is consistent with mid-edit.

Probes are beside this file and assert the *correct* behaviour, so a failing probe
is a confirmed fault:

- `probe_substrate4.py` — 25 Python probes; run from the worktree with
  `PYTHONPATH=. ../CodeNav/.venv/bin/python -m pytest <file> -q -p no:cacheprovider`.
- `editor.probe.test.ts` + `vitest.probe.config.mjs` — 12 vitest probes; run from
  `<worktree>/vscode-codoc` with `npx vitest run --config <scratchpad>/vitest.probe.config.mjs`.
  They build real ProseMirror states over `docFrom`, drive `baseKeymap` and
  `tr.replaceSelection` exactly as the view would, and re-run `editor.ts`'s `record()`
  fallback logic verbatim (there is no jsdom, so `mountEditor` itself cannot be driven).

Baseline at `b95f38f`: 238 Python tests pass (1 skipped); 110 vitest pass; `tsc`
and esbuild clean. Review-3 probes: 28 pass, 5 fail, of which 3 are probe-API drift
(§B).

**Verdict.** The calculus underneath is in good shape: a 40-seed fuzz of text ops,
splits, merges and undo agrees with a prefix replay at every position (`W5b`), and
every review-3 fix except the live half of F2 holds. The editor on top of it does
not yet keep the promise `09-editor.md` §4 makes. The keymap owns Enter, Backspace
and Delete only when the selection is empty; with a selection, or on a paste of
more than one paragraph, or on select-all, the gesture falls through to
ProseMirror's own commands and the "confined fallback" then records the wrong
thing: it deletes text the person did not delete, re-mints another author's words
as the person's, drops whole blocks with no op, and after Enter-with-selection
leaves two blocks with one `bid` and records the head half as deleted (A1). The
`## ` rule eats the first real character of the paragraph (A2). Hand off and undo
both act on a frontier or a window that predates the ops just flushed (A5, A6). On
the hub, the PR worker never checks a return code, so a failed `gh pr create` is
recorded as a PR that exists and a hand-off whose agent wrote nothing is retried
every fifteen seconds forever (A3), and the sandbox denies every edit because the
SDK sends absolute paths and the scope is relative (A4) — so at `b95f38f` a scoped
hand-off cannot become a PR at all. The timeline's un-apply was inexact for plan
verdicts and invented blocks before they were born (A7); the working tree has since
fixed the daemon half of that, and my fuzz now passes there, but the reader in
`window.ts` still shows a split-off block's present text before the split.

## A. Correctness

Ranked. Each item names the probe(s); the probe bodies are in the two files above,
the decisive lines are quoted here.

### A1. Structural gestures the keymap does not own are recorded wrong (HIGH, open)

`09-editor.md` §4 rule 1: "Structural steps are never inferred from a `ReplaceStep`
… Enter, Backspace-at-start, `## `, Tab, drag and delete are keymap or command
handlers … A `ReplaceStep` whose slice or range crosses a block boundary … is
handled by the one fallback: … a `block.add`/`text.insert` sequence for inserted
blocks and a per-block character comparison for blocks whose inline content
changed."

What the code does: `commands.ts:45,90,102` return `null` whenever the selection is
not empty, so `editor.ts:195`'s `Enter`, `Backspace` and `Delete` return `false` and
the transaction falls to `keymap(baseKeymap)` (`editor.ts:249`). `record()`
(`editor.ts:122-141`) then reconciles only blocks that are *still in the final doc*
and have a shadow; it can neither add a block nor notice one that left. Four
everyday gestures:

- **T1/T1b — Enter with a selection** ("he|llo| world", Enter). ProseMirror's
  `splitBlock` copies the node's attrs, so the doc is `[heading:h, paragraph:p,
  paragraph:p]` — two blocks with one `bid`. The fallback reconciles the *last* node
  with that bid (" world") against the shadow ("hello world") and records
  `text.delete "llo"` + `text.delete "he"`. The store now reads " world"; "he" is on
  screen until the next projection, then gone. Text loss.
- **T2 — a selection from one paragraph into the next, then typing** ("one |two" …
  "three| four", type X). Block `b` leaves the doc with no op; " four" is re-minted as
  the person's `text.insert`; on the next adopt `b` is resurrected in full, so the
  words " four" now exist twice and the second copy is attributed to the person.
- **T3 — select-all, Backspace.** Blocks `p` and `h2` vanish with no op (`02-workflows.md:49`
  says "the same per feature, one batch"); the first heading's title is recorded as
  deleted; the next projection brings the rest back.
- **T5/T5b — paste of two paragraphs at "head |tail"**. The second paragraph lands
  with `bid: null` and no op (the next adopt removes it); the fallback records
  `text.delete [s:p,5..8]` = "tail" and `text.insert "first"`. The person's "tail" is
  deleted from the store by a paste. Text loss; §4's "`block.add` per paragraph"
  is not implemented anywhere (there is no `handlePaste`).

Pasteable: `editor.probe.test.ts` T1, T1b, T2, T3, T5, T5b. The one that matters
most:

```ts
const tr = state.tr.replaceSelection(new Slice(Fragment.from(paras), 1, 1));  // paste <p>first</p><p>second</p> at "head |tail"
const ops = recordLikeEditor(tr, shadows);                                     // editor.ts record() verbatim
expect(ops.filter(o => o.kind === 'text.delete').length).toBe(0);              // records: delete "tail"
expect(ops.some(o => o.kind === 'block.add')).toBe(true);                      // records: nothing for paragraph 2
```

Fix (one place, by construction): a `filterTransaction` like the `kindCount` one at
`editor.ts:248` that refuses any non-`RECORDED` transaction whose top-level block
count, block ids or `null`/duplicate `bid`s differ from the previous state. That
makes this whole class impossible rather than patched gesture by gesture. Then own
the three gestures: Enter/Backspace/Delete with a non-empty selection delete the
selection through the Recorder per block (or refuse when it spans blocks, the way
the heading boundary already does), then split/merge; `handlePaste` turns a
multi-block slice into `block.add` + `text.insert` per paragraph (spec §4 table).
`via: "fallback"` on the batch (§4) is also missing.

### A2. The `## ` input rule deletes one character too many (HIGH, open)

`editor.ts:233-236`: `n = match[0].length` and `g.tr.delete(b.before(1)+1,
b.before(1)+1+n)`, `sh.delete(0, n)`. prosemirror-inputrules calls the handler with
the state *before* the typed character is inserted and a `match` over `textBefore +
text`, so `"## "` is 3 while the doc holds `"##"`. With the caret at the start of an
existing paragraph "abc", typing `## ` yields the heading "bc" and a `text.delete`
covering `[s:p,0..2]`: "a" is gone from the store. In an empty paragraph the extra
position happens to be clamped by `replace`'s fitting (T6b passes), which is why
the shipped test did not see it. `makeHeading` from the `/` menu (`menus.ts:117`)
counts correctly; only the input rule is off.

Pasteable: T6a (`expect(g.tr.doc.child(0).textContent).toBe('abc')` → `'bc'`).
Fix: `const n = match[1].length;`.

### A3. The PR worker checks no return code (HIGH, open)

`worker.py:95-112` and `_run` (`:115`) return the exit code and nobody reads it.

- **P1 — `gh pr create` fails** (not authenticated, no push right, PR already
  open): `realize_one` returns `Result(True, branch)` (`:112`) and `process_ready`
  records the hand-off as done (`:136-137`). No PR exists and none will: the ledger
  says it does.
- **P2 — `git worktree add -b` fails** (branch or directory already exists from an
  earlier attempt): the agent is run anyway (`:100`) in a directory git refused to
  create; `changed_files` then raises `FileNotFoundError` from `subprocess.run(cwd=…)`,
  caught at `:128` and printed as a warning — every fifteen seconds, for ever.
- **P3 — the agent writes nothing**: `Result(False, …, "no changes produced")` (`:105`)
  is printed (`:146`) and not recorded; `_cleanup` (`:151`) removes the worktree but
  never the branch, so the next interval hits P2. An unrealizable hand-off is one
  agent run, then an infinite warning loop.
- **P6 — `git worktree add` runs with `cwd=None`** (`:99`) — the hub process's cwd,
  not `root_dir`; `codoc serve --root X` from elsewhere creates the worktree in the
  wrong repository or fails, silently, into P2.
- **Withdrawn hand-off mid-run**: `pending` re-reads a fresh store per interval, but a
  run in flight completes and opens the PR for a hand-off that no longer exists;
  nothing cancels it and the PR body does not know. Design residual; note it in
  `serve-deployment.md`.
- **Two PRs for one hand-off**: the ledger is `cache/hub-prs.json` (ignored,
  per-checkout). One hub per checkout is enforced by `serve.lock`; two checkouts of
  one repo each running a hub open two PRs. Design residual, document it.

Pasteable: `probe_substrate4.py` P1, P2, P3, P6. Fix: `run` raises on non-zero
(`check=True`), every git/gh call gets `cwd`, `_cleanup` also runs
`git branch -D`, and "no changes" is recorded in the ledger with a retry budget
(one retry, then `refused`), so a queue that cannot be built stops costing agent
runs.

### A4. The sandbox denies every scoped edit (HIGH, open)

`sandbox.py:82-83`: `return _posix(path) in {_posix(s) for s in scope}`. The scope is
repo-relative (`worker.py` builds it from `Binding.file`); Claude Code's `Edit` and
`Write` tools send absolute `file_path`s. So for any feature that has bindings, every
edit is refused, the agent produces nothing, and A3's loop begins. At `b95f38f` a
scoped hand-off cannot become a PR. The shipped test
(`test_worker.py:59-64`) uses relative paths only.

Pasteable: P4 (`can_use_tool("Edit", {"file_path": "/srv/wt/c.py"}, scope=["c.py"])`
→ `False`). Fix: `tool_policy(scope, root=worktree)` and compare
`os.path.relpath(path, root)` (refusing paths that resolve outside `root`); the
post-run gate on `git status --porcelain` is already relative and stays as is.

Related, by design but worth stating as the answer to "can a suggester's plan reach
code outside the feature's bindings": **yes, when the feature has none** (P5). A
suggester's accepted ADD plan produces a heading with no bindings; `edit_allowed(…,
scope=None)` allows any non-denylisted path, and the denylist (`sandbox.py:32`) omits
`conftest.py`, `Makefile`, `tox.ini`, `noxfile.py`, `.pre-commit-config.yaml`,
`requirements*.txt`, `Dockerfile`, `.gitlab-ci.yml`, `.gitmodules`, `.gitattributes`,
`.npmrc`. PR review is the only gate. Either extend the denylist or scope an unbound
feature to the directory of its parent's bindings.

### A5. Hand off covers the frontier of the last *published* projection, not the flushed ops (HIGH, open)

`app.ts:192` (`handoffAll`) and `:173` (`buildThread`) call `editor.flush()` and then
submit `feature.handoff` with `covers: projection.frontier`. That frontier is from the
last `projection.json`, which predates the batch just flushed. The daemon honours an
explicit `covers` (`apply.py:873`), so the newest typing is not in the hand-off: the
feature stays `edited`, the directive omits the last words, and ⌘S has to be pressed
twice.

Pasteable: H1 — insert + hand-off in one inbox, `covers` = the pre-insert frontier →
`phase == "edited"`, `"Twice." not in directive`. Fix: send no `covers` from the
editor and the hub (the daemon uses `store.frontier` at apply time, which includes
everything drained before it from the same inbox); `01-calculus.md` already says
"`covers` defaults to the store frontier".

### A6. Undo right after typing inverts the previous batch (MEDIUM-HIGH, open)

`app.ts:203-206`: `undo()` flushes and immediately asks the host for history;
`provider.ts:112` reads `history.json` as it is. The daemon has not drained the
flushed batch, so `nextUndo` picks the batch before it. Typing a word and pressing
⌘Z undoes the edit before the word (the word stays).

Pasteable: H2 (`mine[-1]["batch"] == "t-new"` → `"t-old"`). Fix: defer the undo until
the block's `unacked` set is empty (the webview already tracks it), or have the host
wait for the flushed cids' acks before reading `history.json`.

### A7. The timeline window: verdicts, restores, and blocks born inside the window (MEDIUM-HIGH; daemon half closed in the working tree, reader half open)

At `b95f38f`, `window.py:62-63` ("Anything not listed leaves text alone") covered
text ops, split and merge only:

- **W1** — `plan.accept` tombstones the words under `suggest_del`; un-apply ignored it,
  so every earlier position showed the struck words missing.
- **W2** — `plan.reject` kills the proposal's inserts; earlier positions never showed
  the proposal.
- **W4** — a `text.restore` with no `by` was re-killed as `"view"` (`:76`), so the
  original delete's `revive(by=o.id)` (`:72`) no longer matched: "ac" where "abc" was.
- **W3/W3b** — `_touched` for `block.add` (`:55`) and `block.split` clones the new
  block, so `before` reports it as `""`: an empty phantom paragraph at every position
  before it existed. `textAt`'s `null` branch (`window.ts:67`) is unreachable for
  those because `bid in w.before` (`:64`) wins.
- **W5** fuzz (plans, deletes, restores, verdicts): 24/40 seeds disagreed with a
  prefix replay. **W5b** (text, split, merge, undo only): 0/40 — the claimed subset was
  exact.

The uncommitted `window.py` un-applies verdicts from their recorded
`killed`/`struck`, revives unconditionally on `text.delete` (correct, since every
later kill is already un-applied), and drops born blocks from `before`. W1–W5 pass
on it. Two things remain open:

- **T8b** — `textAt` (`window.ts:67`) only knows `block.add` as a birth. A block
  split off inside the window is no longer in `before`, has no earlier entry, and so
  `textAt` returns `live` — the block's *present* text, shown at a position before
  the split created it. That is text at a time it did not exist. Fix: the daemon
  publishes `born: [bid…]` (it already computes the set) and `textAt` returns `null`
  for any born block at a position before its first entry; or the reader treats
  `block.split`'s `new` like `block.add`.
- **Dead blocks** — `Timeline.show` iterates `proj.blocks` (`timeline.ts:62`), the live
  set, so a paragraph deleted or merged away inside the window is absent from every
  past page, though the window carries its text. The scrubber should walk the union
  of live blocks and blocks named in the window, in the past rank order (the window
  would need to carry `rank` for born/dead blocks).

Pasteable: W1–W5b (Python), T8, T8b (vitest).

### A8. Decomposed input desynchronises the shadow from the store (MEDIUM, open)

`recorder.ts` counts scalars (`Array.from`) and never normalises; `apply.py:103` folds
the insert to NFC. `"é"` is two scalars to the shadow and one to the store, so
the next character the person types is anchored at `["$cid", cid, 1]`, which was
never minted: `unknown_anchor`, a conflict ack, the block is re-adopted and the
keystroke is lost. macOS produces NFD in filenames and some pastes; Korean and
Vietnamese IMEs can commit decomposed sequences.

Pasteable: N1 (`[None, 'unknown_anchor']`). Fix: normalise in one place on the
client — `sliceText` and `reconcile` return `text.normalize('NFC')` and the shadow is
advanced with the normalised scalars — and have the daemon refuse (not fold) a
non-NFC insert so the two can never disagree silently.

### A9. Comment blocks sit in the flow the editor hides (MEDIUM; C1 closed in the working tree, C2 open)

- **C1** — `flowBlocks` (`adopt.ts`) filters comment blocks out of the document, and
  `newThreadOps` (`comments.ts:50`) adds the comment block `after: anchor`. Two
  paragraphs the editor shows as adjacent have a comment block between them in the
  store, so `block.merge` (`commands.ts:123`) conflicts `not_adjacent`
  (`apply.py:360`): Backspace at the start of the paragraph after any commented
  paragraph silently fails and re-adopts. Closed in the working tree (`store.py`/`doc.py`
  changed; C1 passes).
- **C2** — after the anchor paragraph is merged away, the comment mark travels with
  the characters (correct) but the block's `anchor` field still names the dead
  block. `Margin.layout` finds no `[data-bid]`, the card hangs at the floor, and
  `buildThread`'s `featureOfBlock(t.anchor)` returns `null`, so **Build it does
  nothing**, silently. The same happens to a hub contributor's thread when the
  maintainer merges or splits the paragraph. Fix: derive the anchor block from the
  mark's live holder in `threadsOf` (the projection has the `comment{block}` span),
  and keep `anchor` only as a hint.

### A10. Kind codecs are declared and never called (MEDIUM, closed in the working tree)

At `b95f38f` nothing in `codoc/` calls `Kind.lower`, `Kind.consult`, `Kind.view` or
`write_back`; `directive()` (`derive.py:320`) renders only the heading and paragraph
blocks (`derive.py:95`). So `03-extensions.md` §5.1's "`lower` … run when a hand-off
renders" and "`consult` … what the realizing agent reads" were untrue: a drawn
diagram edge, a `url`/`pdf` excerpt, a screenshot and a comment never reached
`realize.md`; a comment rode only in `asked` via Build it. K4 passes on the
working tree (the codecs now reach the directive). Check when reviewing that
commit: `comment_lower` (`builtins.py:203`) returns `None` for `scope == "both"`, so
a "Comment + prose" thread still lowers nothing.

### A11. Authority: a GitHub login can be the local owner, and a later role change rewrites history (MEDIUM; S1 closed in the working tree, S3 open)

- **S1** — `client_name("local")` → `hub-local` → `actor_for` (`log.py:45`) →
  `human:local`, which `Roles.load` (`workspace.py:58`) makes `owner`. A read-only
  collaborator whose GitHub login is `local` (or `import`) is stamped as the
  maintainer; and `record_role` (`auth.py:91`) writes `human:local: suggest` into the
  tracked config, demoting the actual maintainer's own edits to `authority`
  conflicts. Closed in the working tree (`auth.py`/`log.py` changed; S1 passes).
- **S3** — roles live in tracked config (review 3 F7) and `_replay_one`
  (`workspace.py:140`) re-runs the policy on replay. Demote `alice` from
  `maintainer` to `suggest` after her `plan.accept` applied, rebuild, and the accept
  is recorded as `authority` and the plan is open again: I6 ("replay reproduces the
  recorded state") is broken by an edit to `config.json`. Every clone that pulls the
  config change disagrees with the daemon that applied the op. Fix: replay must not
  evaluate policy — apply a recorded op verbatim (a recorded `conflict` is replayed
  as that conflict, a recorded success without the policy check); the policy runs
  once, at first apply. That also removes the dependency review 3 A10 flagged.

### A12. The hub's ack feed loses acks across an SSE reconnect (MEDIUM, closed in the working tree)

`push.py:55`: a new `Feed` starts reading the ack file at its current end.
EventSource reconnects on its own after any blip; every ack written in the gap is
never delivered, the cids stay in `unacked`, the block is held forever (no adoption)
and its ink pulses "unsent" for the rest of the session. `push.py` changed in the
working tree and S2 passes.

### A13. `client.write` without a daemon opens a new session log per call (MEDIUM, closed in the working tree)

`client.py:37,83`: each daemonless MCP write opens `Workspace(codoc_dir)`, which mints
a new session and writes `log/<session>.jsonl` (K3: three writes, three files). Passes
on the working tree.

### A14. `nextUndo`'s window-edge guard has a hole (LOW-MEDIUM, open)

`window.ts:50` skips a batch only when its first *in-window* op sits exactly at
`w.start`. A batch with ops before and after the window edge whose first in-window
op is not at `w.start` (another actor holds that slot) is undone from the inside
half only (T7). Since the window is the last 200 ops this is rare, but it is exactly
the "straddles the window edge" case the guard exists for. Fix: the daemon knows
each batch's first position; publish `batch_start` per entry (or a `complete: bool`)
and drop the heuristic.

### A15. Small editor invariants (LOW, open)

- `deleteFeature` deletes the blocks' shadows (`commands.ts:252`) *before* the "never
  empty the tree" bail-out (`:253`); when it bails, the blocks stay but have no shadow,
  and typing in them is never recorded (the recorder returns `false`, the fallback
  skips blocks without a shadow).
- `adopt()` computes `adoptionPlan` (`editor.ts:294`) and uses it only as a boolean
  (`:295`); it then rebuilds the doc from `flowBlocks(next)` (`:304`), which drops a
  held block that left the projection — `adoptionPlan`'s "a held block keeps its
  content" is tested but not what runs.
- The caret is restored by UTF-16 offset (`editor.ts:299,318`), not "by character
  identity" (§6); a replaced block whose text changed before the caret moves it.
- `Recorder.mark` ignores `comment`, `plan` and `suggest_del` (correct), but a typed
  character inside a `suggest_del` span carries the mark in the doc until the next
  adopt; cosmetic.
- `consult.py:9` documents that redirects must be disabled by the caller; the caller
  is Claude Code's `WebFetch`, which follows them. An allow-listed host with an open
  redirect defeats the SSRF gate. Documented residual; say so in `serve-deployment.md`.

### A16. Confirmed fine (probed, no fault)

- T4: a single-paragraph paste with bold and a link arrives as a closed inline slice
  after `replaceRange` fitting; the Recorder records the insert and both marks.
- T6b: `## ` in an otherwise empty paragraph does not reach the next block.
- K1: `lift_pass` is idempotent on a diagram whose node label contains `[`/`]`.
- K2: export → import round trip keeps a multiline `source` and an empty `caption`,
  and re-exports identically.
- W5b: window vs prefix replay over text ops, splits (including `new_first`), merges
  in both orders, marks and undo — exact, 40 seeds.
- Hub CSRF (custom header + `SameSite=Lax`), the session gate on `/api/events`,
  `/api/message`, `/api/media` and the shell, the OAuth `state` cookie with
  `compare_digest`, `/api/media` traversal (`resolve()` + `parents`), `set-language`
  maintainer-only, the per-login rate limiter on writes, `client_name` sanitisation
  for any real GitHub login (alnum + hyphen; the collision in A11 is the built-in
  names, not injection). `/assets` is unauthenticated — it is the bundle, fine.
- A suggester riding another actor's plan, submitting `ingest` without a plan,
  `plan.accept`, `feature.handoff` or `feature.realized`: all `authority` conflicts
  (`test_hub.py:112` covers the flow); a client-supplied `caused_by` is inert
  (`settle` reads the reflect pass's own ops).
- VS Code and the hub on one checkout: separate inbox files, one drain, verified by
  the shipped test; the daemon re-reads roles per pass.
- A kind block in a deleted feature dies with the body (`body_ids`) and is revived by
  `block.restore`; a `translate` while a thread is open leaves the thread's block and
  loses the anchored words' mark (the mark's characters die) — the card hangs at the
  block top with a quote of words that no longer exist. Degrades honestly; note it.

## B. Review-3 items: what holds

Re-run of `probe_substrate3*.py` (28 pass, 5 fail) plus API-adjusted re-checks:

| # | change | status |
|---|---|---|
| F1 | ingest: insert inside any mark gets an `off` | holds (P1, P1b, P1d pass) |
| F2 | plan marks are proposals | **partial.** The export and the directive keep the person's bold (`"**once**" in export_markdown`), `plan_claims` reports `unmarked`. But the live projection's `spans` for the block are `[]` (P3's first assertion): the person sees their bold gone while the plan is open, with a dashed decoration saying so. Consistent with how plan inserts show, and the editor paints it — so acceptable if the spec says it (`01` §3 now does). Record it as the live-doc behaviour rather than a fault |
| F3 | `_causes` cites every uncovered hand-off | holds (P4) |
| F4 | bindings die with their heading | holds (P6–P8) |
| F5 | inbox totality, per-line ack, `_minted` seeded | holds (P20–P24) |
| F6 | publish key on HEAD change | holds; `Publisher.invalidate()` on rotate |
| F7 | roles in tracked config | holds as stated (P5 passes once the probe writes roles to `config.json` instead of `Roles({...})` in memory). New residuals it creates: A11/S3 (a role change rewrites history on replay) and A11/S1 (built-in `human:local`/`human:import` collide with logins) |
| F8 | torn tail keeps the prefix | holds |
| F9 | translate skips features with an open plan | holds (plan's words and status untouched; P17 as written expected a translation and now fails for the right reason). Cosmetic: the skipped feature is counted as "already in the language" (`translate.py:93-94`), not as skipped |
| F10 | `inline_md` escapes | holds (P2, P2d) |
| F11 | history carries verdicts; no-op delete touches holder; refreshed binding restored; `set_mark` keeps placer | holds (P10, P14–P16) |
| F12 | incremental publish | deferred then; **now landed** in the three later commits (`Publisher` with per-block/per-feature memo). Not reviewed here beyond noting `history_window` is recomputed on every publish (200 ops, cheap) |
| F13 | cleanliness | partial: `demote_to_plan` (`ingest.py:62`) is still a dead field; `MACHINE_KINDS` and `REFUSING_KINDS` remain two tables; A17's "machine may ride a stranger's plan" (P25) was not in the list and still fails |

## C. Cleanliness and performance

- `record()`'s fallback (`editor.ts:127-141`) maps each unhandled step's ranges
  through *that step's* map and then reads `tr.doc` (the final doc) — positions from
  an earlier step are not mapped through later steps. Harmless today because
  unhandled steps arrive alone; wrong the day two arrive together.
- `Recorder.replace` records the delete before advancing the shadow and the insert
  after — correct — but `queue()` pairs `cids` to inserts by order (`editor.ts:151-154`);
  a `reconcile` op list interleaved with recorder ops keeps the invariant only because
  both emit inserts in order. Fragile; return `{op, cid}` pairs.
- `paintMap` recomputes paints for **every** block on every `docChanged` transaction
  (`editor.ts:97-110`); at the perf3 scale (1,500 features) that is tens of thousands
  of runs per keystroke. Memoize per block on `(head, unacked size, history flag)`.
- `adopt()` rebuilds the whole document as one `replaceWith(0, size)` on any plan;
  decorations and `findMatches` then run over the whole doc. Per-block `replaceWith`
  on the planned steps would keep adoption O(changed blocks) and stop the caret
  restore from being a separate pass.
- `Shadow.resolve` runs over every shadow per ack (`editor.ts:180`); index acks by the
  block the cid was queued under (`unacked` already has it).
- Hub: `Feed.pending` reads `projection.json` in full every 0.5 s per connection and
  compares strings; ten viewers of a 2 MB projection is 40 MB/s of reads. Compare
  mtime+size first. `Worker` replays the whole log every 15 s (`client.read`); on
  perf3's 13.5k ops that is seconds of CPU per interval — read `status.json`'s `head`
  and skip when unchanged.
- `serve/app.py` `set-language` writes `config.json` with `write_text` while
  `record_role` uses the atomic `write_config`; the daemon reads the config on every
  inbox pass and a torn read degrades every role to default for that pass.
- Dead or misnamed: `scalarCaret` returns a UTF-16 `parentOffset`; `editor.ts` has a
  `binds` local computed and unused at `deleteFeature`; `adoptionPlan` steps unused
  (A15); `Recorded.cids` vs `queue`'s `ci` counter; `LOCAL = Session("local", …)` in
  `app.py` shares the `local` name space with A11.
- The `## ` rule and `makeHeading` compute the marker length in two places with two
  conventions (A2). One helper.

## D. Spec ↔ code

| spec | says | code |
|---|---|---|
| `09-editor.md` §4 rule 1 | fallback emits `block.add`/`text.insert` for inserted blocks; batch marked `via: "fallback"` | neither: no block ops from the fallback, no `via` (A1) |
| `09-editor.md` §4 table | paste of several paragraphs → `block.add` per paragraph | no `handlePaste`; PM's default paste (A1/T5) |
| `02-workflows.md:49` | select-all, Delete → one batch per feature; refused only if it would empty the tree | blocks vanish with no op (A1/T3) |
| `02-workflows.md:43` | `## ` at the end of a populated paragraph → split + set kind | the rule matches only hashes at the block start; mid-paragraph `## ` is prose |
| `09-editor.md` §6 | caret mapped through the replacement by character identity | by UTF-16 offset (A15) |
| `09-editor.md` §6 | held blocks left alone | a held block absent from the projection is dropped (A15) |
| `09-editor.md` §4 | undo = inverses of the viewer's newest un-undone batch | of the newest batch *the daemon had published*; the just-flushed one is skipped (A6) |
| `09-editor.md` §7, `01` §6 `timeline` | un-apply exact per block | plan verdicts, `restore` without `by`, born blocks (A7; daemon half fixed in the working tree, reader half open) |
| `03-extensions.md` §5.1 | `lower` runs when a hand-off renders; `consult` is what the agent reads | never called at `b95f38f` (A10; closed in the working tree) |
| `03-extensions.md` §4 | a comment anchored by a mark on the words | the block's `anchor` field is the anchor the UI uses; the mark is ignored when they disagree (A9/C2) |
| `01-calculus.md` §3 `feature.handoff` | `covers` defaults to the store frontier | the editor always sends an explicit, stale one (A5) |
| `01-calculus.md` §7.3 / I6 | replay reproduces the recorded state | policy re-evaluated on replay against today's roles (A11/S3) |
| `01-calculus.md` §7.4 | provisional anchors resolve against acks | true, but NFC folding on the daemon side makes the client's `k` wrong (A8) |
| `serve-deployment.md` / `worker.py` docstring | "a queue that stays queued does not open a second one" | true per checkout; false across two checkouts, and a failed `gh` is a phantom "done" (A3) |
| `sandbox.py` docstring | Edit/Write must be inside the directive's scope | a scoped feature can never be edited: absolute vs relative (A4) |

## E. Ranked changes

1. **Make the keymap own every structural gesture and refuse the rest by
   construction** (A1): a block-identity `filterTransaction` (count, ids, no null or
   duplicate `bid`) next to the `kindCount` one; Enter/Backspace/Delete with a
   selection go through the Recorder per block then split/merge (or refuse across
   blocks); `handlePaste` → `block.add` + `text.insert` per paragraph; `via:
   "fallback"` on fallback batches. Put T1–T3, T5, T5b in the suite. ~80 lines.
2. **`## ` rule: `n = match[1].length`** (A2). 1 line; T6a into the suite.
3. **Worker: check every exit code, `cwd=root_dir`, delete the branch on cleanup,
   record "no changes" with a retry budget** (A3). ~15 lines; P1–P3, P6.
4. **Sandbox: scope check relative to the worktree root** (A4). ~6 lines; P4. Then
   decide the unbound-feature scope and extend the denylist (P5).
5. **Drop `covers` from the editor's and hub's hand-offs** (A5). 2 lines; H1.
6. **Undo waits for the flushed batch's acks** (A6). ~10 lines in `app.ts`; H2.
7. **Timeline reader: born blocks (`split` too) are `null` before birth; the past page
   walks live ∪ window blocks** (A7). Daemon publishes `born` (it has the set) and
   `rank` for window blocks. ~20 lines; T8b. Keep the W5 fuzz in `tests/calculus`.
8. **Replay applies recorded ops without policy** (A11/S3). ~6 lines in
   `_replay_one`; S3. Update `01` §7.3.
9. **NFC on the client, refuse-not-fold on the daemon** (A8). ~6 lines; N1.
10. **Thread anchor from the mark's live holder** (A9/C2). ~10 lines in `threadsOf`.
11. **Commit and review the working-tree fixes** for W1–W5, C1, K3, K4, S1, S2 (they
    pass my probes; the four shipped tests failing on that tree should pass before
    it lands), and keep the review-4 probes beside review 3's.
12. **`nextUndo`: publish batch completeness, drop the edge heuristic** (A14).
13. Cleanliness and performance items in §C: per-block paints memo, per-block adopt,
    `Feed` mtime check, worker skip on unchanged head, atomic `set-language`,
    `demote_to_plan` and the unused locals, one marker-length helper.
