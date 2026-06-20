# World-class editing experience — design spec

> 2026-06-20 · scope: the `Codoc Tree` webview (`providers/tree-editor.ts` + `webview/*`)
> and its code-side companion surface (`providers/{code-lens,decoration,inlay,agent}.ts`).
> Goal: a genuine, narration-free wow. EVOLVE the mature system — no rewrite.
> Respect the existing grammar: ONE structural accent (`--accent`) + two directional
> hues (`--dir-review` code-ahead blue, `--dir-await` doc-ahead green); lifecycle =
> phase colour (`--ce-editing` / `--ce-del` / `--ce-staged`); status = motion/shape,
> never hue; authorship = ink tint + opacity; the `body.vscode-reduce-motion` blanket
> gate is sacred and every new motion runs through `motion.ts`.

---

## 1. Diagnosis — the current editing experience's 6 concrete weaknesses

The system is internally rigorous but the *experience* is quiet to the point of
invisible. Six specific gaps keep it from screen-recording wow:

1. **The two surfaces never touch each other on screen.** The doc and the code live in
   separate worlds. The only bridge is a *manual* click on a `codoc:` ref chip, which
   fires `codoc.openRef` → `ViewColumn.Beside` (`extension.ts:269`). Editing prose does
   *nothing* visible to the code; editing code surfaces only as a static dashed-purple
   whole-line border (`decoration.ts:121` `pendingCodeChange`) and a `codoc: ⟳ will
   change` CodeLens (`code-lens.ts:47`) — text, no live link. There is no *bridge that
   reads in a silent video*. This is the single biggest missed opportunity.

2. **Agent presence is a 6px dot.** When Loop A/B or a realize pass runs, the only
   feedback is `.badge.active-write` / `.ce-phase-editing::after` — a pulsing 6px dot
   after a heading (`doc-view.css:317`, `:548`) plus a static SVG gutter icon
   (`agent.ts:19`). There is no sense of *a collaborator in the doc with me*: nothing
   travels, nothing is labelled, you cannot tell *which agent* or *what it is doing*
   without reading a `title` tooltip. Figma multiplayer this is not.

3. **The lifecycle ramp is built from bare CSS dots and rails — no icon language.**
   captured → pending → resolving is rendered as: a dotted rail + faint dot
   (`.ce-captured-*`), a dashed breathing ring (`.badge.pending` / `.ce-pending-dot`),
   a pulsing solid dot (`.ce-phase-editing`). All hand-drawn `border-radius:50%`
   primitives. They are *consistent* but not *legible at a glance* and not *tactile* —
   state changes hard-swap classes with no morph/settle. There is no coherent iconography
   a viewer can learn in one frame. anime.js (`motion.ts`) is wired but used ONLY for
   scroll/minimap; zero lifecycle transitions go through it.

4. **No command palette / no fast path.** Everything is mouse-driven (hover to reveal
   `✓`/`✗`, click the toolbar `↑ Commit & send`, drag handles to reparent). Keyboard nav
   exists only inside the tree pane (`doc-view.ts:741`) and the native VS Code command
   list is a flat 22-command dump (`package.json` contributes) with no fuzzy "go to
   feature / accept / hand-to-agent" scoped surface. A power user has no ⌘K.

5. **Typography leans entirely on `--vscode-font-family`.** Every surface — the editorial
   navigator title (`.title`, `doc-view.css:281`), the whole-doc body (`.ce-whole-surface
   .ProseMirror p`, `:660`), the headings — falls back to the *same* system UI font. The
   doc claims to be "a one-page article" but reads like a settings panel. There is no
   editorial voice, no body/mono pairing, no optical sizing. The type scale
   (`[data-level]`, `:637`) is good; the *typeface* is doing none of the work.

6. **Accept / reject / save / hand-off have no tactile payoff.** A verdict just toggles
   `body.applying` opacity (`doc-view.ts:154`) and waits 5s for a round-trip. `⌘S`
   commits silently. There is no confirming pulse, no "it landed" micro-moment — the most
   emotionally important gestures in the product (accepting the AI, handing off work) feel
   like nothing happened. The 0.1s tactile floor is unmet on the moments that matter most.

The through-line: **the data model and grammar are world-class; the *feedback* is
under-expressed.** Every fix below is presentation-layer and additive.

---

## 2. Headline moments — winning concepts with exact specs

### A. Live cross-surface diff bridge  *(the headline)*

**The moment, in one sentence:** type in a feature's prose and its bound code file glides
in beside you with the exact lines that prose implicates lit up live; conversely, edit
that code and the feature node in the doc lights up with a small travelling spark — the
two panes breathe together.

#### A.1 Layout — the auto-split

- On the **first prose keystroke inside a feature heading or its description** that has at
  least one binding, the host opens that feature's *primary binding file* (highest-weight
  binding from `payload.nodes[fid].bindings[0]`) in `ViewColumn.Beside` — reusing the
  existing `codoc.openRef` path (`extension.ts:269`, already `revealRange` +
  `Beside`), but **non-focus-stealing** (`preserveFocus: true`, `preview: true`). The
  webview keeps the caret; the code is a calm companion, never a context switch.
- New webview→host message: `bridge-open { fid, file, symbol, lines: number[] }`. Debounced
  **180 ms** after the last keystroke (one `--dur-nav`-ish beat) so it never thrashes on
  fast typing. Closing the feature (caret leaves, 1.5 s idle) does NOT close the code pane
  — opening is eager, closing is the user's call. A `bridge-dim { fid: null }` just clears
  the code-side highlight.
- If the feature has **no binding**, the bridge shows nothing on the code side and instead
  renders the doc-side "this will create new code" affordance (A.4) — never an empty split.

#### A.2 The compact diff signal — code side (in-situ, CodeLens-grade)

When prose for feature *F* is being edited, the bound code file shows, on the declaration
line of each implicated symbol, a **single-line living signal** — NOT a diff view, NOT a
panel. Three layers, all already half-built in `decoration.ts`:

1. **A 2px left rail** in `--dir-await` green (this is doc-ahead: *the doc moved, the code
   will follow*) — extend the existing `pendingCodeChange` decoration but recolour from
   dashed-purple to a **solid green** rail (matching `--ce-staged` semantics). Whole-line,
   `overviewRulerLane.Left`.
2. **An inline CodeLens** above the symbol, replacing the static `codoc: ⟳ will change`
   string with a *live* one that updates as the prose settles:
   `◇ implicated by "Persist feature drafts" — handing off will rework this`. The `◇`
   (Phosphor `diamond`, see C) is the doc-ahead glyph. While the user is still typing
   (uncommitted/captured), the lens reads `◇ editing "…"`; once they ⌘S it flips to
   `◆ queued — run /codoc:sync`. Shape carries the lifecycle, green carries the direction.
3. **A breathing gutter dot** on the implicated line: reuse `media/gutter-drafting.svg`
   recoloured to green, `gutterIconSize: contain`. This is the only animated element on the
   code side and it rides the SAME `breathe` keyframe timing (2.6s) as the doc-pane pending
   badge, so the two panes literally pulse in sync — *that* is the screen-recording tell.

Which lines light up: the host already maps pending symbols → declaration lines by leaf
name (`decoration.ts:180`). For the live bridge, send the implicated symbols from the
feature's bindings (`bindings[].symbol` → leaf), no LLM needed — binding anchors are the
ground truth of "what code this prose is about."

#### A.3 The reverse — code edit lights up the doc node

When the user edits a *source file* that codoc has bindings into, the corresponding
feature node in the doc must light up in real time. The plumbing exists: the host watches
files; on `onDidChangeTextDocument` for a bound file, resolve edited line ranges → bindings
→ feature ids, and push `code-touch { fids: string[] }` to the webview.

Visual (doc side): a **travelling spark** lands on the feature heading.

- A new decoration family `ce-code-touch` on the heading: a small **inbound** glyph —
  Phosphor `arrow-bend-down-left` (⤵) in `--dir-review` blue (code-ahead: *code moved, the
  doc may need to follow*) — fades in at the heading's right edge with a 220 ms
  `--ease-spring` rise (`anime.js translateY(6px)→0 + opacity`), holds for ~2.5 s, then
  settles to a quiet persistent **blue underline tick** under the heading until the next
  Loop A pass reconciles (at which point it either clears or graduates into a real `amend`
  proposal via the existing flow).
- If the code change is large enough that Loop A will likely re-question the prose, the
  tick gets the existing `.badge.divergent` halo treatment (`doc-view.css:311`) — *no new
  visual*, reuse.
- In the **tree pane**, the same event flips the row's left rail to a 1.4s blue
  `active-write` pulse momentarily (`.badge.active-write` already exists) so the navigator
  shows where the action is even when that section is scrolled off.

#### A.4 No-binding case (doc-ahead create)

If the edited feature has no code yet (`realized === false`, an accepted plan placeholder)
the code side shows a **single ghost CodeLens at the top of the most-likely target file**
(the parent feature's file, or the file of the nearest sibling binding):
`◇ new code will be added here for "…"`. Green, dashed gutter. This is the
`fileLevel` fallback already in `decoration.ts:193` — just wire it to the live bridge.

#### A.5 Motion + tokens (A)

```css
/* new tokens (extend :root) */
--dur-bridge:     0.18s;                          /* code pane glide-in / highlight settle */
--bridge-await:   var(--dir-await);               /* doc→code rail + lens + gutter (green)  */
--bridge-review:  var(--dir-review);              /* code→doc spark + tick (blue)           */
--ease-bridge:    var(--ease-out);                /* both directions share one curve         */
```

- Code pane open: VS Code's own editor-open transition handles the glide; we add nothing
  (respecting the host). The *highlight* fades in over `--dur-bridge` via decoration swap.
- Doc spark: `motion.animate(headingEl, { translateY: [6,0], opacity:[0,1] }, { duration:220, ease:'spring' })`
  — guarded by `prefersReducedMotion()` (jumps to final). New helper `sparkIn(el)` in
  `motion.ts` beside `staggerHover`.
- Reduced motion: rail + lens + tick are all static; only the gutter breathe and the spark
  rise are gated, and both already have static fallbacks (solid dot / instant tick).

#### A.6 Failure / edge states (A)

- **Binding file deleted / unresolvable:** the bridge silently no-ops (the registry already
  flags dead refs via `isRefResolved`); the doc shows the existing dead-ref `⚠` hovercard,
  never a broken split.
- **Multiple bindings across files:** open only the top-weight file; the doc heading's
  threads line (`.ce-threads`) already lists the rest — clicking another opens it Beside,
  same path. Never open 4 splits.
- **User dismisses the code pane:** remember it (`workspaceState` key `codoc.bridgeOpen`);
  don't re-open on every keystroke that session. A one-line undo-able choice.
- **Rapid section hopping:** the 180 ms debounce + `bridge-dim` on caret-leave keeps it from
  strobing; reuse the `centerTween.cancel()` pattern.

---

### B. Agent-as-collaborator presence

**The moment:** when an agent works, a small labelled avatar glides through the doc and the
tree to the feature it is touching, trailing a soft comet tail, whispering "Claude is
implementing *Persist feature drafts*". It feels like someone is in the document with you.

#### B.1 The presence cursor

A single floating element, `.ce-presence`, absolutely positioned over the doc surface (and
a twin over the tree pane). One per active agent (there is rarely more than one; cap at 3,
then collapse to a "+N" stack). Driven by the *existing* signal — `payload.sync.phase`
(fid → `editing | reflecting | done`) + `activeWrite`/`activeRead` (`protocol.ts` SyncState,
already plumbed from `activity.json` in `tree-editor.ts:365`). No new backend data.

Anatomy (Figma-multiplayer DNA, calm):

```
   ◐  Claude            ← a 16px ring avatar tinted by --ink-claude (role = ink, existing)
   ╰─ implementing      ← a whisper label: phase verb + feature title, 11px, fades after 4s
```

- **Avatar:** a 16px circle, `background: color-mix(--ink-claude 22%)`, `border: 1.5px
  solid --ink-claude`. The agent's glyph sits inside: `editing` → Phosphor `pen-nib`,
  `reflecting` → Phosphor `arrows-clockwise`, `read` → Phosphor `eye`. Role tint comes
  straight from the `--ink-*` family (claude purple, codex green, gemini yellow, cursor
  orange) — already defined (`doc-view.css:34`). Two agents = two inks, instantly distinct.
- **Label:** `Claude · implementing` then the feature title, italic muted. Auto-hides after
  4 s of no movement (just the avatar persists), reappears on the next hop. Copy table B.4.

#### B.2 The glide + trail

When the agent moves to a new feature (phase map gains/changes an fid):

- The avatar **travels** from its current screen position to the new heading's right edge.
  `motion.animate(presenceEl, { top:[y0,y1], left:[x0,x1] }, { duration: navDuration(dist),
  ease:'spring' })` — reuse the existing distance-scaled `navDuration` (`motion.ts:45`) so a
  long hop takes longer, a short one snaps. This is the *one* place a gentle overshoot
  (`--ease-spring`) is earned.
- **Soft trail:** as it travels, drop 3–4 fading ghost dots along the path (a comet tail).
  Each is a 4px `--ink-*` dot at `opacity .35→0` over 500 ms, staggered — a tiny
  `staggerHover`-style emit. Pure decoration, gated by reduced motion (then: no trail, the
  avatar just appears at the destination).
- **Working pulse:** while parked on a feature in `editing`, the avatar ring does a slow
  2.6 s breathe (same keyframe as everything else). `reflecting` → the ring goes hollow +
  rotates the `arrows-clockwise` glyph 360° once per 1.6 s. `done` → it fades out over
  400 ms and the heading gets a one-shot green "landed" check (see C.3 — shared with
  accept).

#### B.3 Tree-pane twin

A miniature version (just the 16px avatar, no label) rides the **tree row** of the active
feature — absolutely positioned at the row's right edge, replacing the bare
`.badge.active-write` dot with the actual agent avatar. Same glide between rows. So whether
you are reading the doc or scanning the navigator, you see *who* is where.

#### B.4 Copy / microcopy (B)

| phase | whisper |
|---|---|
| `editing` (write) | `{Agent} · implementing` |
| `reflecting` | `{Agent} · syncing the tree` |
| `read` | `{Agent} · reading {title}` |
| `done` (transient) | `{Agent} · done` (300 ms, then fade) |
| multi (>1 agent) | stack avatars; hover → `Claude, Codex working` |

Tone: present-continuous, lowercase verb, never exclamatory. It is a colleague, not a
notification.

#### B.5 Tokens / failure (B)

```css
--presence-size:   16px;
--presence-trail:  4px;
--dur-presence:    var(--dur-nav);      /* glide baseline, distance-scaled in JS */
--ease-presence:   var(--ease-spring);
```

- **Stale phase (agent crashed mid-edit):** the host already closes epochs; on a closed
  epoch with no `done`, fade the avatar after a 12 s grace so a dead agent never haunts the
  doc.
- **Feature scrolled off-screen:** clamp the avatar to the top/bottom edge of the doc
  viewport with a tiny chevron (↑/↓) + label, like Figma's off-screen cursors — click to
  scroll there. Reuse the TOC-rail active tick to also mark the agent's position.
- **Reduced motion:** no glide, no trail, no breathe — the avatar simply *appears* at the
  active feature and the label shows. Presence is still legible, just static.

---

### C. Lifecycle micro-interactions + iconography

**Recommendation: Phosphor Icons** (MIT, `@phosphor-icons/core` ships clean SVGs; tree-shake
to the ~10 we use, inline as a sprite — no font, no CDN, webview-CSP-safe). Phosphor's
*weight* axis (thin/regular/fill/duotone) maps perfectly onto our lifecycle intensity ramp:
captured = `thin`, pending = `regular`, resolving = `fill`. Codicons lack this weight axis;
Lucide lacks the fill variant. Phosphor wins.

#### C.1 The icon set (exact names, per state)

| lifecycle state | meaning | Phosphor icon | weight | hue (existing token) |
|---|---|---|---|---|
| **captured** | recorded locally, not sent | `circle-dashed` | thin | `--ce-editing` (blue) |
| **pending / staged** | sent, agent will act, awaiting sync | `diamond` | regular | `--ce-staged` (green) |
| **resolving (editing)** | agent mid-edit on code | `pen-nib` | fill | neutral fg (status axis) |
| **resolving (reflecting)** | agent syncing tree back | `arrows-clockwise` | regular | neutral fg |
| **divergent** | AI changed beyond your edit | `warning-diamond` | regular | `--dir-review` (blue) |
| **unrealized (plan)** | accepted, no code yet | `circle-dashed` | thin | muted, italic kept |
| **deletion caret** | pure removal, no text left | (keep the 2px caret) | — | `--ce-del` (amber) |
| **accept verdict** | landed | `check-circle` | fill | `--accent-add` (green) |
| **reject verdict** | dismissed | `x-circle` | regular | `--accent-retire` (red) |
| **hand-to-agent** | commit & send | `paper-plane-tilt` | regular | `--dir-await` (green) |
| **code→doc spark** | code edit touched this | `arrow-bend-down-left` | regular | `--dir-review` (blue) |
| **doc→code implicate** | prose touched this code | `diamond` (open/fill = phase) | — | `--dir-await` (green) |

The shape is *one family*, the weight is *the intensity ramp*, the hue stays *direction
only* — fully inside the existing grammar. A viewer learns "dashed circle = mine and local,
diamond = sent, filled pen = AI is on it" in literally one frame.

#### C.2 State transitions (anime.js — real values)

All routed through a new `motion.ts` helper `morphLifecycle(el, from, to)`; reduced-motion →
instant class swap (no tween). The icons are inline SVG, so we cross-fade + scale, never
redraw paths (cheaper, smoother):

- **captured → pending** (user hits ⌘S): the `circle-dashed` **scales down to 0.6 + fades**
  (140 ms `--ease-out`) while the `diamond` **scales 0.6→1 + fades in** (180 ms,
  `--ease-spring`, +40 ms stagger) at the same anchor. Reads as "my note crystallised into a
  task." Plus the rail recolours blue→green over 200 ms.
- **pending → resolving** (sync starts): `diamond` (green, regular) → `pen-nib` (fill,
  neutral) cross-fade 160 ms; the breathe (2.6 s) hands off to the working pulse (1.4 s) —
  the rhythm *quickens*, which the eye reads as "it started." No hue change (status axis).
- **resolving → done** (landed): `pen-nib` fades, a `check-circle` (fill, green) **pops**
  (`scale [0,1.15,1]`, 260 ms, `--ease-spring`) and **dissolves** after 600 ms. One
  satisfying beat. This is the same "landed" pop shared with accept (C.3).

#### C.3 Tactile feedback on the key gestures

- **Accept (`✓`):** the proposal row's text does a 1px green flash (`background` pulse,
  120 ms) → the `check-circle` pops at the verdict button → the whole `.ce-diff` row
  collapses its height to 0 over 180 ms `--ease-out` as it is removed (not a hard
  `display:none`). The accepted prose, if it merges into the doc, gets a *brief* green
  `ce-captured-add`-style underline that fades over 800 ms — "this is now yours."
- **Reject (`✗`):** `x-circle` quarter-spin (−90°, 160 ms) + the row slides out left 8px +
  fades (140 ms). Quieter than accept by design — dismissing should not celebrate.
- **Save / ⌘S pulse:** the toolbar `↑ Commit & send` button does a `scale .97→1` press
  (already on `.ce-cmt-send:active`) and a green ripple expands once from it; every
  captured rail in view simultaneously recolours blue→green (the captured→pending morph,
  staggered top-to-bottom by ~20 ms each via `staggerHover`). Watching your whole doc's
  margin shimmer green on one keystroke is the *second* big screen-recording moment.
- **Hand-to-agent:** the `paper-plane-tilt` icon on the button does a tiny launch
  (`translateX/Y` up-right 4px + fade, 220 ms) — the universally legible "sent." Then the
  presence avatar (B) glides in to the first handed-off feature ~400 ms later: *I sent it,
  and there it goes to work.* The two moments chain into one story.

#### C.4 Tokens (C)

```css
--dur-morph:     0.16s;   /* lifecycle icon cross-fade            */
--dur-pop:       0.26s;   /* accept / done celebratory pop        */
--dur-collapse:  0.18s;   /* row remove height-collapse           */
--ease-pop:      var(--ease-spring);
--icon-thin:     1px;     /* phosphor weight ≈ stroke, for inline tuning */
```

#### C.5 Failure / edge (C)

- **No daemon (verdict won't drain):** the `check-circle` pops optimistically but the
  5 s `applyingTimer` (`doc-view.ts:161`) reverts it to a quiet "still pending — run
  /codoc:sync" if nothing lands. Never leave a fake-success.
- **Reduced motion:** every morph becomes an instant icon swap; the pop becomes a single
  static frame of the final icon held 600 ms then removed; row-collapse becomes `display:none`.
  All gated through `motionGuard`.

---

### D. Minimal command palette (⌘K)

**The moment:** ⌘K inside the webview drops a calm, centered fuzzy palette — type three
letters, jump to a feature, accept everything, hand off, all from the keyboard.

#### D.1 Visual

- A centered floating card, reusing the exact chrome already defined for `.ce-peek` /
  `.cr-popup` (`--shadow-pop`, 10px radius, hairline border, `ce-pop-in` entrance). New
  class `.ce-palette`: `position:fixed; top:18vh; left:50%; translateX(-50%); width:min(560px,
  92vw)`. A backdrop `.ce-palette-scrim` at `background: color-mix(--vscode-editor-background
  60%, transparent)` with a 4px backdrop-blur (calm, not modal-heavy).
- **Input row:** a single borderless input, 15px, with a leading Phosphor `magnifying-glass`
  (thin) and a right-side mode hint (`feature ↵ go · ⇧↵ open code`). Placeholder: `Search
  features, run a command…`.
- **Results:** rows at 32px, leading icon (the command's lifecycle icon from C, or a
  feature's status glyph), title, and a right-aligned muted detail (`3 refs · src/loop.py`).
  Active row = `--select-bg` + a 2px `--accent` left tick (rhymes with `.row.selected`).
  Section headers (`FEATURES`, `ACTIONS`) in the existing `.ce-peek-label` style (9.5px,
  uppercase, tracked). Keyboard: ↑/↓ move, ↵ run, ⇧↵ secondary action, Esc close.
- **Fuzzy match** highlights matched chars in `--accent` (bold). Use a tiny local matcher
  (no dep) ranking by contiguous-run + word-boundary, capped at ~30 results.

#### D.2 Default command set (the essential surface)

Navigation (always present, the bulk):
- **Go to feature** — every feature, fuzzy on title; ↵ scrolls the doc + selects the row,
  ⇧↵ opens its bound code Beside (the bridge).
- **Go to feature with drift / pending / divergent** — filtered lists when those exist.

Actions (contextual — only shown when applicable):
- **Accept all proposals (N)** / **Reject all proposals (N)** — when `pendingEventIds.length`.
- **Accept change at cursor** / **Reject change at cursor** — when the caret is in a
  proposal (mirrors `codoc.acceptHunkAtCursor`).
- **Hand to agent (N drafts)** — when `drafts.length` (the ⌘S path).
- **Withdraw realization for {feature}** — when the active feature is held.
- **Open bound code** — the active feature's primary binding, Beside.
- **Toggle Glance** — the existing pref.
- **Open the bound file in the tree** / **Reveal in code** for the active feature.

View / misc:
- **Collapse all / Expand all**, **Go to next/prev sibling** — reuse existing commands.

Each ACTION row carries its C-icon (e.g. Hand to agent = `paper-plane-tilt` green), so the
palette is also a *legend* for the lifecycle language.

#### D.3 Empty / welcome state

- **Empty query:** show a curated default list — `Recent features` (last 3 selected, from
  the persisted `UiState`), then `Quick actions` (whatever is applicable now: "2 proposals
  to review", "1 draft to hand off"). So ⌘K with no typing is already useful — it is a
  *status dashboard*.
- **No matches:** a single calm centered line — `No features or commands match "xyz"` — in
  `.cr-empty` italic muted style. Below it, one affordance: `↵ Create feature "xyz"` (mints a
  new top-level heading with that title — wiring the `#`-input-rule path). Turns a dead end
  into authoring.
- **Fresh repo (no features):** `Run codoc init to bootstrap the tree` with an inline run
  affordance — matches the existing empty-doc copy (`doc-view.ts:563`).

#### D.4 Wiring + tokens (D)

- Bind ⌘K **inside the webview** (not VS Code's) via a `keydown` capture in `doc-view.ts`
  (the editor's keymap must not swallow it — register on `document` with `metaKey&&'k'`,
  `preventDefault`). All actions reuse existing `vscode.postMessage` kinds + `setSelected` /
  `wholeEditor.scrollToFeature` — no new host messages except `bridge-open` (shared with A).
- Reduced motion: scrim blur stays (static), `ce-pop-in` is gated to an instant show.

```css
--palette-w:      min(560px, 92vw);
--palette-top:    18vh;
--scrim-blur:     4px;
--scrim-bg:       color-mix(in srgb, var(--vscode-editor-background) 60%, transparent);
```

---

### E. Typography & color

#### E.1 Fonts (free, webview-clean — ship as bundled `.woff2`, no CDN, CSP-safe)

- **Doc body + headings — `Inter`** (OFL). The editorial-grade neutral sans; ships a
  variable `.woff2` (one file, all weights), has the optical features the type scale needs,
  and looks intentional next to VS Code's UI without clashing. Use its `cv05`/`cv11`
  stylistic sets for a slightly warmer, less "default" feel. Headings lean on the existing
  weight ramp (750/680/640).
  - *Why not the system font:* the whole pitch is "this is a document, not a panel." A real
    typeface is the cheapest, highest-impact upgrade in this entire spec.
- **Navigator titles + whispers — also `Inter`**, at 12.5px, `letter-spacing:-0.005em`
  (already set). One body family across doc and navigator = one editorial voice.
- **Code / mono — `JetBrains Mono`** (OFL) for code-ref chips (`.codoc-code-ref`), the
  `@`-picker `.cr-name`, binding targets (`.ce-hc-target`), the deletion-caret family, and
  inside the bridged code pane labels. It has the ligature-off legibility and the calm,
  even rhythm that pairs with Inter far better than the VS Code editor-font fallback. Falls
  back to `var(--vscode-editor-font-family)` if the user has a strong preference.

Ship via `@font-face` in `doc-view.css`, `font-display: swap`, `src: url(./fonts/Inter.woff2)`.
Add a token layer so a future swap is one line:

```css
--font-doc:  'Inter', var(--vscode-font-family, system-ui);
--font-mono: 'JetBrains Mono', var(--vscode-editor-font-family, monospace);
```

Then repoint `.title`, `.ce-whole-surface .ProseMirror`, `.codoc-feature-heading`,
`.ce-glance-pitch` → `var(--font-doc)`, and the mono surfaces → `var(--font-mono)`. **This
is a pure find-replace of `var(--vscode-font-family…)` at the doc-typography call sites** —
low risk, instant lift.

#### E.2 Refined muted-pastel-on-dark palette

The current palette is *correct* but leans on raw `--vscode-charts-*` which differ wildly by
theme and can come in too saturated. Pin the grammar hues to **hand-tuned, desaturated
dark-mode values** (the lifecycle phases already do this — `doc-view.css:28`), with the
theme token as fallback. Hierarchy comes from *muting*, not new colours.

```css
/* directional grammar — desaturated, dark-tuned (fallback to theme charts) */
--dir-review:  var(--vscode-charts-blue,  #7aa2d6);   /* code-ahead — muted slate-blue   */
--dir-await:   var(--vscode-charts-green, #84b08a);   /* doc-ahead  — muted sage          */

/* authorship inks — pastel, never neon (keep human neutral) */
--ink-claude:  #b9a3e8;   /* soft lilac   */
--ink-codex:   #8fc0a0;   /* soft mint    */
--ink-gemini:  #d8c486;   /* soft wheat   */
--ink-cursor:  #d6a583;   /* soft clay    */

/* lifecycle phases — keep the author's tuned values, nudge toward pastel */
--ce-editing:  #5aa6e0;   /* captured — calmer than rgb(0,142,255)     */
--ce-del:      #e0b46a;   /* deletion caret — softer amber             */
--ce-staged:   #6fae74;   /* staged — sage, matches --dir-await         */

/* text muting ladder — ALL hierarchy lives here, not in hue */
--fg-strong:   var(--vscode-foreground);
--fg-body:     color-mix(in srgb, var(--vscode-foreground) 82%, transparent);  /* (exists inline) */
--fg-muted:    color-mix(in srgb, var(--vscode-foreground) 55%, transparent);  /* (exists)         */
--fg-faint:    color-mix(in srgb, var(--vscode-foreground) 38%, transparent);  /* labels/ticks     */
```

Rule of thumb to enforce: **at most two saturated hues in view at once** (the two directional
grammar colours); everything else is the foreground at a muting level. The pastel inks only
appear when an agent is actually present, so the resting doc is near-monochrome and calm —
which makes the moment an agent *does* arrive (its lilac avatar gliding in) land that much
harder. Calm baseline → expressive event is the whole emotional arc.

The HC floors (`doc-view.css:87`+) already pin every faint tint to a border/solid under high
contrast — keep them; the new pastels inherit those floors unchanged.

---

## 3. Prioritized build order — most wow per unit effort

> Each item is independently shippable and reduced-motion-safe by construction.

**P0 — `morphLifecycle` + Phosphor icon set + tactile accept/save (C).**
*Highest wow/effort.* It touches every surface the user already looks at, needs **no new
host messages or backend data**, and converts the existing bare dots into a learnable icon
language plus the two most emotional gestures (accept-pop, save-shimmer). One `motion.ts`
helper + an inline SVG sprite + repointing existing decoration classes. Ships in days, and
*this alone* produces a shareable recording (the margin-shimmers-green-on-⌘S moment).

**P1 — Typography + palette (E).**
Near-zero-risk find-replace of font tokens + pinning the grammar/ink values. Instantly makes
every frame look intentional and editorial. Do it right after P0 so the icon work is shot on
the final type. Half a day.

**P2 — Live cross-surface diff bridge (A).**
*The headline, but it needs P0's icon/motion vocabulary to read well.* Doc→code first (reuse
`pendingCodeChange` + `openRef Beside`, recolour green, live CodeLens), then the code→doc
spark (the new `code-touch` watcher + `sparkIn`). Two new host messages
(`bridge-open`, `code-touch`). This is the viral one — sync-breathing panes — but build it on
the finished motion layer.

**P3 — Agent presence (B).**
Reuses P0's icons + P2's `motion` glide helpers and the *already-plumbed* `sync.phase` data.
The avatar + trail + whisper is mostly new webview DOM/CSS, no backend. High delight, medium
effort, depends on the motion primitives being solid first.

**P4 — Command palette (D).**
Self-contained, keyboard-power-user delight, reuses `.ce-peek` chrome and existing commands.
Lower viral-per-frame than A/B/C (it is keyboard, not ambient) so it lands last — but it ties
the whole thing together and doubles as the lifecycle legend.

**The single thing to build first:** **P0 — `morphLifecycle` + the Phosphor icon set + the
accept-pop / ⌘S-shimmer.** It is the smallest change that makes the product *feel* alive in a
silent recording, it unblocks the motion vocabulary every later moment depends on, and it
needs nothing from the backend.
