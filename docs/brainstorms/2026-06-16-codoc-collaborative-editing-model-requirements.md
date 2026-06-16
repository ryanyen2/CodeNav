# Collaborative editing model — human ↔ AI change negotiation on the intent doc

> Status: confirmed · Created: 2026-06-16 · Type: Deep — product
> Scope locked via brainstorm dialogue 2026-06-16. Feeds `docs/plans/2026-06-16-001-feat-codoc-collaborative-editing-model-plan.md`.

## Summary

codoc's doc editing becomes **one surface where change flows both ways** between a single human and multiple AI agents. You just edit; codoc classifies each change — pure-doc tweaks commit, code-implying ones become tracked suggestions the agents realize. Every *unresolved* change wears a calm decoration in one of two states — *awaiting AI realization* or *awaiting your review* — and resolution is a negotiation: faithful realizations clear silently, divergent ones surface back for you to agree/reject. It is built on a transaction-based tracked-changes engine, not the current text-diff heuristics.

---

## Problem Frame

Today the webview has a manual **Editing/Suggesting** toggle plus a pen/pencil "to AI / take back" instrument, and "suggestions" are derived by **text-diffing two doc snapshots** then round-tripped through the file + daemon. That model is fragile and confusing: the inline diff only appears after the caret leaves a node, flickers and vanishes within a second (the daemon auto-applies the doc-ahead intent), and saving collides with the daemon (`"content of the file is newer"`). The deeper cause is architectural — a client-side editing affordance is coupled to a file→host→daemon→file round-trip with whole-document replacement, held together by heuristic `if/else` that doesn't survive real workflows.

The desired world: editing the intent doc is **instant and robust**, and a change from *either* side (you or an agent) is a first-class, attributed, **negotiable** unit — visible while unresolved, resolved by the other side, never silently lost or clobbered.

---

## Actors

- **A1 — Human author** (single, today). Authors intent in the doc; reviews and accepts/rejects agent-originated changes; can withdraw their own pending suggestions.
- **A2 — AI agents** (N, distinct identities: `claude-code`, `codex`, `gemini`, `cursor`, …; already modeled as `AuthorRole`). Realize human intent in code and reflect it back; propose doc changes (drift, reflection, amend). Each carries its own author identity/ink.
- **Mediator — the daemon + loops** (`codoc watch`, Loop A/B, the change ledger, `edits.json`/intents, doc-wins holds, `activity.json`). Not a UI actor; it classifies changes, queues realization, reconciles, and attributes authorship. The transport is **asynchronous** (files + daemon), not real-time.

---

## Key Flows (the desired results codoc must support)

**F1 — Pure-doc edit (no code implication).** The human renames a feature or rewords prose with no code meaning. It commits directly; no pending decoration, no ceremony. *Desired result: trivial intent edits feel instant.*

**F2 — Code-implying edit → realization (the core loop).** The human edits intent that implies code work. It commits as their text immediately and the feature gains a calm **"being realized"** badge. An agent picks it up, implements in code, and reflects the result back. If the realization is faithful, the badge clears silently. *Desired result: "I change the intent, the code follows, and I can see it's handled — without being nagged."*

**F3 — Divergent realization surfaced back.** An agent realizes a human suggestion but **diverges** — touches more than the edited feature, changes the meaning, or hits ambiguity. The agent's actual change surfaces back as an agent-authored tracked diff in **"awaiting your review"**; the human agrees (commit) or rejects (revert / redo). *Desired result: "When the AI does something other than what I meant, I see it and decide."*

**F4 — Agent-initiated proposal (drift / reflection / amend).** An agent detects code drift, or proposes a doc change the human didn't make. It renders inline as an agent-authored strike/insert tracked diff in **"awaiting your review"**; the human accepts or rejects in place. *Desired result: "The AI can propose doc changes; I review them inline, not in a separate panel."*

**F5 — Withdraw / reject before resolution.** The human changes their mind on a pending suggestion → withdraw reverts the intent and cancels the queued realize directive. Rejecting an agent proposal reverts it. *Desired result: "Nothing commits against my will; I can always back out."*

**F6 — Concurrency (doc-wins).** The human edits feature F while an agent is mid-realization on F (or an agent proposes on F while the human is editing it). The human's in-flight edit **holds** the agent's reflections on F; the agent's proposal queues behind the human's active edit. *Desired result: "My edits and the AI's don't clobber each other; the doc wins while I'm working."*

**F7 — Multi-agent.** Agent A realizes a human suggestion on F; agent B (drift) also wants F. The daemon serializes the work; authorship attributes each agent distinctly. *Desired result: "Multiple agents can work without stepping on each other, and I can see who did what."*

**F8 — Structural edits (robustness).** Adding a `##` heading creates exactly one new feature (reliable mint, no double-add); blank lines between features are cosmetic and normalized; deleting a feature is a retire intent and undo restores it cleanly. *Desired result: "Editing the outline as text is robust — add/remove/reorder does what I expect."*

---

## Acceptance Examples

- **AE1 (F1):** Human renames `Auth` → `Authentication` (title-only, no code implication). On settle, `tree.codoc` shows the new title; no badge, no pending state.
- **AE2 (F2):** Human appends "…and rate-limit login attempts" to a description. The feature shows a "being realized" badge; an agent implements rate-limiting and reflects; the badge clears with no human click.
- **AE3 (F3):** Same edit, but the agent also changes an unrelated feature's bindings / touches more than the edited feature. The change surfaces back as "awaiting your review" with a tracked diff; the human rejects → reverts.
- **AE4 (F4):** An agent reflection finds the code now does something the doc omits and proposes a description amend as strike/insert; the human accepts → committed.
- **AE5 (F5):** Human types a code-implying edit (badge appears), then withdraws before any agent runs. Intent reverts to baseline; no realize directive remains queued.
- **AE6 (F8):** Human types `## New Feature` mid-document. Exactly one new feature is created (no duplicate), minted with a stable id; saving does not conflict.
- **AE7 (save-integrity):** Human edits in the webview while `codoc watch` is running. Saving never raises "content of the file is newer."

---

## Requirements

- **R1** One editing surface. Remove the Editing/Suggesting toggle and the pen/pencil "to AI / take back" instrument.
- **R2** Per-edit classification via the existing implies-code gate (daemon-side `classify.py`): pure-doc → commit directly; code-implying → tracked suggestion.
- **R3** Two unresolved states with distinct, calm decorations: *awaiting AI realization* (human-proposed) and *awaiting your review* (AI-done).
- **R4** Asymmetric pending visual: the human's own edit commits as their text + a "being realized" badge; an agent's change renders as a strike-old/insert-new tracked diff to agree/reject.
- **R5** Resolution = **confirm-only-on-divergence**: faithful realizations auto-resolve and clear silently; divergent realizations surface back for review.
- **R6** Bi-directional and resolvable by the other side (human ↔ agent), with agent↔agent realization serialized by the daemon.
- **R7** N-author attribution (per-agent identity); the model is multi-agent now with a single human, and stays N-author-capable.
- **R8** Single-writer `tree.codoc`: the daemon is the sole writer; the webview's authoritative artifact is `tree.doc.json`. Fixes the save-conflict.
- **R9** Tracked changes derived from real editor **transactions** via a vendored engine — no snapshot text-diff guessing.
- **R10** Robust structural edits: reliable heading add/mint (no double-add), blank-line normalization between features, clean delete/restore.
- **R11** Withdraw/reject reverts the intent and cancels any queued realize directive.
- **R12** Concurrency via the existing **doc-wins holds** (a held feature suppresses code-side ops while the human's edit is unresolved).

---

## Key Decisions

- **KD1 — Vendor the MIT tracked-changes engine** (`sungkhum/tiptap-track-changes`, MIT) into the repo with attribution, rather than text-diff or a paid/Yjs-based product. It is transaction-based (intercepts edits to mark deletions/insertions), zero-runtime-dep, TipTap-native (peer `@tiptap/core ^2||^3`; we run 2.27.2), and tested. It exposes `getBaseText()`/`getResultText()`/`getTrackedChanges()` and per-change accept/reject, which solves serialization.
- **KD2 — The marks-engine is used primarily for the agent→human direction** (proposals + divergent realizations, where old + new must coexist for review). Human edits commit as their text + a badge; we do not strike the human's own typing. *Why:* no cursor-jank on your own edit, and the daemon classifies asynchronously without flicker.
- **KD3 — `tree.codoc` reflects the human's new intent immediately**; the badge means "code is catching up." *Why:* matches "suggesting = hand it to the AI to realize."
- **KD4 — Single-writer `tree.codoc`** (daemon sole writer; webview → `tree.doc.json`). *Why:* removes the two-writer mtime race behind the save-conflict.
- **KD5 — Confirm-only-on-divergence.** *Why:* respects the human's attention; round-trip review fires only when it matters.
- **KD6 — No real-time infrastructure; async via files/daemon; N-author-capable.** *Why:* matches codoc's identity (a local, file-backed, single-author-of-record intent tool with AI agents as collaborators).
- **KD7 — Retire heuristic machinery:** snapshot text-diff (`diffDocsToSuggestions`), dual-state-by-caret, `pendingDocAhead`, conditional apply/strip, and the pen/pencil re-stamp all go away — the change state lives in the doc as transaction-derived marks, not reconciled across a round-trip.

---

## Scope Boundaries

### Deferred for later
- Real-time multi-user co-editing — live cursors, presence, simultaneous human editing (CRDT/Yjs + a sync server). The model stays N-author-capable so this is an extension path, not a rewrite.
- Human ↔ human resolution (one human today).

### Outside this product's identity
- codoc is a **local, file-backed, single-author-of-record intent tool** with AI agents as first-class collaborators. It is explicitly **not** a Google-Docs-style realtime multiplayer prose editor; the tracked-change UX borrows the *look*, not the multiplayer infrastructure.

---

## Open Questions

- **OQ1 — Divergence detection.** What counts as an agent realization "diverging" enough to surface back (F3/AE3)? Candidate signals: the agent touched features/bindings beyond the edited feature; the reflected doc change differs from the human's text beyond a threshold; the agent flagged ambiguity. Resolve the precise rule during planning/implementation; ship a sensible default (touched-beyond-the-edited-feature OR reflected-doc-change-beyond-the-human's-text).
- **OQ2 — Badge ↔ activity coupling.** Exactly how the "being realized" badge derives from `activity.json` phases (`editing`/`reflecting`) and the realize-directive lifecycle.

---

## Dependencies / Assumptions

- Reuses the existing daemon (`codoc watch`), Loop A/B, `edits.json`/intents channel, doc-wins holds, the change ledger (`actor`/`mode`/`caused_by`), and `activity.json`.
- Assumes the vendored tracked-changes engine integrates with the existing `codocExtensions()` schema (featureHeading/paragraph/codeRef + marks) — verified compatible with TipTap 2.27.2.
