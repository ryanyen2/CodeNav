# In-situ suggesting mode for the codoc webview — requirements

**Date:** 2026-06-18
**Scope:** Deep — feature (architectural change to the webview decoration/suggestion system)
**Status:** Requirements (pre-plan)

## Summary

Rebuild the codoc webview's change decorations as a robust, in-situ **suggesting mode** on the vendored tracked-changes engine. A code-implying intent edit — auto-classified at settle, or any **bolded** span as an override — becomes a tracked-change suggestion the author drafts freely and releases with one explicit **hand-off**; plain prose keeps committing live. The agent's code→codoc changes surface in the *same* in-situ grammar (author-tinted) with inline accept/reject. All marks render against a **stable per-feature baseline** so the diff never erodes or flickers, and the interaction is smooth — no cursor or scroll jumping.

## Problem Frame

Today's decorations (pending dot + gutter rail + changed-text underline) hang off the per-pass realize-directive/hold lifecycle, which is volatile. A ce-debug session (2026-06-18, live repro in the `lmpo` test repo) confirmed three coupled failures:

- **Eroding baseline.** The changed-text underline diffs against a baseline captured *fresh each pass* (`codoc/loop/loop_b.py`, commit `25ec557`). Iterating erodes it — typing `"Should also…` then deleting the `"` makes current ≈ baseline → the decoration vanishes.
- **Phantom re-apply / oscillation.** The `tree.doc.json` ↔ store serialization round-trip isn't idempotent, so `diff_codoc` keeps seeing a one-character normalization delta as a new edit → the daemon re-applies and re-renders repeatedly (observed: 4× identical "queued 1 directive" log lines), churning the hold and blinking the decoration.
- **Lifecycle coupling.** Dot, rail, and underline all derive from the realize directive + hold, so any churn in that lifecycle makes them flicker.

The fix is architectural: drive the in-situ change display from a stable baseline diff via the tracked-changes engine, decoupled from the realize lifecycle — and make the interaction jank-free, which prior decoration work (cursor jumps, tree scroll-thrash) repeatedly failed at.

## Key Decisions

- **True suggesting semantics (reverses U3 for code-implying edits).** A code-implying edit is a *proposal* that doesn't reach the agent or the authoritative doc until the author hands it off — not an immediate commit. We accept the cursor-jank risk U3 cited because the vendored engine (`vscode-codoc/src/webview/tiptap/track-changes/`) is built for tracked-suggestion typing, unlike the old text-diff approach U3 removed.
- **Prose commits live; only code-implying edits draft.** Documentation rewords stay friction-free and immediate; the deliberate suggestion + hand-off gate applies only where code actually changes. This reconciles suggesting with U3 rather than fully reversing it.
- **Auto-classify at settle, bold as a hard override.** The daemon's code-implication gate decides draft-vs-live at settle; a newly-bolded span unconditionally drafts as a code request (reuses the existing bold→Focus channel) and gives the author a reliable manual signal when the heuristic is wrong.
- **Batch hand-off, not per-edit accept.** One explicit action commits all pending drafts at once. The author never accepts their own edits individually; per-✓/✗ is reserved for the agent's direction.
- **Stable baseline.** The diff is computed against the feature's last committed/synced description, persisted across edits and re-renders. This is the foundation that makes everything else robust.
- **Decoupled realize state.** "Being realized" is an orthogonal, calm indicator separate from the suggestion marks; it never gates or blinks the diff.

## Actors

- **Human author** (single, today) — drafts intent as suggestions, hands off to the agent, accepts/rejects the agent's surfaced changes.
- **AI agent(s)** (`claude-code`, `codex`, …; distinct author identities) — realize handed-off intent in code; surface code-derived description changes back as suggestions.
- **codoc daemon / loops** — classify edits, maintain the baseline, queue realize directives, render the authoritative doc + sidecar.

## Key Flows

1. **Draft → hand off → realize (the core loop).** Author types a code-implying edit → it shows as a draft suggestion (their tint) against the stable baseline → author keeps iterating freely → hits hand-off → all drafts commit as authoritative intent, code-implying ones queue directives → agent realizes in code → on sync the feature's baseline advances and the marks clear.
2. **Prose edit (live).** Author rewords prose → at settle it's classified non-code-implying → commits live to the authoritative description, no draft, no hand-off.
3. **Agent surfaces a code→codoc change.** Loop A detects drift → renders a tracked suggestion (agent tint) on the description with inline ✓/✗ → author accepts (commits) or rejects (discards).
4. **Add a new node.** Author adds a feature → the view stays put (no scroll/caret jump); the new node participates in the same suggestion grammar.

## Requirements

**Suggestion model & classification**
- **R1** A code-implying edit to a description becomes a tracked-change suggestion held in draft — not committed to authoritative intent, not handed to the agent — until hand-off.
- **R2** A non-code-implying (prose) edit commits live to the authoritative description, with no draft state.
- **R3** An edit is treated as code-implying when the daemon's code-implication gate classifies it so at settle, OR when it contains a newly-bolded span (bold is an unconditional override).
- **R4** Suggestion marks render in-situ within the description text, tinted per author identity (human vs each agent), via the vendored tracked-changes engine.

**Stable baseline & diff**
- **R5** A feature's in-situ diff is computed against a stable baseline — its last committed/synced description — persisted across edits and re-renders until the next commit/sync, never reset per keystroke or per pass.
- **R6** Iterating, undo/redo, or partially reverting a draft must never erode or vanish the marks; they always reflect the cumulative change against the stable baseline.

**Hand-off & commit**
- **R7** One explicit hand-off action commits all pending drafts at once: they become authoritative intent and the code-implying ones queue realize directives.
- **R8** The hand-off affordance surfaces the count of pending drafts and is reachable in-editor without leaving the feature.
- **R9** A pending draft can be withdrawn (discarded back to baseline) before hand-off without committing.
- **R10** Re-editing a feature that already has a pending draft refines that one draft; it must not stack multiple directives for the feature.

**Agent direction (code→codoc)**
- **R11** A code-derived description change from Loop A renders in the same in-situ tracked-change grammar (agent tint) with inline accept (✓) / reject (✗).
- **R12** Accepting commits the agent's change to the description; rejecting discards it.

**Realize state (decoupled)**
- **R13** "Being realized" (handed-off, agent working) is a calm indicator separate from the suggestion marks; it never gates or blinks the diff. The lifecycle is drafting → handed-off/realizing → synced (clears).

**Interaction robustness**
- **R14** The caret never jumps on a daemon round-trip, payload reload, or suggestion materialization — it stays where the author is typing.
- **R15** Neither the tree pane nor the editor pane scroll-jumps on payload reload, agent activity, or node add/remove; scroll position is preserved.
- **R16** Adding a new feature node does not move the caret or scroll the view away from the author's position.
- **R17** Typing within a draft is smooth: no per-keystroke flicker and no jarring mid-word "flip" of live text into a mark — classification and conversion happen at settle, not per keystroke.
- **R18** Decorations are stable across daemon re-processing; they change only when the underlying change or baseline actually changes, never on a no-op pass.

**Round-trip idempotency (prerequisite)**
- **R19** The `tree.doc.json` ↔ store serialization round-trip must be idempotent: a re-render differing only by normalization must not be read as a new edit. This kills the phantom re-apply/oscillation that underlies R6, R18, and the repeated daemon-log lines.

**Surface boundary**
- **R20** Suggesting mode is the `Codoc Tree` webview's behavior; the raw `tree.codoc` text editor continues to commit directly (no tracked-suggestion mode there).

## Acceptance Examples

- **AE1** Type `Should also cache the palette lookups`, then delete a character — the suggestion mark persists and the diff still shows the full added span (the exact bug that triggered this work). (R5, R6)
- **AE2** Edit a description, pause so the daemon round-trips — the caret stays at the typing position, not the heading or doc top. (R14)
- **AE3** Add a new feature node while scrolled mid-document — the view does not jump to the top or to the new node. (R15, R16)
- **AE4** Draft code-implying edits across three features plus a prose reword on a fourth, hit hand-off — the three realize, the prose change was already committed live. (R1, R2, R7)
- **AE5** The agent surfaces a code-drift description change — it appears as an agent-tinted tracked diff with ✓/✗; accept commits it into the description. (R11, R12)
- **AE6** Make one edit — the daemon log shows a single pass, not repeated identical "queued" lines. (R18, R19)

## Scope Boundaries

- **Webview-only.** The raw text editor's direct-commit behavior is unchanged (R20).
- **No per-suggestion accept of the author's own edits** — release is the batch hand-off; per-✓/✗ is the agent direction only.
- **Single human author** — multi-human concurrent suggesting is not in scope (the engine's multi-author tinting is used for human-vs-agent, not human-vs-human).

## Outstanding Questions

- **Classifier precision.** `classify.is_imperative` matches cues anywhere in a description, so edits to an already-imperative feature may always draft. Bold override mitigates; an LLM-backed classifier may be needed if false-drafts are common.
- **Symmetric coexistence.** When the author's draft and an agent's surfaced suggestion target the same description simultaneously, how do the two mark-sets interact (precedence, visual separation, what hand-off/accept does to the other)?
- **Baseline storage.** Where the stable per-feature baseline lives and exactly when it advances (on hand-off? on code sync? on accept of an agent change?) — a planning decision with correctness implications.
- **Hand-off affordance.** Label, placement, and whether it is per-feature or whole-doc (leaning whole-doc, one gesture).
- **Realize indicator.** Whether the decoupled realize state reuses the existing pending dot or gets a distinct calm treatment (design-taste pass).

## Dependencies / Assumptions

- **Vendored tracked-changes engine** (`sungkhum/tiptap-track-changes`, in `vscode-codoc/src/webview/tiptap/track-changes/`), today used only agent→human (U4). This design makes it the canonical representation for both directions.
- **Single-writer model (U2b)** — the host owns `tree.doc.json`; the daemon owns `tree.codoc`. Draft suggestions live host-side until hand-off; R19 hardens the round-trip this depends on.
- **Existing channels.** Hand-off maps to the settle/commit path Loop B already processes; agent accept/reject maps to the `inbox.json` verdict channel; the coalescing fix (`876eab9`) already prevents directive stacking (R10).
- **EDH verification is mandatory.** `tsc`/vitest/esbuild cannot catch TipTap-view regressions (cursor/scroll/flicker are exactly R14–R18); every stage needs manual Extension-Development-Host verification, and the interaction requirements are the acceptance gate.
- **Design-taste-frontend** governs the visual grammar: in-situ, calm, one status-axis accent, motion only when motivated, no margin-card clutter.
