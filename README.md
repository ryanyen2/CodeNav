# codoc

codoc keeps a **document of features** beside your code and keeps the two in
step, in both directions, while you, a coding agent and codoc's own loop all
edit it. A feature is a heading followed by prose, lists, tables, diagrams and
code that cite the code inline; the document is where you read what a codebase
does, say what it should do, and review what an agent did to it.

Every word in the document records who wrote it, so the editor can show your
unsent edits in blue, an agent's plan in gray, and what the code changed
underneath in green and red, without diffing prose to guess.

## How it works

- **Code → document.** The daemon indexes your repository and, when code
  changes, keeps each feature's bindings current and asks one model call to
  repair prose that the change made false. Small repairs land as the loop's own
  words; anything larger, or anything on a feature you are editing, becomes a
  **plan** you accept or reject.
- **Document → code.** You edit the document like a document. When you hand a
  feature off (⌘S), your edits become a hand-off in `.codoc/realize.md`; a
  Claude Code session (`/codoc:realize`) or `codoc realize` builds it, and the
  daemon marks the request realized when the session closes and the bound code
  moved.
- **The agent in the loop.** Claude Code hooks tell the daemon when a session
  is open (code changes wait until it closes), and MCP tools let the agent read
  the tree, attach code, repair prose and propose plans, under the same rules as
  everyone else.

The document is an op log: every edit is an op that names characters by
identity, records its inverse, and carries its author. One daemon applies the
ops; every view is computed from the log. `docs/model.md` defines the document
and the ops, `docs/concurrency.md` who may write and how logs merge.

## Requirements

- **macOS or Linux.** The applier's file locks use `fcntl`, which Windows does
  not have.
- The **codoc VS Code extension** (it provisions everything else).
- **Claude Code**, for the interactive workflow and, by default, as the engine
  for codoc's own model calls (no separate key). An OpenAI or Anthropic key is
  the alternative.

The extension installs Python 3.11 or 3.12 in an isolated `uv` environment
with cocoindex and LanceDB (the incremental code index) and tree-sitter
(Python and TypeScript).

## Quick start

1. Install the **codoc** VS Code extension.
2. Open your repository and run **"codoc: Set up codoc"** (offered on first
   run, or from the status bar). Setup bootstraps `uv`, installs the core, wires
   the Claude Code plugin (hooks, MCP server, slash commands), runs `codoc init`
   to index the repository and propose the first tree, and starts the daemon.
   A trusted workspace is required.
3. Open the tree (**codoc: Open tree**). Read, edit, hand off.

### The CLI (no IDE, or scripting)

```bash
uv tool install git+https://github.com/ryanyen2/CodeNav.git     # or: pip install -e .  (Python 3.11–3.12)
export CODOC_PROVIDER=claude          # reuse your Claude Code login; or openai + OPENAI_API_KEY

cd my-repo
codoc init                            # index, bootstrap the tree, install the Claude Code plugin
codoc watch                           # the daemon: the only process that applies edits
```

The Claude Code hooks, the MCP server and the permission for its tools are set
up per machine and are not tracked (`.claude/settings.local.json` and Claude
Code's local MCP scope). After cloning a repository that already has a tree,
each person runs `codoc install-hooks` once.

```bash
codoc status                          # where things stand
codoc sync                            # one pass without the daemon
codoc handoff <feature> --asked "…"   # the CLI's ⌘S
codoc accept | reject <plan>          # verdicts
codoc done <feature>                  # mark a request realized when the build moved no bound symbol
codoc realize                         # build the queue now, in the foreground
codoc history <feature>               # ops and per-word attribution
codoc lang [tag] | codoc translate    # the authoring language; convert an existing tree
codoc rebuild | codoc snapshot        # replay the logs; fold them
codoc validate-logs                   # check the session logs a pull request adds
codoc serve                           # serve the tree to GitHub collaborators
```

## Editing the tree

The editor is a document. Type; press Enter for a new paragraph; type `## ` at
the start of a line, or `/`, for a heading; Tab and Shift-Tab change a
heading's level with everything under it, or a list item's depth; drag the
handle to move a feature with everything under it; `@` cites a symbol the tree
already knows; ⌘F finds and ⌘⌥F replaces.

Two signals in prose are instructions: **bold** is *Focus* (the agent is told
which words matter) and an external link is *Consult* (the agent reads it
first). A code citation is a mark over ordinary words, so a label is editable
and attributed like any other text.

What you see:

| colour | meaning |
|---|---|
| blue underline, pulsing | yours, not yet handed off |
| blue underline, steady | yours, handed off and not yet built |
| gray text | a plan awaiting your verdict (struck gray: it proposes removing these words) |
| green / red | what the code loop added / removed since you last edited the feature |
| History | who wrote each run of words, and a scrubber over the recent ops |

Nothing forces a verdict. A plan you ignore stays gray; accepting it keeps the
agent as the author of its words; rejecting it removes exactly what it added.

## What lives in `.codoc/`

| file | role | git |
|---|---|---|
| `log/<session>.jsonl` | the truth: one append-only file per session | tracked |
| `log/snapshot-*.jsonl`, `log/archive/` | a folded prefix of the log, and the files it folded | tracked |
| `tree.codoc` | the Markdown export (`## Title {#b-id}`), for review and for people without codoc; derived, regenerated on every publish | ignored |
| `config.json` | the authoring language and the roles table | tracked |
| `projection.json`, `history.json`, `status.json`, `realize.md`, `activity.json` | derived and local state | ignored |
| `inbox/` | client submissions and the daemon's acks | ignored |
| `cache/`, `lancedb/`, `cocoindex.db` | the code index and graph | ignored |

Two branches never write the same log file, so git merges them by adding
both; the daemon rebuilds from the union after a pull.

## Authoring language

The tree can be written in any language (`codoc lang zh-Hans`, or the toolbar
switcher). Prose is translated; identifiers, symbol paths and citations are
not. `codoc translate` converts an existing tree and refuses any block whose
translation would drop a citation, a link or a person's bold.

## Development

```bash
pip install -e '.[dev]'
python -m pytest tests/                                            # Python (deterministic)
cd vscode-codoc && npx vitest run && npx tsc --noEmit && node esbuild.config.mjs
```

`CLAUDE.md` is the map of the code. The specs are in `docs/`: `model.md`,
`concurrency.md`, `workflows.md`, `kinds.md`, `editor.md`, and
`architecture.md` for modules and environment variables. `CONCEPTS.md` is the
glossary.
