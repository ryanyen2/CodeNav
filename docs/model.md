# The document model

codoc keeps one document per repository. Headings are features, the blocks under a
heading are its description, and bindings join a feature to code symbols. The document
is an op log: every edit is an op, one process applies ops in order, and every view is
computed from the state the log produces.

This file defines that state, the ops, and the views. Who may write, in what order, and
how logs merge is in [concurrency.md](concurrency.md).

## 1. Definitions

```
Session    := "s-" + 12 hex                 one applier's run on one checkout
OpId       := "<session>:<seq>"             seq counts from 1 inside the session
CharRef    := [OpId, k]                     the k-th Unicode scalar the op inserted, k from 0
Anchor     := CharRef | "START" | "END"
BlockId    := "b-" + 12 hex                 minted by whoever creates the block
PlanId     := "p-" + 12 hex
Actor      := "human:<who>" | "agent:<which>" | "translator:<lang>" | "loop" | "import"
HLC        := "<wall ms, 20 digits>-<logical, 20 digits>-<node>"     string order is time order
Frontier   := { Session: seq }              a set of ops: every op of each session up to seq
Rank       := base-62 fraction, as a string

Doc        := Block[]                       sorted by (rank, id); live and dead
Block      := { id, kind, rank, ranked_by, level, content, dead?, dead_actor?, dead_at?, dead_in?, detached?, void? }
content    := { field: Text | JSON }        which fields are Text is fixed by the kind
Text       := { segs: Seg[], marks: Mark[] }       segs in document order, live and dead
Seg        := { op, k0, chars, actor, at, plan?, dead?, dead_actor?, dead_at? }
Mark       := { kind, attrs, start: CharRef, end: CharRef, actor, op, at }     end is inclusive

Binding    := { feature: BlockId, symbol, fingerprint, types_hash }      keyed by symbol, `file::symbol_path`

Plan       := { id, actor,                  actor: who opened it
                kind,                       edit | add | move | delete; only delete has a meaning in the applier,
                                            so any other kind is a label, kept as sent (a newer client's kind
                                            survives an older store)
                target?,                    a block id; a delete plan must name a live heading
                note,
                status,                     open, then accepted | rejected; plan.reopen returns it to open
                ops: OpId[],                the ops applied under the plan, in apply order
                opened_by,                  the plan.open op
                build,                      accepting it hands off its features (an agent's codoc_propose)
                decided_by,                 the actor of the verdict
                verdict }                   the op of the current verdict, the one a reopen may undo

Watermarks := { handoff: Frontier,          the ops the person has handed to the agent
                realized: Frontier,         the ops a realization has settled
                handoffs: OpId[],           the hand-off ops, oldest first; the last holds the person's words
                code_at,                    clock of the newest bind.refresh or bind.detach on the feature
                realized_at }               clock of the newest feature.realized

OpBody     := { kind, target, payload }     what a client submits and what an inverse holds
Op         := { id, at, actor,              set by the applier; actor from the client's identity
                batch,                      one gesture, one ingest or one plan; the undo unit
                kind, target, payload,      payload arrives in wire form, is stored in canonical form
                base?,                      the frontier the client computed against; advisory
                plan?,                      the plan this op is tagged with
                caused_by?,                 the hand-off op this op answers; machines only
                inverse?,                   op bodies that undo it, applied in order
                conflict? }                 a code, when the op was not applied
```

- Ids are minted once and never renumbered. A session id is random and one session has
  one writer, so an `OpId` is unique across clones and survives a git merge.
- `k` counts Unicode scalars of the NFC form. The applier normalises text on insert.
- An actor's kind is the part before the first colon; a kind outside the five above is
  `unknown`. `agent`, `translator`, `loop` and `import` are machines. `agent`, `loop` and
  `translator` are rewriters: they rewrite prose on their own (section 6).
- Frontiers: `union(a, b)` is the per-session maximum; `in_frontier(op, f)` is
  `seq <= f[session]`; `covered(a, b)` holds when `b[s] >= a[s]` for every session in `a`.
- An op with no plan tag is a trunk op. The trunk is the document those ops write.

## 2. Blocks

| field | meaning |
|---|---|
| `id` | Unique for the life of the log. Only `block.add` and `block.split` create one. |
| `kind` | What the block is: a heading, prose, a source kind or a plugin kind. |
| `layout` | `{text: [field, …], flow, export}`: which content fields are `Text`, whether the block is in the reading flow, whether the export writes it. Fixed by the kind for the builtin kinds (heading, prose, source); recorded by `block.add` for any other kind. |
| `rank` | Order key. A dead block keeps its rank, so "after a deleted block" still names a place. |
| `ranked_by` | The op that last set `rank`: the `block.add` or `block.split` that made the block, a `block.move`, a move's `restore`, or a verdict's or reopen's `set` step. A rank string can be handed out again, so whether a block has been moved since is asked of this, never of the rank. |
| `level` | Outline depth of a heading, indent depth of a list item, both 1..6. 0 for any other kind. |
| `content` | A field named in `layout.text` is a `Text` edited by text ops. Any other field is a JSON value replaced whole by `block.set`. |
| `dead` | The op that killed the block. Absent while it is live. |
| `dead_actor`, `dead_at` | Who killed it and when. |
| `dead_in` | The feature whose description it was removed from, read when it died. A block that dies with its heading records that heading; a block merged into its neighbour, or added by a plan that was rejected, records none, since no words were removed. `removed:` and the `edited` phase read it, so where the dead block sits later does not matter. |
| `detached` | On a dead heading: the bindings it owned when it died. A heading made into another kind records them on its `block.set` instead. |
| `void` | `{move: [rank, level, ranked_by]}`: moves of rejected plans that could not be put back, since the block had been ranked again, each with the place it found the block. An undo that would restore `ranked_by` to one of them goes on to its place ([concurrency.md](concurrency.md#9-plans-and-verdicts)). |

### Block kinds

| kind | family | Text fields | other fields | in flow | exported |
|---|---|---|---|---|---|
| `heading` | heading | `text` | | yes | yes, with its id |
| `paragraph`, `bullet`, `numbered` | prose | `text` | | yes | yes |
| `table` | source | `source` | | yes | yes |
| `diagram` | source | `source` | `derived: boolean` | yes | yes |
| `code` | source | `source` | `lang: string` | yes | yes |
| `image` | plugin | `alt`, `caption` | `asset` (required), `source: screenshot \| upload \| agent` | yes | yes |
| `screenshot` | plugin | `caption` | `asset` (required) | yes | yes |
| `url` | plugin | `caption` | `url` (required), `status`, `fetched`, `title`, `excerpt`, `attempted_at` | yes | yes |
| `pdf` | plugin | `caption` | `ref` (required), `status`, `extracted`, `pages`, `excerpt`, `attempted_at` | yes | yes |
| `comment` | plugin | `body` | `thread` (required), `anchor` (required), `anchor_text`, `scope: code \| both`, `status: open \| resolved` | no | no |

- A heading or prose block holds one line of inline text (bold, code, link, cite).
  `ingest` refuses a newline there: a line break is a new block.
- A source block holds raw multi-line text: pipe-table rows, mermaid, or a snippet. It
  is never parsed as inline Markdown.
- `bullet` and `numbered` are list items. A list is a run of consecutive items. Numbers
  are not stored; they are computed when the document is rendered.
- A diagram is authored unless `derived` is true. The loop redraws only a derived diagram
  from the code graph, and reads an edit to one as a dependency request.
- Source and plugin kinds have a declaration: Text fields, a schema for the other
  fields (`type`, `enum?`, `required?`), in-flow and exported flags, and up to three
  codecs. `lift` turns code into content and is run by the loop. `lower` turns a change
  to the block into a directive line when a hand-off is rendered. `consult` is what the
  realizing agent reads. A kind never produces ops, and its schema is checked where
  content becomes ops (section 6), not by the applier.
- **A plugin block's layout is data; its codecs are code.** The declaration's Text
  fields and flags are the layout a writer puts on a new block's `block.add`. Every
  question about a block's fields asks the block's `layout`, never the kind, so a
  clone without the kind's code (an older codoc, a missing plugin) applies text ops to
  its Text fields, exports it and shows its words to a model exactly as a clone with
  the code does. Only the codecs (`lift`, `lower`, `consult`) and the schema check need
  the declaration.
- **A block's layout never changes after it is added.** A kind version cannot give
  existing blocks a new Text field; a field it adds is a JSON field. A block of a kind
  nobody declared, added without a layout, has the empty layout: every field is JSON.
- A block that is not in the flow (a comment) has a rank like any other. `block.merge`
  skips it when it looks for a neighbour, and its text is left out of `ink`, `ground`,
  `phase` and drift. A thread is one `comment` block per message, all with the same
  `thread`, anchored on the prose by a `comment` mark.

### Order

Blocks sort by `(rank, id)`. A rank is a base-62 fraction over `0-9A-Za-z` in ASCII
order, so string comparison is numeric comparison. A rank never ends in `0`.

- `between(a, b)` gives a rank strictly between two ranks; the first rank is `V`.
- Appending at the end steps the last digit by one, which keeps tail ranks short.
- A rank another block holds is not reused. The block takes the nearest free rank below
  it and so sorts immediately before the holder.

### Features

```
feature(h)     := a live heading h
range(h)       := h and every following live block, up to the next live heading with level <= h.level
body(h)        := h and every following live non-heading block, up to the next live heading of any level
feature_of(b)  := b when b is a live heading; else the nearest preceding live heading; else none
tree           := live headings in rank order; a heading's parent is the nearest preceding heading
                  with a shallower level, whatever the gap (an H1 followed by an H3 is parent and child)
```

`block.move` acts on `range`. `block.delete`, `description`, `ink`, `phase` and `history`
act on `body`. A dead heading is not a feature. It stays addressable for undo, for a
hand-off that asks for its code to go, and for history.

## 3. Text

A `Text` is one sequence of segments. A segment is a run of characters minted by one op:
the characters `(op, k0)` to `(op, k0 + len - 1)`.

- `actor` and `at` say who minted the run and when. `dead`, `dead_actor` and `dead_at`
  say which op deleted it, by whom and when; they are absent while it is live.
- `plan` is the plan the minting op was tagged with. It is never cleared; `Plan.status`
  says what became of the plan.
- Every mutation names characters by identity. Offsets exist only at the edges, for the
  editor and for `ingest`.
- A character identity is held by exactly one Text. `block.split` and `block.merge` move
  segments and never copy them.
- A dead segment keeps its place for ever. An insert, a split or a mark may name a dead
  character.
- A segment is cut only where an op touches it, and adjacent pieces with the same op,
  plan, actor, clock and liveness are rejoined. A paragraph typed in one sitting is one
  segment.
- A range `start..end` means whatever is live between the two ends now; clients send
  ranges. A segment list `[(op, k0, len)]` names exactly those characters; the log
  records segment lists, and undo, verdicts and replay act on them, because later
  inserts may sit inside the range.
- Every Text operation costs O(segments + marks) of that Text.

### Example: segments and attribution

Paragraph `b-1` is empty. Four ops in session `s1` (`†` marks a dead segment):

| op | actor | wire payload | segments after | live text |
|---|---|---|---|---|
| `s1:4` `text.insert` | `human:local` | `after: START, text: "Retries twice."` | `(s1:4, 0, "Retries twice.")` | `Retries twice.` |
| `s1:5` `text.insert` | `agent:claude-code` | `after: [s1:4, 7], text: "at most "` | `(s1:4, 0, "Retries ")` `(s1:5, 0, "at most ")` `(s1:4, 8, "twice.")` | `Retries at most twice.` |
| `s1:6` `text.delete` | `human:local` | `from: [s1:5, 0], to: [s1:4, 12]` | `(s1:4, 0, "Retries ")` `(s1:5, 0, "at most ")†` `(s1:4, 8, "twice")†` `(s1:4, 13, ".")` | `Retries .` |
| `s1:7` `text.insert` | `human:local` | `after: [s1:4, 12], text: "once"` | `(s1:4, 0, "Retries ")` `(s1:5, 0, "at most ")†` `(s1:4, 8, "twice")†` `(s1:7, 0, "once")` `(s1:4, 13, ".")` | `Retries once.` |

- `s1:5` cuts the first segment in two. Both halves keep op `s1:4`; only `k0` changes.
  Attribution is now three runs: person `[0, 8)`, agent `[8, 16)`, person `[16, 22)`.
- `s1:6` is stored as `segments: [[s1:5, 0, 8], [s1:4, 8, 5]]`. Its inverse is a
  `text.restore` of those segments with `by: s1:6`.
- `s1:7` anchors after `(s1:4, 12)`, the dead `e` of "twice", and lands at that place.

### Marks

A mark is a range over character identity, inclusive at both ends. It records who placed
it (`actor`), the op that placed it (`op`) and when (`at`). A verdict or an undo finds a
mark by `op`.

| kind | attrs | grows at its end | exclusive | removed whole | Markdown |
|---|---|---|---|---|---|
| `bold` | none | yes | no | no | `**words**` |
| `code` | none | no | no | no | `` `words` `` |
| `link` | `href` | no | with `cite` | no | `[words](https://…)` |
| `cite` | `symbol` | no | with `link` | no | `[words](codoc:file::symbol_path)` |
| `comment` | `block` | no | no | yes | none |
| `suggest_del` | `plan`, `chars` | no | no | yes | none |
| `suggest_mark` | `plan`, `mark`, `on`, `a:<attr>`… | no | no | yes | none |

- **Grows at its end.** An insert anchored on the mark's end character extends the mark
  over the new segment. For every other kind the new characters are outside it (I2). An
  insert anchored strictly inside any mark's range is covered by it.
- **Not whole, turned on.** The range absorbs every mark of the same kind and attrs that
  overlaps it or is adjacent to it (no live character between). The result keeps the
  actor and placing op of the first mark it absorbed.
- **Not whole, turned off.** The range is cut out of every overlapping mark of the same
  kind and attrs, leaving zero, one or two pieces.
- **Exclusive.** Turning a link or cite on cuts every overlapping link or cite with a
  different kind or attrs back to the pieces outside the range. The inverse restores them.
- **Whole.** Turning one on adds a mark with exactly the given range; an exact repeat
  does nothing. Turning one off must name the exact start and end of an existing mark
  with the same attrs, or the op conflicts `mark_partial`. An off that also names the
  placing `op` (the inverse of placing one) removes every mark of that kind, attrs and
  placer, in every field that holds one: the pieces a split cut it into. It conflicts
  `mark_partial` when there is none.
- `block.split` turns a mark that spans the cut into two marks. `block.merge` rejoins a
  mark that ends at the seam with a same-kind, same-attrs mark that starts at it.
- A mark whose characters are all dead produces no span in any view.
- `suggest_del` needs an open plan. `chars` lists the live characters in the range when
  the mark was placed, as `op:k,op:k,…`. Accepting the plan kills exactly those, so a
  word typed inside the struck range afterwards survives.
- `suggest_mark` is written only by the applier, when a plan-tagged `text.mark` names any
  other kind. It records the proposed toggle: `mark` is the kind, `on` is `"1"` or `""`,
  `a:<attr>` are the attrs. The document's own marks do not change until the plan is
  accepted.

## 4. Bindings, plans, watermarks

**Binding.** A symbol has at most one owner (I8). The symbol is the index's name for a
chunk, `file::symbol_path`, so it says its file, and a cite names a binding by the same
string. A dead heading owns nothing. The store also keeps `bound_once`, the set of
features that ever had a binding: the loop proposes deleting a feature that had code and
has none now.

**Plan.** A plan is a tag, and the document stays one document. Words a plan proposes are
live segments carrying `plan`. Words it wants removed stay live under a `suggest_del`
mark. Mark changes it proposes are `suggest_mark` records.

**Watermarks.** One set per feature. A feature is **queued** when `handoff` is not empty
and `covered(handoff, realized)` is false.

## 5. Operations

### What `apply` does

1. An id already recorded returns the stored record and changes nothing.
2. An unknown kind conflicts `unknown_kind`.
3. The policy is asked whether this actor may write this op. A denial conflicts `authority`.
4. A plan-tagged op needs its plan to be open (`plan_not_open`) and its kind to be
   plannable (`not_plannable`).
5. The handler resolves every anchor, block and mark it will touch. A failure is a
   conflict, and nothing has been mutated.
6. The handler mutates the state and returns the canonical payload and the inverse. A
   plan-tagged op is appended to its plan's `ops`.
7. The op is recorded: appended to the op list, the frontier advanced, the head of every
   touched block advanced. A conflicted op is recorded with its code and no inverse,
   under the block or plan target it named.

`apply` is total and deterministic. It costs O(segments + marks of the blocks it
touches), plus one ordered insert for a structural op. The flow of a submission through
these checks is drawn in [concurrency.md](concurrency.md#4-a-submissions-path).

### Text ops

Target `{bid, path}`; `path` defaults to `text`.

| op | wire payload | canonical payload adds | inverse |
|---|---|---|---|
| `text.insert` | `after: CharRef \| START`, `text` | `text` in NFC; `bid`, `path` of the field that holds the anchor | `text.delete {segments: [[op, 0, len]]}` |
| `text.delete` | `from`, `to`, or `segments` | `segments` it killed; `bid`, `path` | `text.restore {segments, by: this op}` |
| `text.restore` | `segments`, `by?` | `segments` it revived | `text.delete {segments}` |
| `text.mark` | `kind`, `attrs`, `start`, `end`, `on` (default true) | `removed` and `added`, the marks it displaced and placed; or `proposed`, the `suggest_mark` record | off for each added mark, on for each removed mark with its original `actor`, `op`, `at`; for a plan toggle, the record turned off |

- A character is found by identity. `bid` and `path` are a hint; the op acts on the field
  that holds the character, preferring a live block. A split or a merge under a
  concurrent text op costs nothing.
- `text.insert` mints one live segment directly after its anchor. Of two inserts after
  the same anchor, the one applied later sits nearer the anchor; neither is interleaved.
  At `START` the named block must be live and `path` a Text field. `END` is refused.
- `text.delete` with a range needs both ends in one field. It kills the live characters
  between them; characters already dead stay dead by their own op.
- `text.delete` with `segments` kills exactly those characters wherever they are now, and
  nothing typed between them since.
- `text.restore` revives the named characters with their identity. With `by`, only those
  that op killed.
- Under a plan, `text.delete` may name only characters that plan minted, and
  `text.restore` may undo only a delete tagged with the same plan.

### Block ops

| op | target | wire payload | canonical payload adds | inverse |
|---|---|---|---|---|
| `block.add` | `bid` | `kind`, `level?`, `after: BlockId \| START` or `under: BlockId \| START`, `content`, `layout?` | `after`, `level`, `rank`; `layout` for a kind that is not builtin | `block.delete {ids: [bid]}` |
| `block.set` | `bid`, `path` | `value`, or `unset: true` to remove a non-Text field; with `path = kind`, optional `level`, and `bindings` on an inverse | `prev`; for `kind` also `prev_level`, `level`, and `detached` when a heading becomes another kind | `block.set` to the previous value (with `bindings: detached`), or `unset` when the field was absent |
| `block.split` | `bid`, `path` | `at: CharRef \| START`, `new: BlockId` | `new_first`, `rank`, `bid`, `path` | `block.merge {into: bid, bid: new}`, with `rejoin` for a heading |
| `block.merge` | `into`, `bid`, `path` | none | `at` (the seam), `order`, `rank` of the dead block | `block.split {at, new: bid, rank, new_takes}` |
| `block.move` | `bid` | `after: BlockId \| START` and `level_delta`, or `under: BlockId \| START` | `after`, `level_delta`, `range`, `prev: [[id, rank, level, ranked_by]]` | `block.move {restore: prev}` |
| `block.delete` | `bid` | none, or `ids` | `ids`, `killed`, `detached` | `block.restore {ids: killed, by: this op, bindings: detached}` |
| `block.restore` | | `ids`, `by?`, `bindings?` | `revived` | `block.delete {ids: revived}` |

- **add.** Text fields start empty. `after` may name a dead block. `level` is clamped to
  1..6 for a heading or list item (1 when not given) and is 0 for any other kind.
- **add, `layout`.** A builtin kind ignores a sent layout. Any other kind takes the
  layout sent; an add without one (an old log, an old writer) takes this process's
  registered layout for the kind, or the empty layout when it has none. The canonical
  payload records the layout used, so replay and a snapshot give the block the same
  layout on any version. A layout that is not `{text: [string, …], flow?, export?}`
  conflicts `malformed`.
- **`under`** (add and move, headings only). The heading goes after the last live block
  of the parent's range, one level deeper; `START` is the end of the document at level 1.
  It is resolved when the op applies, against the tree as it is then, and the record
  keeps the resolved `after` and level, so replay does not resolve it again. A machine
  that writes several nodes in one reply names parents this way, so its edits land the
  same compiled together or one by one. The editor sends `after`.
- **set `kind`.** Only between heading and the prose kinds (`paragraph`, `bullet`,
  `numbered`), which share one inline `text` field. A source or plugin block keeps its
  kind for life. `bullet` to `numbered` and back keeps the level. Any other conversion
  takes the given `level`, or 1, or 0 when the new kind has no level.
- **set `level`.** Only a heading or a list item, clamped to 1..6. It changes the named
  block and no other (I3).
- **set, any other path.** Replaces a non-Text field. `id`, `rank`, `ranked_by` and `dead` are reserved.
- **split.** Only a heading or a prose block; a newline in a source or plugin field is
  content, not a block boundary. The head keeps everything up to and including `at`;
  `START` makes an empty head. The original id stays with the half that has live text, and with the head when
  both or neither do. When only the tail has live text, the new block takes the empty
  head and is ranked before the original. The new block is a paragraph when a heading is
  split and the same kind otherwise; a list item splits into two items at the same level.
- **merge.** `bid` dies and its segments and marks move into `into`. `bid` must be the
  live in-flow block immediately before or after `into`, and both must be a heading or a
  prose block. A
  heading is never merged away, and prose merges into a heading only as the inverse of a
  heading split (`rejoin`).
- **move.** A heading moves with its `range`; any other block moves alone. The range is
  computed at apply and gets fresh ranks after the anchor. `level_delta` applies to the
  headings in the range, clamped.
- **set.** A `kind` change away from heading ends the feature as a delete does: its
  bindings are removed in the same op and recorded as `detached`. Its inverse carries them
  as `bindings` and re-attaches each whose symbol has no owner now. A plan's `block.set`
  never re-attaches from its payload; rejecting the plan puts the detached bindings back
  as `bind` steps.
- **delete.** A heading dies with its `body`; any other block dies alone. Descendant
  headings survive. The bindings of a killed heading are removed, kept on the block as
  `detached`, and recorded. A gesture that deletes a subtree sends one `block.delete` per
  heading, and one that promotes children sends `block.set(level)` per child, in one batch.
- **restore.** Revives the named blocks (with `by`, only those that op killed) and
  re-attaches each recorded binding whose symbol has no owner now.

### Binding ops

Target `{fid}`.

| op | wire payload | canonical payload adds | inverse |
|---|---|---|---|
| `bind.attach` | `symbol`, `fingerprint`, `types_hash` | `displaced`, the prior owner; or the previous hashes when `fid` already owned it | detach, then re-attach the prior owner; or refresh to the previous hashes |
| `bind.detach` | `symbol` | the hashes | `bind.attach` |
| `bind.refresh` | `symbol`, `fingerprint`, `types_hash` | the previous hashes | `bind.refresh` to them |

`bind.attach` needs a live heading and takes the symbol from any other owner (I8).
`bind.detach` and `bind.refresh` need `fid` to own the symbol, and set `code_at` to the
op's clock. `bind.attach` does not: a relocation is not drift.

### Plan and watermark ops

| op | target | wire payload | canonical payload adds | inverse |
|---|---|---|---|---|
| `plan.open` | `pid` | `kind` (default `edit`), `target`, `note`, `build?` (honoured from an agent only) | | none |
| `plan.accept` | `pid` | `subset: OpId[]?` | `accepted`, `rejected`, `steps` | `plan.reopen {by: this op}` |
| `plan.reject` | `pid` | none | `rejected`, `steps` | `plan.reopen {by: this op}` |
| `plan.reopen` | `pid` | `by`, the verdict | `from`, `steps`, the ones it undid | the verdict again, with its `subset` |
| `feature.handoff` | `fid` | `covers?`, `asked`, `delete_code` | `covers` | `feature.handoff {restore: previous frontier, handoff: this op}` |
| `feature.realized` | `fid` | `covers?`, `sha`, `files`, `summary` | `covers` | none |

- A plan tag may ride on `text.insert`, `text.delete`, `text.restore`, `text.mark`,
  `block.add`, `block.set`, `block.split`, `block.move` and `bind.attach`. A plan never
  carries `block.delete` or `block.merge`: a plan to delete a feature has `kind = delete`.
- What a verdict does to each tagged op, and the steps it records, are in
  [concurrency.md](concurrency.md#9-plans-and-verdicts). `plan.reopen` is sent only as
  the inverse of a verdict. Neither reads steps from its payload: a verdict computes
  them from `subset` and the plan, and a reopen undoes the ones the store recorded on
  the plan's current verdict, so a replay recomputes every effect and a log cannot add one.
- `feature.handoff` needs a heading, live or dead, and sets `handoff` to
  `union(handoff, covers)`. `covers` defaults to the store's frontier at apply, which
  includes the ops earlier in the same batch. Its restore form sets `handoff` back and
  drops the named hand-off: the one case in which a watermark shrinks (I9).
- `feature.realized` conflicts `nothing_queued` when the feature is not queued. It sets
  `realized` to `union(realized, covers)`; `covers` defaults to `handoff`.

### Batches and undo

- A batch is not atomic. An op that conflicts is recorded and the rest still applies. A
  caller that needs all or nothing checks its bodies before submitting.
- Undo submits the recorded inverses of a batch, newest op first, as new ops by the
  person. The log keeps both. The inverse of an inverse is the original. An inverse
  carries no plan tag and no `caused_by`. The ops a snapshot writes have none
  ([concurrency.md](concurrency.md#12-snapshots)).

### Conflict codes

Codes are stable identifiers. A conflicted op changes nothing.

| code | from | meaning |
|---|---|---|
| `unknown_kind` | any op | The kind is not in the vocabulary. |
| `authority` | any op | The policy refused this actor. |
| `plan_not_open` | tagged ops, verdicts | The plan is missing or decided. |
| `not_plannable` | tagged ops | This kind may not ride in a plan. |
| `missing:<key>` | any op | A target or payload entry the handler needs is absent. |
| `malformed` | any op | A value has the wrong type, or `under` places a block that is not a heading. |
| `bad_anchor` | text ops, split | Not a character reference, or `START` or `END` where a character is needed. |
| `unknown_anchor` | text ops, split | No field holds the character, or the range ends are reversed. |
| `range_spans_blocks` | delete, mark | The two ends are in different fields. The client reads again and sends one op per block. |
| `empty_insert` | insert | No text. |
| `not_text` | insert or split at `START` | `path` is not a Text field of the block. |
| `no_block` | most ops | No such block. For `block.move` and `block.delete`, also a dead one. |
| `dead_block` | text ops, set, split, merge, attach | The block is dead. |
| `not_this_plans_words` | tagged delete | The range holds characters the plan did not mint. |
| `not_this_plans_delete` | tagged restore | `by` is not a delete of the same plan. |
| `suggest_needs_plan` | mark | A proposal kind with no open plan. |
| `mark_partial` | mark | Turning off part of a whole mark. |
| `duplicate_block` | add, split | The block id exists. |
| `reserved_path`, `text_field` | set | `path` is `id`, `rank`, `ranked_by` or `dead`; or it is a Text field. |
| `kind_incompatible` | set kind, merge | One of the two kinds is not a heading or a prose kind. |
| `not_splittable` | split | The block is a source or plugin kind. |
| `newline_in_line` | insert | A newline into a heading or prose block. A line break there is `block.split`. |
| `no_level` | set level | The kind has no level. |
| `merge_heading` | merge | A heading would die, or prose would merge into a heading. |
| `not_adjacent` | merge | The two blocks are not neighbours in the flow. |
| `bad_target` | move | The anchor, or the `under` parent, is inside the moved range. |
| `bad_rank` | add, split, move | The bounds of a rank request are out of order. |
| `no_feature` | attach, feature ops, delete plans | The target is not a heading. |
| `not_bound` | detach, refresh | The feature does not own the symbol. |
| `duplicate_plan`, `delete_plan_needs_target` | `plan.open` | The plan id exists; a delete plan has no target. |
| `no_plan`, `plan_open` | verdicts, reopen | No such plan; reopening a plan that is open. |
| `not_current_verdict` | reopen | `by` is not the plan's current verdict: someone reopened and decided it again since. |
| `verdict_folded` | reopen | A snapshot folded the verdict, its steps and the plan's ops. |
| `nothing_queued` | `feature.realized` | No hand-off is waiting. |

## 6. Content as ops

A person types ops. A model, a translator or a coding agent hands in content. Content
becomes op bodies in two ways. Both are pure: they mint no ids and apply nothing.

**`block_bodies(specs, after)`** for new blocks. Nothing is diffed. Each block is one
`block.add` after the previous one; each Text field with text is one `text.insert` at
`START`, then one `text.mark` per span. A plugin block's `block.add` carries a layout:
the spec's `$layout` when its fence had one (a copied block keeps its layout), else the
layout this process registers for the kind, else the empty one. The spec's fields are
split into Text and JSON by that same layout.

**`ingest(field, base_text, new_md, actor)`** for new text of an existing field. It is
the one place where a diff decides what happened.

1. `new_md` is normalised to NFC and parsed as inline Markdown into plain text and
   spans. For a source field it is taken as written.
2. A newline in a heading or prose field refuses the call: `multi_paragraph`.
3. `base_text` must equal the field's live text, or the call is refused `stale_base` and
   the caller reads again.
4. Both texts are tokenised: a run of word characters, a run of punctuation, a run of
   whitespace; each character of a word in a script `codoc/calculus/scripts.py` lists as
   `PER_CHARACTER` (Han, Kana, Thai, Lao, Khmer, Myanmar, Hangul) is its own token. The diff is a longest common subsequence over tokens in which whitespace never
   anchors a match on its own.
5. Each hunk is one `text.delete` over the base characters it drops and one `text.insert`
   anchored after the last surviving base character before it, or `START`.
6. A rewriter writing the trunk is refused `would_drop_human_<kind>` when a bold, code,
   link or cite that a person placed would be left on none of its characters. Under a
   plan nothing is refused: the person decides.
7. Marks survive by identity. For each existing bold, code, link or cite: an off over the
   surviving characters the new text no longer marks that way, and an off over inserted
   words the mark would cover once they land and the new text does not mark. For each
   span of the new text not already wholly under the same mark: an on.
8. The result carries the bodies (deletes, inserts, marks) and `deleted_fraction`, the
   share of the field's live characters it deletes.

- The result is a fixed point: after the bodies apply, the field renders as `new_md`.
- A rewritten sentence is one hunk. An appended word re-authors nothing. A changed comma
  leaves the word before it with its author. A rewrite that reorders two sentences
  re-authors the one that moved.
- Under a plan, each `text.delete` in the bodies becomes a `text.mark` of `suggest_del`
  over the same range, so the words stay live and struck until a verdict.
- Whether a machine's ingest lands on the trunk or under a plan is decided from
  `deleted_fraction` and the feature's phase:
  [concurrency.md](concurrency.md#the-machine-gate).

**Anchors inside a batch.** A body may name `["$ins", i, k]` wherever an anchor goes: the
`k`-th character of the batch's `i`-th `text.insert`, counting inserts from 0 at the start
of the batch. It becomes a real `CharRef` as the batch is submitted. Joining two body
lists shifts the second list's indices past the first list's inserts.

**Kind blocks.** A write to a source or plugin block hands in `{field: value}`. It is
checked against the kind's schema, then becomes one `block.set` per changed non-Text
field and one `ingest` per Text field named. Fields not named are left alone.

## 7. The Markdown form

One format serves `tree.codoc`, a feature's description as a model reads and writes it,
and the text of one block in an amend.

```
heading    := "#"{1,6} " " inline [" {#" BlockId "}"]
paragraph  := inline                                    one line
item       := indent ("-" | digits ".") " " inline      indent is 4 spaces per level above 1
table      := lines that each start with "|"            kept verbatim
diagram    := "```mermaid" [" derived"]  source  "```"
code       := "```" [lang]  source  "```"
other      := "```codoc:" kind [" {#" BlockId "}"]  one JSON object  "```"

inline     := (escape | code | link | bold | character)*
escape     := "\" X                 X is one of  * [ ] \ # ` - + . |
code       := "`" raw "`"           not empty, one line; inside, only \` and \\ are escapes
bold       := "**" … "**"           not empty, one line; may hold code
link       := "[" label "](" target ")"       the label may hold bold and code
target     := "codoc:" symbol                 a cite; symbol is a binding key, `file::symbol_path`
            | "http://…" | "https://…"        a link
```

Writing:

- One blank line between blocks. None between consecutive list items, except before the
  first item of a list and between a level-1 bullet and a level-1 numbered item.
- **Lists nest one level at a time.** An item is written at
  `min(level, level of the item before it + 1)`, and at 1 after a block that is not an
  item. An item stored two levels deeper than its predecessor is written one level deeper.
- A numbered item's number is its position among the numbered items at its level. The
  count restarts after a shallower item, a bullet at the same level, or a block that is
  not an item.
- A source block is written in its own form when that form can say it: a table whose
  lines all start with `|`, a diagram with no field but `derived: true`, a code block
  with no field but a one-word `lang`. Otherwise it is a `codoc:` JSON fence with its
  `source` inside. A plugin block is always a JSON fence, with its Text fields as
  Markdown strings and empty ones left out, and the reserved key `"$layout"` holding
  the block's layout, so the fence says which keys are Text. It is written from the
  block, so a kind this process has no code for is written the same way. Reading it
  back gives the same fields; `block_bodies` rebuilds the block with the fence's
  layout, or this process's for the kind when the fence has none. No field of a kind
  may be named `$layout`.
- A fence is one backtick longer than the longest line of backticks in its source.
- **Marks nest link, then bold, then code.** The store allows any overlap. The writer
  cuts a bold at every link edge and a code at every link and bold edge.
- **Escapes.** `\` always; `[` and `` ` `` always; `]` inside a label; `*` when it touches
  a bold edge or another `*`. At the start of the text: `#`, `|`, a `-`, `+` or `*`
  followed by a space, and the dot of a leading number followed by a space. Inside code
  only `` ` `` and `\`.

Reading:

- Line by line, first match: blank line, fence, table row, list item, heading, paragraph
  line. Consecutive paragraph lines join with a space, and so do the indented
  continuation lines of an item.
- The reader also accepts `+` and `*` bullets, tabs (4 columns) and any indent width. An
  item indented more than the open level opens one level, to a depth of 6.
- A fence closes at a line of at least as many backticks; an unclosed one runs to the
  end. A `codoc:` fence whose body is not a JSON object is read as a code block. Inline
  text that does not complete one of the forms above is literal.
- Read as a description, a `#` line is a paragraph: a description cannot hold a heading.

Round trip:

- Inline: reading what the writer wrote gives back the text and the spans, up to the
  merge of adjacent same-kind spans, for every state the store can hold. Applying those
  spans through `text.mark` merges the cut pieces into one mark again.
- Blocks: `parse(render(specs)) == specs` when each prose text is inline Markdown as the
  writer writes it, item levels step by at most one, and ids are written.
- `tree.codoc` is one-way. Nothing reads it back into a store; a clone rebuilds from the log.
  Its first line is `block_md.GENERATED`, a comment saying that a hand edit is
  overwritten. A reader skips that line, so a pasted export parses to the same blocks.

## 8. Derived views

Pure functions of the store. Nothing here writes.

### Read modes

| mode | an open plan's inserted words | words under its `suggest_del` | its `suggest_mark` toggles | read by |
|---|---|---|---|---|
| `live` | shown | shown | not applied | the projection; the editor draws proposals over it |
| `rejected` | hidden | shown | not applied | the export, the directive, every model and agent read, kind codecs |
| `accepted` | shown | removed | applied | nothing shipped |

A decided plan changes no mode: an accepted plan's words are ordinary text, and a
rejected plan's words are dead.

### Views

| view | definition |
|---|---|
| `markdown(field, mode)` | The live text and its marks in a read mode, as inline Markdown. Raw text for a source field. |
| `block_spec(b, mode)` | The block as the Markdown form says it, or nothing when it says nothing in this mode: an empty block, a block holding only an open plan's words under `rejected`, a kind that is not exported. |
| `description(f, mode)` | The prose and source blocks of `body(f)` after the heading, as block Markdown. |
| `attribution(field)` | The live segments as offset runs `{start, end, actor, at, plan, op, k0}`. Merged where adjacent runs share actor and plan, for display; unmerged for the editor, where `(op, k0 + i)` is the identity of character `i`. |
| `block_written_by(b)` | `human` when a person wrote any live character of the block, else the actor of the newest live segment; an open plan's words are left out. |
| `ink(f)` | Untagged live segments a person minted on the in-flow blocks of `body(f)`, outside `realized`: `sent` when inside `handoff`, else `unsent`. |
| `ground(f)` | What the loop did since the person last engaged. `since` is the newest clock of a character the person minted or killed, or a mark they placed. A live untagged loop segment is `added` and a segment the loop killed is `removed`, when its clock is after `since` and its op is outside `handoff`. Empty for a feature with no hand-off and no trace of a person. |
| `plan_claims(f)` | Tagged live runs of open plans (`open`); `suggest_del` spans of open plans (`removed`); then `mark_claims(f)`. An accepted plan's words claim nothing. |
| `mark_claims(f)` | `suggest_mark` records of open plans (`marked`, `unmarked`). |
| `open_plans_on(f)` | Open plans that target a block of `body(f)` or have an applied op recorded on one. |
| `open_plans()` | Open plans whose target is null or live: the ones offered for a verdict. |
| `phase(f)` | Below. |
| `queued()` | Queued features in document order, then queued dead headings (a removal still waiting). |
| `directive(f)` | The realize prompt item: title and `description` in `rejected` mode; `added:` from `sent` ink; `removed:` from characters a person killed inside `handoff` and outside `realized`; `Focus:` from a person's bold marks outside `realized`; `Consult:` from links; each kind block's `consult` and `lower`, or for an in-flow block of a kind this process has no code for, `<kind>: <words>` from its Text fields; `Author asked:` from the latest hand-off. For a dead heading: a removal item, which deletes code only when the hand-off set `delete_code`. |
| `history(f)` | Every op recorded against a block of `body(f)`, oldest first, conflicted ones included. Ops on blocks the feature no longer holds are left out. |
| `history_window(n)` | The timeline: the applied ops among the last `n` (200 as published). Per op: `blocks`, the text of each Text field of each block it touched as it read right after the op (`{bid: {field: text}}`), its recorded inverse, the blocks it took off or put on the page, and the ranks, kinds and levels it displaced; plus what was true entering the window. Computed by un-applying the window backwards on clones of the live fields, by character identity. |
| `dead_cites` | `cite` marks whose `symbol` no binding has. |
| `export` | `block_spec` of every live block in `rejected` mode, written with heading ids. |
| `projection` | `{store, head, frontier, blocks, phases, editors, plans, watermarks, kinds, bindings, claims}`. `kinds` maps each plugin kind registered in this checkout to the layout it gives a new block, so an editor pasting a `codoc:<kind>` fence without `$layout` splits its fields as the applier would. `store` names the store instance and changes on every rebuild; `head` is comparable only under one `store`. Each plan carries `handoff`: the features accepting it hands off (`accept_handoffs`: a plan opened with `build`, which `codoc_propose` sets, that is not a delete plan). A block entry is `{id, kind, level, rank, head, fields}`, plus `layout` for a kind that is not builtin, so an editor without the kind can draw it. A Text field is `{text, runs, spans, suggest_del}` in `live` mode, with unmerged runs and the document's own marks. `plans` is `open_plans()`. `editors` maps each `edited` feature to the actors whose unsent ink or unsent removals are in it (`derive.editors`); the editor's ⌘S hands off only the features that list the viewer. `bindings` is `{by_feature: {f: [symbol]}}`. `claims` is `ground` and `mark_claims` per feature, the claims a block entry does not carry: an editor draws ink and a plan's words and cuts from the runs and `suggest_del`. |
| `state_fingerprint` | Blocks with text and marks, bindings, plans, watermarks. Equal fingerprints mean the same document. |

### Phase

`phase(f)` is the first of these that holds.

| phase | holds when |
|---|---|
| `edited` | `ink(f)` has an `unsent` claim, or a person killed characters on the in-flow blocks of `body(f)` with an op outside both `handoff` and `realized`, or a person deleted a whole in-flow block of `body(f)` that held document words, with an op outside both. Changing a block's kind, level or place is layout and does not count. |
| `realizing` | `f` is queued and a realize pass holds the lease: `status.json` says `realizing` and was written within the last 300 seconds. The lease is the one input from outside the store. |
| `queued` | `f` is queued and has at least one binding. |
| `planned` | `f` is queued and has no binding. |
| `proposed` | `open_plans_on(f)` is not empty. |
| `drifted` | `code_at` is set and is later than both `realized_at` and the newest clock of any character minted or killed, or any mark placed, on the in-flow blocks of `body(f)`. |
| `synced` | Otherwise. |

`edited`, `queued` and `planned` are the held phases: a machine may not write the trunk of
a held feature.

### Cache keys

The store advances three counters, each to the position after the op that moved it:

- `block_head[b]` on any applied op that touched block `b`;
- `structural_head` on `block.add`, `split`, `merge`, `move`, `delete`, `restore`, and a
  `block.set` of `kind` or `level`;
- `plans_head` on an applied `plan.open`, `plan.accept`, `plan.reject` or `plan.reopen`.

A per-block view (a projection entry, an export spec) may be cached on
`(block_head[b], plans_head)`. A per-feature view (phase, claims) may be cached on
`(structural_head, the largest block_head in body(f), plans_head, whether f is realizing)`.
A publish from a cold cache and one from a warm cache write the same bytes.

## 9. Invariants

1. **I1 One author per character.** A segment names one op and one actor.
2. **I2 Suggestion and comment anchors do not grow.** A character typed at the end of a
   `code`, `link`, `cite`, `comment`, `suggest_del` or `suggest_mark` range is outside it.
   `bold` is the one kind that grows.
3. **I3 No cascade.** An op mutates only the blocks it names or the range it computes.
   No apply changes a level it was not asked to change.
4. **I4 Unique block ids.** A `block.add` or `block.split` with an id that exists is a
   recorded conflict.
5. **I5 Anchors are never re-guessed.** An unknown character or block is a recorded
   conflict, never a nearest match.
6. **I6 The store is a function of the log.** Replaying the log rebuilds the store, on
   every apply path, conflicts included.
7. **I7 Documentation never writes code.** No op touches a source file. A directive is
   derived; a hand-off is a gesture.
8. **I8 Binding uniqueness.** A symbol has at most one live owner. `bind.attach`
   displaces and records.
9. **I9 Frontiers are monotone.** A feature's `handoff` and `realized` only grow, except
   when a person undoes their own hand-off.

## Rejected

- **Changing the document's marks when a plan toggles one, and finding them again at
  the verdict.** Three matching rules were tried (by value, by placer, by the op's
  recorded marks). Each failed, because a split moves a recorded start and a merge
  shares a placer. A cold publish of a tree with 20 open plans of 10 mark toggles each
  took 534 ms; with `suggest_mark` records it takes 260 ms.
- **Reading drift, `ground`, Focus and `removed:` from the ops.** A snapshot removes ops,
  and after a reflect pass every feature reads as drifted, so a snapshot of such a tree
  folded nothing. The clocks and actors these views need are kept on segments, marks and
  watermarks.

## Not built

- Reading `tree.codoc` back into a store.
- A time window on `attribution`, and snapping runs to word boundaries.
- A per-block language tag and a per-run screen-reader description in the projection.
- An `Edit only:` scope line in the directive. The scope is the feature's binding set.
- Kind codecs for model context, for an agent's reply, and for a per-kind export form.

