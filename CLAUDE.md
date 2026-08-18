# CLAUDE.md

Guidance for Claude Code working in this repository. Deep internals (data model,
loop phases, bootstrap, storage, control-file schemas, env vars, the hub) live in
`docs/architecture.md`.

## Project Overview

**codoc** maintains a human-intent-level view of a codebase as a navigable
*feature tree*, synchronized to the underlying code. Each node is a **feature**: a
named unit of intent that binds to many code chunks across many files (and one
file's chunks may belong to several features). The tree is **first-class authored
intent** (not LLM-derived); code attribution (bindings) is a secondary index kept
fresh by the reflective pipeline.

The repo has the Python core (`codoc/`) and the VS Code extension (`vscode-codoc/`).
It was rewritten clean-slate in 2026-05 (see `docs/architecture.md` for what was
deleted vs kept).

## Commands

```bash
pip install -e .            # Python 3.11+ (pip or uv); project venv is .venv
pip install -e '.[serve]'   # + the deployed hub (fastapi/uvicorn/sse-starlette)

# Five core CLI commands
codoc init                  # index repo, propose initial tree, write .codoc/tree.codoc
codoc watch                 # the daemon: run both loops as you edit code / tree.codoc
codoc status                # feature count, pending proposals, recent activity
codoc sync                  # one-shot: apply tree edits (Loop B), then reflect code (Loop A)

# Plumbing (agents / no-IDE workflows)
codoc accept <e-id>         # CLI verdict path — mirrors the IDE Accept (then runs Loop B)
codoc reject <e-id>         # CLI verdict path — mirrors the IDE Reject
codoc history <feature>     # one feature's blame timeline (who/when/why, by id or title fragment)
codoc reflect               # recovery-grade state reconciliation (used by the Stop hook)
codoc propose <kind>        # author a plan proposal from the shell (humans/tests)
codoc install-hooks         # (re)install the CC hooks + MCP registration
codoc realize               # implement the realize queue NOW, foreground (SDK or CLI engine)
codoc migrate               # one-time idempotent workspace heal (migrate tree.doc.json comments into the store + converge duplicate features + track config.json + install /codoc:* commands that shipped after this workspace was wired); also runs on daemon startup
codoc lang [<bcp47>]        # show / set the language the TREE is authored in (en, zh-Hans, ja, …) — see "Authoring language" below
codoc translate             # rewrite an EXISTING tree's prose into that language (--dry-run, --limit N)
codoc serve                 # the deployed hub: serve the tree to remote users (docs/serve-deployment.md)

# watch flags: --dry (apply tree edits, don't queue realization), --no-realize
# (never queue this session), --auto-realize (unattended fallback when no session)

python3.11 -m pytest tests/   # Python tests
```

## The `tree.codoc` surface

The only human surface is `.codoc/tree.codoc`. You edit titles/descriptions
directly. Structural proposals render as an **in-place overlay** — ADD/MOVE as
ghost hunks at their destination, RETIRE/AMEND as decorations on the live node —
accepted/rejected with the IDE's inline **Accept / Reject** (which write verdicts
to `.codoc/inbox.json`; there is no accept/reject *syntax*). Feature ids
(`⟨f-id⟩`) stay on disk for stable identity but the IDE hides them. Code is cited
inline with markdown links: `[label](codoc:file.py#symbol)`.

Two markdown-native signals in descriptions feed Loop B directives:
- `**bold**` is **focus** — newly-bolded spans ride in as a `Focus:` line; an
  imperative bolded span queues a directive even when the prose reads descriptive.
- `[label](https://…)` external links become `Consult:` lines — the realizing
  agent WebFetches them before implementing. The editor underlines them so an
  author can see the link registered as an instruction.

**Steering comments** (`STEER FEATURE` directives) come from the IDE's inline-
comment surface, which writes them to `edits.json` (`drain_steers`). Typing a
`> …` line into a description does NOT create one: the webview stopped writing
`tree.codoc` in U6, and U7 retired the text-ingest path that used to read `> ` lines
out of it (see `loop_b` step 2.7), so a `> ` line is now ordinary prose.

**Reading the tree** (surfaces that change nothing):
- `/codoc:ask <question>` answers a question by drawing a numbered *walkthrough* — a
  reading path over features that already exist (the `codoc_walkthrough` MCP tool →
  ephemeral `.codoc/ask.json`; see `codoc/loop/ask.py`). It writes nothing to the
  store or the change ledger, so it is safe at any point in an edit. Prefer it to a
  chat paragraph when the tree already covers the answer; `codoc_walkthrough_read` /
  `codoc_walkthrough_clear` observe and dismiss.
- In the Codoc Tree editor, `Cmd+F` / `Cmd+Alt+F` search and replace across feature
  titles and descriptions (the raw `tree.codoc` is a read-only export, so this is the
  only place to search/rename the tree).

## Authoring language

The tree can be authored in any language; `codoc/doclang.py` owns that (NOT
`codoc/lang/`, which is *programming* languages — the two are orthogonal, a Python
repo can have a Mandarin tree). The setting lives in **`.codoc/config.json`**
(`{"doc_language": "zh-Hans"}`), the one non-export file in `.codoc/` that is
**tracked in git** — it has to travel with the repo, or a contributor's daemon
writes English prose into somebody's Chinese tree. `CODOC_DOC_LANGUAGE` overrides it
per-process; `codoc init --doc-language` sets it before bootstrap so the first tree
is already in the language.

Prose is translated; **addresses are not** — identifiers, symbol paths, and
`codoc:` link targets stay verbatim, and the code an agent writes keeps the
language its neighbours use. Four prompts carry the directive via a `{{doclang}}`
marker (expanded into the cached prefix; empty for English, so English prompts are
unchanged), and the MCP reads return a `doc_language` block because a coding agent
is the one writer with no prompt in front of it.

**The tree may be bilingual, and that is not a defect.** The setting says what codoc
*originates* prose in; what the tree contains is observed per node
(`doclang.detect_prose_language`). Originating → the workspace language. Editing
existing prose → the language that prose is already in, because an author who wrote
one node in English inside a Chinese tree meant to. Chinese prose carrying English
library and API terms is correct writing and nothing flags it. The sidecar and the
MCP feature rows carry a `lang` tag only for nodes that differ from the tree's
language; the webview stamps those on the DOM so the browser gets fonts and
line-breaking right per element, and the toolbar switcher (read-only on the hub)
writes `.codoc/config.json`. `codoc lang` reports what the tree actually contains,
not just what is set.

Switching the language does not retranslate anything, so a tree already built in
English is migrated with **`codoc translate`** (`loop/translate.py`): one LLM pass per
batch of features, rewriting titles and descriptions while copying every `codoc:`
citation, external link, and `**bold**` focus span through unchanged. It REFUSES a
node whose translation dropped one of those, or whose title would collide with a
sibling's, and it preserves each node's `feature_writers` role so a translated
human-authored node keeps the strict amend gate. Previous wording stays in the change
ledger (`codoc history <feature>`). Idempotent — selection is by detected language,
so an interrupted run is safe to re-run.

Setting it also changes what the lexical heuristics do, because every one of them
was written for a spaced Latin script — see the table in `docs/architecture.md`
("Doc language"). The short version: `norm_key` NFKC-folds titles so an IME can't
mint a duplicate node, `terms`/`tokens` segment per script instead of dropping every
non-ASCII character, and `clause_chars` ports the amend gate's 24-character
"preserved clause" into whatever a clause costs in the script at hand.

## Architecture

### Core idea — two loops

- **Loop A — code → codoc** (`loop/loop_a.py`): snapshot-diff the index →
  auto-apply safe ops (refresh/attach/detach/small-amend) → if anything needs
  judgment, ONE LLM pass (`agent/tree_update.py`) returns minimal node ops;
  structural ops (add/move/retire) are logged as pending proposals.
- **Loop B — codoc → code** (`loop/loop_b.py`): drain identity-keyed **commands**
  from `.codoc/edits.json` (add/set_title/set_description/move/retire — the webview
  emits them; an idempotency ledger guards crash-replay). The webview never writes
  `edits.json` directly (no shared lock across processes): it APPENDS ops to
  `edits.host.jsonl`, which the daemon merges into `edits.json` under the lock at each
  Loop B pass (`edits.merge_host_ops`). Plus proposal verdicts →
  apply via `apply_op` → for edits implying code change, build a directive and
  **queue it for the live session** in `.codoc/realize.md` (status `awaiting_impl`).
  Edits are NOT inferred from a text/doc diff (that path was retired). The session
  implements via `/codoc:sync`; the Stop-hook reflection / watch-daemon Loop A then
  closes the loop. No headless `claude -p`.

A single LLM pass with full change + whole-tree-title context (plus the
`UNIQUE(file, symbol_path)` binding constraint) is what prevents duplicate nodes —
no move/fracture/coalesce detectors, no post-hoc dedup gates. The five Loop-A
phases, the Loop-B drain order, bootstrap, and the realization triggers are
detailed in `docs/architecture.md`.

### Package layout (`codoc/`)

```
model/       # Pydantic: Feature, Binding, Event/NodeOp/NodeOpKind, HLC; ids.py;
             #   annotation.py (Mark, CommentThread — store-authoritative rich state)
store/       # db.py — Store over the SQLite tables (features/bindings/events +
             #   blocks/marks/comments + applied-command ledger) + 1 derived graph cache (WAL)
graph/       # code dependency graph (derived, rebuildable): extract.py, query.py
loop/        # the two loops + pieces: classify.py (decision table), phase.py (the
             #   single feature-phase projection — holds/drift/resolution are views),
             #   diff.py (compute_changeset), apply.py, loop_a.py / loop_b.py, edits.py
             #   (edits.json + realize.json), why.py (grounded rationale for
             #   descriptions: commit messages / realized directives / recorded
             #   rationale), inbox.py, status.py, fsio.py (atomic
             #   IO), subtree.py, bootstrap_hier.py, title_dedup.py (opt-in semantic
             #   title dedup), migrate.py (one-time store-authoritative workspace
             #   heal), sdk_realize.py / autorealize.py, watch.py
blocks/      # typed-media blocks + plugin codecs (agent-native notebook protocol):
             #   base.py (Capability LIFT/LOWER/CONSULT + BlockPlugin), registry.py,
             #   builtins.py, prose.py (plugin-zero), diagram.py (graph→mermaid lift +
             #   edge-delta lower), screenshot.py (transient + url/image consult media),
             #   refresh.py (Loop A lift pass), conformance.py (host parity harness)
agent/       # base.py, tree_update.py (the incremental LLM call), bootstrap_agent.py,
             # paths.py, hook.py / install_hooks.py, propose.py
mcp/         # codoc MCP server (FastMCP, stdio): tools.py + server.py (codoc-mcp script)
serve/       # the deployed hub (codoc serve) — see docs/architecture.md + serve-deployment.md
codoc_file/  # render.py (store → tree.codoc + sidecar), parse.py, diff.py (→ user ops)
lang/        # tree-sitter adapters: python.py + typescript.py  [KEPT]
             #   (PROGRAMMING languages — not doclang.py, see below)
doclang.py   # the AUTHORING language of the tree: profiles + the prompt directive,
             #   the .codoc/config.json setting, and the script-aware text helpers
             #   (norm_key / terms / tokens / clause_chars / char_budget) that the
             #   loop's lexical heuristics use instead of Latin-only regexes
core/        # tree_walk.py — tokens_hash/types_hash identity signals  [KEPT substrate]
pipelines/indexing/  # cocoindex_app.py, update_index(), read_all_chunks()  [KEPT]
prompts/     # tree_update.txt, realize.txt, bootstrap_file.txt, bootstrap_org.txt
             #   (each carries a {{doclang}} marker, expanded into the cached prefix)
cli/main.py  # Typer app; config.py — LLM config
```

The data model, NodeOp kinds, storage (the SQLite tables + the cocoindex/LanceDB
index), and the `.codoc/` control-file schemas are documented in
`docs/architecture.md`; the change ledger is `docs/codoc-change-ledger.md`.

### VS Code extension (`vscode-codoc/`)

The local extension is file-based: no HTTP server, no port. `WorkspaceState`
watches the `.codoc/*` control files, reparses on change, and drives the status
bar off `status.json`. The **`Codoc Tree` webview** (`providers/tree-editor.ts`)
is the default editor for `tree.codoc`; both it and the raw-text editor render
**every** proposal type inline (ADD/MOVE ghost rows, RETIRE strike, AMEND
word-level diff) with inline `✓`/`✗` Accept/Reject.

Store-authoritative editing model (2026-06 refactor — see
`docs/plans/2026-06-26-001-refactor-store-authoritative-coordination-plan.md`):
the **SQLite store is the single source of truth**. The webview is a pure
*projection* of the store (it consumes the daemon-written `tree.doc.json`) plus an
identity-keyed *command emitter* — editing actions emit `{id, kind, fid|localId,
baseRev, payload}` commands (add/set_title/set_description/move/retire) by APPENDING
to `edits.host.jsonl` (the webview holds no cross-process lock, so it never writes
`edits.json` directly); the daemon merges that append log into `edits.json` under the
lock and applies via `apply_op`; nothing is inferred from a doc diff. A settle cites
the `baselineId` of the projection it was computed from so the host diffs against that
exact baseline (not an in-flight one — no phantom retire). Both `tree.doc.json` AND
`tree.codoc` are daemon-written **derived artifacts** (`tree.codoc` is a read-only
export; the daemon is their sole writer).
A per-feature HLC version gate keeps a returning projection from clobbering a newer
local edit, and a **draft / hand-off** gate keeps code-implying edits
safe-by-default. Two provenance rules make concurrent editing safe: the EDITOR owns the
baseline citation (stamped at the end of an adopt, so a settle flushed by an arriving
projection still cites what its text was typed against), and a command's `base_text`
comes from this host's own unechoed writes (`state/known-store.ts`) else the cited
baseline — never from a projection the author may not have adopted. On the hub the
BROWSER emits the commands (`webview/command-emitter.ts`) through those same modules,
since it is the only party there that sees a projection. The editing-model details + key
source files are in `docs/architecture.md`.

### The deployed hub (`codoc serve`)

An optional Tier-1 web surface (`codoc/serve/`) that serves the tree to
GitHub-authorized **remote** users from the maintainer's own machine: contributors
*suggest* edits, a maintainer hands them off, and the hub realizes them on a git
worktree → code PR. It is a **separate process** that supervises the daemon and is
a file-channel client (reads `.codoc/*`, writes only the authored-edit + verdict/draft
channels of `edits.json`/`inbox.json` — never `tree.codoc`, and never `tree.doc.json`,
which is the daemon's own projection). See `docs/serve-deployment.md` (setup) and `docs/architecture.md`
(modules). README "Flow 3" is the user-facing overview.

## Tests

- `tests/` — Python unit + integration (`loop/`, `store/`, `graph/`, `codoc_file/`,
  `agent/`, `mcp/`, `cli/`, `serve/`). Ledger suites: `test_classify.py` (the
  decision table), `test_edits.py` (annotations + manifest + hold_set),
  `test_reader.py` (scoped reads).
- `tests/bdd/` — Given/When/Then code↔tree round-trip (deterministic via an injected
  `propose`) + a subprocess-isolated real-LLM E2E position report.
  `tests/loop/test_end_to_end.py` and `tests/bdd/test_e2e_userflows.py` are gated on
  `OPENAI_API_KEY`; everything else is deterministic.
- vscode-codoc: `npx vitest run` from `vscode-codoc/`; `npx tsc --noEmit` + esbuild
  must stay clean. The TS parser is parity-tested against `parse.py`.
- Fixtures: `tests/fixtures/` (self-contained Python + TypeScript samples) and `test/`
  (real-world corpora for bootstrap/E2E: `requests/`, `altair/`, `nanochat/`, …).

Accepted review residuals live in `docs/residual-review-findings/`; superseded
plans/brainstorms from prior feature work are archived under `docs/archive/`.
Documented solutions to past problems (bugs, best practices, workflow patterns),
organized by category with YAML frontmatter (`module`, `tags`, `problem_type`),
accrete in `docs/solutions/`; shared domain vocabulary lives in `CONCEPTS.md`.
