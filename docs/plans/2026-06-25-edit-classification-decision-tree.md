# Edit Classification Decision Tree
*Generated 2026-06-25 — 5-round adversarial analysis, 11 agents, 44 attacks*

## Why this exists

The codoc editing pipeline classifies every user gesture (keystroke, undo, drag, retire, etc.) into
one of: `ADD_NODE`, `AMEND`, `MOVE_NODE`, `RETIRE_NODE`, or `SOFT_RETIRE`. The classification was
originally done by snapshot-diffing the doc against the store — thinking in terms of system state,
not user operations. This document captures the authoritative decision tree that the algorithm should
follow, derived from 5 rounds of adversarial attack + cohesive-solution analysis.

---

## System Invariants (the "why" behind the tree)

| # | Invariant | File |
|---|-----------|------|
| INV1 | `_supersede_directives` keys on **description-bearing ops only** — AMEND + RETIRE. MOVE_NODE never cancels a prior directive. | `loop_b.py:698` |
| INV2 | **Soft-retire is lifecycle-only.** `reconcile_doc_presence` must NOT call `store.delete_binding`. Bindings survive soft-retire and re-activate on un-retire. Only explicit `~` marker retire (RETIRE_NODE) or accepted RETIRE with `delete_code=True` destroys bindings. Loop A skips DETACH for bindings on RETIRED features. | `doc_presence.py` ← **BUG TODAY** |
| INV3 | **Channel arbitration is per-feature.** `_pick_parsed` is replaced by `_merge_parsed`: doc-path AMEND is authoritative for features the doc touched; text-path ops cover features not in `doc_amend_fids`; RETIRE_NODE from text beats concurrent AMEND from doc for the same feature; steers always from text path. | `loop_b.py:_pick_parsed` ← **DESIGN DEBT** |
| INV4 | **Annotation baseline = store-current render.** When `docAhead` is set, `annotateSettle`'s `prevText` must be `renderTreeFromDoc(savedDoc)` (not the stale on-disk `tree.codoc`). | `tree-editor.ts:annotateSettle` ← **BUG TODAY** |
| INV5 | **Write ordering: `write_tree` BEFORE `write_doc_fids`.** `write_doc_fids` is moved out of `reconcile_doc_presence` into `_apply_edits` and called after `write_tree`. This keeps `doc-fids.json` and `tree.codoc` co-consistent. | `doc_presence.py`, `loop_b.py` ← **BUG TODAY** |
| INV6 | **Imperative gate must not fire on compound-word or pronoun-subject openings.** (1) Hyphen in first token → compound noun/adjective, not imperative verb. (2) Personal/demonstrative pronoun as second token → grammatical subject, not directive. | `classify.py:is_imperative` |
| INV7 | **FID write-back after mint.** After Loop B mints a fid for ADD_NODE (with known `localId`), it writes `{localId → fid}` to `.codoc/minted.json`. The TS host applies this back via a non-history ProseMirror transaction so TipTap undo/redo restores the heading with the minted fid. | `loop_b.py`, `tree-editor.ts` |
| INV8 | **Epoch suppression for non-cancellation `edits_touched`.** During an open epoch, `edits_touched` is suppressed unless the `edits.json` change contains cancellations (Withdraw). Annotation-only writes mid-epoch must not trigger `_supersede_directives` and drop the running agent's directive. | `watch.py`, `loop_b.py` |
| INV9 | **`doc-fids.json` and `tree.codoc` are co-consistent** (consequence of INV5). On crash between reconcile and write_tree, the zombie-clone guard in `diff_codoc` (retired-set check before ADD_NODE) prevents resurrection. | `diff.py` ← **BUG TODAY** |
| INV10 | **Accepting a Loop-A proposal while a directive is queued invalidates the directive text.** After inbox Accept mutates the store description for feature F, supersede and re-queue with the post-Accept description. | `loop_b.py:469-501` |
| INV11 | **Steer comments are additive and never superseded.** `_supersede_directives` preserves `kind == 'steer'`. They accumulate across passes and are consumed exactly once by the end-of-pass `write_tree`. | `loop_b.py:344` |
| INV12 | **Last-annotation-wins in `drain_annotations` is intentional.** A user who writes and immediately reverts an imperative edit within one debounce window has expressed no net imperative intent. No directive for the transient state. | `edits.py:177` |

---

## The Decision Tree

Every user gesture arriving at Loop B is classified by walking this tree top-to-bottom. The first
matching condition wins.

### A. Channel selection (replaces `_pick_parsed`)

```
[N0] parse_doc_file() non-empty diff?
  TRUE  → [N1] per-feature merge
  FALSE → [N2] text path only

[N1] text path also has ops for features NOT in doc_amend_fids?
  TRUE  → [N1a] text has RETIRE for a feature doc has AMEND for?
              TRUE  → MERGE_RETIRE_WINS (RETIRE beats AMEND, INV3)
              FALSE → MERGE_DOC_PATH_FOR_AMEND
  FALSE → [N1b] doc diff non-empty? → TRUE → [N3] | FALSE → [N2]

[N2] text path has non-empty nodes?
  TRUE  → [N3]
  FALSE → SKIP
```

### B. Doc-presence reconciliation (runs before diff)

```
[N3] (prev_doc_fids − current_doc_fids) & live_features non-empty?
  TRUE  → [N4] guard: parsed is None OR has errors OR nodes == 0?
              TRUE  → SKIP (corrupt/missing doc, NOT "explicit empty")
              FALSE → SOFT_RETIRE_LIFECYCLE_ONLY (INV2: NO binding deletion)
  FALSE → [N5]

[N5] current_doc_fids & retired_ids non-empty?
  TRUE  → UN_RETIRE (bindings already preserved by INV2)
  FALSE → [N6]

[N6] → [N7] (write_doc_fids is called AFTER write_tree in _apply_edits, NOT here — INV5)
```

### C. Per-op classification

```
[N7] diff.user_ops non-empty?
  TRUE  → route each op through [N8]–[N22]
  FALSE → [N_DIRECTIVES_ONLY]

─── ADD_NODE ─────────────────────────────────────────
[N8]  op.kind == ADD_NODE AND node.id in retired_ids?
  TRUE  → SKIP (zombie-clone guard, INV9)

[N9]  op.title.strip() == ''?
  TRUE  → SKIP (transient mid-creation, already fixed)

[N10] op.realized == False?
  TRUE  → ADD_NODE_THEN_QUEUE_DIRECTIVE

[N11] is_imperative(op.description)?
  TRUE  → ADD_NODE_THEN_QUEUE_DIRECTIVE
  FALSE → ADD_NODE_ONLY

─── RETIRE_NODE ──────────────────────────────────────
[N13] op.kind == RETIRE_NODE
  → [N14] has bindings?
      FALSE → RETIRE_NODE_ONLY
      TRUE  → [N15] prior non-steer directive in manifest?
                TRUE  → SUPERSEDE_PRIOR_THEN_RETIRE_DIRECTIVE
                FALSE → RETIRE_NODE_THEN_QUEUE_DIRECTIVE

─── MOVE_NODE ────────────────────────────────────────
[N17] op.kind == MOVE_NODE
  → MOVE_NODE_ONLY (NEVER queues directive, INV1)

─── AMEND ────────────────────────────────────────────
[N18] op.kind == AMEND
  → [N19] first token is hyphenated compound? → AMEND_ONLY (INV6)
  → [N20] second token is pronoun/demonstrative? → AMEND_ONLY (INV6)
  → [N21] is_imperative(desc) OR bold-span gate?
      FALSE → AMEND_ONLY
      TRUE  → [N22] prior non-steer directive in manifest?
                TRUE  → SUPERSEDE_PRIOR_THEN_AMEND_DIRECTIVE
                FALSE → AMEND_THEN_QUEUE_DIRECTIVE

─── STEERS ───────────────────────────────────────────
[N23] diff.comments or diff.new_node_comments non-empty?
  → [N24] known fid? → QUEUE_STEER_DIRECTIVE
  → [N24a] new_node_comment? → QUEUE_STEER_DIRECTIVE_BY_TITLE (resolve by title after ADD)
  → else SKIP
```

### D. Write ordering (end of pass)

```
[N25] write_tree required this pass?
  TRUE  → [N26] tree.codoc externally modified AND no steer comments?
              TRUE  → SKIP_WRITE_TREE_MARK_RERUN
              FALSE → [N27] NOT dry_run?
                          TRUE  → WRITE_TREE_THEN_WRITE_DOC_FIDS  ← write_doc_fids HERE (INV5)
                          FALSE → DRY_RUN_WRITE_TREE_REINSERT_COMMENTS
  FALSE → skip write_tree
```

---

## Top Attacks by Severity

| Attack | Severity | What Breaks | Fixed By |
|--------|----------|-------------|----------|
| A3 | phantom-data | Undo after cross-pass delete: feature restored but bindings permanently lost | INV2 |
| A2/A6 | data-loss | Raw text editor edit silently dropped when `docAhead` is set | INV3 |
| D3 | phantom-data | Crash between `write_doc_fids` and `write_tree`: zombie feature re-created | INV5+INV9 |
| A11 | wrong-directive | RETIRE via `~` while directive is queued → add+retire churn in queue | N15-N16 |
| A1/A4 | wrong-directive | Rapid undo of imperative edit: stale directive survives despite supersede | INV4+N22 |
| B7 | wrong-directive | Description with compound-word or pronoun-subject triggers false imperative | INV6 |
| MA1 | wrong-directive | Agent mid-realization while user edits supersedes the running directive | INV8 |
| D1 | phantom-data | Undo/redo of ADD_NODE mints a second fid (TipTap history has fid=null) | INV7 |
| C5 | phantom-data | Skipping write_tree leaves live marker in tree.codoc; next pass double-retires | INV5+N8 |
| MA2 | wrong-directive | Accepting a Loop-A proposal while directive is queued invalidates directive text | INV10 |

---

## Open Semantic Questions — Resolved 2026-06-25

| # | Question | Decision | Code consequence |
|---|----------|----------|-----------------|
| OQ1 | Should undo of a description after code was implemented re-queue a directive? | **Yes** if code was already implemented; **No** (just drop directive) if code was still pending | Already correct: `_supersede_directives` drops the pending directive; if code was already implemented the directive slot is empty and `is_imperative` naturally re-queues. No code change. |
| OQ2 | Partial undo landing on an imperative intermediate state — re-queue? | **Yes** — re-queue. | Already correct: `is_imperative` fires on the undo-result state and re-queues. No code change. |
| OQ3 | Remote hub concurrent edits — per-feature locking? | **Same as OQ4** — last-write-wins, user role > contributor role. | Remote hub deferred; no change in this session. |
| OQ4 | Agent MCP AMEND concurrent with user webview edit — last-write-wins or merge? | **Last-write-wins; user > agent.** | Already correct: doc path always overwrites agent MCP writes (Loop B applies user's doc-ahead over the agent's store mutation). No code change. |
| OQ5 | `## ` mid-paragraph: split preceding text into prior feature? | **Hold** — keep current behavior (preceding text stays in prior feature's description). | No change. |
| OQ6 | Deleting a parent — children promoted to root silently? | **Yes** — acceptable. | No change; document as accepted behavior. |
| OQ7 | When should bindings be deleted — only on explicit `~`, or also on soft-retire? | **Maybe soft-retire** — deferred. INV2 (lifecycle-only soft-retire) is kept for now; revisit when undo-binding-restore story is clearer. | No change from INV2. |
| OQ8 | Steer on new node before fid mint — should it buffer until fid is available? | **Yes** — buffer. Currently works via title-lookup for the text path. Webview path (steer before fid mint) requires INV7 (fid write-back via `minted.json`) as a prerequisite. | Deferred behind INV7. |

---

## Bugs Fixed (2026-06-25)

### ✅ Fix 0 — Empty-title ADD_NODE guard
**File**: `codoc/codoc_file/diff.py` — `if not node.title.strip(): continue` before ADD_NODE.

### ✅ Fix 1 — INV2: soft-retire is lifecycle-only (no binding deletion)
**File**: `codoc/loop/doc_presence.py` — removed `store.delete_binding` calls from soft-retire path.

### ✅ Fix 2 — INV9: zombie-clone guard in `diff_codoc`
**File**: `codoc/codoc_file/diff.py` — skip ADD_NODE when `node.id in retired_ids`.

### ✅ Fix 3 — INV5: `write_doc_fids` moved after `write_tree`
**Files**: `codoc/loop/doc_presence.py`, `codoc/loop/loop_b.py`

### ✅ Fix 4 — N15-N16: RETIRE supersedes prior directives
**File**: `codoc/loop/loop_b.py` — `_supersede_directives` called before RETIRE directive appended.

### ✅ Fix 5 — INV6: imperative gate false-positive guards (compound-word, pronoun-subject)
**File**: `codoc/loop/classify.py`

### ✅ Fix 6 — reconcile empty-nodes guard (explicit mass-delete recognized)
**File**: `codoc/loop/doc_presence.py` — `not parsed.nodes` no longer causes early return.

### ✅ Fix 7 — R3-A: block directives survive AMEND supersede
**File**: `codoc/loop/loop_b.py` — `_supersede_directives` preserves `kind.startswith("block:")`.

### ✅ Fix 8 — INV4: annotation baseline uses current doc render when docAhead is set
**File**: `vscode-codoc/src/providers/tree-editor.ts:settleDoc` — `prevText` is
`renderTreeFromDoc(prevDoc)` when `docAhead` is already set, not the stale on-disk `document.getText()`.

---

## Remaining Work

### ✅ INV3 — Per-feature channel arbitration (implemented 2026-06-25)
`_pick_parsed` replaced by `_merge_channels(codoc_dir, store)` in `loop_b.py`. Doc-path wins
for its features; text-path covers the rest; RETIRE beats AMEND across channels. Also eliminates
the double-compute waste of the old winner-take-all probe.

### ✅ INV7 — Undo/redo of ADD_NODE no longer creates duplicate features (implemented 2026-06-25)
`_apply_minted_fids(parsed, store)` restores fids from `Feature.local_id` (already stored in
SQLite) before `diff_codoc`. No new control file needed — the store is the authoritative source.
The TS half (`patchMintedIds` + `setMintedMap` + sidecar `local_id`) was already shipping.

### ✅ INV8 — In-flight directives protected from supersede during active epoch (implemented 2026-06-25)
`_in_flight_directive_ids(codoc_dir)` reads directive ids from `realize.md`, gated on
`activity.json: epoch.open == true`. Without the epoch gate, a queued-but-not-started `realize.md`
would block normal supersede (coalescing regression).

---

## Robustness redesign (Opus workflow, 2026-06-25) — replaced patches with declared identity

A second adversarial workflow audited the patches above and found several were heuristic-on-heuristic
band-aids. The cohesive replacement: **Loop B reads facts the webview already declares (identity,
lifecycle), instead of re-inferring them from snapshot content.** The workflow's most ambitious idea —
a scalar `rev` watermark — was killed by adversaries (three independent clocks: webview instance, hub
`payload_version`, MCP; plus a crash-window that turns recoverable double-fires into permanent data
loss). What survived and shipped:

### ✅ Step 3 — `diff_codoc` keys on declared `local_id` (doc channel), not content/fid
**File**: `codoc/codoc_file/diff.py` — `diff_codoc(..., has_local_ids=True)`. A node whose
author-stable `local_id` maps to ANY existing feature (live or retired) is, by construction, never an
ADD. This single rule **DELETED** two patches: the zombie-clone guard *and* the `_apply_minted_fids`
pre-pass (identity is now resolved inside the diff, not laundered into the fid field first). Includes a
defensive duplicate-`local_id` fallback (cloned subtree before the editor re-mints → second node is an
ADD, never a silent clobber). The raw-text channel keeps the fid+title snapshot diff (no `local_id`
signal). `reconcile.py` doc-channel probes also pass `has_local_ids=True` so an undo'd node isn't a
false-positive pending ADD.

### ✅ Step 1 → SHARED codoc-loop lock (Loop A + Loop B), upgraded 2026-06-25
**File**: `codoc/loop/locks.py` — `loop_lock(codoc_dir)`, a reentrant cross-process FileLock (120s
ceiling) in its own leaf module (no import cycle). **Both** `run_loop_b` AND Loop A (`run_loop_a` +
`reconcile_drift`) AND the derived re-render (`reconcile.safe_write_tree`) acquire it. The audit found
Loop B's original lock only serialized B-against-B, leaving Loop A unguarded — so daemon-Loop-A could
interleave its store mutation + `tree.codoc` re-render with a concurrent CLI/hub Loop B (a stale-render
/ phantom-revert race). Now every loop pass (and the render) is mutually exclusive across processes.
Deadlock-free: loops never nest; `safe_write_tree` only reads the store + writes derived files (no
SQLite-vs-FileLock cycle); the OS releases the lock on crash; the daemon's `safe_process_batch` survives
a rare timeout by skipping one cycle.

### ✅ Step 4 — title-clear data-loss fixed via the channel flag (no lifecycle enum needed)
**File**: `codoc/codoc_file/diff.py` — the blank-title silent-keep is gone. On the **doc channel**
(`has_local_ids=True`) the rich heading's title content is authoritative, so an empty title is a
DELIBERATE clear → `AMEND title=""`; on the **text channel** a blank `-  ⟨fid⟩` line stays
transient-safe (the R19 guard preserves the stored title). The `has_local_ids` flag IS the "declared
vs inferred" signal, so the proposed lifecycle enum proved unnecessary: after Step 3's `local_id`
keying, the empty-title guard is already UNAMBIGUOUS (a title-clear on an existing feature resolves to
that feature → the AMEND branch, never the ADD branch). The empty-title guard stays (correct: an
untitled NEW node is mid-creation). Round-trip of an empty-title feature is a fixed point (no
phantom-AMEND loop).

### ✅ Steps 6+7 — `is_imperative` DELETED; held-draft model (shipped 2026-06-25, maintainer-approved)
The regex gate (`is_imperative` + cue list + verb list + hyphen/pronoun guards) is **gone**. Replaced
by `edit_mints_directive` (STRUCTURAL: AMEND→always mints, ADD→`realized is False`, RETIRE→owns bound
code, MOVE→never). A doc AMEND mints a **held draft** (`handed_off=False`) — visible as the in-situ
pending decoration, withdrawable, coalesced — and realizes ONLY via an explicit gesture:
- **`handoffs` channel** (new one-shot list in `edits.json`): the webview commit / ⌘S writes it
  (`handOff()` → `appendHandoffs`), and `codoc realize` flushes all held drafts into it. Per-feature, so
  a typo-fix draft isn't flushed with a real change.
- **Explicit-realize kinds** handed off on mint: steer (`> …`), RETIRE-with-code, plan ADD
  (`realized=False`), and block `lower` (a diagram delta is unambiguous, unlike prose).
- `handed_off` is **sticky** (never demoted) so an in-flight directive survives a fresh edit.
- The inbox-accept path mints only for plan-ADD (an accepted Loop-A AMEND reconciles to *existing* code
  → no directive). `codoc realize` flushes held drafts (the CLI hand-off gesture).

Behavior change (intended): a doc edit no longer auto-realizes from prose mood. Bold reverts to a pure
`Focus:` presentation signal. Change ledger rows 7/8 updated.

### ✅ Step 5 — `localId` uniqueness among live headings (shipped 2026-06-25)
**File**: `vscode-codoc/src/webview/tiptap/feature-heading.ts` — `uniqueLocalIdPlugin` (an
`appendTransaction`) re-mints the `localId` (and clears `fid`) on any heading that shares a `localId`
with an earlier live heading (copy-paste of a subtree / heading split clone attrs). The FIRST keeps its
id; clones get a fresh one. Convergent (no transaction loop). This makes Step 3's `local_id`-keyed diff
airtight in production; the Python defensive fallback (duplicate `local_id` → ADD) remains as belt-and-suspenders.

### ✅ Step 8 — in-flight protection is structural, no `activity.json` dependency (shipped 2026-06-25)
**File**: `codoc/loop/loop_b.py` — `_in_flight_directive_ids` now returns exactly the directive ids in
`realize.md` (which, under the held-draft model, contains ONLY handed-off directives). The
`activity.json` epoch read is **deleted**. More robust: a stale/missing epoch can no longer wrongly
expose a being-realized directive to supersede. Held drafts (not in `realize.md`) still coalesce freely;
a genuinely handed-off directive is protected whether or not the agent has visibly started.

### ✅ Step 10 — "plan" authoring gesture so a new feature can be built (shipped 2026-06-25)
**Files**: `codoc/codoc_file/parse.py` (`ParsedNode.realized`), `doc_parse.py` (reads the heading's
`realized` attr), `diff.py` (ADD op carries `realized`), `vscode-codoc/.../structure-commands.ts`
(`newFeatureHeading({realized:false})`) + the `◇ plan` toolbar button. After `is_imperative` was deleted,
a descriptive new heading no longer auto-builds; the **◇ plan** button creates a feature *born*
`realized=False` (timing-safe — its first ADD settle carries the flag), which mints a directive handed
off on mint → the agent builds it. The typed "build this new feature" gesture that replaced the prose guess.

### ✅ MCP store mutations now serialized (shipped 2026-06-25)
**File**: `codoc/mcp/tools.py` — `_apply_single`, `reflect`, and `await_verdicts._resolve_once` now
wrap their mutation + render in `loop_lock`. The concurrency story is complete: **every** state
transition (Loop A, Loop B, the derived render, and every MCP op) acquires the one shared lock, so
nothing interleaves across the daemon / CLI / hub / MCP processes.

### ✖ Steps 2 / 9 — evaluated and deliberately NOT implemented (closed 2026-06-25)
Both are the "churn against working code / add a fragile mechanism" the redesign set out to avoid:

- **Step 2 (input-fingerprint idempotency watermark)** — *Not implemented.* The system is ALREADY
  idempotent through four independent, proven layers: the watch.py self-write hash guards
  (`last_tree/edits/inbox_hash`), the empty-diff early-return in `_apply_edits` (a double-fire finds the
  store caught up → no ops), `reconcile.has_pending_*`, and the shared `loop_lock` (no concurrent
  interleave). A store-backed fingerprint adds no capability over these. Worse, it has an **asymmetric
  failure mode**: a fingerprint that stores the post-pass file hash *skips* a genuine new edit that
  arrived while the pass held the lock (the post-pass hash already includes it) — silently dropping work.
  A redundant pass (the cost of NOT having it) is harmless; a skipped pass (the risk of a fingerprint bug)
  is data loss. The adversarial review flagged the whole watermark family as the design's most dangerous
  idea, and that holds. Closed.

- **Step 9 (delete `doc-fids.json`, source prev-fids from a store meta row)** — *Not implemented.* It is
  cosmetic (one fewer control file) and does NOT improve crash-consistency: the zombie-clone guard (INV9)
  and the write-ordering (INV5) exist because the derived `tree.codoc` file can desync from the
  authority on a crash — moving prev-fids from `doc-fids.json` into a store meta row changes
  *doc-fids-vs-tree.codoc* into *store-meta-vs-tree.codoc*, the exact same desync window, so both guards
  remain. The change touches the resurrection-sensitive `reconcile_doc_presence` for no robustness gain.
  Net-negative risk/reward. Closed.

### Deferred (need product decision)
- OQ3: Remote hub per-feature optimistic locking
- OQ7: Whether soft-retire should also detach bindings in some cases

---

## Old bug-fix notes (for reference):
### Fix 6 details — reconcile empty-nodes guard (R3-B / 3.3)
**File**: `codoc/loop/doc_presence.py:60-61`
```python
# Only skip when the doc is genuinely missing/corrupt:
if parsed is None or parsed.errors:
    return (0, 0)
# len(parsed.nodes) == 0 with an existing file = explicit mass-delete; fall through
```
