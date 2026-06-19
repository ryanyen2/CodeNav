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

## Aesthetic verdict gate (the subjective goal)

- [ ] Compare before/after side-by-side. Answer honestly: does the redesign read as **more
      considered**, or just **busier / more noise**? The motion (re-center + momentum + wave +
      breathing dots) must add up to *calmer and more legible*, not more clutter. If it reads
      busier, tune durations/intensities down before ship — green behavior checks are necessary
      but not sufficient for "feels alive and considered."
