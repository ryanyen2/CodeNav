# Workflows

What each party does, which ops it records, and what the person then sees. The state and
the ops are defined in [model.md](model.md). Who may write, the inbox, the machine gate
and plans are in [concurrency.md](concurrency.md).

## 1. The two loops

### Code to document: reflect

The daemon reflects every changed code file that an agent session is not holding
([concurrency.md](concurrency.md#4-a-submissions-path), the daemon's batch). A code file
is one the index reads: a language adapter parses it (`.py`, `.ts`, `.tsx`, `.mts`,
`.cts`), it is not a `.d.ts` or `.min.ts`, it is under 1.5 MB, and no exclude covers it.
The excludes are:

- `node_modules`, `bower_components`, `__pycache__`, `site-packages`, `venv`, `.venv`,
  `virtualenv`, `.codoc` and `.next`, at any depth;
- `env`, `build`, `out`, `dist`, `target`, `coverage`, `vendor`, `third_party` and every
  dot-directory, at the repository root only, since a package may be named `env` or
  `build`;
- the root `.gitignore`;
- `index_exclude` in `.codoc/config.json`, a list in `.gitignore` syntax. When it is
  absent the list is tests, docs and examples (`test/`, `tests/`, `__tests__/`,
  `*.test.*`, `*.spec.*`, `test_*.py`, `*_test.py`, `conftest.py`, `docs/`,
  `examples/`): they are code, but the tree describes what the product does, and every
  indexed file costs a bootstrap call. `"index_exclude": []` indexes them.

`codoc sync` runs one pass without a daemon. A pass, in `codoc/loops/reflect.py`:

| step | ops, all by `loop` | lands |
|---|---|---|
| Binding maintenance: a changed bound chunk, a removed one, a relocated one (same content moved, or a unique AST shape renamed). A changed bound chunk is refreshed, and judged in the LLM pass below, unless `loop` set the hashes it has now (`Binding.set_by`): then the loop described that code already, as after switching back to a branch. An agent's or a person's binding is judged even when its hashes match. | `bind.refresh`, `bind.detach`, `bind.attach` | trunk |
| A live feature that once had a binding, has none now, has never had a `delete` plan, and is not held. | `plan.open` with `kind = delete` | plan |
| One LLM pass (`codoc/prompts/tree_update.txt`) over the unbound added chunks and the bound chunks to judge, with the ego-graph context of `loops/context.py`. It names features and blocks by short aliases (`f12`, `f12.3`), which are mapped back to ids; an alias that names nothing is refused. Its answer is a list of tree edits, compiled by `loops/edits.py`. | below | below |
| Chunks the model left unplaced: each goes to the feature that owns most of its graph neighbours, else to a new node named after its file. | `bind.attach`; `block.add` for a new node | trunk; a plan for a new node |
| Kind blocks with a `lift` (a derived diagram, a `url`, a `pdf`) on features that are not held ([kinds.md](kinds.md)). | `block.set`, `ingest` | trunk |

How each tree edit lands:

| edit | lands |
|---|---|
| `attach`, `detach` | Trunk. Under the node's plan when the node is new in the same answer. |
| `add_node`, `move_node`, `delete_node` | A plan. `delete_node` is a `delete` plan. |
| `amend` | Through `ingest` and the machine gate: the trunk when every block is a small rewording and the feature is not held, otherwise one plan for the whole amend. The model reads each feature in the `loop` mode: with the loop's own open plans taken, and every open plan that answers a hand-off (one of its ops cites it, and it is not a `codoc_propose` build plan, which comes before the code), such as the plan an agent's `codoc_reflect` opened while it built. So it proposes only what those plans leave out, and nothing when they say it all: one change gets one proposal. When the loop has an open plan on the feature, the amend's plan is that one, and a block holding its words is rewritten under it; the loop never writes into another actor's plan (`plan_ingest` refuses a block holding its words). A new block follows the block the entry before it names, else ends the description. |

When the pass runs because an agent session closed, every op it writes on a queued
feature carries `caused_by` the feature's latest hand-off. When it runs over commits whose
`Codoc-Handoff` trailer names a hand-off and whose `Codoc-Base` trailer names the branch
`HEAD` is on, the ops on that hand-off's feature carry it. A
pass caused by a hand edit cites nothing.

### Document to code: realize

1. The person edits the document. Their words are `ink`: `unsent` until a hand-off
   covers them ([model.md](model.md#views)).
2. A hand-off records `feature.handoff`. Its `covers` defaults to the applier's frontier,
   so it includes the edits flushed in the same batch.
3. The applier writes every queued hand-off into `.codoc/realize.md`, as `loops/realize.py`'s
   `directive` renders it.
4. A coding agent builds it: `/codoc:realize` in Claude Code, `codoc realize`
   (`--engine sdk` or `cli`), or `codoc watch --auto-realize`. On the hub, the worker
   opens a pull request (section 3.6). `codoc realize` runs the agent with
   `--permission-mode acceptEdits`: it may edit files but not run a shell command
   nobody allowed, so it cannot run the tests unless the person allows the test
   command in `.claude/settings.local.json`, for example
   `{"permissions": {"allow": ["Bash(pytest:*)"]}}`. The agent says when it could not
   run them. At the end `codoc realize` prints the run's cost and time.
   `codoc watch --auto-realize` starts a pass only for a hand-off no finished pass was
   started for: a pass that ended with its hand-off still queued (one the agent could not
   satisfy, or a build that moved no bound symbol) is not run again for it. After a pass
   that failed (no login, a rate limit) it waits a minute from when that pass ended before
   the next, doubling with each failure in a row, up to an hour. A failed pass counts
   against each hand-off it was started for; a hand-off that three passes in a row failed on
   is left until the daemon restarts, and the daemon says so once, since a pass that fails
   after spending spends again. A hand-off queued later gets its own three passes. A
   `codoc snapshot` records each hand-off again under a new id, so one that was given up on
   is tried again after a snapshot. The daemon runs an empty batch after 30 seconds with no
   file event, so the retry and the close of a stale agent epoch do not wait for an edit.
5. The build cites the hand-off in binding ops, in any of three ways. The agent's
   `codoc_reflect` attaches the symbols it wrote or changed, with `caused_by` the
   hand-off's `d:` id; any agent that runs the MCP server can do this, with or without
   a daemon. The attach is read off the code on disk: a symbol the files do not define
   is refused, a symbol the feature owns is refreshed only when the build changed its
   code, so an attach is evidence only of code the build left there
   ([concurrency.md](concurrency.md#10-hand-off-and-realization)). Claude Code's hooks also keep the agent
   epoch open in `.codoc/activity.json` while a turn runs, and the daemon reflects the
   deferred files with `caused_by` when the epoch closes. A commit whose `Codoc-Handoff`
   trailer names the hand-off and whose `Codoc-Base` trailer names a branch (the hub's
   pull requests carry both, the base being the branch the pull request targets) is
   reflected with `caused_by` when `HEAD` moves over it while `HEAD` is on that branch:
   a merge, a pull with or without rebase, or a checkout of the base. A checkout or a
   pull of the pull request's own branch, or a detached `HEAD`, counts nothing.
6. The applier writes `feature.realized` as `loop` once a binding op citing the latest
   hand-off has landed, whoever wrote it (under a plan, once accepted), and no agent epoch
   is open. The op may bind any feature: the request's own, a new node built under it,
   or the feature that owns a shared symbol the request changed. A Claude Code turn
   settles when it ends, in the daemon's batch that sees the epoch close or, with no
   daemon, in a process the Stop hook starts
   ([concurrency.md](concurrency.md#1-processes)). When the build moved
   no bound symbol, or the code was written by hand, the person runs `codoc done`.

No op touches a source file (I7). A hand-off is the only gesture that turns words into a
request.

## 2. Gestures

One gesture is one batch, and a batch is the undo unit. Typing is flushed as one batch
350 ms after the last keystroke.

| gesture | where | ops | what the person sees |
|---|---|---|---|
| Type in a heading or paragraph | editor | `text.insert`, `text.delete` | Blue ink, unsent. |
| Enter | editor | `block.split` | Two blocks. Splitting a heading leaves a heading and a paragraph. |
| Enter over a selection inside one block | editor | `text.delete`, then `block.split` (two batches) | |
| `#`..`######` and a space at the start of a block | editor | `text.delete` of the hashes, `block.set(kind = heading, level)` | A new feature. The blocks after it, up to the next heading, are its body. |
| `/` menu: Heading 1..3 or Paragraph | editor | `block.set(kind)`, after a `text.delete` of the typed `/query` | A paragraph made from a heading joins the previous feature's body. |
| Backspace at the start, or Delete at the end, of a paragraph next to a paragraph | editor | `block.merge` | One paragraph. Each word keeps its author. |
| Backspace or Delete at a heading boundary | editor | none | The caret moves across the boundary. |
| Delete or Backspace over a selection across paragraphs | editor | `text.delete` per block, one `block.delete` naming every block wholly inside, `block.merge` of the two ends | A selection that touches a heading is refused with a message. |
| Tab, Shift-Tab on a heading | editor | `block.move` of the heading to where it is, with `level_delta` ±1 | Every heading under it changes level with it. |
| Tab, Shift-Tab on a list item | editor | `block.set(level)` on that item | Only that item changes depth. |
| Drag a block's handle | editor | `block.move` | A heading moves with its range. |
| Paste several paragraphs | editor | `text.insert` into the caret's block, then `block.add` and `text.insert` per further paragraph | The person is the author of all of it. |
| Paste inside one block | editor | `text.delete`, `text.insert`, `text.mark` per mark in the pasted text | |
| `@` and a symbol | editor | `text.delete` of `@query`, `text.insert` of the label, `text.mark(cite)` | An inline citation; clicking it opens the code. |
| Find and Replace | editor | `text.delete`, `text.insert` | |
| Comment on selected words | editor | `block.add(comment)`, `text.insert` of the body, `text.mark(comment)` | A card in the margin. |
| Reply, Resolve | editor | `block.add(comment)` with the same `thread`; `block.set(status)` on each message | |
| Build it on a comment | editor | `feature.handoff` with the thread's words as `asked` | The host runs `codoc realize` in a terminal. |
| Hand off (⌘S or the toolbar) | editor | `feature.handoff` per `edited` feature the projection's `editors` lists the viewer in | Ink turns from unsent to sent. A feature only a teammate edited stays edited, drawn as a ring, and goes out with their ⌘S. |
| `codoc handoff <feature> [--asked …] [--delete-code]` | CLI | `feature.handoff` | |
| Accept all, Reject all | editor | `plan.accept` or `plan.reject` per open plan; with Accept, `feature.handoff` for each feature a plan opened with `build` touches (the projection's `plans[].hands_off`) | Accepted words become ordinary text with the plan's author; an accepted `codoc_propose` plan is queued. |
| `codoc accept <plan>` | CLI | `plan.accept`, and `feature.handoff` per feature by the same rule as the editor; `--build` hands off any plan's features, `--no-build` none; never for a delete plan | As above. |
| `codoc reject <plan>` | CLI | `plan.reject` | |
| Undo, redo (⌘Z, ⇧⌘Z, ⌘Y) | editor | The recorded inverses of the viewer's newest batch, newest first, as batch `u-<batch>`; redo undoes that | The log keeps both. |
| Delete feature | editor, the `/` menu in a heading | `block.delete` of the heading, `block.set(level)` per promoted child heading | The heading and its body are gone; History keeps them. |
| `codoc done <feature>` | CLI | `feature.realized` | The feature leaves the queue. |
| Switch language | editor (the VS Code host runs `codoc lang`; the hub, for a maintainer, writes the same normalized tag), `codoc lang` | none; writes `doc_language` in `config.json` | New features are written in that language. An amend, a new block included, keeps the language of the description it changes; `codoc translate` converts existing prose. |

Two outcomes have no gesture: a heading merged into prose by a keystroke, and a heading
removed by a text edit. A feature goes only through `block.delete` or an accepted
`delete` plan.

## 3. Workflows

### 3.1 First run

`codoc init` indexes the code, runs the bootstrap (`loops/bootstrap.py`: a brief from the
project's prose, one call per source file, one organizing call) and installs the Claude
Code hooks, the MCP registration and the `/codoc:*` commands. The bootstrap's edits land
on the trunk as `loop`, in one batch: a first tree has nothing to be a plan against.
Every chunk ends up bound, to the most specific node that claims it. The organizing call
names features by short aliases (`f3`), and `codoc init` lists every top-level feature it
placed under no theme.

### 3.2 Edit, hand off, review

The person edits prose; ink appears. They hand off. The agent builds, and the hand-off
settles when a binding op citing it lands. The feature's phase goes `edited`,
`queued`, `realizing`, `synced`. Afterwards `ground` shows, in the same paragraphs as the
person's ink, the words the loop added (green) and removed (red) since the person last
touched the feature.

### 3.3 The agent proposes first

`/codoc:plan` has the agent read `codoc_context` or `codoc_tree` and call
`codoc_propose` with tree edits. The additions and amendments become one plan, and each
removed feature becomes a delete plan of its own, so a deletion is accepted or rejected
on its own. A plan is tagged ops that apply at once and show as gray text, struck gray
for `suggest_del`. Whatever the person types inside a plan's words is theirs and untagged.
Accept or reject is a verdict ([concurrency.md](concurrency.md#9-plans-and-verdicts)).
The editor's Accept sends the verdict and, for a plan `codoc_propose` opened, the
hand-off; `codoc accept` does the same. Accepting a plan from `codoc_reflect` or the
loop hands nothing off: it describes code that already exists.

### 3.4 Reviewing what happened

The History toggle draws each run of words in its author's colour (`attribution`) and
opens a scrubber over the last 200 ops (`history.json`). Each position shows the page as
it read after that op, with the op's change marked by a word diff. A change's `caused_by`
names the hand-off it answers.

### 3.5 Asking

`/codoc:ask` has the agent answer a question in chat: one or two sentences, then a
numbered reading path over existing features, each with one line on why it matters to the
question. It reads with `codoc_tree` and `codoc_context` and writes nothing.

### 3.6 The hub

`codoc serve` serves the same editor to people signed in with GitHub
([serve-deployment.md](serve-deployment.md)).

- A visitor's role comes from their collaborator permission: `admin`, `write` or
  `maintain` is `maintainer`; `read` or `triage` is `suggest`; no permission is `none`.
  The hub records it in `config.json` under `human:gh:<login>`.
- Their submissions go to `inbox/hub-<login>.jsonl`. A suggester's untagged edit becomes a
  plan of theirs in the drain; their comments stay untagged.
- With GitHub configured, the worker builds each hand-off that has no realization and no
  pull request yet, on its own worktree, with a sandboxed agent, and opens a pull request.
  Its commit names the hand-off in a `Codoc-Handoff` trailer and the branch the pull
  request targets in a `Codoc-Base` trailer, so the hand-off settles when a daemon on
  that branch reflects the merged commit.
- A pull request carries its author's session logs. `codoc validate-logs` checks them
  before a merge ([concurrency.md](concurrency.md#7-roles-and-authority)).

### 3.7 Branches and merges

The log is one file per session under `.codoc/log/`, tracked in git. A merge keeps both
branches' files and the store is rebuilt from the union
([concurrency.md](concurrency.md#11-git)).

### 3.8 The authoring language

`codoc lang <tag>` sets `doc_language` in `config.json`. From then on the loop writes a new
feature's title and description, and its rationales, in that language. An amend keeps the
language of the description it changes, a block it adds included, so the loop does not
leave a description half in one language (`doclang.prompt_directive` tells the model so).
`codoc translate` rewrites existing prose as `translator:<lang>`, one `Workspace.amend`
per feature: on the trunk when machines wrote the feature, as one plan when a person wrote
any of it, the feature has pending intent, or the translation would drop a citation, link
or bold a person placed (the machine gate,
[concurrency.md](concurrency.md#the-machine-gate)). A feature with an open plan is
skipped, so a second run does not stack translations. The daemon must be stopped.

## 4. What the person sees, and where it comes from

| surface | reads |
|---|---|
| Ink, plan words, cuts and mark toggles, ground | `claims` in `projection.json` |
| Marker at each heading, tree rows | `phases`; `realizing` comes from the lease in `status.json`, applied by the daemon |
| History | `attribution` runs; `history.json` for the scrubber |
| Status line, toolbar chip | `status.json` |
| Comment cards | `comment` blocks of the projection, beside their anchor block |
| `tree.codoc` | `export`: every live block in the `rejected` read mode |

Each is a read of the published files. The editor keeps no model of its own.
