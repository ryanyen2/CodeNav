# Loop Robustness Audit — Issues, Unknowns, and Plan

**Date:** 2026-07-11
**Goal:** the doc↔code loops must be robust against real user behavior: manual code
edits, agent code edits, every style of doc editing (select-delete, char-by-char
deletion, line deletion, cut/paste, undo), and — critically — *interrupted /
cancelled / crashed* sessions. Today the system is fragile: state indicators wedge
("Claude is editing" forever), edits can be silently dropped, and several documented
surfaces no longer work.

This document has three parts, per the finding-unknowns method
(`docs/CLAUDE_FINDING_UNKNOWN.md`):

1. **Confirmed issues** — evidence-backed, with file:line citations.
2. **Known unknowns** — plausible failures that need a verification experiment first.
3. **Unknown unknowns** — blindspot areas the current design has no answer for.

Then **the plan**: invariants, prioritized workstreams, and a test harness.

---

## Part 0 — The territory (how liveness state actually flows)

The system's "who is doing what" is spread across five signals with **different
owners and different clearing rules**:

| Signal | Written by | Cleared by | Failure mode |
|---|---|---|---|
| `activity.json` `epoch.open` | `SessionStart` hook | `Stop` hook only | stuck open on interrupt/kill |
| `activity.json` `features.{fid}.phase` (`editing`/`reflecting`) | Pre/PostToolUse hook, MCP reflect | `Stop` hook only (`data["features"] = {}`) | stuck "editing" |
| `status.json` `realizing` | MCP `codoc_realize_progress`, sdk_realize, autorealize | next loop pass's `refresh_status` (needs a file event) | stuck "implementing…" |
| `status.json` `awaiting_impl` | Loop B / `refresh_status` floor on `realize.md` | `/codoc:sync` deleting `realize.md` | OK (self-clearing) |
| daemon `WatchState.epoch_open` | daemon epoch transitions | falling edge or 900 s staleness — but only when a file event arrives | loops suppressed while idle |

Nothing in this table is *derived with an expiry*; every "in progress" state is
edge-triggered and relies on a specific process surviving to write the clearing
edge. Hooks are best-effort by contract (`hook.py` never raises, exits 0), yet
they are the **only** writer of three clearing edges. That asymmetry is the root
cause of the reported "status keeps showing Claude is editing" bug.

---

## Part 1 — Confirmed issues

### A. Stuck-state lifecycle (the user's direct pain)

**A1. Interrupt/kill never closes the epoch → status bar shows "agent working…" forever.**
- Registered hooks (`codoc/plugin/hooks/hooks.json`): `SessionStart`, `Stop`,
  `PreToolUse`, `PostToolUse`, `UserPromptSubmit`. **No `SessionEnd`.**
- Claude Code's `Stop` hook does **not** fire on user interrupt (Esc) and cannot
  fire on a killed terminal / closed window / crashed CLI.
- The extension computes `agentActive` as literally `epoch.open === true`
  (`vscode-codoc/src/state/workspace-state.ts:206`,
  `activity-model.ts:63-65`) with **no staleness check**, and
  `status-presentation.ts:71-78` ranks "agent active" above every lifecycle
  state. Result: the bar shows `$(zap) agent working… (N files)` until some
  *other* session's `SessionStart` happens to rewrite `activity.json`.

**A2. Per-feature `"editing"` phase is only cleared by the Stop hook.**
- `agent/hook.py:344-350` marks `features[fid] = {"phase": "editing"}` on every
  write; only `handle_stop` (`hook.py:249-251`) resets `features = {}`. Cancel
  the session mid-edit → the doc view shows the skeleton/"editing" animation
  indefinitely. `at` timestamps exist but no reader applies a TTL
  (`activity-model.ts:143-149` surfaces the phase verbatim).

**A3. Daemon stale-epoch recovery never runs while idle, and never repairs the file.**
- `watch.py:609-620`: `yield_on_timeout` ticks arrive every 3 s but
  `if not changes: continue` skips `process_batch`, so the step-0 stale-epoch
  recovery (`watch.py:337-345`) only runs when a *file event* arrives — after a
  kill, possibly never (the user "does something else", e.g. outside the repo).
- Even when it runs, recovery only fixes the daemon's in-memory `WatchState`; it
  **never writes `activity.json`** (`epoch.open` stays `true`, `features`
  phases stay) — so the IDE keeps showing agent-active even after the daemon has
  recovered. And 900 s (`EPOCH_STALE_SECONDS`) is a long time to show a lie.

**A4. Stuck `realizing` wedges future `/codoc:sync` runs.**
- The interactive realize path stamps `status.json = realizing` from the MCP
  tool (`mcp/tools.py:301-312`). If the user cancels mid-queue, nothing rewrites
  status until *some* loop pass calls `refresh_status` — which requires a file
  event, and while the (stuck-open) epoch suppresses Loop A/B routing
  (`watch.py:429-441`) that pass may be a long time coming.
- Worse: `plugin/commands/codoc/sync.md:84-86` instructs a fresh `/codoc:sync`
  to **stop** when state is `realizing` ("another realize pass is running").
  A stale `realizing` therefore blocks the very command that would repair it.

**A5. While an epoch is (stuck) open, human work is silently deferred.**
- `watch.py:428-441`: mid-epoch, code file changes are only *accumulated* and
  `tree.codoc`/`tree.doc.json` edits are suppressed outright. With a stuck-open
  epoch, the user's manual code edits and doc edits produce **no visible loop
  activity** — the tree just quietly stops following, with no indication why.

**A6. Transient status states can stick on a crashed pass.**
- `watch.py:455` writes `TREE_DIRTY` ("applying tree edits") *before* running
  Loop B; if the pass throws, `safe_process_batch` swallows the error and no one
  rewrites status — "applying tree edits…" sticks until the next successful pass.
- Similarly `autorealize.spawn_realize` writes `REALIZING` before the child
  proves viable (`autorealize.py:93-96`); a child that dies instantly (auth
  failure) leaves `realizing` until the next reaped batch.

### B. Concurrency / channel races (lost edits and verdicts)

**B1. The IDE's verdict write is a lock-less, non-atomic read-modify-write.**
- `workspace-state.ts:186-197` (`writeVerdict`) reads `inbox.json`, merges, and
  `fs.writeFileSync`s it — no cross-process lock, no atomic rename. The daemon
  (`inbox.py:71-90`) and the serve hub both mutate the same file under a Python
  `FileLock` the IDE doesn't hold. This is exactly the bug class U9 fixed for
  `edits.json` (see `edits.py:592-603`), left unfixed for the verdict channel.
  Interleavings can lose an accept ("dead click") or resurrect just-consumed
  verdicts.

**B2. The hook's activity update is a lock-less RMW.**
- `hook.py:316-354` (`_handle_tool`): `_read_activity` (no lock) → mutate →
  `_write_activity` (lock held only for the write). Two concurrent hook
  invocations — parallel tool calls are normal — can drop each other's `touched`
  entries and phase marks. (`mark_feature_phase` in `activity.py:114-141` does
  hold the lock across its RMW; the hook path is the inconsistent one.)

**B3. Concurrent Claude sessions clobber each other's epoch.**
- `handle_session_start` (`hook.py:218-236`) **replaces the whole
  `activity.json`** with a fresh document. Two sessions in one repo (a second
  terminal, a subagent CLI, the hub's worktree agent with the same repo cwd):
  session B's start wipes session A's epoch + touch log; A's `Stop` then closes
  *B's* epoch (`handle_stop` reads whatever epoch is current). Epoch identity is
  effectively last-writer-wins; the daemon's edge detection
  (`watch.py:349-391`) gets rising/falling edges for *different* epochs.

**B4. Any Claude session — codoc-related or not — suppresses the loops and takes over the status bar.**
- The hooks are installed repo-wide; a session doing something entirely
  unrelated opens an interactive epoch, which (a) suppresses independent Loop A
  *and* human doc-edit routing for its whole duration (`watch.py:428-441`), and
  (b) flips the status bar to "agent working". Long-running interactive sessions
  (hours) mean the tree effectively stops syncing for hours. There is no notion
  of "this session is realizing codoc directives" vs "this session is unrelated".

### C. Dead or contradictory paths (documented surface ≠ actual behavior)

**C1. Raw `tree.codoc` text edits go nowhere — and can wedge or be clobbered.**
- Post-U7, Loop B's `_merge_channels` returns an **empty diff by design**
  (`loop_b.py:432-455`); the `commands` channel is the only apply path. But the
  daemon still routes a `tree.codoc` edit to Loop B as if it were user intent
  (`watch.py:453-460` via `has_user_edits`), which then does nothing with it.
- Consequences: the edited file now permanently differs from the store, so
  `safe_write_tree` refuses every future render (`reconcile.py:75-76`) — until a
  mutating Loop B pass calls the *unconditional* `write_tree`
  (`loop_b.py:938-945`) and **silently reverts the user's text**.
- Meanwhile `CLAUDE.md` ("The only human surface is `.codoc/tree.codoc`. You
  edit titles/descriptions directly") and the raw-text-editor affordances still
  advertise direct editing. Either the absorption path must come back or the
  file must be made genuinely read-only everywhere (docs, extension, watcher).

**C2. `tree.codoc`/`tree.doc.json` are committed to git, but there is no import path from git to the store.**
- `.gitignore:4-12` deliberately commits both files as "the team's shared
  intent map", and the store (`codoc.db`) is *not* committed. After a
  `git pull` / branch switch, the checked-out `tree.codoc` differs from the
  local store — and per C1 there is **no mechanism that absorbs that difference
  as commands**. The daemon will either skip renders forever or clobber the
  pulled file back to the local store's state, i.e. a teammate's tree edits are
  silently discarded. (Merge conflicts inside `tree.codoc` are a further
  unhandled case — conflict markers would parse as garbage titles.)

### D. Destructive-edit edges in the webview (the "how users actually delete" problem)

The settle pipeline itself is well-designed: identity-keyed diff against a cited
baseline (`commands-from-doc.ts`), deterministic `add` ids, ledger idempotency,
supersede/coalesce per feature, HLC per-feature adopt gate (`doc-gate.ts`). The
remaining holes are at the *edges*:

**D1. Delete → undo permanently loses the node.**
- Deleting a heading (any style — select-delete, line-delete, cut) produces a
  `retire` command at the next settle (`commands-from-doc.ts:157-162`). There is
  **no `unretire` command kind** (`COMMAND_KINDS`, `edits.py:154`). If the user
  hits ⌘Z after the settle (or pastes back what they cut), the restored heading
  carries a fid the baseline no longer has → `commandsForSettle` deliberately
  ignores it ("the projection is the baseline", `commands-from-doc.ts:136-140`)
  → the node vanishes on the next projection adopt. The store still has the
  retired row, but the user has no affordance to recover it. Cut/paste of a
  section across the 1200 ms settle debounce (`whole-doc-editor.ts:152`) hits
  the same path.

**D2. A settle can fire mid-gesture and capture transient states.**
- The settle debounce is time-based, not gesture-based: pause >1.2 s halfway
  through deleting a title char-by-char and the store gets `set_title` with the
  half-deleted string (an AMEND event, a held-draft directive, HLC bump). The
  supersede logic coalesces later, but the event ledger and activity feed record
  garbage intents, and an empty-but-present heading settles as `set_title: ""`
  (no validation anywhere on empty titles — check `apply.py`).

**D3. Retire suppression on an evicted baseline silently no-ops the delete.**
- When a settle cites no/evicted baseline, retires are filtered
  (`commands-from-doc.ts:194-199`) — correct for safety, but the user's delete
  then *silently reverts* on the next projection with no explanation. Safe, but
  reads as "the app ignored me".

**D4. Cross-node selection deletion is ambiguous and unconfirmed.**
- Selecting from mid-description of A through B's heading and deleting merges
  B's tail into A and emits `retire B` + `set_description A` — destructive
  (B's bindings are detached, `loop_b.py:719-722`) with no confirmation and no
  visual distinction from a prose edit. Retire is the one irreversible command
  (per D1) yet it rides the same silent settle as a typo fix.

### E. Hold/draft hygiene

**E1. Held drafts never expire and silently suppress Loop A.**
- A draft directive (`handed_off=False`) survives without `realize.md`
  (`edits.py:725-746`) and its feature enters `hold_set` forever. Doc-wins holds
  suppress code-side AMEND/RETIRE proposals (`loop_a.py` `held` plumbing) — so a
  forgotten draft means that feature's docs *silently stop following the code*
  indefinitely. Intents have a 7-day backstop (`INTENT_STALE_MS`); manifest
  drafts have none.

**E2. Draft affordances live partly in host memory.**
- `tree-editor.ts:600` filters the hand-off affordance through
  `draftFidsByUri` (in-memory). After a window reload the manifest still holds
  the draft (E1) but the UI affordance to hand it off or withdraw it may be
  gone. (Needs verification — see U5.)

**E3. Cancelled realize leaves partial-progress ambiguity.**
- `/codoc:sync` deletes `realize.md` only at the end (`sync.md:59-62`). Cancel
  after directive 2 of 5: fine, the floor keeps `awaiting_impl` — but the queue
  has no per-directive done-marking, so a re-run re-implements 1–2 (agent
  judgment whether that's idempotent), and the manifest's holds persist for
  already-implemented features until the whole queue clears.

---

## Part 2 — Known unknowns (verify before/while fixing)

- **U1. What exactly fires on interrupt in current Claude Code?** Test: Esc
  mid-tool, Esc between tools, `claude` killed with SIGKILL, terminal closed,
  VS Code window closed. Which of Stop/SessionEnd fire in each? (Design the
  lease TTLs from measured behavior, not assumptions.)
- **U2. Crash between store mutation and render.** Kill the daemon between
  `apply_op` and `write_tree` in a Loop B pass: does the next pass converge, or
  does the stale `tree.codoc` read as "pending user edits" and wedge renders
  (per C1's mechanism)? Write the test; the code path suggests a wedge.
- **U3. `edits.host.jsonl` `.merging` crash-recovery double-fires.** A crash
  mid-merge re-appends one-shot channels on recovery (`edits.py:697-708` calls
  it "low-stakes") — verify a duplicated steer doesn't produce two STEER
  directives to the agent.
- **U4. Loop B inside the UserPromptSubmit hook's 10 s timeout.**
  `_drain_inbox_fallback` (`hook.py:398-413`) runs a full Loop B pass inside a
  hook with `timeout: 10`; a slow pass gets killed mid-flight. The ledger
  should protect commands — verify verdicts/steers also survive a mid-pass kill.
- **U5. Draft state after window reload** (E2): is the hand-off/withdraw
  affordance rebuilt from `edits.json` `drafts` + the manifest, or lost?
- **U6. Undo semantics inventory.** Enumerate what ⌘Z can produce in the
  webview after each settle-boundary (D1) — this needs an actual EDH session,
  not code reading.
- **U7. Multi-window / multi-root.** Two VS Code windows on the same repo both
  run webviews (single daemon via lockfile, but two hosts appending host-ops and
  two in-memory baselines). Does the HLC gate + baseline history hold up?

---

## Part 3 — Unknown unknowns (blindspots the design has no story for)

1. **Git as an editing surface.** Branch switch, pull, rebase, stash — all
   rewrite `tree.codoc` *and dozens of code files at once* under the daemon.
   Today: no import path for the tree file (C2), and a checkout storm hits
   Loop A as if a human edited 200 files (LLM churn, mass retire/amend
   proposals). The system needs an explicit git stance: detect HEAD change and
   run a *reconcile* (state-based, no proposals for unchanged intent) instead of
   treating checkout as editing; define which of store vs committed tree.codoc
   wins after pull.
2. **Session identity.** "An agent is working" conflates: the realize agent,
   an unrelated Claude session, a subagent, the hub's worktree agent. Each needs
   different suppression/attribution behavior (B3/B4). The epoch model is
   single-slot; reality is N concurrent actors.
3. **The IDE as a crash domain.** Webview reload, extension host restart,
   VS Code update mid-edit — in-memory baselines, `draftFidsByUri`,
   comment threads, pendingFids all reset. Which user intent survives a reload
   is currently accidental.
4. **Clock skew / HLC across processes.** The version gate compares HLC strings
   minted by different processes (daemon Python, hub, host `Date.now()` salts).
   A machine with a jumping clock (sleep/wake, NTP) could make "strictly newer"
   comparisons misbehave.
5. **Scale edges.** Very large settle diffs (paste a whole document), 100+
   directive queues, `recent` log growth, sidecar size — no backpressure or
   bounds anywhere in the channel layer except `_MAX_RECENT`.
6. **Non-VS-Code editing of code** while no daemon runs (SSH, another editor):
   startup reconcile covers it, but only if the daemon is ever started again on
   that machine; the hub deployment story assumes the maintainer's daemon owns
   truth.

---

## Part 4 — The plan

### Invariants to adopt (the north star)

- **I1 — No lie without an expiry.** Every "in progress" indicator must be a
  *lease* (state + `last_seen` timestamp) and every reader must treat an expired
  lease as closed. No state may be cleared *only* by a hook.
- **I2 — Every multi-writer file is append-log or locked.** No lock-less RMW
  from any process (IDE included).
- **I3 — Destructive commands are reversible or confirmed.** `retire` must have
  an inverse (`unretire`) and/or a confirmation affordance.
- **I4 — Every queue has an owner and a recovery on owner death.**
- **I5 — Convergence.** With no new inputs, repeated passes must reach a
  fixpoint: `in_sync`, all derived files consistent with the store, no residual
  holds/drafts/status lies. This is testable and should be *the* core test.

### Workstream 1 — Truthful liveness (P0, fixes the reported bug)

1. **Lease-based epoch.** Add `epoch.last_seen` (updated by every hook write —
   already implicit in the file mtime) and define
   `epoch_alive = open && (now - last_seen) < TTL`. One shared helper in Python
   (`activity.py`) and one in TS (`activity-model.ts`); replace every raw
   `epoch.open` read (`isAgentActive`, `autorealize._epoch_open`, watch step 3).
   TTL: seconds-scale for UI display (~90 s), the existing 900 s for the
   daemon's "fold suppressed files back in" decision.
2. **Same for feature phases**: `phase.at` already exists — readers expire
   `editing`/`reflecting` after a TTL (~2 min) instead of trusting them forever.
3. **Register `SessionEnd`** in `hooks.json` (same handler as `stop`) so clean
   exits that skip Stop still close the epoch; keep the lease as the backstop
   for Esc/kill.
4. **Daemon idle repair.** Run the stale-epoch check on the timeout tick
   (before `if not changes: continue` in `run_watch`), and make recovery
   *write* `activity.json` (`epoch.open=false`, `features={}`) so every other
   reader heals too. Also `refresh_status` on recovery.
5. **Un-wedge `/codoc:sync`.** Make `realizing` a lease: `codoc_status` (MCP)
   reports stale-`realizing` as `awaiting_impl` (realize.md present) or
   recomputes; update `sync.md` to treat `realizing` with a stale timestamp as
   resumable. Progress writes already carry `at` (HLC) in `status.json`.
6. **Status floor after crash.** `safe_process_batch`'s exception path calls
   `refresh_status` (best-effort) so `tree_dirty`/`realizing` can't outlive the
   pass that wrote them.

### Workstream 2 — Channel hardening (P0/P1, prevents lost edits)

1. **Verdicts through the host-op log.** Add `appendVerdict` to the
   `edits.host.jsonl` op vocabulary (`_dispatch_host_op` already has the
   dispatch-table shape) or a parallel `inbox.host.jsonl`; delete the IDE's
   direct `writeVerdict` RMW. Same for any remaining direct IDE writes.
2. **Lock the hook RMW.** `_handle_tool` takes the activity lock across
   read+mutate+write (mirror `mark_feature_phase`).
3. **Multi-session epochs.** Change `activity.json` to
   `epochs: {session_id: {...}}` (or keep `epoch` as the *merged* view) so
   SessionStart doesn't wipe a concurrent session; `epoch_alive` = any live
   session. Daemon suppression keys off "any live *interactive* session", and
   attribution (touched/features) merges rather than replaces.
4. **Tag realize sessions.** The sdk/`CODOC_EPOCH_ORIGIN` mechanism already
   distinguishes loop_b epochs; extend so the *status bar* can say
   "implementing your edit" vs "a Claude session is active" (B4), and so an
   unrelated session doesn't suppress doc-edit routing for hours — consider
   suppressing Loop A only for files the session actually touched (the
   `touched` map exists) instead of all code files.

### Workstream 3 — Destructive-edit safety (P1)

1. **Add `unretire` as a command kind** (webview emits it when a settle's diff
   shows a fid returning that the store has retired; requires the settle diff to
   consult a "recently retired" set the projection can carry). Loop B maps it to
   an un-retire apply (the store already models lifecycle).
2. **Gesture-aware retire.** Only emit `retire` on a *stable* absence: node
   absent across two consecutive settles, or absent at commit (⌘S), or via the
   explicit delete affordance. A mid-debounce cut/paste then never mints one.
3. **Confirm bulk destruction.** N≥2 retires in one settle → the host asks
   (toast with undo) before appending the commands.
4. **Reject empty-title settles** (`set_title: ""` → keep local, mark invalid
   in-editor) and rate-limit mid-gesture AMENDs (settle only on
   selection-leaves-feature or idle ≥ debounce, which is current behavior —
   verify the debounce isn't restarted per keystroke… it is; consider also
   requiring caret-out-of-node for `set_title`).

### Workstream 4 — Surface truth: tree.codoc and git (P1)

Decide one of:
- **(a) Read-only export (recommended, matches U6/U7 direction):** the raw-text
  editor becomes explicitly read-only (extension sets `files.readonlyInclude`
  for `tree.codoc`, watcher stops routing its changes to Loop B, docs updated);
  **plus** a git-import path: on daemon startup / HEAD change, if the on-disk
  `tree.codoc` differs from the store *and* git says the file changed with
  HEAD, parse it and import the delta **as commands** (the one sanctioned
  inference point, gated to git-caused changes so it can't feedback-loop).
- **(b) Re-add text absorption** for all `tree.codoc` divergence (reverts U7;
  not recommended — it re-opens the feedback-loop class).

Either way: detect HEAD changes (`.git/HEAD` watcher) and route checkout storms
to `reconcile_drift` (state-based, no per-file LLM churn), not the edit path.

### Workstream 5 — Hold/draft/queue hygiene + `codoc doctor` (P2)

1. **Draft TTL + surfacing:** a held draft older than N days surfaces as a
   visible "stale draft" card (hand off / discard), and `codoc status` lists
   drafts with ages. Holds must never be invisible.
2. **Re-seed draft affordances** from `edits.json` `drafts` + manifest on host
   start (fix E2 if verification confirms it).
3. **`codoc doctor`** (or fold into `codoc status --repair`): one command that
   detects and repairs every stuck state in this audit — stale epoch, stale
   `realizing`/`tree_dirty`, orphaned manifest, `realize.md`↔manifest mismatch,
   diverged `tree.codoc`, orphaned `.merging` file, dead pidfile. The daemon
   runs the same checks at startup. This is the user's escape hatch while the
   preventive fixes land.
4. **Per-directive completion marks** in the manifest (the agent's
   `codoc_reflect(caused_by=d-id)` already identifies them — `sdk_realize`
   counts them; persist that as `done: true`) so a cancelled queue resumes
   instead of re-implementing, and holds release per-directive.

### Workstream 6 — The robustness harness (P0 in parallel — this is how we find the rest)

Build a **lifecycle simulator** test suite (pytest, deterministic, no LLM):

1. **Convergence property (I5):** from any reachable control-file state, run
   N idle passes → assert fixpoint (`in_sync`, derived files == store, no
   expired leases surfaced). Seed states from every scenario below.
2. **Crash injection:** kill (raise) at each yield point of a Loop B pass
   (post-`apply_op`/pre-`write_tree`, mid-merge `.merging`, post-manifest/
   pre-realize.md) and assert the next pass converges (extends the existing
   KTD8 ledger tests to the whole pass).
3. **Session chaos:** scripted hook sequences — SessionStart → tools → *no
   Stop*; two interleaved sessions; Stop for a wiped epoch — assert leases
   expire and status heals (drives Workstreams 1–2).
4. **Editing-gesture corpus:** drive `commandsForSettle`/`settleCommands` (and a
   headless TipTap doc where feasible) with recorded gesture traces:
   char-by-char delete with mid-pauses, select-across-nodes delete, line
   delete, cut→settle→paste, delete→undo, paste-a-document. Assert: no
   unintended retire, no lost node, no empty-title AMEND. Extend the existing
   vitest suites with this corpus.
5. **Git scenarios:** branch switch / pull modifying `tree.codoc` + code files;
   assert the chosen Workstream-4 semantics.

### Suggested order

| Phase | Content | Why first |
|---|---|---|
| 1 | WS1 (leases, SessionEnd, idle repair, sync un-wedge) + WS6.3 tests | kills the reported bug class outright |
| 2 | WS2 (verdict log, hook lock, multi-session) + WS6.2/6.1 | lost-edit/-verdict prevention |
| 3 | WS3 (retire safety) + WS6.4 corpus | destructive-edge data loss |
| 4 | WS4 (tree.codoc/git stance) + WS6.5 | resolves the doc/behavior contradiction |
| 5 | WS5 (`codoc doctor`, draft hygiene) | recovery + hygiene, builds on 1–4 |

---

## Appendix — quick-reference issue → fix mapping

| Issue | Fix |
|---|---|
| A1/A2/A3 stuck "editing"/agent-active | WS1.1–1.4 |
| A4 `/codoc:sync` wedge | WS1.5 |
| A5 silent deferral | WS2.4 |
| A6 sticky transient status | WS1.6 |
| B1 verdict race | WS2.1 |
| B2 hook RMW | WS2.2 |
| B3/B4 session identity | WS2.3–2.4 |
| C1/C2 tree.codoc/git | WS4 |
| D1–D4 destructive edits | WS3 |
| E1–E3 holds/drafts/queue | WS5 |
