# codoc for VS Code

The editor for a codoc tree: a document of what a codebase is for, kept in step
with the code.

- A heading is a feature. The blocks under it are its description: paragraphs,
  bullet and numbered lists, tables, diagrams, code snippets.
- Each feature is bound to the code symbols it owns.
- You, a coding agent and codoc's own loop edit the document at the same time.
  The page shows who wrote each word.

The document is the op log in `.codoc/log/`. A Python daemon (`codoc watch`) is
the only process that applies ops; this extension records what you do as ops,
sends them to the daemon, and draws the page from what the daemon publishes.
How that works is in [docs/editor.md](../docs/editor.md).

## Set up

Run **codoc: Set up codoc** (the status bar item, the walkthrough, or the command
palette). It needs a trusted workspace, because it installs and runs code.

1. Installs `uv`, then the codoc Python core into its own environment.
2. Picks the model provider: your Claude Code login when there is one, otherwise
   it asks for an API key and writes it to the repo's `.env`.
3. Runs `codoc init`: indexes the code, writes the first tree, and installs the
   Claude Code hooks, `/codoc:*` commands and MCP server into the repo.
4. Starts the daemon and stops it when the window closes.

Prefer the terminal: `pip install -e .` in the codoc repo, then `codoc init` in
yours.

## Reading the page

| colour | means |
|---|---|
| blue | words you wrote that the code has not caught up with (pulsing until handed off) |
| gray | an open proposal from an agent, the loop or a contributor; struck gray is a proposed deletion |
| green, red | what the code added or removed under the description since you last looked |

A marker beside each heading gives the feature's phase: edited, realizing,
queued, planned (queued with no code yet), proposed, drifted (the code changed and
the description has not been looked at since), or in step. **History** shows who wrote each word
and a scrubber over the recent past.

## Editing

| you do | it becomes |
|---|---|
| type, delete, Enter, Backspace | ops on the characters you touched |
| `# ` … `###### ` | a heading (a feature) |
| `- `, `* `, `1. ` | a list item; Tab and Shift-Tab change its level |
| ```` ``` ````, ```` ```mermaid ````, a `\|` row, then Enter | a code block, a diagram, a table |
| Tab, Shift-Tab on a heading | the feature and everything under it move a level |
| Mod-B, Mod-E, Mod-K | bold, code, link |
| paste Markdown | the blocks and marks it describes |
| `@` | cite a bound symbol |
| `/` | change a block's kind; in a heading, Delete feature |
| ⌘Z, ⇧⌘Z | undo, redo your own last change |

**Bold** marks what an agent must get right. A web link tells the agent to read
that page.

## Getting code built

| control | does |
|---|---|
| ⌘S, Hand off | sends your edited features to the coding agent's queue (`.codoc/realize.md`) |
| Build now, **codoc: Implement queued changes now** | runs `codoc realize` in a terminal |
| Accept all, Reject all | answers the open proposals; accepting a coding agent's proposal also queues it |
| a comment on selected words | a request about those words; Build it on the card hands it off |

In Claude Code, `/codoc:sync` builds the queue and `/codoc:plan <task>` proposes a
change as tree edits before any code is written.

## Commands

| command | does |
|---|---|
| codoc: Set up codoc | the setup above |
| codoc: Open | opens the tree |
| codoc: Start the daemon | starts `codoc watch` for this repo |
| codoc: Sync | one daemon pass: apply pending edits, reflect changed code |
| codoc: Implement queued changes now | `codoc realize` in a terminal |
| codoc: Find in Tree, Replace in Tree | ⌘F, ⌘⌥F in the tree |
| codoc: Share … | serve the tree to other people from this machine ([docs/serve-deployment.md](../docs/serve-deployment.md)) |

Setting: `codoc.serverPath`, a `codoc` executable to use instead of the one setup
installs (for working on the Python side).

## Development

```bash
npm install
npx tsc --noEmit && npx vitest run && node esbuild.config.mjs
```

Press F5 in VS Code to start an Extension Development Host. `src/host/` runs in
the extension host, `src/web/` in the page (the webview and the hub's browser),
and `src/wire.ts` is the message protocol between them.
