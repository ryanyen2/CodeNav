# Edit-Tracking Engine — Action-Sequence Tree + Cohesive Architecture

**Date:** 2026-08-07
**Status:** analysis + architecture proposal (no code changed in this pass)
**Method:** four independent code deep-reads (client capture engine, host settle
protocol, daemon apply/merge, test-coverage audit) + a mining pass over the six
prior patch generations. Every claim below carries a `file:line` reference and
was verified against the working tree, not the docs.

**The problem in one sentence:** the engine was built and tested around
*batch-shaped* edits (a settled doc arrives, a diff is computed, commands apply),
but real editing is a *timeline* — char-by-char typing, undo, cosmetic layout
keystrokes, selections, IME, all interleaved with daemon renders and agent
edits — and the engine re-derives "what changed" from snapshots at several
independent places instead of recording what happened once, so every timing gap
between those snapshots is a place where content is silently lost, silently
reverted, mis-attributed, or wedged.

---

## Part 0 — Why patching plateaued: the six generations

| Gen | Date | Fixed | Mechanism added | Left untouched |
|---|---|---|---|---|
| G1 | 06-16 | agent→human change display | vendored track-changes engine (marks) | user-side diff (led to G2) |
| G2 | 06-18 | suggest-mode jank | derived `changedRange` vs stable baseline; draft/hand-off gate | baseline lifecycle under concurrency |
| G3 | 06-25 | 44 attacks (zombie clones, undo double-mint, …) | declared identity (`local_id`-keyed diff), one loop lock, held-draft model | content-change fidelity still snapshot-diffed |
| G4 | 06-26 | dual-source-of-truth divergence | store-authoritative projection + identity-keyed commands + HLC gate + ledger | gate is whole-slice LWW; `base_rev` never enforced; R8/R10 (marks/drafts in store) never delivered |
| G5 | 08-01 | retire-by-absence; geometric prose attribution | I1 explicit retire; I2 `ownerId`; sequence fuzzer | net-no-op detection; changed-region fidelity; user↔agent overlap |
| G6 | 08-03 | misplaced underlines | display-space diff + `alignParas` | still a re-derivation; W5 backlog open |

The pattern: each generation repairs one *symptom* of the same root design —
facts are **re-derived from state snapshots** (settle diffs, decoration
re-diffs, presence checks) instead of **recorded at the moment they happen**
(the ProseMirror transaction). G3 named the right principle — *"Loop B reads
facts the webview already declares, instead of re-inferring"* — and applied it
to identity and lifecycle only. Content change, changed-region display, and
authorship still each have their own re-derivation engine:

1. `commandsForSettle` — settled doc vs cited-baseline projection → commands
   (`vscode-codoc/src/state/commands-from-doc.ts:149-203`)
2. `capturedFids`/`blockDiffSpans` — live doc vs `capturedBaseline` → underlines
   (`vscode-codoc/src/webview/tiptap/captured-decorations.ts:93-225`)
3. `hold-decorations` `changedRange` — a third diff, single-region
   (`hold-decorations.ts:51-65`)
4. `applyAgentProposals` — index-aligned paragraph diff → agent ins/del marks
   (`vscode-codoc/src/state/agent-proposals.ts:76-107`)
5. The vendored track-changes engine — schema marks only; every behavioral
   entry point is dead for users (`schema.ts:55` hardcodes `mode: 'edit'`;
   all `suggest-mode-plugin.ts` handlers gate out; zero callers of
   accept/reject/setSuggestMode outside the vendored dir)

Five change representations, four baseline concepts, zero shared event log.
That is why patches don't converge: each fix re-aligns two of the five
representations while the timing gaps between the others stay open.

Also load-bearing context: the tests are structurally blind to this. **No test
ever separates a keystroke from a settle.** The sequence fuzzer
(`commands-from-doc.sequence.props.test.ts`) is real and valuable, but models
one transaction == one settle == an instantly-advanced baseline (`:190`), so
the three production timing layers — the 1200 ms debounce
(`whole-doc-editor.ts:157`), the baseline-citation window
(`tree-editor.ts:790-817`), and the async daemon round-trip — do not exist
anywhere in the suite. There is also no convergence property: nothing applies
the emitted commands to a store and asserts the re-projection equals what the
user typed.

---

## Part 1 — The action-sequence tree

Organized as six branches of one realistic session timeline. Each leaf:
**sequence → broken assumption (evidence) → consequence**, tagged with a
severity class:

- **LOSS** — user content destroyed without trace
- **REVERT** — someone's persisted edit undone as if intended
- **ATTR** — wrong author or wrong feature attributed
- **PHANTOM** — state shown or applied that isn't real (dup apply, ghost UI)
- **STUCK** — a permanent bad state requiring manual surgery
- **CHURN** — event/directive/command noise (incl. unbounded loops)

### T1 — Solo prose editing (one feature, no concurrency yet)

- **T1.1 Sustained typing, then close the tab.** The settle debounce is
  trailing-edge with no max-wait (`whole-doc-editor.ts:157,414-418`); continuous
  typing with <1200 ms gaps never settles; `destroy()` clears the timer and
  never flushes (`:1346`); `isDirty` has no consumer anywhere. Closing the
  panel, a webview dispose, or a doc-less payload remount
  (`doc-view.ts:931,661-663`) discards the whole burst. The user saw "saved"
  labels meanwhile — `markSaving('saved')` fires with no host ack (`:429`).
  **LOSS.**
- **T1.2 Type a sentence → pause (settle) → Cmd-Z → pause (settle).** The
  underline clears (content-equality re-diff) — but: (a) `pendingFids` is only
  ever added on edit (`:332`) and removed on projection-adopt (`:1180`); undo
  does not remove it; (b) the first settle emitted a `set_description`, marked
  a draft, and minted a directive (AMEND always mints,
  `codoc/loop/classify.py:82-83`); the second settle emits the inverse
  command. Net result of "no change": two events in the blame ledger, directive
  churn, and — because a held draft is released only by hand-off and
  `hold_set` includes every manifest directive regardless of `handed_off`
  (`codoc/loop/edits.py:843-856,757`) — **the feature is now held forever**,
  which silently suppresses Loop A's AMEND/RETIRE/MOVE proposals and its drift
  badge on it (`loop_a.py:322-325,585-592,726-728`). An undone edit
  permanently stops code-tracking for that feature. **STUCK + CHURN.**
- **T1.3 Select-all → delete → think → retype.** If the pause after the delete
  exceeds 1200 ms, the intermediate empty state settles: `set_description('')`
  applies, a directive for the emptiness is minted, and only then the retype
  arrives as a second command. The agent can start realizing the transient.
  **CHURN + wrong-directive window.**
- **T1.4 Select a phrase → delete → retype (different words).** Display only:
  a `del` run adjacent to an `ins` is suppressed as "replacement"
  (`captured-decorations.ts:137`), so a total rewrite renders as a pure
  addition; the reviewer cannot see what was removed. **ATTR (display).**
- **T1.5 Enter mid-sentence to re-wrap lines (cosmetic layout).** The split
  becomes a real content change: paragraphs serialize joined by `\n\n`
  (`pm-doc.ts:241-247`), so layout intent becomes a `set_description` and a
  minted directive. Meanwhile `alignParas` pairs the two new halves against one
  baseline paragraph only above similarity 0.5 (`display-text.ts:77-95`) —
  short halves fall below it and draw phantom whole-paragraph underlines.
  **CHURN + ATTR (display).**
- **T1.6 Shift+Enter (hardBreak).** One-way mapping: `hardBreak → '\n'`
  (`pm-doc.ts:176`) has no inverse (`textToInlineRuns` never emits one;
  `descriptionToBlocks` splits only on `\n{2,}`, `pm-doc.ts:207-213`). The
  break cannot round-trip; depending on the daemon echo this is either silent
  flattening or a normalization-drift re-emit seed (see T4.1). Untested on
  both sides. **CHURN.**
- **T1.7 Backspace at the start of a description's first paragraph.** No
  handler exists on the live path; default `joinBackward` merges the paragraph
  **into the feature title** (`featureHeading` and `paragraph` are both
  `content: 'inline*'`; `defining: true` does not block the join). The settle
  then emits a corrupted `set_title` plus a description that lost its first
  paragraph. `commands-from-doc.ts:16-20` names this hazard and defends only
  against phantom retires. The single most common boundary keystroke corrupts
  the title. **LOSS + ATTR.**
- **T1.8 Cmd+Backspace on a heading line.** Empty title survives client-side,
  but `apply_op` drops empty titles (`apply.py:105-106`) — a title clear is
  unappliable — so doc and store diverge and re-diff every pass. **CHURN.**
- **T1.9 IME composition (CJK).** No `view.composing` guard exists on
  `setDoc`, `patchMintedIds`, or any `set*` metadata dispatch; only the
  comment composer/bubble defer projections (`whole-doc-editor.ts:1166`). A
  payload landing mid-composition force-flushes the DOM and aborts the
  composition — dropped or duplicated characters. **LOSS.**
- **T1.10 Edit prose whose `ownerId` differs from the nearest heading above.**
  The version gate's protection is keyed by `activeFid()` — a positional
  caret heuristic (`whole-doc-editor.ts:360-374`) — while attribution is by
  `ownerId` (`commands-from-doc.ts:109-111`). The wrong feature is marked
  pending; the actually-edited feature is unprotected, and the very next
  payload (including the host's own post-settle repost, `tree-editor.ts:202`)
  reverts the visible edit. Same for any multi-feature edit (select-all
  delete, cross-feature paste, subtree Tab): exactly one fid is marked.
  **REVERT.**

### T2 — Structure gestures

- **T2.1 Delete a heading to delete the feature.** By design (I1) absence is a
  no-op — but there is *no visual record the feature was deleted* (it simply
  leaves the captured map, `captured-decorations.ts:93-106`) and the heading
  resurrects on the next projection. The user's gesture is silently discarded
  with no affordance pointing at `~ retire`. **PHANTOM (by design, unsignaled).**
- **T2.2 Retire via `~`, settle fires, then un-toggle.** There is no
  `unretire` command kind (`commands-from-doc.ts:182-185` handles only
  false→true). Stuck retired; recovery requires the daemon side. **STUCK.**
- **T2.3 Paste a copied heading/subtree *above* its original.**
  `uniqueLocalIdPlugin` keeps the *first* occurrence in document order
  (`feature-heading.ts:119-145`) — the paste — and re-mints the original,
  clearing its fid. Identity swap: the original's history now belongs to the
  pasted copy; the original re-mints as a new feature. **ATTR.**
- **T2.4 Paste + ⌘S in the same tick.** The localId-dedup plugin repairs one
  transaction *after* the paste; `commitNow` reads the pre-repair state → two
  `add`s share `c-add-<localId>` → the daemon ledger folds the second
  (`loop_b.py:656-660`) → one pasted feature never exists. **LOSS.**
- **T2.5 Copy a paragraph from feature A, paste under feature B.** It keeps
  `ownerId = A` (`paragraph-owner.ts:48-51` fills nulls only, never remaps) →
  the prose is attributed to A forever, invisible under B. I2's anti-steal
  rule backfires on cross-feature paste. **ATTR.**
- **T2.6 ⌘K "Create feature" ×2 quickly.** This path mints no `localId`
  (`whole-doc-editor.ts:1324-1327`) → dropped from the captured map, unmatchable
  by the exact mint path, so `patchMintedIds` falls back to title-then-order
  guessing (`:744-750`) and can cross-bind the two features' fids. **ATTR.**
- **T2.7 Drag or retype to reorder siblings.** No command kind encodes ordinal
  position; the daemon re-imposes pre-order. The reorder silently reverts on
  the next projection. **REVERT (unsignaled no-op).**
- **T2.8 Tree-pane drag move.** `editMove` (`tree-editor.ts:912-916`) bypasses
  the settle/baseline machinery entirely; the only cycle guard is a check
  against possibly-stale `payload.nodes`; `doc-move.ts` (the guarded transform)
  is dead code on this path. **latent PHANTOM.**
- **T2.9 Type prose above the first heading.** Silently dropped
  (`commands-from-doc.ts:112`), never persisted, deleted by the next
  projection. The props generator deliberately never generates this position
  (`props:183`). **LOSS.**

### T3 — The settle boundary (timing)

- **T3.1 The cited baseline is whatever payload arrived last, not the one the
  doc came from.** `onSettle` reads `payload.baselineId` from a module-global
  at *send* time (`doc-view.ts:41,942,1236`). Any payload arriving during the
  1200 ms debounce that the gate keeps-local (or the composer defers) advances
  the citation without changing the doc. The settle then diffs the user's doc
  against a baseline containing daemon/agent changes the user never saw → it
  emits `set_title`/`set_description` **reverting those changes**, drafts
  them, and mints AMEND directives. **REVERT (of agent work, without user
  intent).**
- **T3.2 Baseline-ring burn.** A new `baselineId` is minted on *every* post
  (`tree-editor.ts:544`), and posts fire on every `state.onDidChange` —
  status.json, activity.json, realize heartbeats
  (`workspace-state.ts:95-105`) — with a ring bound of 16 (`:94`). One realize
  cycle while the user thinks for 20 seconds evicts the true baseline; the
  fallback is *the newest projection* (`commands-from-doc.ts:229-239`), i.e.
  T3.1's revert class. The comment at `commands-from-doc.ts:222-227` claims
  the fallback "can only produce redundant content commands" — **that claim is
  false** in the direction where the projection moved ahead of the user's doc.
  **REVERT.**
- **T3.3 Extension-host restart.** `baselineSeq` resets to 0 while the webview
  (retained: `retainContextWhenHidden`) still cites the old id → permanent
  fallback until the next adopt. **REVERT.**
- **T3.4 Edit a feature the cited baseline doesn't contain.** (Daemon added
  it; projection landed; user typed into it; settle cites the older
  baseline.) `commandsForSettle` silently `continue`s
  (`commands-from-doc.ts:169-175`) — the edit is discarded with no command, no
  diagnostic, no UI signal. Neither property harness can generate this (the
  fuzzer's baseline is always the immediately-prior doc). **LOSS.**
- **T3.5 Debounced settle + ⌘S commit in the same millisecond.** Non-add
  command ids are salted with `Date.now()` (`commands-from-doc.ts:123-125`);
  identical `(kind, fid, ms)` → identical id → the daemon ledger folds the
  second, different-content command as a replay. **LOSS.**
- **T3.6 Interleaved message handlers.** `onDidReceiveMessage` is async with no
  queue (`tree-editor.ts:195`); `emitCommands` awaits per command
  (`:744-746`). A settle, a commit, a move, and a comment op can interleave
  their appends → out-of-order apply → older description applied after newer.
  Separately, `handOff`'s synchronous `set.clear()` racing a settle's
  `setDrafts([...set])` snapshot leaves disk and memory drafts permanently
  disagreeing (`:848-862,733-739`). **REVERT + STUCK.**
- **T3.7 `appendFile` rejection (ENOSPC, EACCES, missing `.codoc`).** No
  try/catch anywhere on the path (`:721-724,744-746,790-817,195`): an
  unhandled rejection, the trailing `post()` never runs, the edit is lost and
  the UI freezes on a stale payload. **LOSS.**
- **T3.8 A crash of the `setDoc` caret-restore.** `suppressUpdate` is cleared
  outside the try, not in a finally (`whole-doc-editor.ts:1204-1220`); an
  exception after the replace leaves it stuck `true` — `onUpdate` permanently
  dead, settle permanently disabled, silently. **STUCK.**

### T4 — User ↔ daemon echo loop

- **T4.1 Normalization drift = infinite loop.** The *real* echo-cancellation
  mechanism is content equality of normalized text
  (TS `normalizeDescription` ≡ Python `normalize_description`); any byte of
  divergence (unicode whitespace, `\r`, a hardBreak, a codeRef-only paragraph)
  re-emits `set_description` on every settle and re-differs on every
  projection, forever, with no circuit breaker. **CHURN-∞.**
- **T4.2 The post-settle repost window.** `settleDoc` immediately reposts the
  *pre-settle* projection with a *new* baselineId (`tree-editor.ts:202`). A
  second settle in that window re-emits the same logical edit with a new salt
  → applied twice → two directives, and each apply bumps `updated_at`, making
  the next projection "strictly newer" and eligible to clobber in-flight
  typing. **CHURN + REVERT window.**
- **T4.3 Ledger-folded command pins the feature forever.** If a command is
  folded (dup id, T3.5, or `local_id` fold), `updated_at` never advances, the
  projection never becomes strictly newer, and `pendingFids` — cleared only on
  adopt (`:1180`) — pins the feature: no future projection can correct it for
  the life of the window. **STUCK.**
- **T4.4 A heading with no `version` attr never adopts.** `'' > ''` is false
  (`doc-gate.ts:54-57,121-163`). Known W5 backlog item. **STUCK.**
- **T4.5 An unreadable/empty projection collapses the doc.**
  `readProjectionDoc` degrades to an empty doc (`tree-editor.ts:751-760`);
  the gate then emits only pending/null-fid slices — every clean feature
  vanishes from the editor. **PHANTOM (catastrophic display).**
- **T4.6 A pass whose commands were all folded/skipped does not render**
  (`loop_b.py:978-985`) — no fresh projection, the webview waits on a stale
  view indefinitely. **STUCK window.**
- **T4.7 Global re-baseline on unrelated updates.** Any adopted projection
  where `sameText` is false re-baselines **globally**
  (`whole-doc-editor.ts:1198`): typing in feature A while the daemon updates
  unrelated feature B silently erases A's captured underlines mid-edit (the
  edit itself survives; its visual record does not). **ATTR (display).**

### T5 — User ↔ agent concurrency (the two-author core)

- **T5.1 Agent lands while user types the same feature — adopt branch.** If
  the projected version is strictly newer, the whole slice is replaced: the
  user's un-settled keystrokes are dropped with no merge and no warning
  (`doc-gate.ts:25-26` documents "There is no merge"); the caret restores by
  absolute position into shifted text. **LOSS ("LLM covers user").**
- **T5.2 Same race — keep branch.** If the user's fid is pending, the agent's
  proposal marks are discarded from display, but `currentSuggestions` still
  lists the amend, so accept/reject affordances anchor to an **invisible
  diff** (`suggestion-decorations.ts:187-191`). **PHANTOM UI.**
- **T5.3 MCP reflect bypasses holds entirely.** `_apply_single`/`reflect`
  (`codoc/mcp/tools.py:310-331,384-436`) never consult `hold_set`;
  `should_auto_apply` auto-applies a ≤30 % AMEND over the human's held,
  in-progress prose; the `updated_at` bump then makes the webview gate adopt
  the overwrite. The classify table claims doc-wins; only Loop A honors it.
  **LOSS ("LLM covers user", second mechanism).**
- **T5.4 User's whole-description command covers the agent's landed amend.**
  `apply_op` is blind last-write-wins (`apply.py:165-179`); `base_rev` is
  declared, persisted, and **read by nothing** (`edits.py:173-184,272,417`;
  zero readers in `codoc/loop/`; `commandsForSettle` never even sets it). With
  T3.1/T3.2 skew this happens *without the user touching the agent's
  sentences*. **REVERT ("user covers LLM").**
- **T5.5 Typing inside an agent `<ins>` span.** The text inherits the
  `insertion` mark (marks are `inclusive:false` but interior insertion
  resolves the enclosing marks); `inlineRunsToText` **excludes**
  insertion-marked runs (`pm-doc.ts:169-179`) → the user's own words are
  silently dropped from `tree.codoc`, uncounted as a change, and destroyed by
  a later reject. Meanwhile the captured diff *draws* agent insertions as the
  user's own additions (`paraDisplayText` does not filter marks;
  `captured-decorations.ts:204,221`). No `filterTransaction` exists anywhere
  in the repo. **LOSS + ATTR.**
- **T5.6 The accept/reject window.** Verdicts are a daemon round trip
  (`doc-view.ts:256-258,944-945`); the engine's local accept/reject is dead
  code. Marks remain in the doc and editable until the echo lands — during
  which T5.5 applies to an already-accepted span. **LOSS window.**
- **T5.7 Agent amend inserts a paragraph mid-description.** Agent marks are
  materialized by raw index alignment (`agent-proposals.ts:76-85`): every
  subsequent paragraph diffs against the wrong one — whole-paragraph
  strike/insert noise. **ATTR (display).**
- **T5.8 User edits a feature whose directive is awaiting_impl.** Supersede
  protects in-flight directives → the feature carries two; `_hold_detail`
  shows the **first** (`phase.py:175-194`) so the in-situ diff cites the wrong
  baseline; the agent implements prose that no longer exists. **CHURN + wrong
  directive.**
- **T5.9 ⌘S then keep typing, merged in one pass.** `handoffs` is a bare fid
  list (`edits.py:557-569`); the pass applies the newer edit first, then hands
  off the **new text the user never confirmed**. **wrong directive.**
- **T5.10 Any never-handed-off edit holds its feature forever** (see T1.2) —
  the quiet failure mode by which features drift out of tracking one by one.
  **STUCK.**
- **T5.11 Command lands on a just-retired feature.** `get_feature` returns
  tombstones (`db.py:316-318`); the guard tests only `is None`
  (`loop_b.py:710-714`) → invisible prose, an invisible directive, a permanent
  hold. **STUCK.**
- **T5.12 Retire + steer in one settle.** Channel fan-out destroys author
  order (`edits.py:300`; fixed phase drain) → the steer resolves after the
  retire and is silently discarded (`loop_b.py:270-271`). **LOSS (note).**
- **T5.13 Undo after an adopted agent echo.** `setDoc`'s
  `replaceWith(0, size)` with `addToHistory:false` collapses history mappings
  — ⌘Z afterwards no-ops or applies at corrupted positions; there is no
  history reset either. Also, undo *re-stamps* author marks with fresh
  timestamps (`author-plugin.ts:57-60`) — provenance is rewritten. **LOSS +
  ATTR.**

### T6 — Crash / multi-process

- **T6.1 Crash after a command's transaction, before `write_manifest`.** The
  prose lands; on replay the ledger short-circuits (`loop_b.py:655-660`)
  *past directive-building* — the directive is lost permanently, silently.
  The idempotency boundary (command) ≠ the intent boundary (settle) ≠ the
  effect boundary (directive, end-of-pass). **LOSS (directive).**
- **T6.2 Crash before step 3 loses every one-shot drain** — annotations,
  cancellations, steers, block edits, handoffs. **LOSS.**
- **T6.3 `.merging` replay double-fires steers/handoffs** (commands alone are
  ledger-protected); a replayed handoff flips a *newly minted* draft to
  handed-off. **PHANTOM.**
- **T6.4 Torn jsonl line.** O_APPEND is atomic only up to platform write
  size; a multi-KB `set_description` from two windows can tear; the daemon
  skips the malformed line *and unlinks the file* (`edits.py:676-684`).
  **LOSS.**
- **T6.5 Two windows.** `setDrafts` is a wholesale snapshot on both sides —
  last window wins; the host's draft mirror survives dispose and re-seeds
  additively (`tree-editor.ts:101,136-138`). **STUCK/PHANTOM.**
- **T6.6 Newer IDE against older daemon.** Unknown `fn` is dropped and
  destroyed, not deferred (`edits.py:654-655,681`). **LOSS.**
- **T6.7 No daemon running.** Appends accumulate unmerged (merged only at
  daemon startup) while the UI says "saved". **LOSS window (misleading ack).**
- **T6.8 Wall-clock HLC regression.** `retire_feature`/`mark_realized`/
  `unretire_feature` stamp `HLC.now()` (`db.py:368,380,391`), not
  `advance()` — a backwards clock jump makes the retire look older and the
  gate refuses to adopt it. **STUCK.**

---

## Part 2 — Root causes

Every leaf above is generated by one of six structural decisions. This is the
map that makes "stop patching" actionable — a patch fixes a leaf; only changing
the generator kills the branch.

- **RC1 — Change is re-derived, not recorded.** ProseMirror hands the client
  steps, authorship-at-dispatch, and composition boundaries for free; the
  client throws them away, then five subsystems reconstruct "what changed"
  from snapshot pairs, each against a different baseline. *(T1.2–T1.6, T3.1–4,
  T4.1–2, T5.4, T5.7, all display leaves.)*
- **RC2 — Baselines are global and mutable; the unit needing a baseline is the
  per-feature pending change.** One `capturedBaseline` map globally replaced;
  one process-local whole-doc `baselineId` counter with a ring of 16, minted
  per post rather than per projection. *(T3.1–3, T4.7.)*
- **RC3 — Concurrency is adopt-or-discard + blind LWW; conflict is never
  *represented*.** The gate swaps whole slices; `apply_op` overwrites;
  `base_rev` is dead; there is no content-conflict surface (the resolution
  surface exists only for realize divergence). *(T5.1–T5.6.)*
- **RC4 — Granularity misalignment: intent per settle, apply per command,
  effect per directive, render per pass.** Crash or replay at any boundary
  loses exactly one layer. *(T3.5–7, T5.9, T6.1–3.)*
- **RC5 — Identity is declared for headings only; heuristic everywhere else.**
  `ownerId` fill-only, doc-order dedup, ⌘K minting nothing, title/order fid
  guessing, caret-positional `activeFid`. *(T1.10, T2.3–2.6.)*
- **RC6 — Authorship is decoration, not data.** Marks re-stamped on undo,
  inherited across span boundaries, drawn wrong by the captured diff; the
  store-persistence of marks/drafts (G4's R8/R10) was never delivered.
  *(T5.5, T5.13.)*

---

## Part 3 — The architecture

Principle: **record at the source, derive every projection from the record,
and represent conflicts instead of resolving them silently.** Each design
decision below carries its own "I might be able to break this if…" test.

### L1 — The client change ledger (kills RC1, RC2)

One ProseMirror plugin owns change tracking. On every transaction it appends
`(steps, author, origin)` where origin ∈ {user-input, system (REFLECT/mint/
migration), agent-materialization}, taken from transaction metadata at
dispatch — never inferred later. Per feature it maintains a **PendingChange**:

```
PendingChange {
  key: fid ?? localId          // rebound on mint ack
  base: { version: HLC, hash: normHash, text }   // the projection slice the
                                                 // edit sequence started from
  change: composed changeset (step-map composition, content-compared)
  seq: monotonic per-feature counter
}
```

- Composition simplifies against content: type-then-undo composes to the empty
  changeset → the PendingChange evaporates → nothing pends, nothing settles,
  nothing holds. Net-no-op is a *structural* fact, not a diff outcome
  (kills T1.2's entire chain).
- **All** decorations (captured, hold, blame overlays) become projections of
  PendingChange spans. `capturedBaseline`, the per-keystroke `doc.toJSON()`
  re-diff, `alignParas`-vs-baseline pairing, and the separate `changedRange`
  die (kills T1.4/T1.5-display, T4.7).
- The base is **per feature**: adopting a projection for feature B never
  touches A's record.

*Break-if:* non-PM mutations (`setDoc` replace, `patchMintedIds`, migrations)
would be recorded as user changes → they are dispatched as origin=system and
excluded by construction; the plugin asserts (dev-mode) that no doc-changing
transaction lacks an origin. *Break-if:* appendTransaction repairs (localId
re-mint, owner fill) pollute the record → they run under system meta already
(`addToHistory:false` sites) and are tagged so. *Break-if:* the changeset
grows unboundedly during a long unsettled session → composition bounds it to
O(changed spans), not O(keystrokes).

### L2 — Projection application is a rebase, not a gate (kills RC3 client-side)

An incoming projection is applied as a **system transaction per feature
slice** (old projected slice → new projected slice), and the feature's
PendingChange plus the undo history are **mapped through it** — the standard
ProseMirror rebase discipline the current code never uses (every decoration
plugin rebuilds; `DecorationSet.map` is never exercised).

- Non-overlapping remote change + local pending → both survive; the user's
  underlines stay put; the caret maps positionally instead of restoring by
  absolute offset (kills T5.1's silent drop, T4.7, the caret jump).
- **Overlapping** remote and local spans → neither side is silently chosen.
  The overlap becomes a first-class **conflict region**: local text stays in
  the doc, the remote text is held as the other lane of the region, and the
  existing direction-colored disagreement grammar renders keep-mine /
  take-theirs. (T5.1/T5.4 stop being data loss and become a visible state.)
- Composition/IME: projections defer on `view.composing`; deferred projections
  **queue** and apply on composition end / composer close — never discarded,
  never kept-only-latest (kills T1.9, the W5 composer-drop).
- Undo history is mapped through the rebase like any pending change — ⌘Z after
  an echo undoes *the user's* steps, not the agent's (kills T5.13).

*Break-if:* the remote change is structural (split/move/reorder) and step-maps
don't span features → rebase is scoped per feature slice; structural remote
ops re-anchor whole PendingChanges by identity key, not by position.
*Break-if:* conflict regions themselves race a second remote update → a
conflict region is itself a PendingChange (it has a base; a newer remote
rebases it like anything else). *Break-if:* the projection is empty/unreadable
→ a projection that would *remove* every clean feature is refused and
surfaced, never applied (kills T4.5).

### L3 — The wire: per-feature change records, acked (kills RC2, RC4 at the boundary)

Settle stops sending the whole doc against a process-local counter. A settle
flushes each non-empty PendingChange as a **ChangeRecord**:

```
{ key, kind, base: {version, hash}, seq, payload }   // command id = (key, base.version, seq)
```

- Deterministic ids end the `Date.now()` collision class (T3.5) and make
  re-emission idempotent *by identity* instead of accidentally by ledger
  collision.
- Because every record carries **its own base**, the whole-doc `baselineId`,
  the 16-entry ring, its per-post minting, and the "feature absent from cited
  baseline" drop (`commands-from-doc.ts:169-175`) are deleted, not fixed
  (kills T3.1–T3.4).
- The projection carries **acks**: for each feature, the last applied
  `(command id, seq)` and the resulting HLC. The client clears a
  PendingChange when its flushed seq is acked — explicit lifecycle instead of
  content-equality echo detection. `pendingFids` and the `sameText` early
  return die (kills T4.1's *correctness* hazard — drift becomes a visible
  re-flush, not an invisible loop — and T4.3's pinning; normalization parity
  remains a quality goal, no longer a correctness dependency).
- One durable command queue with explicit outcomes replaces "saved" fiction:
  the UI reflects flushed → merged → applied → acked, and `destroy()`/
  `beforeunload` flush the queue (kills T1.1, T3.7's silent freeze, T6.7's
  misleading ack).

*Break-if:* two windows interleave records for one feature → per-feature `seq`
is scoped by a session id; the daemon orders by `(base.version, session, seq)`
and flags cross-session overlap as a conflict rather than interleaving blindly.
*Break-if:* an old client omits base/seq → the daemon treats a record without
a base as today's semantics behind a version field — migration is additive.

### L4 — Daemon: enforce the base, merge or conflict, never blind LWW (kills RC3, RC4 server-side)

- `apply_op` for `set_description`/`set_title` compares the record's
  `base.hash` to the store's current normalized hash:
  - equal → clean apply (the overwhelmingly common case);
  - differs but edits touch **disjoint paragraphs** → three-way merge at
    paragraph granularity;
  - overlapping → **no write**; a conflict entry (reusing the
    `resolution.json` surface) is emitted and projected back, where L2
    renders it. Neither "user covers LLM" nor "LLM covers user" can happen
    silently in either direction (kills T5.4; with L2, kills T5.1).
  - The bias is deliberate: a false conflict is visible and recoverable; a
    false clean-apply is silent loss. When unsure, conflict.
- The applied-command ledger grows to
  `{id, feature_id, kind, base, seq, outcome, directive_id}` so a replay can
  *reconstruct* the directive instead of short-circuiting past it — the
  settle/command/directive granularity misalignment closes (kills T6.1); the
  one-shot channels (steers, handoffs, annotations) move to the same
  outcome-recorded pattern (kills T6.2/T6.3).
- Hand-off carries the draft's content hash: a hand-off applies to the text
  the user confirmed, or degrades to a visible "content changed since ⌘S —
  re-confirm" (kills T5.9).
- Hold lifecycle splits: *draft-exists* (bounded; releasable; never
  indefinitely suppresses Loop A) vs *directive-in-flight* (held until
  outcome). Every store-writing path — Loop A, Loop B, **MCP reflect**, hub —
  goes through the same hold check (kills T5.3, T5.10, T1.2's permanent
  hold; T5.11 adds a retired-target guard at the same choke point).
- Structural gestures get their missing verbs: `unretire` (T2.2) and, if
  sibling order is to be user-ownable, an ordinal in the move payload (else
  the UI must visibly refuse the reorder rather than silently reverting —
  T2.7).
- HLC discipline: all lifecycle mutations use `advance()`, never raw
  `now()` (T6.8).

*Break-if:* hash mismatch false-positives from normalization drift → the hash
is computed by the *one* shared normalize function pair, already
parity-pinned by `doc-roundtrip.test.ts` / `test_roundtrip_idempotency.py`,
extended with a cross-language golden corpus (hardBreak, codeRef-only
paragraphs, unicode whitespace, CRLF). And the failure mode is a visible
conflict, not loss. *Break-if:* paragraph-granular merge produces semantically
wrong text → merging is only attempted for disjoint paragraph sets; anything
same-paragraph conflicts. *Break-if:* this resurrects the killed 2026-06-25
"rev watermark" → it does not: that design fingerprinted *inputs to skip
passes* (its failure mode was silently skipping work); this checks *a
command's declared base to refuse unsafe writes* (its failure mode is a
visible conflict). The asymmetry the adversaries demanded is preserved.

### L5 — Authorship is data (kills RC6)

- Author is a property of **steps** (from L1's origin tag), materialized as
  store-persisted spans — finally delivering G4's R8/R10 so attribution
  survives reload and lives where blame lives.
- Mark hygiene at the input boundary: an appendTransaction strips
  `insertion`/`deletion` engine marks from user-typed text (splitting the
  agent's span), so user words can never inherit agent authorship and never
  be dropped by `inlineRunsToText` or killed by a reject (kills T5.5, T5.6's
  window). Undo restores the *original* author stamps rather than re-inking
  (kills T5.13's provenance rewrite).
- The captured/pending display reads authorship from the record, so agent
  insertions can never render as user additions.

### L6 — Identity hardening (kills RC5)

- Every feature-creation path mints a `localId` (⌘K included); the legacy
  title/order fid-guess in `patchMintedIds` is deleted once nothing needs it
  (T2.6).
- `uniqueLocalIdPlugin` prefers the occurrence that *keeps* the store history:
  on duplicate, the node whose content hash matches the store keeps the id,
  not blindly the first in document order (T2.3).
- Paste re-stamps `ownerId` when the paste target's owning heading differs
  from the carried owner (fill-only stays for splits; cross-feature paste
  re-owns) (T2.5).
- Block-boundary keystrokes are schema-guarded: joinBackward from a
  description's first paragraph into a heading is intercepted (caret moves,
  content does not merge) — a title can only change by editing the title
  (T1.7/T1.8).
- The pending/protection key is the **edited node's identity** (from the
  transaction's step ranges via L1), never the caret's nearest heading
  (T1.10).

### L7 — The harness the engine never had

A timing-aware virtual user, because the failure class lives between the
timers: a fuzzer driving a real editor where **transactions ≠ settles** (a
simulated debounce clock), against a model daemon with configurable
apply/render latency and crash injection, plus an agent lane emitting
overlapping amends. Properties, checked at every quiescent point:

- **N1 no-silent-loss** — every character the user typed is in the store,
  visibly pending, or visibly conflicted. (Nothing else is acceptable.)
- **N2 no-silent-revert** — no applied command writes content whose base the
  author never saw.
- **N3 net-no-op ⇒ ∅** — an edit sequence composing to identity emits
  nothing, holds nothing, and leaves zero pending state.
- **N4 attribution** — every persisted span's author equals the origin of the
  steps that produced it.
- **N5 convergence** — at quiescence, client doc == projection(store) and all
  pending sets are empty (the round-trip property the suite has never had).
- **N6 ack-liveness** — every flushed record is eventually acked or
  conflicted; no permanent pending/hold state exists absent user action.

The op alphabet must include what the current fuzzer omits: undo/redo, join,
selection-replace, paste (incl. cross-feature and above-original), caret-
position variety, hardBreak, IME composition brackets, and mid-sequence
projection arrivals (adopt, defer, refuse).

### What gets deleted (the point of the exercise)

`capturedBaseline` and the per-keystroke re-diff; the whole-doc `baselineId`,
its ring, and its citation; `pendingFids` + `activeFid` protection;
`sameText` early return; the adopt-or-discard slice gate; `Date.now()` salts;
content-equality echo cancellation as a correctness mechanism; the
index-aligned agent-proposal diff; the dead suggest-mode machinery (either
the vendored engine's input path is deleted outright, or L2's conflict lanes
adopt its marks — but not the current half-dead state). Five change
representations collapse into one record with projections.

---

## Part 4 — Phasing

Ordered so every phase is a strict subset of the end state — no throwaway
conditionals. P0 items are input-boundary invariants the final architecture
needs anyway; they also stop today's worst bleeding.

- **P0 — Input-boundary invariants (small, immediate).** Strip engine marks
  from user-typed text (T5.5); joinBackward heading-boundary guard (T1.7);
  `view.composing` defer (T1.9); flush on destroy/`visibilitychange` (T1.1);
  `suppressUpdate` in a finally (T3.8); deterministic command ids from
  `(key, baseVersion, seq)` (T3.5); error handling + queue on the append path
  (T3.7); `advance()` for all HLC lifecycle stamps (T6.8); retired-target
  guard in Loop B (T5.11).
- **P1 — L1 client change ledger + single decoration projection.** Kills the
  net-no-op chain, the display-diff classes, global re-baselining. The old
  decorations run one release behind a flag for visual parity.
- **P2 — L3 wire + acks, L4 base enforcement + extended ledger + hold split.**
  Kills the stale-baseline revert class, the pinned-pending class, silent
  LWW in both directions, the lost-directive crash class, permanent holds.
- **P3 — L2 rebase + conflict regions in the UI.** Kills adopt-or-discard.
- **P4 — L5 authorship persistence + L6 identity hardening.**
- **P5 — L7 harness**, built alongside P1 and gating every later phase on
  N1–N6.

Prior decisions respected: local stays CRDT-free (G4 KTD1 — L2's rebase is
single-author-local, not a CRDT); the input-fingerprint watermark stays dead
(2026-06-25 — L4's base check is per-command optimistic concurrency, the
deferred pre-deploy-audit item, with the opposite failure asymmetry); `~`
retire stays the only destruction gesture (I1 — T2.1 gets *feedback*, not new
semantics).

---

## Part 5 — Implementation status

### P0 — LANDED (2026-08-07)

Green: **1281 pytest · 739 vitest · tsc · esbuild**. Each item ships with a
regression test that was first shown to FAIL against the old behaviour, so none
of them can quietly stop testing anything.

| Leaf | Fix | Where |
|---|---|---|
| T5.5 | `MarkHygiene` strips `insertion`/`deletion` marks from user-typed spans, splitting the agent's span instead of joining it. Origin is now declared on the transaction (`edit-origin.ts`) and read from there by both the authorship stamp and hygiene — one predicate, so two plugins cannot disagree about what a person typed. | `tiptap/mark-hygiene.ts`, `tiptap/edit-origin.ts`, `tiptap/tx-ranges.ts` |
| T1.7 / T1.8 | A deletion at a block boundary never merges content across a heading; the caret moves instead. All six backward/forward delete chords route through one verdict, above StarterKit's priority. | `tiptap/block-boundary.ts` |
| T1.9 | Projections defer while `view.composing`, flushed on `compositionend`. | `doc-gate.ts`, `whole-doc-editor.ts` |
| T1.1 | The pending settle flushes on `destroy`, `pagehide` and hide — a typing burst under the debounce is no longer discarded on the way out. | `whole-doc-editor.ts` |
| T3.8 | `suppressUpdate` clears in a `finally`, so a throw can't permanently disable settling. | `whole-doc-editor.ts` |
| T3.5 | Command ids carry a per-emission token from a session-tagged counter instead of `Date.now()`. | `commands-from-doc.ts`, `tree-editor.ts` |
| T3.6 / T3.7 | One settle's commands are written in a single append (no interleaving with a concurrent handler), with mkdir-retry and a visible error when it fails. | `tree-editor.ts` |
| T5.11 | Loop B refuses commands whose target is retired — `get_feature` returns tombstones, so the old `is None` check let prose land on one. | `loop/loop_b.py` |
| T6.8 | Lifecycle mutations stamp `advance()`, never raw `HLC.now()`. | `store/db.py` |

Also folded in: `tx-ranges.ts` maps inserted spans through LATER transactions in
the batch, not just the rest of their own — the previous copy was off by
however much the rest of an IME/batched flush moved, which is why authorship
drifted on exactly the inputs hardest to reproduce.

**A design correction worth keeping.** The first cut of T3.5 made the command id
a hash of `(kind, feature, baseVersion, payload)` — self-describing, clock-free,
idempotent on replay. It was wrong, and the "I might break this if…" test caught
it before it shipped: type "A", settle, type "B", settle, type "A" again. With no
projection in between, the base version never moves, so the third command hashes
to the first, the ledger folds it as a replay, and the store keeps "B". A silently
lost edit — the exact type-undo-retype pattern this work exists to fix. **Every
edit a person makes is a new instruction, even when it restores earlier text;**
only the replay of a *recorded* command is a replay, and that carries its recorded
id. Hence per-emission tokens. The base version returns in P2 as a command
*field* the daemon enforces, which is a different job from identity.

### Residuals found while implementing P0

- **Selection deletes still cross heading boundaries.** The guard covers
  collapsed-caret deletions; a range selection spanning a heading is an explicit
  gesture and is allowed through, so it can still merge prose into a title. I1
  keeps it from destroying the feature, but the title is corrupted. Wants the L1
  changeset (which sees what the deletion actually spanned) rather than another
  keystroke special case.
- **Drag-and-drop of text across a boundary** is unguarded for the same reason.
- **The keymap guard is wired, not proven.** Its verdicts are unit-tested; the
  binding order relies on TipTap's documented priority. A real end-to-end
  keystroke test needs the browser harness in L7.
- **`add` commands remain content-free and localId-keyed** (`c-add-<localId>`),
  which is correct — a localId is minted once, so re-emission is the same
  creation — but it means T2.4 (two pasted headings sharing a localId before the
  dedup plugin fires) still folds one away. That is an identity bug for L6.

### P1 (first slice) + selected P2/P4 items — LANDED (2026-08-07)

Green: **1289 pytest · 747 vitest · tsc · esbuild**.

| Leaf | Fix | Where |
|---|---|---|
| T4.7 | The captured baseline is carried forward PER FEATURE (`rebaseCaptured`): only a feature that actually adopted the projection re-baselines, so a daemon write to an unrelated feature can no longer erase the change marks under the user's cursor. | `tiptap/captured-decorations.ts`, `whole-doc-editor.ts` |
| T1.2 / T4.3 / C2 | A feature stops being pending once its text returns to the projection it last adopted (`settledPendingFids`). Undo no longer pins a feature pending forever against a version the daemon will never advance — which had made every later projection unreachable for that feature. | same |
| T5.10 / T1.2 (hold half) | A held draft's hold now EXPIRES on the same window as an abandoned intent; a handed-off directive still holds until it drains, and a legacy entry with no timestamp never expires. Previously any never-handed-off edit held its feature forever, silently ending Loop A's tracking of it. | `loop/edits.py` (`Directive.ts`, `hold_set`), `loop/loop_b.py` |
| T5.3 | MCP `codoc_reflect` / `_apply_single` honour the hold set: an agent amend on a feature the author is editing becomes a PROPOSAL rather than an overwrite. Suppressed means proposed, not dropped — an agent's reflection is a one-shot report, so discarding it would lose it. The exception is an agent completing its own directive (`caused_by` names it), which must still apply or the loop cannot close. | `mcp/tools.py` |

Two of these deserve emphasis because they were silent by construction: a
feature that stopped tracking code (T5.10) and a feature no projection could
reach (T4.3) both look completely normal in the UI. Nothing surfaced either
one; they simply subtracted from the tree over time.

### P2 / P4 / P5 — LANDED (2026-08-07)

Green: **1298 pytest · 760 vitest · tsc · esbuild**.

**P4 — identity, on one rule.** Three separate bugs turned out to be the same
missing question: did this node ARRIVE, or was it already here? `tx-ranges.nodeArrived`
answers it from the transaction, and both repairs now use it. A paragraph pasted
under a different feature adopts its new home instead of keeping the owner it was
copied with (its text used to be filed under the feature it came from, invisibly);
a duplicated `localId` re-mints the node that arrived rather than the first in
document order (pasting a heading ABOVE its original used to hand the copy the
original's identity, bindings and history, and restart the real feature as new).
Prose that merely STAYS keeps its owner — that is I2, unchanged. Separately, ⌘K
"Create feature" now mints a `localId` like every other creation path.

**P2 — no content command applies blind.** A command carries `base_text`: the
value it replaces, as its author last knew it. The daemon refuses to overwrite a
feature whose text has moved since, keeping the author's version as a pending
proposal — so neither side's words are discarded and the disagreement is visible.
Full text rather than a hash, deliberately: the comparison then runs through ONE
normalizer (the daemon's own), and there is no TypeScript/Python hash parity to
drift into conflicting on every edit.

**P5 — the harness, and what it immediately found.** `virtual-user.props.test.ts`
drives a real editor through real transactions where a keystroke and a settle are
different events: a debounce, a model daemon applying commands the way Loop B
does, projections landing a configurable number of settles late, and undo, joins
and selection-deletes in the alphabet. On its first run, with a single author and
no agent anywhere, it reported conflicts — ordinary typing disagreeing with
itself. The cause was real and would have shipped: a projection is a snapshot of
the store at the moment it was rendered, so resetting the client's base from it
regresses that base behind commands already sent, and the next keystroke looks
like a conflict.

The fix is the lineage rule, and it belongs on the daemon because only the daemon
knows who wrote what: **a stale base is only a disagreement when somebody else put
the current text there.** `feature_writers` records the last writer at the one
apply boundary (so an agent write counts as somebody else without every agent path
having to remember), and a command carries the `session` that authored it. An
author outrunning the round-trip continues their own work; a second window, or an
agent, conflicts. Both directions are pinned by tests on both sides.

**Flush before adopt.** The version gate resolves a same-feature disagreement by
swapping a whole slice, and when the projection won, whatever had been typed since
the last settle went with it — unsent and unrecorded. `setDoc` now settles first,
so that text reaches the wire and the base check decides its fate honestly. This
is L2's no-silent-loss half; it composes with P2 rather than needing the rebase.

### L2 (merge half) — LANDED: role-ranked three-way merge

Detection without resolution had one policy for every disagreement: refuse the
command, park it as a proposal. That is right when two peers rewrote the same
sentence and wrong everywhere else — an author fixing paragraph one while an
agent rewrote paragraph three was told they "conflicted" over words nobody
contested, and a settle carrying the *whole* description from a stale baseline
read as a deliberate deletion of the paragraph the agent had just appended.

The boolean fused two independent questions; they are now answered separately.

1. **Do the edits overlap?** Textual, answered by `codoc/loop/merge3.py` — a
   diff3 over `Command.base_text` as the common ancestor. Disjoint edits merge:
   both land, nobody reviews anything.
2. **Who wins where they do?** Authority, answered by `model.event.outranks`:
   **a person outranks anything that is not a person.** The human authors the
   intent; agents and the loop maintain an index of it. Where they disagree
   about the same sentence the human is not proposing a change, they are
   correcting one.

`loop_b._resolve_content` combines them into one table — four outcomes, no
ad-hoc conditionals:

| current vs base | overlap | incoming outranks holder | outcome |
|---|---|---|---|
| unchanged, or same session | — | — | `CLEAN` — apply verbatim |
| moved | no | — | `MERGED` — both edits land |
| moved | yes | yes | `SUPERSEDED` — the author's text wins the region |
| moved | yes | no | `DEFERRED` — whole edit → proposal |
| (merge yields the stored text) | — | — | `NOOP` — write nothing |

Design decisions, each with the assumption it survives:

- **Rank is binary, not graded.** *Break it if:* an ordering existed between an
  agent and the loop, or between two agents. Nothing in the system can justify
  one, and every level added is another way for the wrong side to win silently.
  Non-human sources tie, and a tie never overwrites.
- **Unknown actor ranks non-human.** *Break it if:* backfilling old
  `feature_writers` rows as human. Those rows are mostly `loop_a`'s — precisely
  the ones a person's edit is meant to win against. "" means codoc cannot show
  who wrote this, which is not a claim to authority over someone who can.
- **The superseded agent text gets no proposal; a deferred incoming edit does.**
  Proposals exist for text with no other home. An applied-then-superseded agent
  edit is fully recorded in the event ledger (`codoc history` reads it); minting
  a review prompt every time someone types over an amend would make the tree
  argue with them. Incoming text that never applied is nowhere else — it must
  be kept.
- **A tie does not partially merge.** *Break it if:* applying the disjoint half
  between two peers. That builds a document neither of them wrote, in the one
  case where nobody has authority to be merged into.
- **`NOOP` exists because of the writer record, not the text.** A command whose
  merge yields exactly what is stored (type-then-undo) is harmless to write back
  and corrosive to `feature_writers`: it would stamp that session as the author
  of the agent's prose, and its next stale command would then read as
  *continuing its own work* and overwrite with no merge at all.
- **The merge runs on raw text; only the moved-check normalizes.** *Break it if:*
  merging normalized text — it would store the normalized form and reflow prose
  the author never touched.
- **No new wire field.** The incoming role is the settle's existing authorship
  annotation, and the recorded role is `event.actor` at the one `apply_op` write
  boundary. Same derivation both sides, so the ledger can never disagree with
  the decision it justified. `feature_writers.role` (schema v5) carries it.

`merge3`'s first implementation dropped every zero-width insertion — its
coordinate filter could not distinguish "inserts at the start of this span" from
"ends where this span begins", so two people appending a line both lost it. The
anti-vacuity floor caught it; the sweep now partitions changes into clusters
directly instead of re-deriving membership from intervals.

Covered by `tests/loop/test_merge3.py` (14, incl. a 400-case fuzz asserting the
author's text always survives and the merge never invents a line) and 10 new
policy tests in `tests/loop/test_commands.py`.

### Still open

- **In-place conflict regions** (T5.2). A `DEFERRED` edit still surfaces on the
  proposal list rather than as a conflict region rendered where it happened.
  Resolution is now correct and legible in the daemon's summary; it is not yet
  *situated* in the document.
- **The ack channel** (T4.2). Content equality is still how an echo is
  recognised, so the post-settle repost can re-apply an edit — churn, not loss,
  and the base check now bounds its blast radius.
- **Selection deletes and drag-drop across a heading boundary** (P0 residual).
- **Sibling reorder** (T2.7) still reverts silently; it needs either an ordinal
  in the move payload or a visible refusal.
