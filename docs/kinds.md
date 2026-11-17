# Block kinds

A block kind decides which of a block's content fields are attributed `Text` and what the
other fields hold. The calculus knows the heading and prose kinds and the `source` field
of the source kinds. Every source and plugin kind is also a declaration in `codoc/kinds/`.
The kinds and their fields are listed in [model.md](model.md#block-kinds).

## 1. A declaration

```python
Kind(
    name: str,
    text_fields: tuple[str, ...],      # attributed Text; everything else is JSON
    in_flow: bool = True,              # False: hangs off words, not between them (comment)
    schema: dict,                      # field -> {type, enum?, required?}
    lift:    (LiftContext) -> dict | None,             # code -> content
    lower:   (old, new, LowerContext) -> str | None,   # a change to the block -> a directive line
    consult: (content) -> str,         # what the realizing agent reads
    exported: bool = True,             # False: left out of tree.codoc (comment)
)
```

- `register(kind)` adds it to the registry and tells the calculus its Text fields, whether
  it is in the flow and whether it is exported. A prose kind cannot be registered.
- `schema` types are `string`, `number`, `boolean`, `object`, `array`. `Kind.validate`
  refuses an unknown field, a wrong type and a value outside `enum`. A partial write skips
  `required`. Text fields arrive as Markdown strings.
- A codec sees content through `kinds.plain`: each Text field as a string in the
  `rejected` read mode, raw for a source field and inline Markdown otherwise.
- A kind never produces ops, actors or plans.

| codec | input | output | run by |
|---|---|---|---|
| `lift` | the store, the block, its feature, the feature's bindings, the code graph, the `.codoc` path | the fields that changed, or None | the loop, after each reflect pass |
| `lower` | the content as the last realization read it, the content now, the feature and its bindings | one directive line, or None | the directive, when `realize.md` is rendered |
| `consult` | the content now | text for the agent | the directive, for every in-flow block of the feature |

## 2. Text fields

A field named in `text_fields` is a `Text`. It gets character identity, per-word
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
| `comment` | `body` | | While the thread is open and the body is not empty: the anchor text and the body; with `scope: both`, also a request to amend the description through `codoc_reflect`. | The anchor text and the body. |

`table` and `code` are part of the feature's description, so the agent reads them there.
The `url` and `pdf` extractors are in the optional `media` extra.

## 4. How content becomes ops

Content written to a kind block goes through `codoc/kinds/write.py`
([model.md](model.md#6-content-as-ops), Kind blocks):

- `plan_write(block, content)` is pure. It validates `content` against the schema and
  returns one `block.set` body per changed non-Text field and one ingest request
  `(field, base_text, new_md)` per Text field named. Fields not named are left alone. A
  dead or missing block, an unknown kind or a schema error is a refusal.
- `add_body(kind, content, after)` returns the `block.add` body for a new kind block,
  validated with `required`. Its Text fields are written after it through `plan_write`.
- `write_block` and `add_block` run those against a `Workspace`, with the caller's actor,
  plan and `caused_by`.

| writer | how | actor | lands |
|---|---|---|---|
| A coding agent | `codoc_write_block(content, bid)`, or `kind` and `feature_id` to add a block at the end of the feature's body. It runs `plan_write` against a read-only replay and submits through the inbox. | `agent:claude-code` | The trunk, or the agent's plan when it passes `plan`. An ingest goes through the machine gate. |
| The loop | `kinds.lift.lift_pass`: every live block whose kind has a `lift`, on a feature that is not held. Only the changed fields are written. | `loop` | The trunk. The ingest is marked `derived`, which skips the machine gate. |
| The editor | Comment cards write comment blocks directly as ops (`block.add`, `text.insert`, `text.mark(comment)`, `block.set(status)`). | the person | The trunk. |

A tree edit (`add_node`, `amend`) carries prose and source blocks only. A plugin kind block
in a model's description is dropped.

## 5. Kinds in the directive

For each block of the feature's body (`loops/realize.py`, `_kind_lines`):

- An in-flow block adds its `consult` line.
- A block whose kind has a `lower` adds what `lower` makes of `(old, new)`. `old` is the
  content as the last realization read it: Text fields without the characters a person
  minted or killed since `realized` (an accepted plan's words count as the person's), and
  JSON fields rolled back through the `block.set` records outside `realized`.
- A comment is out of the flow, so it speaks only through `lower`. Its `lower` does not
  compare `old` and `new`: an open thread is in every directive of its feature until it is
  resolved.

## 6. Comments

A thread is one `comment` block per message, all with the same `thread` (the first
message's block id), each with `anchor` (the block the thread is on), `anchor_text`,
`scope` (`code` or `both`) and `status` (`open` or `resolved`). The words it discusses
carry a `comment` mark whose `block` attr names the first message. The mark is removed
whole and does not grow (I2). A comment is not exported to `tree.codoc` and is left out of
`ink`, `ground`, `phase` and drift.

## 7. In the editor

The editor edits heading and paragraph blocks. Every other in-flow block is a read-only
`kindBlock` node drawn from the projection's fields (kind name, then one row per field).
Comments are cards in the margin ([editor.md](editor.md)). The structure guard refuses a
transaction that would drop a kind block, and a selection delete across one is refused.

## Not built

- A per-kind Markdown form in `tree.codoc`. A plugin kind is written as a
  ```` ```codoc:<kind> ```` JSON fence ([model.md](model.md#7-the-markdown-form)).
- Codecs for how a kind block reads as model context and how an agent's reply becomes
  content. The context builder sends prose and source blocks; an agent writes content
  through `codoc_write_block`.
- Editing a kind block's fields in the editor.
