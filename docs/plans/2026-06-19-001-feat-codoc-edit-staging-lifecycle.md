---
type: feat
status: active
origin: docs/plans/2026-06-18-002-feat-vscode-codoc-webview-ux-redesign-plan.md
created: 2026-06-19
execution: code
---

# codoc edit staging lifecycle + cohesive two-family decorations

A continuation of the webview UX redesign (branch `feat/vscode-codoc-webview-ux-redesign`).
Make the **edit → save → agent-resolve → review** lifecycle visible and legible with two
well-designed decoration families and clear stage cues. No "is this edit big enough?" guessing —
**every** user edit is captured and decorated.

## Problem

Today the visible "you edited this" decoration (`ce-intent-underline` + pending rail) only
renders when the daemon's `classify.py` deems an edit **code-implying** (it enters
`sidecar.holds`). Pure-prose / small edits settle to a *clean* doc, so from the user's side it
looks like nothing was recorded — the perceived "big enough" gate. Separately, agent edits
surface as **word-level** ins/del marks (many tiny accept/reject targets), and the stage signals
(status-bar dot, 7 tree badges, doc-heading dots/rail, activity shimmer) are scattered and don't
read as one language. The user can't tell, in situ, what stage an edit is at or what the system
did underneath.

## Decisions (locked with the user)

- **Two user states, save sends.** `captured` (typed, recorded locally, not sent) →
  `staged & sent` (an explicit Save / Commit gesture hands code-implying edits to the agent in
  one step). No separate "Hand to agent" step.
- **Sentence-level agent diffs.** A changed sentence renders as one struck old sentence + one new
  sentence = one accept/reject per sentence. Far less review burden than word-by-word.
- **Capture everything.** Every edit is decorated as `captured` the instant it's typed, regardless
  of code-implying classification — client-side, not gated on the daemon hold set.
- **Webview-only**, in-situ / inline, no extra cards (carries the parent plan's scope).

## The four-phase lifecycle (one user gesture)

```
[CAPTURED]  type → client-side "recorded" mark on every edited feature (vs last-rendered baseline)
   │  (debounced settle persists tree.doc.json + marks drafts = held, NOT sent)
   ▼
[STAGED & SENT]  Save (webview Cmd/Ctrl+S) OR Commit button → flush settle, then hand-off
   │  prose-only edits just round-trip (captured clears when daemon renders back)
   │  code-implying edits → realize.md → agent
   ▼
[RESOLVING]  agent works via /codoc:sync (activity phase) — captured mark gives way to pending/pulse
   ▼
[REVIEW]  agent's description/structure edits → sentence-level inline diff → ✓ / ✗
```

Two decoration **families**, one visual grammar (rail intensity ramps dotted → dashed → pulsing;
one accent hue; direction hue reserved for the agent-review family):

| Family | Phase | Treatment | Source |
|---|---|---|---|
| **F1 user-edit** | captured | thin dotted left rail + soft change underline + "recorded" dot | client (current vs baseline) |
| **F1 user-edit** | staged & sent | dashed breathing dot + firmer rail (accent) | `sidecar.holds` (`awaitingAI`) |
| **F1 user-edit** | resolving | pulse shimmer | `activity.json` phase |
| **F2 agent-review** | review | sentence ins/del, direction-tinted, ✓/✗ per sentence | sidecar proposals |

## Implementation units

### U1 — Sentence-level diff engine
- **Files:** `vscode-codoc/src/state/doc-diff.ts` (modify), `vscode-codoc/src/test/doc-diff.test.ts` (extend/create).
- **Approach:** Add `sentenceSplit(s): string[]` (split on `.?!` + trailing whitespace, keep the
  delimiter; a no-punctuation string is one sentence) and `sentenceDiff(old, new): DiffRun[]`
  (token-LCS over sentences, reusing the suffix-DP shape of `wordDiff`; each changed run is one
  `del` (old sentences) + one `ins` (new sentences)). Keep `wordDiff`/`compactRuns` (still used).
- **Verification:** unit tests — identical→all same; one changed sentence→one del+one ins; added
  sentence→ins only; removed→del only; no-punctuation→whole-string one unit; trailing-space round-trip.

### U2 — Agent diff renders at sentence level
- **Files:** `vscode-codoc/src/state/agent-proposals.ts` (modify), `vscode-codoc/src/webview/tiptap/suggestion-decorations.ts` (modify), tests.
- **Approach:** Switch `markedRuns` to `sentenceDiff`; give each changed sentence its own
  `changeId` so accept/reject acts per sentence; keep inline `[label](codoc:…)` refs inside a
  sentence as marked `codeRef` nodes. Ensure `amendActions` ✓/✗ stays one-per-sentence (or grouped
  per feature with per-sentence marks), inline, no card.
- **Verification:** existing agent-proposal tests adapted; a multi-sentence amend yields one
  del+ins pair per changed sentence, each with a distinct change id.

### U3 — "Captured" decoration for ALL edits (client-side)
- **Files:** `vscode-codoc/src/webview/tiptap/captured-decorations.ts` (create),
  `vscode-codoc/src/webview/tiptap/whole-doc-editor.ts` (wire + baseline snapshot),
  `vscode-codoc/src/webview/doc-view.css` (`.ce-captured*`), `vscode-codoc/src/test/captured-decorations.test.ts` (create).
- **Approach:** A ProseMirror plugin that, against the **last-rendered baseline** (the doc as last
  `setDoc` from a payload), decorates every feature whose text changed — independent of
  `sidecar.holds`. Reuse the `changedRange` word-snap for the underline. Baseline updates on each
  payload apply; clears per feature when current == baseline (daemon rendered it back). Suppress on
  a feature already in the hold set (it graduates to the pending treatment — no double mark).
- **Verification:** pure helper (`capturedFeatures(baselineDoc, currentDoc): Set<fid>` /
  changed-range) unit-tested headless; EDH confirms the instant mark on prose-only edits.

### U4 — Save = stage & send (the one gesture)
- **Files:** `vscode-codoc/src/webview/doc-view.ts` (Commit button + Cmd/Ctrl+S keydown → `commit`
  message; relabel the existing draft hand-off affordance), `vscode-codoc/src/webview/protocol.ts`
  (`commit` message), `vscode-codoc/src/providers/tree-editor.ts` (`commit` handler: flush the
  latest settle via the doc the webview sends, then `handOff`).
- **Approach:** Webview captures Cmd/Ctrl+S (preventDefault) and posts `{kind:'commit', doc}`; the
  toolbar Commit button posts the same. Host `settleDoc(doc)` then `handOff(document)` (clear drafts
  → realize.md) in one turn, then `post()`. Keep the debounced `doc-settle` as captured-only
  (persist + mark drafts, no hand-off). No Python change (drafts → `handed_off` already exists).
- **Verification:** EDH — type, see captured; Cmd+S / Commit → pending+sent (status `awaiting_impl`),
  agent resolves; prose-only Commit just round-trips (no stuck pending).

### U5 — Unified stage-indicator grammar
- **Files:** `vscode-codoc/src/webview/doc-view.css` (modify), small render tweaks in
  `doc-view.ts` / `hold-decorations.ts` as needed, `vscode-codoc/src/test/decoration-grammar.test.ts` (extend).
- **Approach:** Pin the four-phase family so rail/dot intensity ramps coherently
  (captured dotted → staged dashed-breathing → resolving pulse), one accent hue, direction hue only
  for the agent-review family, high-contrast floors for every new token. Consolidate redundant cues.
- **Verification:** `decoration-grammar.test.ts` asserts the `.ce-captured` family exists, keys off
  `--accent` (status axis, not a direction hue), and the agent diff stays on `--dir-review`; EDH
  aesthetic verdict.

### U6 — Tests + EDH checklist + green gate
- **Files:** the test files above; `docs/edh-interaction-checklist-webview-ux.md` (extend with the
  lifecycle section).
- **Verification:** `npx tsc --noEmit` clean, `node esbuild.config.mjs` clean, `npx vitest run`
  green, `python3.11 -m pytest tests/` unaffected.

## Scope boundaries

- No raw-text-editor changes (webview only).
- No new daemon/Python behavior — reuses drafts → `handed_off` → realize.md.
- No new fonts/colors beyond `--vscode-*` tokens + the established direction/accent axes.
- RETIRE path-asymmetry, hold semantics, and the single-writer model are unchanged.

## Risks

- **Cmd+S in a custom editor** doesn't dirty the text document (single-writer) — handled by a
  webview keydown capture, not VS Code's save event.
- **Captured vs pending double-marking** — suppress captured once a feature enters the hold set.
- **Sentence splitting** on abbreviations / refs — keep the splitter conservative (delimiter +
  whitespace), fall back to whole-paragraph as one unit; never split inside a `codeRef`.
- **Interaction is invisible to tsc/vitest** — the EDH gate (U6) remains the real "done."
