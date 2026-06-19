# EDH interaction checklist — webview UX redesign

Manual acceptance gate for the `Codoc Tree` webview redesign
(plan: `docs/plans/2026-06-18-002-feat-vscode-codoc-webview-ux-redesign-plan.md`).

**Why this exists (KTD6):** the webview's caret, scroll, momentum, and decoration behavior
**cannot** be observed by `tsc` / `vitest` / `esbuild` — those verify the pure logic + CSS
invariants + that it builds. The *interaction* is invisible to them, and webview regressions
have shipped silently before (two TDZ blank-editor bugs). This is the "done" gate, not optional
polish.

## How to run

Before **each** section: rebuild the bundle and reload the Extension Development Host.

```bash
cd vscode-codoc && node esbuild.config.mjs   # rebuild dist/webview/doc-view.js
# then: in the EDH window, Developer: Reload Window
```

Open a repo that has a `.codoc/tree.codoc` with a reasonably deep tree (≥ ~20 features, ≥ 3
levels). For the code→codoc diff checks, run `codoc watch` so the daemon's `reconcile_drift`
pass can raise amend proposals.

Mark each box `[x]` pass / `[ ]` fail; a failed box blocks ship.

---

## R1 / R2 — Tree re-center + focus line

- [ ] Scroll the **document** (not the tree). The matching tree row **eases toward the vertical
      centre** of the tree pane as each new feature becomes active — smooth, with momentum, not a
      snap.
- [ ] The centred/active row carries a **right-edge vertical line** (the focus line).
- [ ] Micro-scrolls within a feature do **not** jitter the tree (the ±15% centre deadband holds).
- [ ] Grab the **tree** scrollbar and drag: the spy does **not** yank the pane back for ~0.8s
      (the suppress window); after you stop, re-centering resumes on the next doc scroll.

## Keystroke-jank guard (the load-bearing regression check)

- [ ] Click into the **document editor** and type, then arrow-key across a feature boundary.
      The **tree does NOT move** on caret changes — only document *scrolling* re-centers it.
      (If the tree animates while you type, the source gate has regressed — fail.)

## R3 — Momentum document scrolling

- [ ] Click a **tree row** / a **minimap tick** far from the current position. The document
      **glides** to it with momentum (longer travel = slightly longer glide), and lands with the
      heading just below the top — the **correct** section ends up active (no off-by-one).
- [ ] During a long glide, no neighbour feature briefly flashes as active (the mute window covers
      the whole glide).
- [ ] Click a second target mid-glide: the first glide **cancels** and the new one starts (no
      queue / fighting).

## R4 — Minimap wave + depth

- [ ] Tick **widths visibly encode depth** (top-level wider, nested narrower).
- [ ] Sweep the cursor down the minimap: a **wave ripples** out from the hovered tick to ±3
      neighbours and settles back together on leave — it feels like a wave, not one tick popping.
- [ ] The **active** ("you are here") tick stays clearly dominant throughout the wave.

## R5 — Resize stability

- [ ] Type mid-document, then **resize the window** (drag the editor split / window edge). The
      **caret stays put**; text reflows around it without jumping.
- [ ] With the selection bubble or comment composer open, resize: it **re-anchors** to its text
      (or dismisses if the text scrolled off) — it does not strand at stale coordinates.
- [ ] With a threads-peek or comment/hover card open, resize: it **closes** (reopens on next hover).

## R6 — State survives close→reopen + reload

- [ ] Expand/collapse some features, select one mid-tree, scroll both panes, put the caret in a
      description. **Close** the `tree.codoc` editor tab and **reopen** it: expansion, selection,
      both scroll positions, and the caret are **restored** (not reset to expand-all + first root).
- [ ] Repeat with **Developer: Reload Window**: same restore.
- [ ] On first open the restored expansion is there from the **first paint** — no expand-all flash
      then collapse.
- [ ] The restored caret is **where you left it**, not snapped to the nearest heading (distinguish
      "restored" from "fell back to heading").

## R7 / R8 / R9 — Decoration cohesion, code→codoc diff, orthogonal lifecycle

- [ ] With `codoc watch` running, make a code change large enough to trigger an amend proposal
      (> ~30% of a feature's description's worth). The webview shows an **inline word-level diff**
      (struck old / underlined new, agent-tinted) with a plain-text **"▲ from code"** label — the
      direction reads without relying on colour.
- [ ] *(by-design)* A **small** code tweak auto-applies with **no** diff — confirm this is the
      expected behavior, not a bug (see U6 audit verdict in the plan).
- [ ] The **"being realized"** indicator (dashed breathing dot / chip) sits **beside** the diff
      and never gates or blinks it; an idle daemon pass (no real change) does **not** flicker any
      decoration.
- [ ] Scanning the whole surface, the decorations read as **one language** — no clashing extra
      cards, no per-op colour rainbow.

## R10 — Reduced motion + screen reader

- [ ] Enable **Reduce Motion** (VS Code / OS). Reload. Re-run R1/R3/R4: the tree re-center,
      momentum scroll, and minimap wave are all **suppressed** (instant snaps / single-tick hover) —
      no animation anywhere.
- [ ] With a screen reader / `vscode-using-screen-reader` active, motion is likewise suppressed and
      the scroll-spy auto-select is calm.

## High-contrast legibility

- [ ] Switch to a **high-contrast** theme. The **focus line**, the **minimap ticks** (incl. active),
      and the **"▲ from code"** diff label all stay clearly legible (no washed-out low-opacity tints).

## Edit staging lifecycle (plan 2026-06-19-001 — captured → staged & sent → resolving → review)

The load-bearing new behavior: every edit is visibly *captured*, an explicit *Save* sends it,
and the agent's edits come back as a *sentence-level* accept/reject diff. Run with `codoc watch`.

### Captured — every edit is recorded in situ (additions AND deletions), persists until commit

Phase colours (dark-mode tuned): **editing = blue** `rgb(0,142,255)`, **deletion caret = amber**
`rgb(255,185,11)`, **staged & sent = green** `rgb(0,150,0)`; surfaced agent edits keep their hue.

- [ ] **Add** words. They get a **blue underline** immediately (not only after commit), plus the
      feature's blue gutter rail + "recorded" dot. No size threshold.
- [ ] **Pure delete** (e.g. "I don't think" → "I think"). A small **amber caret** appears at the
      gap ("I |think") — visible and counts; it does NOT just flash and vanish.
- [ ] **Replace** = select-delete-retype ("the cat" → "the dog"). This is *editing*, not deletion →
      you see ONLY the **blue underline** on the new word, **no amber caret**.
- [ ] A pure deletion AND a separate addition elsewhere in the same paragraph → caret at the
      removal **and** underline on the addition (they're independent edits, not a replacement).
- [ ] The captured marks **persist** after the ~1s autosave round-trip — they stay until you
      **⌘S / Commit** (then they clear; code edits become green pending). Previously they vanished.
- [ ] A **whitespace-only** change (trailing space, extra blank line) does **not** register.
- [ ] Captured (blue, static) reads as calmer than pending (green, *breathing*) and resolving
      (*pulse*) — the phases form a coherent ramp. The tree pane shows a blue "captured" badge on
      staged (code-implying) rows; the pending badge is green.

### Save = stage & send (one gesture)

- [ ] With captured edits pending and the **caret in the editor**, press **⌘S** (or **Ctrl+S**).
      No native save dialog flashes; the staged code-implying edits flip from **captured →
      pending** (staged & sent) and the status bar moves toward `awaiting_impl` / `realizing` —
      the agent picks them up. (⌘S is editor-focus-scoped; with focus on the tree/toolbar use the
      button.)
- [ ] The **"↑ Commit & send (N)"** toolbar button does the same as ⌘S, from any focus.
- [ ] A **prose-only** edit: captured shows, then **clears on its own** once the daemon renders it
      back — it never gets stuck as pending and never needs a send (nothing went to the agent).
- [ ] A handed-off feature shows **pending only** (no double captured+pending mark on the same row).

### Review — agent edits come back at sentence level

- [ ] When the agent amends a **multi-sentence** description, only the **changed sentence(s)**
      render as struck-old + inserted-new — **one ✓/✗ per amend**, not a word-by-word peppering.
- [ ] A **title** edit still diffs at **word** level (a one-word title fix doesn't strike the whole title).
- [ ] Accept / reject resolves the amend cleanly (marks clear, tree.codoc unaffected until the verdict).

### Lifecycle legibility + accessibility

- [ ] Scanning the surface, the four phases (captured · pending · resolving · review) read as **one
      language**, not four unrelated treatments — and you can always tell which phase a feature is in.
- [ ] **High-contrast** theme: the captured rail/dot + tree badge stay legible (HC floor holds).
- [ ] **Reduce Motion**: pending stops breathing / resolving stops pulsing; captured is unaffected
      (it was already static).

## Aesthetic verdict gate (the subjective goal)

- [ ] Compare before/after side-by-side. Answer honestly: does the redesign read as **more
      considered**, or just **busier / more noise**? The motion (re-center + momentum + wave +
      breathing dots) must add up to *calmer and more legible*, not more clutter. If it reads
      busier, tune durations/intensities down before ship — green behavior checks are necessary
      but not sufficient for "feels alive and considered."
