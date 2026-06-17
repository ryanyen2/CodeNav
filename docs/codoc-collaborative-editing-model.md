# codoc — Collaborative Editing Model

The conceptual model for the doc surface: one rich-text document, shared between a
human and one or more AI agents (claude-code, codex, gemini, cursor, …), kept in
sync with the codebase. This is the spec the editor implementation follows.

It is built on a **vendored, transaction-based tracked-changes engine**
(`sungkhum/tiptap-track-changes`, MIT — see `vscode-codoc/src/webview/tiptap/track-changes/`),
so a change from either side is a first-class, attributed, negotiable unit derived
from real editor transactions — not a snapshot text-diff.

> Origin: `docs/brainstorms/2026-06-16-codoc-collaborative-editing-model-requirements.md`
> · plan: `docs/plans/2026-06-16-001-feat-codoc-collaborative-editing-model-plan.md`.
> This document describes the model **as shipped** (units U1–U8).

---

## The core shift: one surface, two directions

The earlier model had the human juggle two toggles (pen/pencil authorship × editing/
suggesting mode). That is gone. There is **one editing surface and no modes**: the
human just edits. What an edit *means* is decided by the daemon, and changes flow in
two asymmetric directions.

### Human → agent: edit, commit, "being realized"

The human types; every edit **commits** immediately (the doc reflects their new
intent at once — no strike on your own typing, no cursor jank). The daemon's
`classify.py` then decides, per edit:

- **pure-doc** (renaming, rewording, documenting existing code) → it just commits.
  No badge, nothing queued. *Feels instant.*
- **code-implying** (the description reads imperative — "Add validation…", or it's a
  plan node) → the edit commits **and** Loop B queues a realize directive for the
  agent. The feature wears a calm **"being realized"** badge meaning *code is
  catching up*. A faithful realization clears it on its own — no nagging.

The badge is a pure projection of the daemon's **doc-wins hold set** (`sidecar.holds`
= live intents ∪ queued directives) — never a client-side guess. There is no
"suggesting mode": asking the AI to take something over *is* committing a
code-implying edit.

### Agent → human: tracked changes you review

When an agent changes things (drift reflected back by Loop A, a plan, an amend
proposal), the change surfaces in the doc as the **engine's `insertion`/`deletion`
marks**, authored by that agent (tinted per role), in an *awaiting your review*
state. Old and new text coexist exactly where you read it. You **accept** or
**reject** inline; the verdict rides `inbox.json` → Loop B (the daemon is the
authority — accept/reject is your verdict over the agent's change, never a local
double-apply). add / move / retire (which can't be in-prose tracked changes) keep a
compact ghost-row / strike / widget with the same inline ✓/✗.

This is the **asymmetry** (R4): *your* edit = your text + a badge; the *agent's*
change = a tracked diff you review.

---

## Resolution & divergence

The human→agent loop closes by comparing what the agent realized to what was asked
(`codoc/loop/divergence.py`, classified in Loop A):

- **faithful** — the agent did what was asked, on the feature asked → the badge
  **clears silently** (auto-resolve, no human action).
- **divergent** — the agent changed a feature **beyond** the one you edited (scope),
  or added a new node → it is flagged "review what the AI did" (`feature_resolution`
  in the sidecar), surfaced alongside the agent-authored proposal so you can
  agree/reject. The reliable signal is *scope*; an intent-text-drift signal exists
  but ships off by default (tunable).

Everything is **always back-out-able**:

- **Withdraw** a queued realization (the ✕ on the "being realized" badge) → cancels
  the directive and releases the hold; your committed prose stays (re-wording it is a
  normal edit). Rides `edits.json` `cancellations` → Loop B prunes the directive.
- **Reject** an agent change → reverts it (verdict).

**Concurrency — doc always wins.** While a feature has pending doc-ahead intent (a
queued directive), code-side AMEND/RETIRE/MOVE on it are suppressed (`classify.py`
`suppressed_by_hold`); binding maintenance still runs. An agent proposal on a feature
you are actively editing queues behind your edit. The model is N-author-capable
(multi-agent now, single human) — agents serialize through the daemon; each change
shows its author.

---

## The document is the tree

One rich-text document **is** the feature tree:

- **Headings** = feature nodes; heading depth = parent/child. Editing a heading's
  text = AMEND title; Tab / Shift-Tab indent/outdent = MOVE; a `## ` heading = ADD
  (exactly one node, stable id, no double-add); marking `~` = RETIRE. A raw line
  *deletion* is intentionally NOT a retire (too easy by accident) and is restored on
  the next render — use `~`.
- **Body** under a heading = that feature's description (prose + inline `codeRef`
  chips; `@`-autocomplete inserts a ref from the AST-bound symbols). Blank lines
  normalize to single paragraph breaks consistently on both the TS and Python sides,
  so adding headings / blank lines never reflows or jumps the caret.
- Marks: bold / italic / highlight, plus **comment** — a span-anchored note to the
  agent. Select prose → bubble → composer; the note is handed to Loop B as a one-shot
  `STEER` directive (it shows the anchored snippet as context).

---

## Single-writer & persistence (the data model)

The webview's authoritative artifact is **`.codoc/tree.doc.json`** — the rich
ProseMirror doc (authored intent + authorship/comment marks). The host writes only
that file (+ the `edits.json` provenance channel); it **never writes `tree.codoc`**.

- **`tree.codoc`** is derived, and the **daemon is its sole writer** — which removes
  the two-writer mtime race ("content of the file is newer" is gone). It stays the
  canonical, byte-stable text the loops and the change ledger consume.
- **Loop B reads `tree.doc.json`** (`codoc/codoc_file/doc_parse.py` → the same
  `ParsedTree` shape as the text parser) to learn webview edits, falling back to the
  `tree.codoc` text for raw-text-editor edits. The daemon watches `tree.doc.json` and
  renders `tree.codoc` itself after applying; `safe_write_tree` yields while a doc
  edit is pending so it never overwrites an unabsorbed edit.
- Agent tracked-change marks live only in the **payload** doc the webview renders
  (materialized host-side from the sidecar proposals); `tree.doc.json` and
  `tree.codoc` stay the clean baseline. The baseline-aware serializer (insertions
  excluded, deletions kept) guarantees a marked doc renders back to the exact
  pre-proposal text — old + new coexist for review without leaking into canonical
  state.

### The `edits.json` channel — three one-shot lists + the hold set

The host bridges what `tree.codoc` text can't carry to the loops (mirrored by
`codoc/loop/edits.py` / `src/state/edits-channel.ts`):

- **`edits`** — per-feature authorship annotations for each settle (Loop B stamps the
  ledger; default human/pen).
- **`steers`** — one-shot inline-comment notes; Loop B drains each into a `STEER`
  directive exactly once.
- **`cancellations`** — realize-withdrawals; Loop B prunes the matching directive.
- **`intents`** — the live doc-ahead hold set (dormant backend kept for the holds
  mechanism; the human path no longer writes it).

### The change ledger (see `docs/codoc-change-ledger.md`)

Every store `Event` carries `actor` (human / agent id / loop), `mode` (pen / suggest
/ auto), and `caused_by` (the directive / event / suggestion this change implements).
`caused_by` is what lets the IDE group a surfaced-back agent change under the doc edit
that triggered it (the `↳ from your edit` cascade cue), and what U5 reads to classify
a realization faithful vs divergent.

---

## How this maps to the implementation

Realized in the `Codoc Tree` webview (`vscode-codoc/src/webview/`), the default
editor for `tree.codoc`, plus the Python daemon:

- **One whole-doc editor** with headings as the tree (`tiptap/whole-doc-editor.ts`) —
  no mode toggle, no pen/pencil. The vendored engine + the codoc decorations (binding
  rail, threads/connections, activity shimmer, the "being realized" badge in
  `tiptap/hold-decorations.ts`, TOC/scroll-sync) are ProseMirror widgets /
  heading-anchored decorations.
- **Agent proposals → engine marks** (`state/agent-proposals.ts` materializes them;
  `tiptap/suggestion-decorations.ts` anchors the inline ✓/✗ + the add/move/retire
  widgets).
- **Single-writer host** (`providers/tree-editor.ts`): `settleDoc` / `editMove`
  (a pure `state/doc-move.ts` transform) / comments persist `tree.doc.json` +
  `edits.json`; `buildPayload` sources from the saved doc while it leads `tree.codoc`
  so a payload never reverts a just-settled edit.
- **Daemon** (`codoc/loop/`): `loop_b._pick_parsed` chooses `tree.doc.json` vs
  `tree.codoc`; `loop_a` classifies divergence + persists `resolution.json`;
  `watch.py` routes a `tree.doc.json` change to Loop B (guarded);
  `reconcile.safe_write_tree` yields to pending doc edits.
