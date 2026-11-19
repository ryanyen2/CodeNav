# Block kinds

A block kind's declaration says which content fields of a new block are attributed `Text`,
what the other fields hold, and how the block reads to the loop and to the agent. The
calculus knows the heading and prose kinds and the `source` field of the source kinds.
Every source and plugin kind is also a declaration in `codoc/kinds/`. The kinds and their
fields are listed in [model.md](model.md#block-kinds).

A plugin block's layout (its Text fields, whether it is in the reading flow, whether the
export writes it) is data recorded on the block by its `block.add`; its codecs are code.
A clone that lacks the declaration still edits, exports and shows the block the same way,
from the layout. Only `lift`, `lower`, `consult` and the schema check need the
declaration.

## 1. A declaration

```python
Kind(
    name: str,
    layout: Layout,                    # calculus.doc.Layout(text, flow=True, export=True)
    schema: dict,                      # field -> {type, enum?, required?}
    lift:    (LiftContext) -> dict | None,             # code -> content
    lower:   (old, new, LowerContext) -> str | None,   # a change to the block -> a directive line
    consult: (content) -> str,         # what the realizing agent reads
    lifted:  tuple[str, ...],          # fields only `lift` writes
)
```

`layout.text` names the attributed Text fields; every other field is JSON. A comment has
`flow=False` (it hangs off words, not between them) and `export=False` (`tree.codoc`
leaves it out).

- `register(kind)` adds it to the registry and gives the calculus its layout. A writer
  building a `block.add` (`ingest.block_bodies`) puts that layout on it, and the applier
  uses it for an add that carries none. A prose kind cannot be registered. A source
  kind's layout is the calculus's own and registering it does not change it.
- A block keeps the layout it was added with. A new version of a kind cannot give
  existing blocks a new Text field; a field it adds is a JSON field. `layout.text` may
  name fields existing blocks lack, and a write to one is then a `block.set`.
- No field may be named `$layout`: the `codoc:` fence carries the layout under that key.
- `schema` types are `string`, `number`, `boolean`, `object`, `array`. `Kind.validate`
  refuses an unknown field, a wrong type and a value outside `enum`. A partial write skips
  `required`. Text fields arrive as Markdown strings.
- A codec sees content through `kinds.plain`: each Text field as a string in the
  `rejected` read mode, raw for a source field and inline Markdown otherwise.
- `lifted` names the fields only the loop's `lift` writes: what it fetched or extracted.
  `inbox.client_payload` drops them from a client's `block.add` and refuses a client's
  `block.set` of one, so a url block's excerpt is always what the URL served.
- A kind never produces ops, actors or plans.

| codec | input | output | run by |
|---|---|---|---|
| `lift` | the store, the block, its feature, the feature's bindings, the code graph, the `.codoc` path | the fields that changed, or None | the loop, after each reflect pass |
| `lower` | the content as the last realization read it, the content now, the feature and its bindings | one directive line, or None | the directive, when `realize.md` is rendered |
| `consult` | the content now | text for the agent | the directive, for every in-flow block of the feature |

## 2. Text fields

A field named in the block's layout is a `Text`. It gets character identity, per-word
attribution, marks, plan tags, comments anchored in it, `ingest`, translation and History,
with no code in the kind. A mermaid source edited by a person and redrawn by the loop
shows ink and ground as a paragraph does. Any other field is replaced whole by
`block.set`, and its author is that op's actor.

## 3. The shipped kinds

| kind | Text fields | `lift` | `lower` | `consult` |
|---|---|---|---|---|
| `table` | `source` | | | |
| `code` | `source` | | | |
| `diagram` | `source` | When `derived` is true: a `flowchart TB` of the bound symbols' outgoing edges in the code graph. | When `derived` is true: one line per edge the author added or removed. A change that is not an edge edit asks the agent to check with the author. | |
| `url` | `caption` | Fetches the page through `fetch_guard.safe_get` once per URL; on failure sets `status: unreachable` and retries after 300 s. Extracts the title and an excerpt (2,000 characters) when `trafilatura` is installed. | | The excerpt, or the URL. |
| `pdf` | `caption` | Extracts text from a file under `.codoc/media/` when `pypdf` is installed. | | The excerpt, or the file path. |
| `image` | `alt`, `caption` | | | The alt text, caption and file. |
| `screenshot` | `caption` | | | The file and caption. |
| `comment` | `body` | | While the thread is open (its first message says so) and the body is not empty: the anchor text and the body; with `scope: both`, also a request to amend the description through `codoc_reflect`. | |

`table` and `code` are part of the feature's description, so the agent reads them there.
The `url` and `pdf` extractors are in the optional `media` extra.

## 4. How content becomes ops

Content written to a kind block goes through `codoc/kinds/write.py`
([model.md](model.md#6-content-as-ops), Kind blocks):

- `plan_write(block, content)` is pure. It validates `content` against the schema and
  returns one `block.set` body per changed non-Text field and one ingest request
  `(field, base_text, new_md)` per Text field named, Text by the block's layout. Fields not named are left alone. A
  dead or missing block, an unknown kind or a schema error is a refusal.
- A new kind block is a `block.add` built with `ingest.block_bodies`, its Text fields
  in the same batch. `mcp.tools.write_block` runs both against the workspace, with the
  agent's actor, plan and `caused_by`.

| writer | how | actor | lands |
|---|---|---|---|
| A coding agent | `codoc_write_block(content, bid)`, or `kind` and `feature_id` to add a block at the end of the feature's body. It runs `plan_write` against a read-only replay and submits through the inbox. | `agent:claude-code` | The trunk, or the agent's plan when it passes `plan`. An ingest goes through the machine gate. |
| The loop | `kinds.lift.lift_pass`: every live block whose kind has a `lift`, on a feature that is not held, that no open plan added (`unvetted_block`). A field an open plan set is read as the owner's value (`derive.rejected_content`), so a url a suggester proposed is not fetched. Only the changed fields are written. | `loop` | The trunk. The ingest is marked `derived`, which skips the machine gate. |
| The editor | Comment cards write comment blocks directly as ops (`block.add`, `text.insert`, `text.mark(comment)`, `block.set(status)`). | the person | The trunk. |

A tree edit (`add_node`, `amend`) carries prose and source blocks only. A plugin kind block
in a model's description is dropped.

## 5. Kinds in the directive

For each block of the feature's body (`loops/realize.py`, `_kind_lines`) except one an
open plan added (`derive.unvetted_block`), with its content in the `rejected` mode
(`kinds.plain`). A JSON field carries no plan tag, so one an open plan set reads as its
value from before the plans, the owner's (`derive.rejected_content`, by the rule in
[model.md](model.md#read-modes)):

- An in-flow block adds its `consult` line.
- A block whose kind has a `lower` adds what `lower` makes of `(old, new)`. `old` is the
  content as its author last had it realized (`derive.realized_content`): Text fields
  without the characters the author minted or killed since `realized`, and JSON fields as
  they were before the author's first `block.set` since (`Block.prior`). The author's
  changes are a person's own and those of a plan they adopted (`Plan.adopted`); what the
  loop lifted from the code is already in the code.
- A comment is out of the flow, so it speaks only through `lower`. Its `lower` does not
  compare `old` and `new`: an open thread is in every directive of its feature until it is
  resolved. Each message's `status`, `scope` and `anchor_text` are its thread's (section 6).
  No other line of the directive reads a comment: a link in one is not a `Consult:` line,
  and its words are not `added:`.
- A client's words that a codec or the directive quotes inside one of its lines (a
  comment's body and `anchor_text`, a title, a link's label, a caption, a url) are
  `derive.one_line`'d: every run of whitespace, line breaks included, is one space. The
  directive is read line by line, so a line break in them would write a `Consult:` or an
  `Author asked:` of their own. A comment's body is collapsed rather than kept on
  prefixed lines, because its `Comment on` line is what voices it as a comment.
- An in-flow block of a kind this process has no declaration for adds `<kind>: <words>`,
  its Text fields in the `rejected` mode (`derive.kind_words`).

The reflect context (`loops/context.py`, `feature_entry`) lists every in-flow plugin block
the same way, as `kind_blocks: ["<kind>: <words>"]`. They are read, not amended: an amend
names only prose and source blocks.

## 6. Comments

A thread is one `comment` block per message, all with the same `thread` (the first
message's block id), each with `anchor` (the block the thread is on), `anchor_text`,
`scope` (`code` or `both`) and `status` (`open` or `resolved`). The words it discusses
carry a `comment` mark whose `block` attr names the first message. The mark is removed
whole and does not grow (I2). A comment is not exported to `tree.codoc` and is left out of
`ink`, `ground`, `phase` and drift. A client's `block.add` of a comment takes the comment
kind's layout whatever `layout` it sends (`inbox.client_payload`): a suggester writes a
comment untagged, and a layout of their own would put their words into the prose.

A block's thread is its `thread` when that is a non-empty string, else its own id. A
thread is open or resolved as a whole, and its first message says which, what it is on
(`anchor_text`) and what it asks for (`scope`): the block the thread names, when that is a
live comment of this thread, else the thread's earliest live message in document order. A
reply's own `status`, `scope` and `anchor_text` are whatever its writer sent and decide
nothing, wherever it was placed. The rule is written once per language,
`derive.thread_id` and `derive.first_message` in Python and `threadIdOf` and `threadsOf`
in `comments.ts`, and `vscode-codoc/src/test/fixtures/thread-cases.json` holds both to the
same cases. The directive (`realize._kind_lines`), the margin, and the policy on who may
resolve a thread ([concurrency.md](concurrency.md#7-roles-and-authority)) read it, so a
reply that says `open` on a resolved thread, or `both` on a `code` one, neither shows in
the margin nor reaches the agent.

Each message is voiced by its own author's role (`realize._kind_lines`, from the role
table). An owner's, a maintainer's or a machine's is `Comment on "…": …`, the person's
note, which the realize prompt lets win over the description. Anyone else's, a hub
contributor's on a thread of their own or as a reply in the owner's, is
`Contributor <actor> commented on "…": …`, which the prompt reads as a suggestion the
person did not write: followed only where it agrees with the description and the
person's words, never over them.

## 7. In the editor

The editor edits headings, paragraphs, list items, and the `source` of a table, diagram or
code block. A plugin block is a `kindBlock` card: the kind's name, a text area per Text
field, and its other fields read-only. Its projection entry carries its `layout`, which
says which fields are Text.
Comments are cards in the margin ([editor.md](editor.md)). The structure guard refuses a
transaction that would drop a kind block, and a selection delete across one is refused.

## Not built

- A per-kind Markdown form in `tree.codoc`. A plugin kind is written as a
  ```` ```codoc:<kind> ```` JSON fence that carries the block's layout under `$layout`,
  with or without its code ([model.md](model.md#7-the-markdown-form)).
- Codecs for how a kind block reads as model context and how an agent's reply becomes
  content. The context builder sends prose and source blocks to amend and each in-flow
  plugin block's words to read; an agent writes content through `codoc_write_block`.
- Editing a kind block's fields in the editor.
