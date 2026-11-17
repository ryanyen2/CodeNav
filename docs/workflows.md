# Workflows

What each party does, which ops it records, and what the person then sees. The state and
the ops are defined in [model.md](model.md). Who may write, the inbox, the machine gate
and plans are in [concurrency.md](concurrency.md).

## 1. The two loops

### Code to document: reflect

The daemon reflects every changed code file that an agent session is not holding
([concurrency.md](concurrency.md#4-a-submissions-path), the daemon's batch). `codoc sync`
runs one pass without a daemon. A pass, in `codoc/loops/reflect.py`:

| step | ops, all by `loop` | lands |
|---|---|---|
| Binding maintenance: a modified bound chunk, a removed one, a relocated one (same content moved, or a unique AST shape renamed). | `bind.refresh`, `bind.detach`, `bind.attach` | trunk |
| A live feature that once had a binding, has none now, has never had a `delete` plan, and is not held. | `plan.open` with `kind = delete` | plan |
| One LLM pass (`codoc/prompts/tree_update.txt`) over the added and modified chunks, with the ego-graph context of `loops/context.py`. Its answer is a list of tree edits, compiled by `loops/edits.py`. | below | below |
| Chunks the model left unplaced: each goes to the feature that owns most of its graph neighbours, else to a new node named after its file. | `bind.attach`; `block.add` for a new node | trunk; a plan for a new node |
| Kind blocks with a `lift` (a derived diagram, a `url`, a `pdf`) on features that are not held ([kinds.md](kinds.md)). | `block.set`, `ingest` | trunk |

How each tree edit lands:

| edit | lands |
|---|---|
| `attach`, `detach` | Trunk. Under the node's plan when the node is new in the same answer. |
| `add_node`, `move_node`, `retire_node` | A plan. `retire_node` is a `delete` plan. |
| `amend` | Through `ingest` and the machine gate: the trunk when every block is a small rewording and the feature is not held, otherwise one plan for the whole amend. |

When the pass runs because an agent session closed, every op it writes on a queued
feature carries `caused_by` the feature's latest hand-off. A pass caused by a hand edit
cites nothing.

### Document to code: realize

1. The person edits the document. Their words are `ink`: `unsent` until a hand-off
   covers them ([model.md](model.md#views)).
2. A hand-off records `feature.handoff`. Its `covers` defaults to the applier's frontier,
   so it includes the edits flushed in the same batch.
3. The applier writes every queued feature's `directive` into `.codoc/realize.md`.
4. A coding agent implements it: `/codoc:sync` in Claude Code, `codoc realize`
   (`--engine sdk` or `cli`), or `codoc watch --auto-realize`. On the hub, the worker
   opens a pull request (section 3.6).
5. The agent's hooks keep the agent epoch open in `.codoc/activity.json` while a turn
   runs. The daemon holds the changed files until the epoch closes, then reflects them
   with `caused_by`.
6. The daemon writes `feature.realized` as `loop` once any pass since the hand-off has
   applied a binding op citing it ([concurrency.md](concurrency.md#10-hand-off-and-realization)).
   When the build moved no bound symbol, the person runs `codoc done`.

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
| Delete or Backspace over a selection across paragraphs | editor | `text.delete` per block, `block.delete` per block wholly inside, `block.merge` of the two ends | A selection that touches a heading is refused with a message. |
| Tab, Shift-Tab on a heading | editor | `block.set(level)` on that heading | Only that heading changes level (I3). |
| Drag a block's handle | editor | `block.move` | A heading moves with its range. |
| Paste several paragraphs | editor | `text.insert` into the caret's block, then `block.add` and `text.insert` per further paragraph | The person is the author of all of it. |
| Paste inside one block | editor | `text.delete`, `text.insert`, `text.mark` per mark in the pasted text | |
| `@` and a symbol | editor | `text.delete` of `@query`, `text.insert` of the label, `text.mark(cite)` | An inline citation; clicking it opens the code. |
| Find and Replace | editor | `text.delete`, `text.insert` | |
| Comment on selected words | editor | `block.add(comment)`, `text.insert` of the body, `text.mark(comment)` | A card in the margin. |
| Reply, Resolve | editor | `block.add(comment)` with the same `thread`; `block.set(status)` on each message | |
| Build it on a comment | editor | `feature.handoff` with the thread's words as `asked` | The host runs `codoc realize` in a terminal. |
| Hand off (⌘S or the toolbar) | editor | `feature.handoff` per `edited` feature | Ink turns from unsent to sent. |
| `codoc handoff <feature> [--asked …] [--delete-code]` | CLI | `feature.handoff` | |
| Accept all, Reject all | editor | `plan.accept` or `plan.reject` per open plan; with Accept, `feature.handoff` for each feature a plan opened with `build` touches (the projection's `plans[].handoff`) | Accepted words become ordinary text with the plan's author; an accepted `codoc_propose` plan is queued. |
| `codoc accept <plan>` | CLI | `plan.accept`, and `feature.handoff` per feature by the same rule as the editor; `--build` hands off any plan's features, `--no-build` none; never for a delete plan | As above. |
| `codoc reject <plan>` | CLI | `plan.reject` | |
| Undo, redo (⌘Z, ⇧⌘Z, ⌘Y) | editor | The recorded inverses of the viewer's newest batch, newest first, as batch `u-<batch>`; redo undoes that | The log keeps both. |
| Delete feature | editor command `deleteFeature` (no control on the page calls it) | `block.delete` of the heading, `block.set(level)` per promoted child heading | The heading and its body are gone; History keeps them. |
| `codoc done <feature>` | CLI | `feature.realized` | The feature leaves the queue. |
| Switch language | editor, `codoc lang` | none; writes `doc_language` in `config.json` | New prose is written in that language. |

Two outcomes have no gesture: a heading merged into prose by a keystroke, and a heading
removed by a text edit. A feature goes only through `block.delete` or an accepted
`delete` plan.

## 3. Workflows

### 3.1 First run

`codoc init` indexes the code, runs the bootstrap (`loops/bootstrap.py`: a brief from the
project's prose, one call per source file, one organizing call) and installs the Claude
Code hooks, the MCP registration and the `/codoc:*` commands. The bootstrap's edits land
on the trunk as `loop`, in one batch: a first tree has nothing to be a proposal against.
Every chunk ends up bound.

### 3.2 Edit, hand off, review

The person edits prose; ink appears. They hand off. The agent builds; the daemon reflects
the session's changes and settles the hand-off. The feature's phase goes `edited`,
`queued`, `realizing`, `synced`. Afterwards `ground` shows, in the same paragraphs as the
person's ink, the words the loop added (green) and removed (red) since the person last
touched the feature.

### 3.3 The agent proposes first

`/codoc:plan` has the agent read `codoc_context` or `codoc_tree` and call
`codoc_propose` with tree edits. The additions and amendments become one plan, and each
removed feature becomes a delete plan of its own, so a deletion is accepted or rejected
on its own. A plan is tagged ops that apply at once and show as gray text, struck gray
for `suggest_del`. Whatever the person types inside a proposal is theirs and untagged.
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

`/codoc:ask` has the agent write a walkthrough (`codoc_walkthrough`): a reading order
over existing features, stored in `.codoc/ask.json`. It writes no op.

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
  The hand-off settles when the merged code is reflected.
- A pull request carries its author's session logs. `codoc validate-logs` checks them
  before a merge ([concurrency.md](concurrency.md#7-roles-and-authority)).

### 3.7 Branches and merges

The log is one file per session under `.codoc/log/`, tracked in git. A merge keeps both
branches' files and the store is rebuilt from the union
([concurrency.md](concurrency.md#11-git)).

### 3.8 The authoring language

`codoc lang <tag>` sets `doc_language` in `config.json`; prose written from then on is in
that language. `codoc translate` rewrites existing prose per feature through `ingest` with
`actor = translator:<lang>`: on the trunk when machines wrote the feature, as one plan when
a person wrote any of it or the feature is held. `ingest` refuses a block whose
translation drops a citation, link or bold a person placed. A feature with an open plan is
skipped, so a second run does not stack translations. The daemon must be stopped.

## 4. What the person sees, and where it comes from

| surface | reads |
|---|---|
| Ink, ground, plan words in the prose | `claims` and the runs and spans of each block in `projection.json` |
| Marker at each heading, tree rows | `phases` |
| History | `attribution` runs; `history.json` for the scrubber |
| Status line, toolbar chip | `status.json` |
| Comment cards | `comment` blocks of the projection, beside their anchor block |
| `tree.codoc` | `export`: every live block in the `rejected` read mode |

Each is a read of the published files. The editor keeps no model of its own.
