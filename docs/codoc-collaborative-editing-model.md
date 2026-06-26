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

> **⚠ Coordination model superseded (2026-06).** The "Single-writer & persistence"
> section below described a **webview-authoritative** model: the webview owned
> `tree.doc.json` and the daemon derived `tree.codoc` from it, with the claim that
> one writer per file "removes the content-is-newer race." That race *recurred*
> live (deletions resurrected, one node duplicated into 3–5 features, "content is
> newer" save dialogs) because Loop B was diffing the daemon's own output back as
> input. The coordination model is now **inverted to store-authoritative**: the
> SQLite store is the single source of truth, both `tree.doc.json` and `tree.codoc`
> are daemon-written derived projections (`tree.codoc` is a read-only export), and
> the webview emits identity-keyed *commands* instead of persisting a doc. The
> human-facing model in this document (one surface, two directions, doc-wins holds,
> the change ledger) is unchanged; only the file-coordination mechanics inverted.
> See `docs/plans/2026-06-26-001-refactor-store-authoritative-coordination-plan.md`
> (origin brainstorm under `docs/brainstorms/`). The superseded mechanics are kept
> below struck-through for provenance.

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

## Store-authoritative coordination & persistence (the data model)

The single source of truth is the **SQLite store** (`.codoc/codoc.db`). The webview
is a *projection* of the store plus a *command emitter*; it persists no document.

- **Both `tree.doc.json` and `tree.codoc` are daemon-written derived projections.**
  The daemon is the sole writer of each (`loop_b.write_tree_doc` /
  `codoc_file.render.write_tree`); `tree.codoc` is a **read-only export**. The
  webview *consumes* the daemon-written `tree.doc.json`
  (`codoc_file/doc_render.build_doc_from_store`), which carries each feature's
  `localId` + per-feature `updated_at` HLC and re-emits the store's marks/comments.
- **Edits flow as identity-keyed commands, not a doc diff.** Editing actions emit
  `{id, kind, fid|localId, baseRev, payload}` (add/set_title/set_description/move/
  retire) on the `edits.json` `commands` channel; Loop B applies each via `apply_op`
  with an applied-command-id ledger (idempotent) and a `(normalized_title,
  parent_id)` dedup guard. Loop B no longer reads `tree.doc.json`/`tree.codoc` back
  as input — that inference (`doc_presence` / `doc-fids.json`) is **retired**: a
  deletion is now an explicit `retire` command, not an absence inferred from the doc.
- **A per-feature HLC version gate** (replacing the old `rev` / exact-text-equality
  `docAhead`) keeps a returning projection from clobbering a newer un-acked local
  edit on a *different* feature; an advance on feature B never reverts a pending edit
  on feature A.
- **Marks and comment threads live in the store** (`marks` / `comments` tables,
  `model/annotation.py`) so they survive a reload; the projection re-emits them onto
  the inline runs. Agent tracked-change marks still materialize host-side from the
  sidecar proposals for review; the baseline-aware serializer (insertions excluded,
  deletions kept) guarantees a marked doc renders back to the exact pre-proposal
  text, so old + new coexist without leaking into canonical state.
- **Migration.** A one-time idempotent heal (`codoc/loop/migrate.py`, run by
  `codoc migrate` and once on daemon startup) lifts pre-existing `tree.doc.json`
  comment threads into the store and converges re-minted duplicate features onto the
  binding-owner — so a diverged workspace converges instead of multiplying.

> **Superseded mechanics (webview-authoritative, pre-2026-06 — kept for provenance):**
> *~~The webview's authoritative artifact was `.codoc/tree.doc.json`; the host wrote
> only that file and never `tree.codoc`. `tree.codoc` was derived (daemon sole
> writer), claimed to remove the two-writer mtime race. Loop B read `tree.doc.json`
> via `doc_parse.py` to learn webview edits, falling back to `tree.codoc` text, and
> `safe_write_tree` yielded while a doc edit was pending. This is what broke: the
> daemon diffing its own output re-minted nodes and resurrected deletions.~~*

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
- **Projection consumer + command emitter** (`providers/tree-editor.ts`):
  `settleDoc` / `editMove` (a pure `state/doc-move.ts` transform) / delete handlers
  emit identity-keyed `commands` on `edits.json` (not a persisted doc); `buildPayload`
  sources only from the daemon-written `tree.doc.json` projection, gated by the
  per-feature version (U5) so a returning projection never reverts a newer pending
  edit. The host writes no document file.
- **Daemon** (`codoc/loop/`): Loop B drains the `commands` channel and applies via
  `apply_op` (ledger + dedup guard), then re-renders `tree.doc.json` **and**
  `tree.codoc` from the store (`write_tree_doc` + `write_tree`); `loop_a` classifies
  divergence + persists `resolution.json`; `watch.py` routes an `edits.json` change
  to Loop B; `migrate.py` self-heals diverged legacy workspaces on startup.
