# codoc

codoc keeps a **document of features** beside your code and keeps the two in
step, in both directions, while you, a coding agent and codoc's own loop all
edit it. A feature is a heading and a few paragraphs of intent that cite the
code inline; the document is the surface where you read what a codebase does,
say what it should do, and review what an agent did to it.

Every word in the document carries who wrote it, so the editor can show your
unsent edits in blue, an agent's proposal in gray, and what the code changed
underneath in green and red, without ever diffing prose to guess.

## How it works

- **Code → document.** The daemon indexes your repository and, when code
  changes, keeps each feature's bindings current and asks one model call to
  repair prose that the change made false. Small repairs land as the loop's own
  words; anything larger, or anything on a feature you are editing, becomes a
  **plan** you accept or reject.
- **Document → code.** You edit the document like a document. When you hand a
  feature off (⌘S), your edits become a directive in `.codoc/realize.md`; a
  Claude Code session (`/codoc:sync`) or `codoc realize` implements it, and the
  daemon marks the request realized when the session closes and the bound code
  moved.
- **The agent in the loop.** Claude Code hooks tell the daemon when a session
  is open (code changes wait until it closes), and MCP tools let the agent read
  the tree, attach code, repair prose and propose plans, under the same rules as
  everyone else.

The substrate is an attributed edit calculus: an append-only op log, character
identity, recorded inverses, plans as tags. Read `docs/architecture.md` for the
map and `docs/substrate/01-calculus.md` for the design.

## Requirements

- The **codoc VS Code extension** (it provisions everything else).
- **Claude Code**, for the interactive workflow and, by default, as the engine
  for codoc's own model calls (no separate key). An OpenAI key is the fallback.

The extension installs Python 3.11+ in an isolated `uv` environment with
cocoindex + LanceDB (the incremental code index) and tree-sitter (Python and
TypeScript).

## Quick start

1. Install the **codoc** VS Code extension.
2. Open your repository and run **"codoc: Set up codoc"** (offered on first
   run, or from the status bar). Setup bootstraps `uv`, installs the core, wires
   the Claude Code plugin (hooks, MCP server, slash commands), runs `codoc init`
   to index the repository and propose the first tree, and starts the daemon
   for you. A trusted workspace is required.
3. Open the tree (**codoc: Open tree**). Read, edit, hand off.

### The CLI (no IDE, or scripting)

```bash
uv tool install git+https://github.com/ryanyen2/CodeNav.git     # or: pip install -e .  (Python 3.11–3.12)
export CODOC_PROVIDER=claude          # reuse your Claude Code login; or openai + OPENAI_API_KEY

cd my-repo
codoc init                            # index, bootstrap the tree, install the Claude Code plugin
codoc watch                           # the daemon: the only process that applies edits
```

```bash
codoc status                          # where things stand
codoc sync                            # one pass without the daemon
codoc handoff <feature> --asked "…"   # the CLI's ⌘S
codoc accept | reject <plan>          # verdicts
codoc done <feature>                  # mark a request realized when the build moved no bound symbol
codoc realize                         # implement the queue now, in the foreground
codoc history <feature>               # ops and per-word attribution
codoc lang [tag] | codoc translate    # the authoring language; convert an existing tree
codoc rebuild | codoc snapshot        # replay the logs; fold them
codoc import-v1                       # bring a v1 workspace into the log
```

## Editing the tree

The editor is a document. Type; press Enter for a new paragraph; type `## ` or
`/` at the start of a line for a heading; Tab and Shift-Tab change a heading's
level; drag the handle to move a feature with everything under it; `@` cites a
symbol the tree already knows; ⌘F finds and ⌘⌥F replaces.

Two signals in prose are instructions: **bold** is *Focus* (the agent is told
which words matter) and an external link is *Consult* (the agent reads it
first). A code citation is a mark over ordinary words, so a label is editable
and attributed like any other text.

What you see:

| colour | meaning |
|---|---|
| blue underline, pulsing | yours, not yet handed off |
| blue underline, steady | yours, handed off and being built |
| gray text | a plan awaiting your verdict (struck gray: it proposes removing these words) |
| green / red ground | what the code loop added / removed under you |
| History | who wrote each run of words |

Nothing forces a verdict. A plan you ignore stays gray; accepting it keeps the
agent as the author of its words; rejecting it removes exactly what it added.

## What lives in `.codoc/`

| file | role | git |
|---|---|---|
| `log/<session>.jsonl` | the truth: one append-only file per daemon session | tracked |
| `tree.codoc` | the Markdown export (`## Title {#b-id}`), for review and for people without codoc; derived, regenerate on conflict | tracked |
| `config.json` | the authoring language and other settings that travel with the repo | tracked |
| `projection.json`, `history.json`, `status.json`, `realize.md`, `activity.json` | derived and ephemeral | ignored |
| `inbox/` | client submissions and the daemon's acks | ignored |
| `cache/`, `lancedb/`, `cocoindex.db` | the code index and graph | ignored |

Two branches never write the same log file, so git merges them by adding
both; the daemon replays the union deterministically after a pull.

## Authoring language

The tree can be written in any language (`codoc lang zh-Hans`, or the toolbar
switcher). Prose is translated; identifiers, symbol paths and citations are
not. `codoc translate` converts an existing tree and refuses any block whose
translation would drop a citation or a person's emphasis.

## Development

```bash
pip install -e .
python -m pytest tests/                                            # Python (deterministic)
cd vscode-codoc && npx vitest run && npx tsc --noEmit && node esbuild.config.mjs
```

`docs/substrate/` holds the design notes and the adversarial reviews of each
stage; `docs/architecture.md` is the code map; `CONCEPTS.md` the glossary.
