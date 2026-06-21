# Core data-model & code↔tree merge — architecture review + proposal

**Date:** 2026-06-20 · **Status:** ✅ **Phases 1–3 IMPLEMENTED** (2026-06-20) ·
**Trigger:** post-merge of PR #11 (deployed-codoc-suggestion-surface). Review asked for
bugs/semantic-conflicts + "merge some of the attributes, change the data structure to
better host the domain, more robust merging algo."

> **Implementation note (2026-06-20).** All three sequenced phases are now built + tested
> (566 pytest core + 530 vitest + tsc/esbuild clean; only the pre-existing PyTorch-env-gated
> real-LLM E2E fails). Map: **Phase 1** — Proposal **B** (`codoc/loop/phase.py`
> `compute_phases`, the single projection; `render.write_sidecar` now emits `holds`/
> `hold_detail`/`feature_drift`/`feature_resolution` + the new `feature_phase` slice as thin
> views) · **D5** (`phase.is_held`, the one predicate all three loop guards call) · **D6**
> (`loop_b._snapshot_pre_mutation` → explicit `_PreMutation` object) · **C** (`delete_code`
> on the op — was already present; verified + locked, §7). **Phase 2** — Proposal **A1**
> (`Lifecycle` enum + `Feature.lifecycle`; `retired`/`realized` are derived `@computed_field`
> views; additive DB column + backfill migration; sidecar `lifecycle` + TS accessors).
> **Phase 3** — **D2** (binding-less `(title,parent)` identity guard) · **D3** (cross-file
> rename when `types_hash` globally 1:1-unique) · **D4** (`types_hash` backfill in reconcile)
> · **D1** (opt-in semantic title dedup, `loop/title_dedup.py`, `CODOC_SEMANTIC_DEDUP`).
> Plus the §6 PR #11 TS robustness fixes (anchored `parseRealizeProgress` + unified producer,
> qualified decl→feature spark mapping, bridge close-vs-switch dismiss + re-arm). The text
> below is the original proposal, kept for rationale.

This document was **propose-only** for the data-model reshape. The tactical bugs surfaced in
the same review were **already fixed** (see §7); the simplicity cleanup is applied. What
remains here is the deeper reshape, with options + a recommendation per item.

---

## 0. TL;DR

The authoritative state is small and healthy — `features`, `bindings` (with
`UNIQUE(file, symbol_path)`), `events`. Everything else is **derived re-emission**, and the
single concept **"this feature is mid-flight"** is independently encoded **≥8 ways across 6
control files, kept in sync by hand**. That hand-syncing is where every fragility in the merge
concentrates. Three moves fix the class of problem rather than the instances:

- **B. One derived "feature phase" projection** — compute the entire mid-flight status in *one*
  pure function from authoritative inputs; emit it as one slice. Kills the 8-way encoding and
  the 3 hand-synced hold-guards.
- **A. One `lifecycle` field** on Feature (replacing the `retired`+`realized` bool pair +
  scattered flags) so the domain's actual state machine is named, not inferred.
- **D. Robustness at the merge edges** — semantic (not exact-string) dedup, feature-identity
  guard, cross-file rename, and making the fragile ordering/derivation invariants *structural*.

Sequence them additive-first (§6) so nothing requires a risky big-bang migration.

---

## 1. The problem — attribute sprawl & hand-synced state

### 1.1 Authoritative vs derived (the good news)
Only three stores are authoritative: `features`, `bindings` (the `UNIQUE(file, symbol_path)`
constraint is the structural dedup guarantee), and `events` (append-only; `applied=0` = pending
proposal). On the IDE side, `tree.doc.json` (authored intent) + `edits.json`
(`intents`/`drafts`/`steers`/`cancellations`) are the authoritative input channels.

Everything in the sidecar (`tree.bindings.json`) — `by_feature`, `by_file`, `features{}`,
`proposals`, `changes`, `holds`, `hold_detail`, `feature_kind`, `feature_see_also`,
`feature_drift`, `feature_resolution`, `pitch`, the registry — is **derived and recomputable**.
That's fine in itself; the issue is *how many* derivations independently re-encode the same
domain fact.

### 1.2 "Mid-flight" is encoded eight times (the core sprawl)
The single user-facing concept *"this feature is doc-ahead / queued / being realized / drifted"*
is independently represented by:

| # | encoding | home | authoritative? |
|---|---|---|---|
| 1 | `Feature.realized = False` | features table | **input** |
| 2 | `edits.json drafts` | edits.json | **input** |
| 3 | `realize.json` manifest entry / `Directive.handed_off` | realize.json | **input** |
| 4 | `holds` = `intents` ∪ realize feature_ids | sidecar (derived) | derived |
| 5 | `hold_detail` (holds + a directive gloss) | sidecar (derived) | derived |
| 6 | `feature_drift` (`questioned`/`binding-lost`) | drift.json → sidecar | derived |
| 7 | `feature_resolution` (`scope`/`intent`) | resolution.json → sidecar | derived |
| 8 | `status.state ∈ {awaiting_impl, realizing}` | status.json | derived (global rollup) |

Only #1–#3 are authoritative inputs; #4–#8 are derived re-emissions of those plus the events
table. They overlap (e.g. an unrealized placeholder and a `binding-lost` realized feature both
mean "no code," distinguished only by history) and they are recomputed in *different places*
with *different filters*, which is exactly the desync surface.

### 1.3 Redundancy by admission
- `feature_see_also` ⟷ `feature_edges` — both computed from `code_edges`; render.py itself notes
  the overlap.
- `Binding.fingerprint` duplicates the index `tokens_hash` (the index is the source of truth; the
  binding caches it for a staleness compare).
- `drift.json` ⟷ `realized` — "no code now" is reachable two ways.

### 1.4 Where the fragility lands (fixed targets)
From the fragility inventory, the high-risk items all trace back to §1.2/§1.3 hand-syncing:
- **Stale hold can revert prose.** If `realize.md` is gone but `realize.json` lingers, the hold
  silently drops → a code-side AMEND rewrites prose under a pending edit.
- **Three duplicated hold-guards.** The hold check is applied separately in the `emptied`
  comprehension, the per-op loop, and `_compute_drift`; a held feature must be excluded in all
  three or it gets a contradictory retire/badge.
- **Pre-mutation diff ordering is an implicit contract.** Loop B must snapshot the text diff
  *before* draining verdicts; one reorder silently reverts accepted changes.
- **Re-emit filters can only drop, never recompute.** A stale `questioned`/`scope` badge survives
  every non-loop render until the next real loop pass — the badge can mislead between passes.
- **`Event.op_json` is opaque.** NodeOp fields aren't columns, so every pending scan deserializes
  + filters in Python with no DB-level integrity check.

---

## 2. Proposal B (do first) — one derived "feature phase" projection

**Change:** define a single pure function

```
feature_phase(feature, bindings, events, edits, manifest) -> Phase
```

where `Phase` is a small closed enum — e.g. `synced | drafting | queued | realizing | drifted |
divergent | planned | retired` — computed from the authoritative inputs (#1–#3 + events) in ONE
place. Emit it as ONE sidecar slice (`feature_phase`), and derive the existing UI slices
(`holds`, `hold_detail`, `feature_drift`, `feature_resolution`, the per-feature dot) as thin
views of it instead of re-deriving overlapping subsets.

**Why:** collapses the 8-way encoding to one source → the hold predicate is computed once and
consumed everywhere (fixes the three duplicated hold-guards), and the "stale badge between
passes" problem disappears because there is a single recompute path. The hold-set staleness that
can revert prose becomes a single guarded transition rather than a cross-file invariant.

**Risk:** low — additive. The function is pure and unit-testable against fixtures; the existing
slices can be re-expressed as views and diffed for parity before cutover.

**Recommendation:** **adopt.** This is the highest leverage / lowest risk change and unblocks
D5/D6.

---

## 3. Proposal A — one `lifecycle` field instead of `retired` + `realized` + flags

**Change:** the domain's real state machine is currently split across `Feature.retired` (bool),
`Feature.realized` (bool), and the scattered mid-flight flags. Name it:

```
lifecycle: planned → realizing → realized → (drifted | divergent) → retired
```

- **A1 (additive, recommended):** add a `lifecycle` column; keep `retired`/`realized` as derived
  read-only views for back-compat; migrate readers incrementally; deprecate the bools later.
- **A2 (replace):** drop `retired`/`realized`, migrate the table. Cleaner end-state, bigger blast
  radius (every reader + the TS parser + sidecar).

**Why:** `realized=False` today silently flips True on the *first* binding from *any* path
(relocation, coverage net, dedup) with no event recording the lifecycle transition distinctly
from the ATTACH. A named field makes the transition explicit and auditable, and gives Proposal B
a clean input.

**Recommendation:** **A1.** Additive; pairs with B (B reads `lifecycle` as one of its inputs).

---

## 4. Proposal C — make events queryable; store the RETIRE path decision

**Change:**
- Promote `op_kind`, `op_feature_id`, and keep `applied` as real **columns** (or a generated
  view over `op_json`) so pending-event scans are indexed and get a DB-level integrity check,
  instead of full `applied=0` scans that deserialize + filter in Python.
- Store the **detach-vs-delete** decision (`delete_code`) on the op *at raise time* so an inbox
  accept doesn't have to re-derive the path asymmetry (the current re-derivation is the spot
  where "accept a RETIRE" can wrongly delete or orphan code).

**Why:** removes the opaque-`op_json` fragility and the hand-coded path-asymmetry in two places.

**Recommendation:** adopt the `delete_code`-on-op part with B/A (cheap, removes a real bug class);
treat the column promotion as an optional perf/integrity follow-up.

---

## 5. Proposal D — a more robust merging algorithm

The merge is solid for *bound* symbols (tokens_hash move detection + types_hash same-file rename
+ the single-LLM-pass-with-all-titles dedup). It is fragile at the *identity* edges:

- **D1 — semantic title dedup.** Today `_norm_title` is exact-normalized-string only, so a
  paraphrased duplicate ("Persist drafts" vs "Save draft edits") slips past the dedup and mints a
  new node. The embedder is *already present* (bootstrap uses it). Add a near-duplicate gate in
  the coverage net / LLM-pass: when an unbound add's title is embedding-close to an existing
  feature, fold (ATTACH) instead of mint. **Recommend** (needs an eval pass against the test
  corpora to tune the threshold).
- **D2 — feature-identity guard.** `UNIQUE` covers bindings, not features, so binding-less theme
  parents can duplicate. Add a soft uniqueness on `(normalized_title, parent_id)` for binding-less
  nodes (or a structural check in the LLM pass). **Recommend.**
- **D3 — cross-file rename.** `_detect_relocations`' rename pass is same-file 1:1 only; a
  cross-file rename falls through to the LLM and may re-place as a new node. Extend the
  `types_hash` correspondence to cross-file when globally 1:1-unique. **Consider** (moderate risk
  of false pairing; gate on global uniqueness).
- **D4 — `types_hash=""` legacy bindings** silently disable rename detection. Backfill
  `types_hash` on the next index, or treat empty as "recompute, don't trust." **Recommend** (cheap).
- **D5 — single hold predicate** consumed by the loops (ties to Proposal B): removes the
  three-place hand-sync.
- **D6 — make the pre-mutation-diff ordering structural.** Pass the pre-mutation snapshot as an
  explicit object into Loop B's verdict drain rather than relying on call ordering, so a future
  reorder can't silently revert accepted changes. **Recommend.**

---

## 6. PR #11-specific robustness (deferred product calls from the review)

These are **misleading-signal / complexity** calls, not corruption — the reviews confirmed the
doc round-trip stays untouched (no doc-mutating path in bridge/presence). They're yours to weigh:

- **Bridge decl→feature mapping** (`state/bridge.ts`): qualify by full `symbol_path`, not the leaf
  name — two `def run` in different classes currently spark each other; fall back to the
  file-level marker when a binding leaf has no matching decl (today it silently lights nothing);
  handle realized `__module__`-only bindings as file-level; gate the reverse-spark to languages
  whose decl regex is actually defined (parity with the lens registration).
- **`userTouchedFids` suppression** (`state/bridge.ts`): treat phase `done` within an open epoch
  as agent-owned, and prefer suppressing by "file ∈ the agent's touched-this-epoch *write* set"
  over per-feature phase — this decouples from the `activity.json`-vs-filesystem write-ordering
  race that can both (a) false-fire "external drift" on the agent's own writes and (b) swallow a
  human's code edit on a held feature.
- **Dismiss-memory** (`providers/bridge-controller.ts`): a benign editor-group reshuffle / tab
  switch can permanently disable the bridge for the session (any bridge-opened file leaving the
  visible set is read as a dismissal, with no re-arm affordance). Track explicit close events;
  add a re-arm path.
- **`parseRealizeProgress`** (`providers/tree-editor.ts`): anchor the regex to the known
  `^implementing N/M` head and co-locate it with the producer — today any `status.detail`
  containing a stray `d/d` (a path, a date) parses as progress.
- **Simplicity #6 — the `fileLevel` / `likelyTargetFile` §A.4 path** (`bridge-controller.ts`): the
  most complex single branch in the bridge, guessing where not-yet-written code will go by walking
  parent→siblings. Either drop it (bridge only to code that exists — honest doc→code) or keep it
  as a deliberate spec item. **Lowest value-per-line in the new code.**

---

## 7. Already fixed in this pass (the real bugs)

Both the correctness and adversarial reviews converged on the **realize-progress signal**
(`codoc/loop/sdk_realize.py`) as the one place with real bugs. Fixed + regression-tested
(23 pytest in `tests/loop/test_sdk_realize.py`, all loop+store green):

1. **Empty `caused_by` over-count.** Every codoc MCP tool defaults `caused_by=""`; the falsy
   string bypassed the idempotency guard so *every* bookkeeping reflect bumped `_done` → the
   avatar read "implementing N/N" while the agent was still on directive 1. → `_advance_progress`
   now ignores untagged calls.
2. **Draft-inflated denominator.** `_total` counted draft (`handed_off=False`) directives that
   never realize this epoch → progress could never reach done/done. → `_load_manifest` now counts
   only handed-off directives.
3. **Stale denominator vs the append-only queue.** `_total` was read once at construction; the
   queue *appends* mid-epoch. → the denominator is re-read on each landing.

Also applied (simplicity pass): deleted dead helpers (`waveDelays`/`motionGuard`/`morphLifecycle`,
`agentStack`/`stackTooltip`, `hasIcon`) and deduped `bindingLeaf` → canonical `symbolLeaf`.

---

## 8. Suggested sequencing

1. **Phase 1 (additive, low-risk):** Proposal **B** (single phase projection) + **D5/D6** + the
   `delete_code`-on-op part of **C** + the PR #11 robustness fixes in §6. No migration.
2. **Phase 2 (migration):** Proposal **A1** (`lifecycle` column) + optional **C** column
   promotion. One schema migration, parity-tested.
3. **Phase 3 (algorithm, needs eval):** **D1** (semantic dedup) + **D2** (feature-identity guard)
   + **D3** (cross-file rename) + **D4** (types_hash backfill), validated against the `test/`
   corpora before shipping.

Each phase is independently shippable and reversible. Phase 1 alone removes the majority of the
hand-sync fragility for the least risk.
