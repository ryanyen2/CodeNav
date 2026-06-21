---
date: 2026-06-15
topic: codoc-documentation
focus: improve codoc-as-documentation — generation quality/structure, layout/content, and reading/navigation interactions; borrow architecture/layout/interaction patterns from doc systems (Sphinx, JSDoc, pydoc, mathlib, rustdoc) and AI doc tools (DeepWiki, OpenDeepWiki, CodeWiki, RPG/CoderMind, Swimm)
mode: repo-grounded
---

# Ideation: Improving codoc-as-documentation

## Grounding Context (Codebase Context)

**codoc today.** A human-intent "feature tree": each node is a feature (title + 1–3 sentence prose description, multi-paragraph allowed) bound to code chunks across files. Rendered as `tree.codoc` (markdown-like text) + `tree.bindings.json` sidecar (v4) + a TipTap `Codoc Tree` webview (3 panes: tree nav · document · TOC rail). Code cited inline as `[label](codoc:file#symbol)`. Markdown-native signals: `**bold**`→Focus, `> …`→steering note, `[..](https://)`→Consult. Existing interactions: clickable code links (open file Beside via `openRef`), inlay binding chips (`9 refs`), decorations, span-anchored inline comments (`❝` popover), source-file code-lens, `[`-triggered ref completion, dependency-focus dimming. **There is NO hover-preview / tooltip-on-link today.** `feature_edges` (cross-feature coupling) + `code_edges` exist in the sidecar/store; a change ledger records actor/mode/caused_by provenance.

**Hard constraints (feasibility filter applied to every idea).**
- `tree.codoc` holds ONLY settled text; round-trip parse→render must stay a no-op. So TOC, previews, cross-refs, badges, search indexes must be **sidecar/decoration artifacts, not text**.
- The VS Code extension has **NO host→Python path** — any hover/preview/graph content must be assembled **host-side from the sidecar JSON** (or a new sidecar the loops emit, or a new MCP tool the agent calls).
- Three-pane topology is deliberately KEPT; consolidate the existing FOUR scattered dependency homes instead.
- Conventions: color = who/direction, shape = kind; pure `--vscode-*` theme tokens; avoid motion-heavy affordances.
- Free-prose descriptions are the deliberate default (structured fields are opt-in) — a real tension to respect.

**External prior art studied.** CodeWiki [arXiv 2510.24428] (bottom-up hierarchical generation + global cross-ref registry + hierarchical-rubric quality eval / CodeWikiBench); RPG/CoderMind [arXiv 2509.16198] (node = capability description + structural map + typed I/O edges); DeepWiki / OpenDeepWiki (auto Mermaid diagrams, Q&A grounded with line-cited sources, per-repo MCP, mindmap/graphify views, `.devin/wiki.json` steering file); Swimm (token-level coupling, drift-as-first-class-signal); Sphinx (`objects.inv` registry + dead-ref validation at build time, typed domains); rustdoc (§-anchored deep-linkable sections, source-link round-trip, "Implementors" backlink panel, intra-doc autolinks); NumPy/Google docstrings (See-Also-with-one-line-rationale, Examples as a distinct slot); Diátaxis (reference / explanation / how-to separation); mathlib (concept-first overview page + one-click depth, dual nomenclature); Sourcegraph (3-tier hover → references-panel → navigate); Obsidian (backlinks + unlinked mentions); Understand Anything (sequenced guided tour, dependency pathfinder).

**Synthesized insights (the three questions asked).**
- **Architecture (history/context):** mature systems maintain a machine-readable cross-reference registry (Sphinx `objects.inv`), provenance/drift state (Swimm), and a derived dependency graph (RPG/DeepWiki/rustdoc). codoc already has the graph (`feature_edges`/`code_edges`) and provenance (change ledger) but **no registry and no reader-facing drift signal**.
- **Layout/content:** recurring shape = concept-first overview → one-click depth (mathlib), progressive disclosure (gist → full), See-Also-with-rationale + Examples slots (NumPy), Diátaxis separation. codoc drops readers into a flat depth-first text dump with **no landing/overview**.
- **Interactions:** canonical pattern = 3-tier hover → references-panel → navigate (Sourcegraph), backlinks/Implementors (rustdoc/Obsidian), grounded Q&A with line-cited sources (DeepWiki), source round-trip. codoc has clickable links only — **no hover-preview**.
- **Through-line:** a cross-reference registry is the keystone — the one artifact that answers the architecture question and unlocks hover-preview, dead-ref validation, and scale-search.

## Topic Axes
- A1 — Generation quality & steering (how prose/fields are produced; lightweight structure with finer steering knobs; quality eval & sibling-consistency; freshness)
- A2 — Sync & provenance architecture (state/history/context that keeps docs correct: cross-ref registry, dead-ref/drift detection, source provenance, knowledge graph)
- A3 — Information architecture (macro doc shape: concept-first overview, learning-path/guided tour, Diátaxis layering, reading order ≠ structural order)
- A4 — Entry layout & content (per-feature fields/sections: See-Also-with-rationale, examples, I/O/signature, source-link round-trip, badges, dual nomenclature)
- A5 — Reading interactions (hover-preview tooltips, peek panels, in-doc search/Q&A, diagrams, progressive disclosure, backlink panels, breadcrumbs)

## Ranked Ideas

### 1. Three-tier hover-preview, assembled host-side from the sidecar
**Description:** Hovering a `[label](codoc:file#symbol)` ref or a feature link pops a *curated* card (not a truncated dump): tier-1 = target title + one-sentence gist + binding count; modifier-hover = full prose + immediate `feature_edges`; click = navigate. All content read from `tree.bindings.json` (or a small new `previews` slice the loops emit) — no synchronous Python. Reuses the existing comment-popover machinery + the `openRef` navigate path.
**Axis:** A5
**Basis:** `direct:` the named user wish ("hovering a link shows a preview of that section so you don't navigate back and forth") + the hard constraint that preview content is "assembled host-side from sidecar JSON"; the sidecar already carries `by_feature`, `features{title}`, `feature_edges`. `external:` Sourcegraph 3-tier hover → panel → navigate; museum wall-label curation (show the *right three lines*, not a wall of text).
**Rationale:** Kills the back-and-forth navigation the user called out, reusing data the extension already loads — the lowest-risk, highest-want item.
**Downsides:** Code-ref previews need a code snippet — best paired with #2's line ranges; otherwise tier-1 for code refs is only a signature.
**Confidence:** 90%
**Complexity:** Medium
**Status:** Unexplored

### 2. Cross-reference registry (`tree.index.json`) + dead-ref validation
**Description:** Emit one new sidecar each loop pass: a flat `{anchor → location, kind}` index over every feature id, title/slug, bound `(file, symbol)` (with line range), and inline `codoc:` ref label. On render, validate every `codoc:` link against it and flag dead ones as decorations; reuse Loop A's existing move/rename detection to auto-repoint relocated refs.
**Axis:** A2
**Basis:** `external:` Sphinx `objects.inv` + build-time dead-ref validation; CodeWiki global cross-ref registry. `direct:` the sidecar is already regenerated every pass and holds `by_feature`/`by_file`; `_detect_relocations` already computes the move/rename correspondence that makes auto-repoint deterministic.
**Rationale:** The keystone artifact — one index unlocks #1 (hover resolution), dead-ref lint, registry-ranked `[`-completion, scale-search, and future cross-repo (intersphinx-style) federation. Directly answers "what architecture is needed."
**Downsides:** Storing line ranges adds a binding field that drifts as code edits; auto-repoint is the higher-risk slice and can ship in a second phase behind plain flagging.
**Confidence:** 85%
**Complexity:** Medium
**Status:** Unexplored

### 3. "Connections" panel — consolidate the four dependency homes + add backlinks
**Description:** One per-feature panel in the detail pane unifying **Depends-on** (forward `feature_edges`), **Used-by** (the missing *inverse* — a host-side transpose), **Bound code** (the `N refs` list), and **Consult** links — rows colored by direction, shaped by kind. Focus-dimming becomes a hover affordance on this panel rather than a separate mode.
**Axis:** A3
**Basis:** `direct:` the constraint explicitly says "consolidate the four scattered dependency homes"; `feature_edges` is already computed and shipped, only the inverse view is missing. `external:` rustdoc "Implementors" panel, Obsidian backlinks, NumPy See-Also.
**Rationale:** Backlinks ("who relies on this?") are the single most-asked navigation question a forward-only edge map can't answer — and the data already exists, currently spent only on opacity-dimming.
**Downsides:** Risks becoming a dense panel on highly-coupled features; needs ranking/collapse.
**Confidence:** 80%
**Complexity:** Medium
**Status:** Unexplored

### 4. Concept-first overview landing + glance-mode pitches (progressive disclosure)
**Description:** A synthetic, non-editable **Overview** entry at the top of the document pane (pure decoration, never in `tree.codoc`): the bootstrap org-pass theme parents as mathlib-style cards + a *grounded* Mermaid map drawn from real `feature_edges` (every arrow provably real, unlike free-form AI diagrams). Plus a derived ≤8-word **pitch** per feature (sidecar `features{}.pitch`) used as the collapsed-row label, so the whole tree is skimmable; expand for full prose.
**Axis:** A4
**Basis:** `external:` mathlib concept-first overview + one-click depth; DeepWiki/OpenDeepWiki auto-Mermaid + mindmap. `direct:` the bootstrap org-pass already produces 3–6 broad theme parents (`propose_organization`); the pitch is cheap to derive in the same LLM pass that writes the description (or as a zero-cost fallback = first sentence).
**Rationale:** codoc already *computed* the repo's intent altitude during bootstrap and throws it away at read time. A landing page + one-line pitches answers "common layout/content" directly and makes a flat tree scannable.
**Downsides:** Pitch is another LLM-derived field that can drift; Mermaid layout for large trees needs subtree-scoping.
**Confidence:** 75%
**Complexity:** Medium
**Status:** Unexplored

### 5. Structure-when-present: Diátaxis-lite inferred slots + See-Also-with-rationale
**Description:** Keep free prose as the default, but have the loops *detect* (never mandate) lightweight structure: a derived `kind` hint (overview / how-to / reference) for tree-filtering, recognized-and-styled `Examples:` / See-Also sub-headings, and an opt-in `> see: [ref] — reason` cross-ref that rides the existing `> …` channel. Surface all as sidecar metadata.
**Axis:** A1
**Basis:** `external:` Diátaxis reference/explanation/how-to separation; NumPy See-Also-with-one-line-rationale + Examples slot; RPG typed node. `reasoned:` infer-don't-impose honors the deliberate free-prose default while delivering the user's stated "lightweight structure for finer control over what to steer."
**Rationale:** The direct response to the user's quality goal — structure that's *offered*, giving Diátaxis navigability without a rigid schema.
**Downsides:** Inference precision risk (mis-tagged kinds); overloading the `> …` channel (steer vs see-also vs comment) needs grammar disambiguation.
**Confidence:** 70%
**Complexity:** Medium
**Status:** Unexplored

### 6. Drift-as-trust: typed treatment/freshness badges + one-tap re-steer
**Description:** When Loop A sees a bound chunk's `tokens_hash` change under a realized feature whose prose wasn't amended, stamp a per-node signal — but *typed*, Shepardize-style (followed / refreshed / questioned / overruled-binding-lost), not a binary alarm. The webview renders a quiet `--vscode-*` badge; clicking a stale node offers one-tap "regenerate description from current code" that authors a pre-filled `> …` STEER directive.
**Axis:** A2
**Basis:** `external:` Swimm drift-as-first-class-signal; legal Shepardizing (typed treatment, not binary). `direct:` Loop A's `amend_on_change` trigger already detects this condition; the change ledger + `proposals.by_feature` already ship in sidecar v4; the comment→`> …`→STEER pipeline already exists.
**Rationale:** A doc that's silently wrong is worse than one labeled uncertain. Typing the drift turns "is this still true?" into a ranked worklist and a one-tap fix — turning sync into a *reader-trust* feature.
**Downsides:** Drift lifecycle (when set/cleared) touches Loop A semantics, not just decoration; risk of badge noise if thresholds are loose.
**Confidence:** 80%
**Complexity:** Medium-High
**Status:** Unexplored

### 7. Generation-quality eval harness + rubric
**Description:** A scriptable harness scoring a generated tree against a hierarchical rubric (coverage = every chunk bound, non-duplication, description specificity, hierarchy balance, ref validity via #2), emitting per-feature quality scores to a `tree.quality.json` sidecar. Feeds CI gates, the reading surface (flag thin nodes), and prompt tuning.
**Axis:** A1
**Basis:** `external:` CodeWiki hierarchical-rubric eval + CodeWikiBench. `direct:` the `codoc-ux-tester` skill + `tests/bdd` position reports already audit quality informally.
**Rationale:** The compounding flywheel — every prompt change becomes measurable ("specificity +12%, duplication →0") instead of vibes. Especially valuable for a research project where the benchmark is itself a contribution.
**Downsides:** Some rubric dimensions need an LLM judge (cost, variance); designing a defensible rubric is non-trivial.
**Confidence:** 70%
**Complexity:** Medium-High
**Status:** Unexplored

## Suggested build order
A tightly-coupled spine: **#2 (registry) → #1 (hover-preview) → #3 (backlinks/Connections panel)**. The remaining ideas (**#4, #5, #6, #7**) are largely independent and can be sequenced by appetite. Axis spread across the survivor set: A1×2, A2×2, A3, A4, A5 — all covered, weighted toward the user's explicit "architecture" and "generation quality" foci.

## Rejection Summary

| # | Idea | Reason Rejected |
|---|------|-----------------|
| 1 | Grounded Q&A / agent-brief MCP (`codoc_ask` / `codoc_brief`) | Strong runner-up (DeepWiki parity, agent-native) but a larger feature — deferred to keep the survivor set focused |
| 2 | Plain-text `OVERVIEW.md` portability companion | Useful distribution play; partial overlap with #4 — separate follow-up |
| 3 | Selects-reel generation (evidence-tagged sentence candidates) | Best answer to "finer steering" but a high-burden generation rewrite; goal folded into #5 |
| 4 | Audience lenses (newcomer / reviewer / agent) | Heavier; partially covered by #4 + #6; better as a brainstorm variant |
| 5 | Fog-of-war per-reader visibility state | Needs per-user state tracking; novel but lower value/complexity ratio |
| 6 | Desire-path traversal weighting | Needs click telemetry + privacy handling; depends on #3 |
| 7 | Genome-browser metadata tracks | Elegant but heavy UI; overlaps #3/#5 |
| 8 | Challenge-response realized audit | Narrower special case of #6 drift + #2 lint |
| 9 | Quest-log steering view | About the realize pipeline more than docs reading; one-tap steer kept in #6 |
| 10 | Unlinked-mention auto-bind | Precision risk (false matches); depends on #2 registry |
| 11 | Zero-budget deterministic descriptions | Niche audience; overlaps #5 docstring extraction |
| 12 | Audio tour mode | Niche; the guided-tour value is kept in #4 |

_Convergence note:_ the ~48 raw candidates collapsed heavily — most singletons folded into the survivors above (all hover variants → #1; registry/dead-ref/line-anchored bindings → #2; backlinks/consolidation → #3; pitch/overview/tour/Mermaid → #4; Diátaxis-lite/See-Also/structured slots → #5; drift/Shepardize/auto-steer → #6; eval/capability-first generation → #7).
