# World-class editing — EDH verify checklist + silent-demo script

> 2026-06-20 · companion to `2026-06-20-world-class-editing.md`. Everything below is
> structurally green (tsc 0 · esbuild 0 · **529 vitest** · **554 pytest**) but is
> **pixel-level** — it can only be confirmed in a live Extension Development Host (EDH).
> Launch the EDH (F5 / "Run Extension"), open a repo that has a `.codoc/tree.codoc`,
> and open the **Codoc Tree** webview.

---

## A. One-time visual-verify checklist

### P1 — typography & palette
- [ ] Doc body / headings / navigator render in **Inter** (not the system UI font); code-ref
      chips + binding targets render in **JetBrains Mono**. (If they fall back to the theme
      font, the `dist/webview/fonts/*.woff2` didn't resolve — check the webview console.)
- [ ] The resting doc reads **calm / near-monochrome** — at most the two grammar hues
      (slate-blue code-ahead, sage doc-ahead) in view at once; no neon.

### P0 — lifecycle icons + tactile gestures
- [ ] Lifecycle badges read as a **learnable icon family**: dashed-circle = captured/mine-local,
      diamond = pending, **filled** diamond after ⌘S = queued/sent, pulsing pen-nib = agent
      editing, spinning arrows = reflecting, warning-diamond = divergent.
- [ ] **Accept** a proposal (`✓`): the check-circle **pops**, a quick (~120ms) green row flash,
      then the row **height-collapses** to 0 (not a hard vanish). **Reject** (`✗`): a quiet
      quarter-spin + slide-out.
- [ ] **⌘S (the hero moment):** every captured rail in view **sweeps blue→green top-to-bottom**;
      the pending badge advances hollow→**filled** diamond.
- [ ] **Hand to agent:** the paper-plane icon does a tiny up-right launch.

### P2 — cross-surface diff bridge
- [ ] Start typing in a feature's heading/description that **has bindings** → its primary
      binding file **opens in the column beside** the webview *without stealing your caret*
      (you keep typing), ~180ms after you pause.
- [ ] The bound file's implicated declaration lines **flash a brighter green then settle**
      (the "it responded" beat) — solid green left rail + overview tick + gutter dot — with a
      live `◇ implicated by "<title>"` CodeLens that flips to `◆ queued — run /codoc:sync` after ⌘S.
- [ ] Editing an **unrealized** (italic) feature opens its likely target file with a single
      top-of-file `◇ new code will be added here for "<title>"` lens (no empty split).
- [ ] **Reverse:** edit the bound **source** file → a blue `⤵` spark **rises** on the matching
      doc heading, holds ~2.5s, settles to a blue underline tick that **clears within ~8s**
      (and immediately if you edit that feature) — it never accumulates.
- [ ] **Close** the bridged code pane → it **stops auto-reopening** that session.
- [ ] During a **realize** pass, the agent's own edits to a bound file do **not** spark the doc
      heading (only *your* hand-edits to code do).

### P3 — agent-as-collaborator presence
- [ ] When an agent works a feature, a 16px **lilac ring** (pen-nib glyph) appears at the
      heading's right edge with an italic whisper: `Claude · implementing 3/5 · <title>` while
      realizing (or the bare verb when no progress), `Claude · syncing the tree` while reflecting.
- [ ] **Scroll the doc while it's active → the avatar stays glued to its heading, tracking
      smoothly (no drift / jitter / snap).** ← the anchor fix; the previous build slid it off.
- [ ] It **glides** (with a faint comet trail) to the next feature as the agent hops; finishing
      one then starting the next glides across rather than blink-out/in; a true idle fades after
      a grace.
- [ ] The active feature's **tree row** shows the twin avatar at its right edge with **no
      separate active-write dot** beside it (one signal, not two).
- [ ] Scroll the agent's feature **off-screen** → the avatar pins to the doc pane's top/bottom
      edge with a ↑/↓ **chevron**; click → scrolls back.
- [ ] Kill the agent mid-edit → the avatar **fades after ~12s** (no haunting ghost).

### P4 — ⌘K command palette
- [ ] **⌘K** (macOS) / **Ctrl+K** (Win/Linux) drops a centered card over a blurred scrim;
      the editor never swallows it; Esc / scrim-click closes. (On macOS, Ctrl+K still does the
      native delete-to-EOL inside inputs — it no longer opens the palette.)
- [ ] Type 2–3 letters → features rank best-first with **accent-bold matched chars**; ↑/↓ move,
      ↵ scroll+select, **⇧↵ opens the bound code** (the bridge).
- [ ] Contextual **actions** appear only when applicable (Accept/Reject all, Hand to agent,
      Withdraw, Open bound code), each with its lifecycle icon — so the palette doubles as the
      **legend**.
- [ ] **Zero-typing ⌘K** is a dashboard: Recent features + "N proposals to review" / "N drafts
      to hand off". **No-match** → `Create feature "<query>"` mints a heading. Fresh repo →
      "Run codoc init".

### Accessibility (do not skip)
- [ ] Toggle **Reduce Motion** on (VS Code: `workbench.reduceMotion: on`) → no glides/trails/
      shimmer/pops; the avatar just **appears** at its feature with a (non-hiding) label; bridge
      decorations apply without the flash. Everything stays legible.
- [ ] **High-contrast** theme: lifecycle glyphs, bridge rail/gutter, avatar ring, and palette
      chrome all stay legible (the HC floors pin faint tints to borders/solids).

---

## B. The silent-demo recording script (≈45–60s, no narration)

The arc is **calm baseline → expressive event**. Record at a comfortable zoom on a dark theme.

1. **Open `tree.codoc`** in the Codoc Tree webview. Hold 2s on the calm, editorial doc
   (Inter, pastel). *Establishes taste.*
2. **⌘K**, type three letters, ↵ — the palette fuzzy-jumps to a feature. *Establishes speed.*
3. **Click into that feature's description and type a sentence.** The bound code file **glides
   in beside you** and its lines **flash green then settle**, the `◇ implicated` lens appearing
   live. *The headline: two surfaces, one gesture.*
4. **Click into the code, change a line.** A blue spark rises on the doc heading; the tree row
   pulses. *The bridge goes both ways.*
5. **⌘S.** The whole left margin **shimmers blue→green top-to-bottom**; the badge fills.
   *The most shareable single frame.*
6. **Toolbar "Hand to agent"** (or run `/codoc:sync`). The paper-plane launches; a moment later
   a **lilac avatar glides in**, whispering `Claude · implementing 1/3 · <title>`, then **travels
   between features** with a comet trail, landing a green check as each lands. *"Wait — is
   someone *in* my document?"*
7. End on the avatar fading out and the doc settling back to calm. *Return to baseline.*

> Tip: step 5 (⌘S shimmer) and step 6 (avatar glide) are the two frames most likely to carry the
> post on their own — lead the edit with them if cutting a shorter clip.

---

## C. Accepted follow-ups (non-blocking, out of this scope)
- **Palette nav completeness (§D.2):** "go to next/prev sibling" and "reveal-in-code / open
  bound file in tree" are not built (kept minimal); drift features get no palette attention
  glyph (drift is host-side decoration data, not in the webview `DocPayload` — needs a payload
  field to surface).
- **Lifecycle icon *morph*:** captured→pending→resolving icons hard-swap rather than tween,
  because the decoration DOM rebuilds per payload (no element identity across families). The
  `morphLifecycle` primitive is built+tested, ready if element-identity plumbing is ever added.
  Decision: not worth the doc-round-trip risk for a ~160ms cosmetic cross-fade.
- **Multi-agent presence stack (+N):** built + tested but dormant until codoc emits a per-agent
  author signal in `activity.json` (today all live features attribute to the single keyless-Claude
  role; the layer is parameterized to drop the signal in without a rewrite).
- Fresh-repo palette "init" row is currently inert (`noop`); persist `recentFids` to `UiState`
  for cross-reload continuity.
