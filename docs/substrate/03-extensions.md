# codoc v2 — block kinds and how agents write to them

## 1. What a kind is

A block kind is a declaration of a content schema plus the arrows the kind
supports between the document and the code. The calculus knows two kinds,
`heading` and `paragraph`, whose content is a single Text. Every other kind is
a plugin, and a plugin never touches ops, runs, attribution or storage: it
declares a schema and implements codecs.

```python
class Kind:
    name: str                                # "image", "diagram", "latex", "url", "table", "comment"
    schema: JSONSchema                       # content shape; fields typed "Text" are attributed
    lift:    (code_context, block?) -> content | None     # code → block, read-only, idempotent
    lower:   (old, new, feature, bindings) -> Directive | Draft | None   # block → code request
    consult: (content) -> str                # what the realizing agent reads before implementing
    text_view: (content) -> str              # what an LLM reads when the block is context
    write_back: (content, agent_text) -> content   # how an agent's reply becomes content
    export:  (content) -> Markdown           # deterministic; fenced with the kind name
    import_: (Markdown) -> content           # inverse of export, for hand-written trees
```

The three arrows keep today's names and rules (`lift` may only read code and
return content; `lower` may only propose; `consult` is passive). The three
additions, `text_view`, `write_back` and `export`/`import_`, are what "how does
an agent write to an image block" and "what does it look like in git" needed
and did not have.

## 2. Text fields are attributed automatically

Because `Text` is a type in the calculus, a kind whose schema declares
`caption: Text` or `source: Text` gets, for free: character identity, per-word
attribution, comments anchored in it, plan tags on it, translation, and
History. The mermaid source of a diagram edited by a person and repaired by
the loop shows ink and ground exactly as a paragraph does. Non-Text fields
(an asset reference, a status, a numeric setting) are replaced whole by
`block.set` and attributed at block granularity by that op's actor.

This is the extensibility claim: a new kind adds a schema and codecs, and the
substrate's provenance, review and history apply to it without the kind
knowing they exist.

## 3. Worked example: an image block

```
kind: image
schema: { asset: Ref, alt: Text, caption: Text, source: "screenshot" | "upload" | "agent" }
lift:    for a bound UI feature, run the registered screenshot tool; return new content
         when the rendered pixels' hash changed
lower:   a person edited `caption` → a Draft directive quoting old and new caption,
         scoped to the feature's bindings (a caption edit is documentation until handed off)
consult: "Image <alt>. Caption: <caption>. File: .codoc/media/<asset>."
text_view: the same string
write_back: the agent cannot produce pixels from text, so write_back accepts a
         tool result: an asset ref the agent captured (the screenshot tool) or a
         diagram the agent drew that a renderer turned into an asset
export:  ![alt](.codoc/media/asset.png){#b-id}\n: caption
import_: the reverse
```

How an agent writes to it: through one MCP tool, `codoc_write_block(bid, content)`,
validated against the kind's schema; the daemon lowers it to `block.set` for
non-Text fields and `ingest` for Text fields, all with `actor=agent:<id>` and,
when the agent is proposing rather than reflecting, a `plan` tag. The agent
never emits ops; it emits content, and the boundary turns content into ops.

## 4. Comments are a kind

A comment thread is a block of kind `comment`, out of the reading flow
(`rank=null`), anchored by a `comment{block}` mark on the span it discusses:

```
kind: comment
schema: { status: "open" | "resolved", scope: "code" | "both", messages: [{ actor, at, body: Text }] }
lower:   scope=code → a steer directive scoped to the citations inside the anchored span
export:  omitted from tree.codoc; present in the projection
```

Message bodies are Text, so replies are attributed and a comment written by an
agent (AnchoredAI's finding: anchored beats a chat panel) shows as the agent's.
Resolving is `block.set(status)`. The anchor mark has `expand=none`; when its
characters are all tombstoned the card reports what it commented on, from the
tombstones, and ages out.

## 5. Registry and the editor

Kinds register on the Python side (`codoc.kinds.register`) and on the webview
side by name (a NodeView per kind). A block whose kind the editor does not know
renders as a read-only fenced view of its `text_view`, so an old extension
never breaks a new tree and never authors ops against content it cannot
display. The projection carries `kind` and the content; the editor does not
need the schema to render Text fields, only to render the rest.

## 5.1 As built (`codoc/kinds/`)

- `Kind(name, text_fields, schema, lift, lower, consult, text_view, write_back, exported)`.
  `lower(old_content, new_content, LowerContext) -> str | None` returns the
  directive text or nothing; `lift(LiftContext) -> content | None` returns
  only the fields that changed. Both see content with Text fields rendered as
  Markdown in the `rejected` read mode (`kinds.plain`).
- The schema is a per-field `{type, enum?, required?}` table checked by
  `Kind.validate`; a partial write (fields not named are left alone) skips
  `required`. Text fields arrive as Markdown strings.
- `kinds.write.plan_write` is the pure boundary (content → `block.set` bodies
  + ingest requests); `write_block` / `add_block` run it on a workspace; the
  MCP tool `codoc_write_block` runs it against a read-only replay and submits
  through the inbox. A Text field of a kind block may hold newlines (a
  diagram's source): `ingest(multiline=True)`; prose still refuses them.
- `kinds.lift.lift_pass(ws, graph, codoc_dir)` re-derives every live kind block
  with a `lift` after a reflect pass, skipping held features (doc wins), and
  writes the delta with `actor=loop` on the trunk.
- The export fences a kind as `codoc:<kind> {#b-id}` + JSON with Text fields
  as Markdown and empty Text fields left out; a kind registered with
  `exported=False` (comment) is omitted from `tree.codoc`.
- A comment is one message per block (`thread`, `anchor`, `anchor_text`,
  `scope`, `status`, `body: Text`); a reply is another block with the same
  `thread`. Per-message attribution is then the block's own, and the schema
  stays flat (the calculus attributes named fields, not items of a list).
- The editor renders any plugin kind read-only from the projection
  (`kindBlock`) and refuses a keystroke that would drop one; a kind's editing
  surface writes content through the daemon.

## 6. Trade-offs

- **Schema in the plugin, attribution in the substrate.** The alternative,
  each kind managing its own history, is what today's `blocks` table with an
  opaque `content` string does, and it is why a diagram edit has no ink and no
  blame. Making Text a substrate type moves that cost to zero per kind.
- **write_back takes content, not ops.** Letting agents emit ops would give
  them character-level control and a way to forge attribution. Content in,
  ops out, at one boundary, with the actor stamped by the daemon.
- **Comments as blocks, not a table.** A table would need its own history,
  its own attribution and its own anchor maintenance. As a kind, a comment is
  a block like any other, and the export simply omits it.
