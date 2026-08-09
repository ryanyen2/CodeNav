---
title: "A merge base sourced from the newest projection silently reverts the other party's edit"
date: 2026-08-08
last_updated: 2026-08-08
category: docs/solutions/logic-errors
module: "vscode-codoc/src/webview/tiptap/whole-doc-editor.ts + src/providers/tree-editor.ts + codoc/loop/loop_b.py"
problem_type: logic_error
component: tooling
symptoms:
  - "An agent's amend to feature B disappears when the user was typing in feature A at the moment the projection carrying it arrived"
  - "codoc history shows no merge, no SUPERSEDED and no conflict for the lost text — the store simply holds the older version"
  - "The three-way merge (merge3 / _resolve_content) never fires in practice even though its unit tests pass"
  - "Two IDE windows on one repo: window A's settle overwrites window B's prose with no conflict recorded"
root_cause: logic_error
resolution_type: code_fix
severity: high
tags:
  - base-text
  - three-way-merge
  - projection-baseline
  - optimistic-concurrency
  - silent-data-loss
  - store-authoritative
  - loop-b
  - command-channel
---

# A merge base sourced from the newest projection silently reverts the other party's edit

## Problem

codoc's authored-edit channel carries `base_text` on every content command: the value the
AUTHOR last knew for the field being replaced. `loop_b._resolve_content` merges from it —
disjoint edits both land, contended ones are arbitrated by rank, peers go to review.

Two bugs put a value in `base_text` that the author had never seen, and both failed the
same way: **silently, on the success path**. If `base_text` happens to equal what the store
currently holds, `_resolve_content` reads a clean continuation and applies the incoming text
verbatim — no merge, no arbitration, nothing in the event ledger. The other party's write is
gone and nobody, including the author who overwrote it, is told.

## Symptoms

The branch's flagship scenario — "the user keeps typing while an agent edit lands" — lost
the agent's edit every time. Concretely (review findings #2 and #7):

1. The user types in feature A. Their editor is showing projection #8.
2. An agent amends feature B in the store; the daemon renders projection #9.
3. The host reads #9 and posts it. The editor's `setDoc` FLUSHES the unsent typing first
   (correct — otherwise the version gate's slice swap discards it), so the settle carries
   pre-adoption content: A's new text and B's OLD text.
4. `doc-view.ts` stamped that settle with `payload.baselineId`, read from the module-level
   `payload` variable — which the message handler had already reassigned to #9.
5. The host diffed the pre-adoption doc against #9's units. B's old text ≠ #9's agent text,
   so it emitted `set_description(B, old text)` for a feature the user never touched.
6. `base_text` for that command came from `knownStoreByUri`, which every `buildPayload`
   reset from the projection it had just read — so it was the agent's text, i.e. exactly
   what the store held. `moved = False` → CLEAN → applied verbatim.

Finding #7 is the same mechanism without the flush: the host reads projections the webview
may never adopt (the doc gate defers during IME composition and while a comment composer is
open, and keeps a feature local while its edit is unsent), so `base_text` taken from the
newest projection routinely claimed the author had seen a write they had not.

## What Didn't Work

Fixing the citation alone leaves the loss in place. Half the mechanism is the citation
(which baseline the diff runs against, i.e. WHICH commands are emitted) and half is the
`base_text` source (what each command CLAIMS to replace, i.e. whether the daemon merges or
overwrites). With the citation fixed but `knownStore` still mirroring projections, any
window where a third party writes between the author's last adopt and their next settle
still produces a blind overwrite. They had to land together.

A test oracle based on "the adopted doc's text equals the foreign write" also misfires:
prose with no owner (a paste, a block split) attributes positionally, so the rendered
slice can differ from the projection by a paragraph the author put there. The oracle has
to key on the PROJECTED text of the features the gate actually adopted.

## Solution

**The editor owns the citation.** `mountWholeDocEditor` records `adoptedBaselineId` at the
END of `setDoc` — after the flush, after the gate — and `onSettle`/`onCommit` pass it out.
`setDoc` and the deferred-projection stash take the `baselineId` alongside the doc, so a
projection deferred through an IME composition cannot be adopted under a citation that has
since moved on:

```ts
// whole-doc-editor.ts — inside setDoc, after gateProjection + the adopted bookkeeping
adoptedBaselineId = baselineId;
// doc-view.ts — the editor supplies the citation; `payload` is not consulted
onSettle: (doc, baselineId) => vscode.postMessage({ kind: 'doc-settle', doc, baselineId }),
```

**`base_text` comes from the author, and a projection may only retire it.** The host's
`knownStoreByUri` became a per-field OPTIMISTIC OVERLAY (`state/known-store.ts`):
`advanceKnown` folds in commands this host successfully appended; `pruneKnown` drops a
field once a projection shows the store agrees. Nothing else may write to it. Everything
absent falls back to the baseline the settle CITES:

```ts
// commands-from-doc.ts
const known1 = known?.get(u.fid);
base_text: known1?.title ?? b.title          // own unechoed write, else what the author saw
```

Per FIELD, not per feature: a whole-unit overlay had to fill the untouched field from
somewhere, and the only thing available was the projection — smuggling it back in through
the field the author never edited.

**When the base cannot be established, claim an OLDER one.** An unresolvable citation
(the baseline was evicted from the bounded history) now falls back to the oldest retained
baseline rather than the newest projection, and `settleDoc` drops baselines older than the
one just cited so the live window stays inside the bound.

The same modules now drive the hub: `webview/command-emitter.ts` gives the browser the
host's role, because on the hub the browser is the only party that ever sees a projection.

## Why This Works

The invariant is a provenance rule, not a comparison: **`base_text` may only come from the
author.** There are exactly two honest sources — this host's own emitted-but-unechoed
writes, and the baseline the settle cites — and a projection is not a third one, because
"what the store holds" and "what the author last knew" are different facts that diverge
precisely when somebody else has written. Sourcing from the projection made them look
identical, which is why the failure was invisible: the guard's own input had been rewritten
to agree with the thing it was guarding against.

The asymmetry is what makes the fallback safe. An under-claimed base makes the daemon
cautious (it sees divergence and merges, or defers to review); an over-claimed one makes it
blind. Both are wrong, only one loses text, so every uncertain path is resolved downward.

## Prevention

- **Never source a "what the author knew" value from a channel the author does not
  control.** Ask, for each field on a command: could this have been written by a process
  the author never saw? If yes, it is not provenance, it is a guess.
- **A settle flushed by an arriving update must cite the state it was computed from.** Any
  code that reads a module-level "current payload/version/rev" at POST time rather than
  capturing it at COMPUTE time has this bug shape, whatever the domain.
- **Test the silent path, not the loud one.** These bugs produce no error, no conflict and
  no log line, so a passing suite proves nothing about them. The pinning tests are
  `src/test/virtual-user.props.test.ts` (property `N2`, plus the deterministic
  `a projection arriving mid-word …` block) and `src/test/known-store.test.ts`. N2's
  oracle counts CLEAN applies over foreign text the editor never adopted; it reports 46
  reverts under the pre-fix citation order and 0 after, and its companion test deliberately
  reproduces the pre-fix order so the oracle can never go vacuous.
- **Model the clocks separately in the harness.** A fuzzer whose alphabet is
  single-transaction keystrokes never visits the states where this class lives; paste, IME
  composition and drag arrive as several transactions with no settle between them, which is
  exactly the window a projection lands in.

## Related Issues

- `docs/solutions/logic-errors/multi-feature-file-activity-fanout.md` — the sibling shape:
  one signal derived twice on opposite sides of a file boundary, where fixing one side is a
  silent no-op.
- `docs/residual-review-findings/2026-08-01-pre-deploy-audit.md` — recorded the blind
  last-writer-wins this closes.
