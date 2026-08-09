# Residuals after the edit-tracking review (2026-08-08)

Review run `20260808-081758-51857537` against `fix/edit-tracking-robustness` left five open
findings (#2, #7, #16, #17, #18) plus six pre-existing ones (P-1…P-6). This records what
was fixed, what was found while fixing it, and what is knowingly still open.

## Closed

| Finding | Fix |
|---|---|
| #2 (P0) settle cites the arriving baseline | The editor owns the citation: `adoptedBaselineId` stamped at the end of `setDoc`, passed out through `onSettle`/`onCommit`. A deferred projection carries its baseline id with it. |
| #7 (P1) `base_text` from an unadopted projection | `state/known-store.ts`: a per-field optimistic overlay, advanced only by this host's successful appends and *pruned* (never seeded) by a confirming projection; everything else falls back to the cited baseline. |
| #16 hub viewer capability untested at the route level | `tests/serve/test_viewer.py` — no-auth → HANDOFF, per-session capability on `/api/payload`, the SSE route's viewer argument, and the 401 gate ahead of it. |
| #17 concurrent first-open can race the v6 migration | Every `ALTER` goes through `Store._add_column` (tolerates a peer's duplicate column); every backfill is gated on the data it writes; the schema pass retries a lock-refused attempt; and the journal-mode switch — the one lock `busy_timeout` cannot wait for — tolerates a peer performing it. Pinned by a 4-thread concurrent-open test, which caught the last two by flaking. |
| #18 fuzzer alphabet omits paste/IME/drag | Both prop fuzzers gained the three gestures as multi-transaction gestures, plus the projection-arrival flush and property `N2` (no silent revert). |
| P-3 `events.feature_id` backfill crash hazard | Data-gated like `rank`; a torn migration heals on the next open. |
| P-4 `heal_tree_integrity` re-parents without re-ranking | Re-homed nodes get a fresh append rank at their new parent. |
| P-5 outbox retries only on `online`/next message | Backoff timer (1 s → 30 s) in `createNetworkBridge`, plus `dispose()`. |
| P-6 `_supersede_directives` outside the ledger transaction | `_prune_dead_directives` re-derives the invariant once per Loop B pass, so the crash window heals instead of needing to be closed. |
| Agent-native reorder gap | `after_id`/`before_id` threaded through `tools.reflect`, `propose_add`, `propose_move`, `propose_plan` and `codoc propose --after/--before`. |
| `base_rev` residual | Deleted — nothing read it; `base_text` replaced it. |

## Found while fixing (not in the review)

**The hub never emitted authored commands.** `doc-view` posted `doc-settle` and
`serve/dispatch._settle` wrote the doc to `tree.doc.json` — a file the daemon has owned
since U4 and nothing has read as input since U7. A remote contributor's prose was
overwritten at the next daemon render, and the write itself made
`reconcile.safe_write_tree` treat the projection as ahead of the store and skip
re-rendering both exports. Fixed by giving the browser the host's role through the same
modules (`webview/command-emitter.ts`), and by removing every derived-artifact write from
`dispatch.py`. Two adjacent breakages went with it: the tree-pane drag posted a `move`
message whose shape the hub read as an identity-keyed command (appending a move with no
feature id to `edits.json` — now a distinct `tree-move` gesture), and `_hand_off` cleared
`drafts` without writing `handoffs`, so a maintainer's hand-off on the hub silently did
nothing.

## Still open

- **P-1 realize-queue manifest TOCTOU.** Unchanged: `read_manifest` still uses
  realize.md-absence as the completion signal, so the manifest-loss window needs a
  `triggered` flag on `Directive`. `_prune_dead_directives` narrows the blast radius (a
  directive can no longer outlive its feature) but does not close this.
- **Remote comment edit / resolve has no channel.** `edits.json` carries one-shot `steers`
  (what `comment-create` uses); there is nothing for editing or resolving an existing
  thread. The handlers now acknowledge instead of writing `tree.doc.json`, which is honest
  — the write did not apply the change either — but the surface is unimplemented, and the
  hub UI still offers it.
- **merge3 is line-granular**, so every title edit contends. Two hub authors on one-line
  descriptions will see frequent DEFERRED proposals. By design; watch it.
- **Rank keys grow** ~1 char per 62 same-slot insertions with no rebalancer.
- **`feature_writers` records the last writer only**, so arbitration cannot see a chain of
  writers behind the current text.
- **A 7-day-stale draft's hold lapses with no webview cue.**
- **Select-delete across a feature heading** files trailing prose under the wrong feature
  while the heading resurrects on the next projection (I1 by design — the sharpest
  remaining UX edge).
- **The client-supplied `session` tag must stay out of every outbound payload.** Forging it
  would let a command claim to be continuing somebody else's work and bypass arbitration.
- **Baseline eviction** still degrades gracefully rather than exactly: an unresolvable
  citation now claims the OLDEST retained baseline (cautious) instead of the newest
  projection (blind), and `settleDoc` drops baselines older than the one just cited to keep
  the live window inside the bound — but a settle can still, in principle, cite a baseline
  that is gone.
- **`decoration-cost.perf.test.ts` is timing-sensitive** — it failed once under a parallel
  load and passed on every re-run. If it flakes in CI, give it a floor rather than a
  deadline.
- **The concurrent-open test is a race, so it can only ever be evidence, not proof.** It
  found two real defects by flaking (see `00b34f9`) and is now 40/40 with a 60-trial probe
  clean, but a rarer interleaving would show up the same way: as a flake. Treat a failure
  there as a store bug until proven otherwise.
