---
title: "feat: in-situ suggesting mode for the codoc webview"
type: feat
status: active
date: 2026-06-18
origin: docs/brainstorms/2026-06-18-codoc-insitu-suggesting-mode-requirements.md
depth: deep
---

# feat: in-situ suggesting mode for the codoc webview

## Summary

Rebuild the webview's change decorations as a real suggesting mode on the vendored tracked-changes engine. Code-implying intent edits become tracked ins/del suggestions held against the **engine's own baseline** (`getBaseText`), released to the agent by one **hand-off** action; prose commits live. The agent's code→codoc changes use the same author-tinted marks with inline accept/reject. A prerequisite round-trip-idempotency fix kills the phantom re-apply that drives the flicker, and a dedicated interaction-hardening unit gates cursor/scroll stability. The diff stops eroding because the engine — not a per-pass field — owns the baseline.

## Problem Frame

Today's decorations hang off the volatile per-pass realize-directive/hold lifecycle. A ce-debug session (2026-06-18, live repro in the `lmpo` test repo) confirmed three coupled failures: the changed-text baseline is captured fresh each pass (`codoc/loop/loop_b.py`, commit `25ec557`) so iterating erodes it (delete one `"` → the underline vanishes); the `tree.doc.json`↔store round-trip isn't idempotent so `diff_codoc` sees a one-char normalization delta as a new edit, re-applying in a loop (4× identical "queued" log lines) that churns the hold; and the dot/rail/underline all derive from that churning lifecycle, so they blink. The fix is architectural — drive the in-situ diff from the engine's stable base, decoupled from the realize lifecycle, and make the interaction jank-free (the requirement prior decoration work kept failing). Full requirements and acceptance examples in the origin doc.

---

## Key Technical Decisions

- **KTD1 — The engine owns the baseline (resolves the origin's baseline-storage question).** Use the vendored engine's `'suggest'` mode for the human direction. The stable baseline (R5/R6) is the engine's tracked-change base text (`getBaseText`, `vscode-codoc/src/webview/tiptap/track-changes/helpers.ts`); it advances only when changes are accepted (hand-off) — never per keystroke or per daemon pass. This deletes the eroding `baselines[fid]` in `codoc/loop/loop_b.py` and the `changedRange` underline in `hold-decorations.ts`. *Trade-off:* the baseline is coupled to editor state, so a reload must preserve pending suggestions (KTD3, U2) — confirmed direction (see origin: outstanding questions).
- **KTD2 — Suggest mode always on; settle-time classification routes draft-vs-live.** The editor stays in `'suggest'` mode. At settle, the host classifies the changed text via codoc's existing code-implication gate (`codoc/loop/classify.py:is_imperative`); non-code-implying changes are **auto-accepted** (commit live, no lingering marks), code-implying changes — and **any bolded span** (the existing bold→Focus override) — stay pending as suggestions for hand-off (R1–R3). Classifier precision stays the heuristic + bold for v1 (LLM classifier deferred, see Open Questions).
- **KTD3 — Hand-off = accept-all pending → existing commit path.** One whole-doc action accepts all pending human suggestions, advancing the engine base; the accepted text serializes to `tree.doc.json` and rides codoc's existing settle/commit path (`edits.json` + Loop B) so code-implying ones queue realize directives (R7). Reuses the directive-coalescing fix (commit `876eab9`) for R10. *Affordance:* a toolbar control showing the pending count (R8).
- **KTD4 — Both directions on one engine, author-tinted (resolves symmetric coexistence).** The agent's code→codoc surfacings become engine marks under a distinct `ChangeAuthor` (`track-changes/types.ts`), accepted/rejected by `changeId` via `acceptChange`/`rejectChange` → the `inbox.json` verdict channel. This merges `hold-decorations.ts` + `agent-proposals.ts` into one suggestion surface (R4, R11, R12). Per-change ids make human/agent overlaps on one description individually addressable.
- **KTD5 — R19 round-trip idempotency is the prerequisite.** Make `parse_doc_file`↔render and the `pm-doc.ts` serializers idempotent so a normalization-only re-render is not read as a new edit (kills the oscillation underlying R6/R18 and the repeated daemon passes). The seam: `vscode-codoc/src/state/pm-doc.ts` (`inlineRunsToText`/`blocksToDescriptionText`) ↔ `codoc/codoc_file/{render.py,parse.py,doc_parse.py}` ↔ `codoc/loop/{reconcile.py:has_pending_doc_edits, diff.py:diff_codoc}`.
- **KTD6 — Realize state decoupled from the marks.** "Being realized" stays a calm badge (the existing pending dot) driven by the hold set, but it no longer gates or recomputes the diff (R13) — the marks are engine-owned, the badge is a passive projection of `sidecar.holds`.

---

## High-Level Technical Design

State of a feature's description in the webview (suggest mode always on):

```
            edit (typing, suggest mode → tracked ins/del marks, engine base unchanged)
                                   │
                            settle (debounce)
                                   │
                 ┌─────────────────┴─────────────────┐
       not code-implying                       code-implying OR bolded
                 │                                     │
         auto-accept change                     hold as pending suggestion
         (engine base advances,                 (marks persist vs engine base;
          commits live to doc)                   diff never erodes — R5/R6)
                 │                                     │
                 │                            "hand to agent" (accept-all)
                 │                                     │
                 └─────────────────┬───────────────────┘
                                   │
                    serialize committed text → tree.doc.json
                                   │
                 existing settle/commit path (edits.json → Loop B)
                                   │
              code-implying → realize directive (pending dot / "realizing")
                                   │
                       agent writes code → reflect → sync
                                   │
              Loop A drift → agent suggestion (same marks, agent tint) → ✓/✗ → inbox
```

The engine base text is the single baseline for both directions; the daemon round-trip (R19) must round-trip the *committed* text only, never the pending marks.

---

## Requirements

This plan implements all of R1–R20 and is gated on AE1–AE6 from the origin doc. Mapping below; each unit cites the requirements it advances.

- Suggestion model & classification — R1, R2, R3, R4 → U2, U3
- Stable baseline & diff (no erosion) — R5, R6 → U2
- Hand-off & commit — R7, R8, R9, R10 → U4
- Agent direction — R11, R12 → U5
- Realize state decoupled — R13 → U6
- Interaction robustness — R14, R15, R16, R17, R18 → U2 (cursor/reload), U7 (scroll/node-add/flicker gate)
- Round-trip idempotency — R19 → U1
- Surface boundary — R20 → carried as a constraint (raw editor untouched)

---

## Implementation Units

Build order is dependency-driven: U1 (prerequisite) → U2 (core) → U3/U4/U5/U6 → U7 (hardening + acceptance gate). **Every unit's final verification is a manual Extension-Development-Host (EDH) check** — `tsc`/vitest/esbuild cannot catch TipTap-view regressions (R14–R18 are exactly those). The webview bundle (`dist/webview/doc-view.js`) must be rebuilt and the EDH window reloaded before each EDH check.

### U1. Round-trip idempotency (R19 prerequisite)

- **Goal:** A `tree.doc.json`↔store round-trip that differs only by normalization is not read as a new edit — no phantom re-apply, no oscillation, one daemon pass per real edit.
- **Requirements:** R19; unblocks R6, R18. Covers AE6.
- **Dependencies:** none.
- **Files:** `vscode-codoc/src/state/pm-doc.ts` (`inlineRunsToText`, `blocksToDescriptionText`, `descriptionToBlocks`, `textToInlineRuns`), `codoc/codoc_file/{render.py,parse.py,doc_parse.py}`, `codoc/loop/reconcile.py` (`has_pending_doc_edits`), `codoc/loop/diff.py` (`diff_codoc`); tests: `tests/codoc_file/test_refs_roundtrip.py`, `tests/loop/` (new round-trip test), `vscode-codoc/src/test/` (pm-doc parity test).
- **Approach:** Find the exact normalization divergence (the live repro: a trailing/leading `"` and whitespace around an appended sentence produced a one-char delta between the host's `pm-doc` serialization and the Python render/parse). Define one canonical normalization for description text (whitespace/blank-line collapse already partly addressed by U7-era work; extend to the char-level delta) and apply it on BOTH sides so `diff_codoc(parse_doc_file(...), store)` is empty after any render. Prefer normalizing at the parse/diff boundary so committed text is compared canonically, not byte-wise.
- **Patterns to follow:** the existing parity test harness in `tests/codoc_file/test_refs_roundtrip.py` and the TS↔Python parser parity tests; the U7 blank-line normalization (memory `project-codoc-collab-editing-model-2026-06-16`) is the precedent — this extends it.
- **Test scenarios:** (happy) append a sentence to a description, render→parse→diff is empty. (edge) leading/trailing quote and surrounding whitespace round-trip to empty diff. (edge) multi-paragraph description with a code-ref round-trips idempotently. (integration) `Covers AE6.` two consecutive `run_loop_b` passes on the same doc edit produce one applied event, not a repeating sequence. (regression) the existing 28-feature `test/requests` round-trip stays byte-identical.
- **Verification:** the daemon log shows a single `codoc→code` pass per edit (no repeats); diff-empty property holds across the test corpus; EDH: type and pause, the daemon log does not repeat.

### U2. Human direction on the engine's suggest mode + stable baseline (R5/R6, R4, R14)

- **Goal:** Human edits render as engine tracked-change suggestions against the engine base; the diff never erodes across iteration/undo/redo, and a daemon reload preserves pending suggestions (no clobber, no cursor jump).
- **Requirements:** R1, R4, R5, R6, R14. Covers AE1.
- **Dependencies:** U1.
- **Files:** `vscode-codoc/src/webview/tiptap/whole-doc-editor.ts` (enter `'suggest'` mode via `setTrackChangesMode`; `setDoc` must not reload over pending suggestions; restore caret), `vscode-codoc/src/webview/tiptap/schema.ts` (engine already registered — confirm `mode` config), `vscode-codoc/src/webview/tiptap/hold-decorations.ts` (remove `changedRange`/baseline-diff underline — engine marks replace it), `vscode-codoc/src/webview/doc-view.ts`; remove the eroding baseline in `codoc/loop/loop_b.py` (the `baselines[fid]` capture) and the `hold_detail.baseline` slice in `codoc/codoc_file/render.py`; tests: `vscode-codoc/src/test/` (engine-mark presence + base-stability), update `vscode-codoc/src/test/classify-surface.test.ts`.
- **Approach:** Put the editor in `'suggest'` mode so typing produces tracked ins/del against `getBaseText`. The base is stable — iterating refines marks, never resets (deleting a char shrinks the ins run, doesn't vanish it). `setDoc` (daemon round-trip) gains a guard: when the engine reports pending changes (`getPendingChangeCount > 0`), do not replace the doc — reconcile only the committed/baseline layer, leaving the human's marks intact; preserve caret position (the existing `savedPos` restore from this session's caret fix, extended for suggest state).
- **Patterns to follow:** U4's agent-mark rendering in `vscode-codoc/src/webview/tiptap/agent-proposals.ts`; the engine helpers (`getBaseText`, `getPendingChangeCount`) in `track-changes/helpers.ts`; the caret-preservation fix already in `whole-doc-editor.ts` `setDoc`.
- **Test scenarios:** (happy) typing in suggest mode yields tracked-change marks; `getBaseText` is unchanged by typing. (edge) `Covers AE1.` add "Should also cache the palette lookups", delete a character — the mark persists and the diff still spans the full added run. (edge) undo/redo within a draft keeps marks consistent with the base. (integration) a daemon payload reload with pending human changes does not drop the marks or move the caret.
- **Verification:** EDH — `Covers AE1` and the no-erosion behavior hold by hand; caret stays put across a round-trip; `tsc`/vitest green.

### U3. Settle-time classification: auto-accept prose, hold code-implying + bold (R2, R3, R17)

- **Goal:** At settle, prose changes commit live (auto-accepted) while code-implying changes (or any bolded span) remain pending suggestions — with no jarring mid-word "flip".
- **Requirements:** R2, R3, R17. Covers AE4 (prose-live half).
- **Dependencies:** U2.
- **Files:** `vscode-codoc/src/webview/tiptap/whole-doc-editor.ts` (settle handler decides accept-vs-hold per changed range), a host/webview classification seam (TS port of `is_imperative` OR a host round-trip — resolve in this unit), `codoc/loop/classify.py` (reuse `is_imperative`/`implies_code`), `codoc/loop/edits.py` (bold→Focus channel already exists); tests: `vscode-codoc/src/test/classify-surface.test.ts`, `tests/loop/test_classify.py`.
- **Approach:** On the settle debounce, classify each pending change's resulting text. Non-code-implying → `acceptChange` (commits live, base advances). Code-implying or bold-containing → leave pending. Classification/acceptance happen at settle, never per keystroke (R17 — avoids the flip). Decide the classification location: a small TS port of `is_imperative` (fast, no round-trip) is preferred for responsiveness; document the parity-test obligation with the Python gate.
- **Patterns to follow:** the existing bold-amplified imperative gate (`codoc/loop/loop_b.py` + `classify.py`); the TS↔Python parity-test pattern (`classify-surface.test.ts`).
- **Test scenarios:** (happy) a prose reword auto-accepts at settle (no lingering mark). (happy) an imperative edit stays a pending suggestion. (edge) a bolded non-imperative span is held (bold override). (edge) a mixed edit (prose + imperative sentence) holds only the code-implying part. (parity) the TS classifier agrees with `classify.is_imperative` on a shared fixture set.
- **Verification:** EDH — reword prose (commits, no mark); type an imperative sentence (stays a draft mark); no mid-word flip while typing.

### U4. "Hand to agent" batch commit (R7, R8, R9, R10)

- **Goal:** One whole-doc action accepts all pending human suggestions and hands the code-implying ones to the agent; a pending draft can be withdrawn before hand-off.
- **Requirements:** R7, R8, R9, R10. Covers AE4.
- **Dependencies:** U2, U3.
- **Files:** `vscode-codoc/src/webview/doc-view.ts` (toolbar affordance + pending count), `vscode-codoc/src/webview/tiptap/whole-doc-editor.ts` (accept-all of human-authored changes), `vscode-codoc/src/webview/protocol.ts` (hand-off message), `vscode-codoc/src/providers/tree-editor.ts` (host commit), `codoc/loop/loop_b.py` (existing settle/commit + coalescing path); tests: `vscode-codoc/src/test/`, `tests/loop/test_directive_coalescing.py`.
- **Approach:** Hand-off accepts all human-authored pending changes (not agent ones), advancing the base; the resulting committed text serializes to `tree.doc.json` and rides the existing commit path so Loop B queues directives for the code-implying ones. Withdraw (R9) = reject the human change(s) for a feature back to base. Affordance shows the pending-suggestion count and is reachable in-editor (design-taste: calm, one accent, no margin-card clutter).
- **Patterns to follow:** the existing settle→`edits.json`→Loop B commit; the coalescing fix (`876eab9`); the U6 withdraw/`cancellations` channel in `codoc/loop/edits.py`.
- **Test scenarios:** (happy) `Covers AE4.` draft code-implying edits on three features + a prose reword on a fourth, hand off — three realize, the prose was already committed live. (happy) the pending count reflects held suggestions and clears after hand-off. (edge) withdraw a draft before hand-off — base restored, no directive queued. (edge) re-editing a feature with a pending draft refines one directive, not N (coalescing).
- **Verification:** EDH — the count + hand-off behave; `status.json` → `awaiting_impl` only for code-implying; vitest + `tests/loop` green.

### U5. Agent direction unified onto engine marks + accept/reject (R11, R12)

- **Goal:** Loop A's code→codoc surfacings render as agent-tinted engine suggestions accepted/rejected inline, replacing the bespoke agent-proposal decorations.
- **Requirements:** R11, R12. Covers AE5.
- **Dependencies:** U2.
- **Files:** `vscode-codoc/src/webview/tiptap/agent-proposals.ts` (migrate onto engine `acceptChange`/`rejectChange` + `ChangeAuthor`), `vscode-codoc/src/webview/tiptap/whole-doc-editor.ts`, `vscode-codoc/src/state/bindings-model.ts` (proposal→change mapping), `vscode-codoc/src/providers/tree-editor.ts` (verdict→`inbox.json`); tests: `vscode-codoc/src/test/`.
- **Approach:** Materialize a code-drift AMEND proposal as an engine tracked change authored by the agent identity, against the same base. Accept (`acceptChange`) commits it to the description and writes an accept verdict to `inbox.json`; reject discards. This unifies the two decoration modules onto one engine surface; human and agent changes coexist as distinct-author marks on the same description.
- **Patterns to follow:** the U4 agent-mark approach already in `agent-proposals.ts`; the `inbox.json` verdict channel (`codoc/loop/inbox.py`); author-tint CSS (`--ink-*`).
- **Test scenarios:** (happy) `Covers AE5.` an agent code-drift change appears as an agent-tinted tracked diff with ✓/✗; accept commits it into the description. (edge) reject discards with no description change. (integration) a human draft and an agent suggestion on the same feature render as distinct-author marks; accepting one leaves the other intact.
- **Verification:** EDH — agent proposal shows as a tracked diff, accept/reject work and persist via the inbox; vitest green.

### U6. Decouple the realize badge from the diff (R13)

- **Goal:** "Being realized" is a calm passive badge driven by the hold set; it never gates or recomputes the suggestion marks.
- **Requirements:** R13.
- **Dependencies:** U2.
- **Files:** `vscode-codoc/src/webview/tiptap/hold-decorations.ts` (badge only — the rail/underline/changedRange removed in U2), `vscode-codoc/src/webview/doc-view.ts` (tree-row badge); tests: `vscode-codoc/src/test/classify-surface.test.ts`.
- **Approach:** Reduce `hold-decorations.ts` to the pending/realizing dot, projected purely from `sidecar.holds` (and `sync.phase` for the active shimmer), with no dependency on the marks/baseline. The marks (U2/U5) are the diff; the dot is orthogonal state.
- **Patterns to follow:** the existing pending-dot/`badge.pending` work from this session; activity-phase shimmer in `activity-decorations.ts`.
- **Test scenarios:** (happy) a held feature shows the calm badge; the badge does not change when suggestion marks change. (edge) badge clears when the feature leaves the hold set. (regression) badge presence still keys on `holds` only.
- **Verification:** EDH — badge is stable while editing marks; no flicker coupling.

### U7. Interaction hardening + acceptance gate (R15, R16, R18)

- **Goal:** No scroll-jump on payload reload, agent activity, or node add/remove; no decoration flicker on no-op passes; the full AE1–AE3 interaction gate passes in the EDH.
- **Requirements:** R15, R16, R18. Covers AE2, AE3 (and re-verifies AE1).
- **Dependencies:** U2, U3, U4, U5, U6.
- **Files:** `vscode-codoc/src/webview/doc-view.ts` (`reconcileTree`/`appendRow` scroll preservation, node-add path), `vscode-codoc/src/webview/tiptap/whole-doc-editor.ts` (decoration stability on no-op reload); tests: `vscode-codoc/src/test/` where logic is extractable.
- **Approach:** Preserve tree-pane and editor-pane scroll across payload reloads and node add/remove (extend the `syncingFromEditor` scroll-gate fix from this session to the node-add path). Ensure decorations only rebuild when the change/baseline actually changes (no rebuild on a no-op daemon pass — guaranteed by U1 idempotency + decoupled badge). This unit is largely verification: most stability comes from U1 (no oscillation) + U2 (engine-owned state, no reload-clobber), so its job is to find and close the residual scroll/flicker paths and run the acceptance gate.
- **Test scenarios:** (happy) `Covers AE3.` add a feature node while scrolled mid-document — no scroll jump to top or to the new node. (happy) `Covers AE2.` edit + pause; daemon round-trips; caret stays at the typing position. (edge) agent activity (phase shimmer) does not scroll the tree. (regression) `Covers AE1.` no-erosion still holds end-to-end.
- **Verification:** EDH acceptance gate — AE1, AE2, AE3 all pass by hand; no scroll jump on node-add; no decoration flicker across daemon passes. This is the plan's final acceptance gate.

---

## System-Wide Impact

- **Reverses U3** (commit-immediately) for code-implying edits and **reworks the agent decoration path** (merges `hold-decorations.ts` + `agent-proposals.ts`) — a wider blast radius than the additive decoration work so far. Update memory `project-codoc-collab-editing-model-2026-06-16` on completion.
- **Single-writer model (U2b) preserved:** the host owns `tree.doc.json`; pending human suggestions live in editor/engine state until hand-off, then commit through the existing path. U1 hardens the round-trip this depends on.
- **Raw text editor (R20) untouched** — it keeps committing directly; suggesting is webview-only.

## Risks & Mitigations

- **EDH-only verification (highest risk).** R14–R18 cannot be caught by `tsc`/vitest/esbuild; a regression ships invisibly (a TDZ blank-editor already did, commit `d7c48a0`). *Mitigation:* every unit ends with an EDH check; U7 is a dedicated acceptance gate; rebuild the bundle + reload the EDH before each check.
- **Suggest-mode cursor-jank (the reason U3 removed suggesting).** *Mitigation:* the engine's native suggest input (not the old text-diff) handles tracked typing; classification/acceptance at settle, never per keystroke (R17/U3); U2 explicitly tests typing smoothness in the EDH.
- **codeRef / multi-paragraph descriptions in suggest mode.** Marks over `codeRef` atoms and across paragraph blocks may interact with serialization. *Mitigation:* U1 covers codeRef + multi-paragraph round-trips; U2/U5 test marks spanning a codeRef.
- **Classifier coarseness** (`is_imperative` matches cues anywhere). *Mitigation:* bold override (U3) as a reliable manual signal; LLM classifier deferred (Open Questions).

## Scope Boundaries

- **Webview-only** — raw `tree.codoc` editor unchanged (R20).
- **No per-suggestion accept of the human's own edits** — release is the batch hand-off; per-✓/✗ is the agent direction only.
- **Single human author** — engine multi-author tinting is used for human-vs-agent, not human-vs-human.

### Deferred to Follow-Up Work

- LLM-backed imperative classifier (if heuristic false-drafts prove common).
- Fine-tuning the simultaneous human-draft + agent-suggestion interaction on one description beyond distinct-author marks + per-change accept.

## Open Questions (execution-time)

- Exact location of the TS classifier (port vs host round-trip) — resolve in U3 by responsiveness measurement; parity test is mandatory either way.
- Whether the hand-off affordance is a toolbar button vs a status-bar action — design-taste pass during U4.

## Sources & Research

- Origin requirements: `docs/brainstorms/2026-06-18-codoc-insitu-suggesting-mode-requirements.md` (R1–R20, AE1–AE6).
- Live ce-debug findings (2026-06-18, `lmpo` test repo): eroding baseline, A↔B one-char oscillation, lifecycle coupling.
- Engine API (first-hand read): `vscode-codoc/src/webview/tiptap/track-changes/` — `setTrackChangesMode`, `acceptChange`/`rejectChange`, `getBaseText`/`getResultText`/`getPendingChangeCount`, `ChangeAuthor`.
- Reusable from this session: directive coalescing (`876eab9`), per-writer-unique atomic write (`149730b`), caret-preservation + scroll-gate fixes (`42ef742`).
