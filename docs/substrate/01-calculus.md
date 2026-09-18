# codoc v2 — the calculus

Status: specification, revision 2 (2026-09-17), after the adversarial review in
`07-design-review-1.md`. Everything else (loops, editor, hub, export,
translation, kinds) is defined as a function of what is written here.

## 1. Thesis

A codebase has two texts: the code, and the intent behind it. codoc keeps the
intent text as a document a person can read and edit, joins it to the code by
bindings, and keeps the two in step in both directions while three parties edit
the document concurrently: a person, a coding agent, and codoc's own reflective
loop.

The substrate is an **attributed edit calculus**: a small set of operations over
a document of identified blocks, where every character carries the operation
that inserted it, and every surface the product shows or acts on is a function
of the operation log. Nothing is decided by comparing two snapshots. A diff may
be used to draw, and it is used exactly once to decide, at one boundary, under
rules stated in §4.

Four consequences shape every decision below:

- **Identity is minted, never recovered.** Blocks and characters get ids when
  created and keep them through every edit. A missing id is reported, never
  guessed.
- **Provenance is written at the write.** Who wrote a character is recorded by
  the op that wrote it. There is no attribution pass.
- **One applier, one order, one truth.** A single daemon serializes ops. The log
  is the truth; the store is a cache rebuilt by replay.
- **No op mutates what it did not name.** Apply never cascades. Consequences a
  gesture wants (promote the children of a deleted heading) are ops the gesture
  emits, in the same batch, so the ledger says why each thing moved.

## 2. Types

```
Session  := str                         # one daemon lifetime on one checkout; see §7
OpId     := (session: Session, seq: int)   # globally unique, stable across merges
CharRef  := (op: OpId, k: int)          # the k-th Unicode scalar inserted by op
BlockId  := str                         # "b-" + 12 hex, minted by the creator
PlanId   := str                         # "p-" + 12 hex
Actor    := "human:<id>" | "agent:<id>" | "loop" | "translator:<lang>" | "import"
Frontier := { Session: int }            # per-session max seq; a set of ops, compactly

Seg      := { op: OpId, k0: int, len: int, actor: Actor, at: HLC,
              plan: PlanId?, dead: OpId? }
Text     := { segs: Seg[], marks: Mark[] }      # segs in document order, live and dead
Mark     := { kind: MarkKind, attrs, from: CharRef, to: CharRef }   # inclusive
MarkKind := bold | link{href} | cite{file, symbol} | comment{block} | suggest_del{plan}

Block    := { id: BlockId, kind: Kind, rank: Rank, level: int, content: Content, dead: OpId? }
Content  := JSON value whose schema is declared by `kind`; any field may be a Text
Rank     := fractional base-62 string
Kind     := "heading" | "paragraph" | plugin kinds

Doc      := Block[] ordered by rank, live and dead
Feature  := a live heading block h with the live blocks it dominates:
            every following live block until the next live heading with level ≤ h.level
FeatureId := BlockId of the heading

Binding  := { feature: FeatureId, file, symbol_path, fingerprint, types_hash }
Plan     := { id: PlanId, actor, kind: edit | add | move | delete, target: BlockId?,
              note, status: open | accepted | rejected }

Op       := { id: OpId, at: HLC, actor: Actor, batch: str, kind: OpKind,
              target, payload, base: Frontier, plan: PlanId?, caused_by: OpId?,
              inverse: payload, conflict: str? }
```

Notes on the types:

- **`OpId` is global.** The applier also assigns a local `pos` for ordering, and
  `pos` is never written into a payload. After a merge of two branches' logs
  (§7) every op keeps its id, so every anchor, frontier and subset it is named
  in stays valid.
- **`Text` is one sequence of live and dead segments.** A segment is Etherpad's
  attribute run with Peritext's addressing. A dead segment is a positioned
  tombstone: it keeps its place in the sequence forever in the truth, so an
  insert after a deleted character, a split at one, or a comment on one still
  has a position. The live text is the concatenation of segments with
  `dead = null`. Segments carry `actor` and `at` so a snapshot (§7.5) is
  self-describing without the archived ops.
- **`k` counts Unicode scalars of the NFC form**, not UTF-16 units. The
  applier normalizes on `text.insert` and `ingest`; a client computing anchors
  into text it is about to send normalizes first or its `k` is off by the
  composition delta. The TypeScript side converts at its boundary.
- **Marks are ranges over character identity**, inclusive, with no id of their
  own but with an `actor`: the directive builder needs "who bolded this", since
  a person's bold is a Focus line and the loop's is not. A comment's identity
  is its `block`; a `suggest_del`'s is its `plan`. A
  mark split by `block.split` becomes two marks with the same kind and attrs, and
  `block.merge` rejoins adjacent ones. Growth at the edges is per kind: `bold`
  grows after (typing at the end of bold text stays bold); `link`, `cite`,
  `comment`, `suggest_del` do not grow (I2).
- **A feature is its heading block**; one id space for features, blocks and
  everything keyed on them. A dead heading is not a feature, but it remains
  addressable (for undo, for the removal directive, for History).
- **Hierarchy is the heading level**, gaps tolerated: a heading deeper than the
  preceding one is its child, whatever the numeric difference. `level` is
  clamped to `[1, 6]` on the named block only. There is no cascade.
- **A plan is a tag.** Segments minted by an op with `plan=pid` are suggested
  insertions; characters a plan wants gone carry a `suggest_del{pid}` mark and
  stay live until accept. One document; live, all-accepted and all-rejected are
  three read modes.
- **`base` is the client's frontier** when it computed the op: advisory for
  landing (anchors are identities), authoritative for `ingest` (§4) and for the
  projection's "which ops has this client not seen".

## 3. Operations

Nineteen, plus one internal. Each is primitive because no composition of the
others expresses it without re-minting identity, and the set covers every
gesture in `02-workflows.md`. Adding one is a design change argued in this file.

Two were added by the implementation: `text.restore` and `block.restore`
resurrect dead segments and dead blocks *with their identity*. They are the
inverses of the two deletes, and they had to be ops rather than internal
effects because an undo is a new op by the person (§3.1) and a re-insert would
mint new characters, orphaning every mark and comment anchored to the old
ones. `plan.reopen` is internal: the inverse of a verdict, never sent by a
client, present in the log only through undo.

| op | inverse |
|---|---|
| `text.restore (bid, path) {segments, by?}` | `text.delete {segments}` |
| `block.restore bid {ids, by?}` | `block.delete {ids}` |
| `plan.reopen pid {from, …}` | the verdict it undid |

| op | target | payload (wire) | canonical payload (stored) | inverse |
|---|---|---|---|---|
| `text.insert` | (bid, path) | `after: CharRef \| START`, `text` | same | mark the minted segment dead |
| `text.delete` | (bid, path) | `from: CharRef`, `to: CharRef` | the explicit live segments `[(op, k0, len)]` the range covered at apply | mark those segments live |
| `text.mark` | (bid, path) | `kind, attrs, start, end, on` | same, plus `added` / `removed` (the marks it placed and displaced) and, on a restore, the original `actor` / `op` | turn the added marks off, put the removed ones back |
| `block.add` | bid | `kind, level, after: BlockId \| START, content` | rank assigned | mark dead |
| `block.set` | (bid, path) | `value` for a non-Text field (`kind`, `level`, an asset ref, a status) | same | previous value |
| `block.split` | (bid, path) | `at: CharRef \| START`, `new: BlockId` | same | merge |
| `block.merge` | (into: bid, bid) | | `at: CharRef \| END` (the survivor's last live char) | split at `at` |
| `block.move` | bid | `after: BlockId \| START`, `level_delta` | the range `[bid…]` computed at apply, with each old `(rank, level)` | restore ranks and levels |
| `block.delete` | bid | (or `ids`) | `ids, killed, detached` (the own body computed at apply; the bindings of every killed heading) | `block.restore` the killed ids and re-attach `detached` |
| `bind.attach` | fid | `file, symbol, fingerprint, types_hash` | plus the prior owner it displaced | detach; re-attach the prior owner |
| `bind.detach` | fid | `file, symbol` | plus the hashes | attach |
| `bind.refresh` | fid | `file, symbol, fingerprint, types_hash` | plus previous hashes | previous hashes |
| `plan.accept` | pid | `subset: OpId[]?` | the closure actually accepted | reopen |
| `plan.reject` | pid | | | reopen |
| `feature.handoff` | fid | `covers: Frontier, base_sha, asked, delete_code` | `covers` defaults to the store frontier | previous frontier, naming the hand-off it removes (the one sanctioned regression of I9: a person undoing their own hand-off) |
| `feature.realized` | fid | `covers: Frontier, sha, files, summary` | `covers` defaults to the hand-off frontier | none: realization is monotone |
| `plan.open` | pid | `actor, kind, target, note` | | none: an open plan with no ops is harmless and `plan.reject` closes it |

Semantics beyond the table:

**Text ops resolve characters by identity.** The `bid` is where the client
believes the character is; the applier looks the character up in the
`CharRef → block` index and uses the block it is actually in. A split or merge
under a concurrent text op therefore costs nothing. An unknown `CharRef` (never
minted, or minted by an op this store has not applied) is the only anchor
conflict, and the op is recorded with `conflict` and not applied.

- `text.insert` mints `(op, 0..len)` as one live segment after the anchor
  character, whether that character is live or dead. Two inserts after the same
  anchor land in apply order, each whole; a segment is never interleaved.
- `text.delete` takes a range on the wire and stores the explicit segments it
  covered. Only live characters are covered; a character already dead is not in
  the range and stays dead by its own op. A delete given segments (an undo, a
  redo, a replay) kills exactly those characters wherever they now sit and
  nothing typed between them since. A range whose ends have come to rest in
  different blocks is a recorded conflict: the client re-reads and resends per
  block, because honouring it would mean guessing which blocks lie between.
  Deleting a plan's suggested insertions is an ordinary delete: the plan sees
  fewer of its words survive.
- `text.mark`: `bold` merges with adjacent bold. `link` and `cite` are
  exclusive: turning one on over another displaces it and the inverse restores
  the displaced one. `comment` and `suggest_del` are toggled whole or refused:
  an `on=false` that covers only part of one is a recorded conflict.
- `block.split` with `at = c`: the new block starts with the character after
  `c`; `at = START` makes an empty first half. The id stays with the half that
  has live text; if both do, the first; an empty half never keeps it. The
  inverse of a merge overrides this with `new_takes: head | tail` so the
  revived block gets back exactly the half it held. Splitting a heading yields
  heading + paragraph, never two headings. Marks spanning the cut are split.
- `block.merge` never kills a heading (that would destroy a feature by the
  back door) and never merges prose into a heading, except as the recorded
  inverse of a heading split, which carries `rejoin: true`. The survivor is
  `into`; the other block is marked dead, left empty, and its segments and
  marks MOVE to the survivor (one holder per character identity), so nothing
  is re-authored. `at` is the survivor's last character, live or dead, `END`
  when it was empty; splitting there again restores the sequence exactly.
- `block.move` computes its range at apply: a heading moves with every live
  block it dominates; a paragraph moves alone. `level_delta` applies to the
  headings in the range. Ranks are reassigned between the new neighbours.
  `after` a dead block means at that block's rank.
- `block.delete` covers a block's *own body*: a paragraph alone; a heading and
  the following non-heading blocks. Descendant headings survive; a gesture
  deleting a subtree emits one `block.delete` per descendant heading, and one
  promoting children emits `block.set(level)` per child, all in one batch.
  The applier detaches a dead heading's bindings and records them
  (`detached`); `block.restore` and `plan.reopen` re-attach any whose symbol
  is still free. A dead heading that had bindings stays in the queue view as a
  phantom until a `feature.realized` names it (§6, `directive`). A plan whose
  target heading is dead is not offered for a verdict.
- `bind.attach` enforces I8: a symbol bound elsewhere is detached from its
  prior owner by this op, recorded in the inverse.
- `plan.open` then ops carrying `plan=pid`. Plan ops apply to the live document
  immediately, tagged. A proposed node's `bind.attach` ops are part of its plan
  (rejecting the plan detaches them; reopening restores them). A plan-tagged
  `text.mark` is a proposal like a plan's words: the `rejected` read mode
  (the export, the directive, the model's context) reverses every open plan's
  mark toggles from their recorded `added`/`removed`, `plan_claims` reports
  them as `marked`/`unmarked`, and the verdict decides. A plan may not
  contain `block.delete` or `block.merge`:
  a plan to delete a feature has `kind=delete`, marks `suggest_del` over all of
  the feature's Text, and its accept emits the delete batch. `plan.accept`
  clears nothing on segments (the tag stays as provenance; `Plan.status`
  says accepted), tombstones the characters under its `suggest_del` marks, and
  for `kind=delete` emits the delete. A `subset` must be closed under anchor
  dependency (the minting op of every anchor an accepted op uses); the applier
  computes the closure and stores it; the rejected remainder is undone as
  below. `plan.reject` applies the inverse of every plan op in reverse, with
  three refinements: an insertion is killed by identity wherever its
  characters now sit (a split or merge may have moved them); a block the plan
  added or split off dies when it is now empty; a move is not undone when
  someone re-ranked the block since (their move stands). Marks are removed by
  the op that placed them. Both verdicts record exactly what they changed so
  their inverse, `plan.reopen`, restores it, subset included.
- `feature.handoff` records the `Frontier` of ops it drained. A feature's
  queued work is the set difference between its latest handoff frontier and
  its latest realized frontier, restricted to ops on its blocks. Its held draft
  is the human ops on its blocks beyond the handoff frontier. Frontiers are
  merged by per-session max, so a late `feature.realized` for an earlier
  hand-off never moves anything backwards.
- `feature.realized` is emitted by the daemon, not the agent, when a reflect
  pass that ran because an agent session closed (or was recovered dead) cites
  the hand-off in `caused_by` on a binding op; `files` and `summary` come from
  that pass. A reflect pass triggered by a hand edit cites nothing. A request
  whose implementation moved no bound symbol (a config change, a new file the
  model did not attach) has no evidence, so the owner may write
  `feature.realized` by hand (`codoc done`); the policy allows it to `owner`
  and `maintainer` and to the loop, never to an agent.

### 3.1 Batches

Every op carries a `batch` id. A gesture the editor performs in several steps
is one batch; an `ingest` is one batch; a plan's ops share a batch. Batches are
the undo unit, the review unit in History, and the unit the directive builder
summarizes. The person's undo stack holds only their own batches and their
verdicts; a loop batch is undone only by the explicit "Restore mine" gesture,
which emits the inverse as the person's own batch. Bootstrap and import batches
are not undoable.

## 4. Ingest: the one boundary where a diff decides

A person types ops. An LLM, a translator, a Markdown import, or a `codoc_write_block`
call produces whole new text for a Text field. It enters the calculus through
`ingest`, which runs once per write and whose output is ops.

```
ingest(bid, path, base: Frontier, base_text: str, new_text: str, actor, plan?, caused_by?)
```

1. The applier checks that `base_text` equals the field's current live text.
   If not, the call is refused (`stale_base`) and the caller re-reads: a write
   computed against text someone has since changed is stale by construction,
   whoever changed it. This is the ledger's `base_text` arbitration rule, kept
   in its simplest form; the frontier the diff ran at is recorded as `base` on
   every op the call mints, so History can say what the writer saw.
2. `new_text` is NFC-normalized. A paragraph break inside it is refused: the
   caller addresses blocks, never a whole description, so the loop's prompt is
   block-keyed (each paragraph it may change is named by id, and new
   paragraphs are `block.add` ops it requests by position). This keeps every
   structural decision an op and keeps `ingest` a function of one field.
3. Tokens are script-aware: words, punctuation runs and whitespace runs for
   spaced scripts, one character per token for unspaced ones. The diff is a
   longest common subsequence over tokens in which whitespace never anchors a
   match on its own (it survives only beside a surviving word), so a rewritten
   sentence is one hunk, a word appended after the last word re-authors
   nothing, and a changed comma leaves the word before it its author. Equal
   tokens keep their characters, deleted tokens become `text.delete`, inserted
   tokens become `text.insert` anchored after the preceding surviving
   character, all with this call's actor.
4. (Removed: rule 1 already refuses a write against text that changed since
   the writer read it. Whether a repair may land on the trunk at all while the
   person has pending intent is rule 8, decided by the loop per feature.)
5. Marks survive by identity, and the call is a fixed point: after its ops
   apply, `markdown(field) == new_text`. An existing mark whose surviving
   characters `new_text` still marks the same way is kept and, where the new
   extent is wider, widened by an `on` over the new span (which merges into it
   and keeps its placer); surviving characters `new_text` no longer marks that
   way get an `off`. A `bold` whose end an insert lands on grows over the
   insert (I2's one growing kind), so when `new_text` does not bold those
   words an `off` over them follows the insert. A mark present in `new_text`
   over no existing mark is minted by this call. A trunk call with
   `actor=loop` or `translator` that would remove a human-placed `bold`,
   `link` or `cite` from every character it covers is refused for that block;
   under a plan the person decides, so nothing is refused. An insert whose
   anchor sits inside an existing mark (in sequence order, a dead end
   character included) would be swallowed by it; the call emits an `off` over
   the inserted words `new_text` does not mark, so the fixed point holds for
   every mark shape.
6. An empty `new_text` deletes every live character and leaves the block; the
   caller decides whether to `block.delete`.
7. The call records `deleted_fraction`, the share of the field's live
   characters it tombstoned. The loop's amend gate reads it per block: above
   the threshold for a block whose last live author (open plans excluded) is a
   person (stricter) or anyone else, the amend becomes a plan instead of
   landing on the trunk.
8. While a feature has unsent human ink or is queued, a loop amend on any of
   its blocks becomes a plan regardless of size (the ledger's row 13: the
   document wins). One amend is one plan: every block it repairs, adds or
   strikes rides under the same plan id, so one repair gets one verdict.

Known and accepted: a rewrite that reorders two sentences re-authors the moved
one (its characters die and are re-minted by this call). The gate in step 7
usually turns such a rewrite into a plan; when it does not, History shows a red
copy above a green one, which is true and legible. Diff output may differ
across library versions; it is recorded, never re-run.

## 5. Apply

```
apply(store, op) -> store'
  check authority (§7.3); on failure record conflict and stop
  resolve anchors by identity; on an unknown one record conflict and stop
  record op.inverse from the current state
  mutate the named blocks, segments, marks, bindings, plans, frontiers
  store.applied += op.id; store.pos += 1
```

`apply` is total (every op is stored, applied or with a recorded conflict),
deterministic, and O(segments of the touched blocks). `replay(log)` folds
`apply` over the log in the order of §7.2 and reproduces the store; the store
records the set of op ids it has applied so a replay resumes.

## 6. Derived views

Pure over the store, cached by the keys stated.

| view | definition | cache key |
|---|---|---|
| `text(field)` | live segments concatenated | per block, on write |
| `tree` | live headings in rank order folded by level; a heading deeper than the preceding one is its child | structural head (advances on add/delete/move/set kind or level) |
| `description(f)` | `f`'s own live paragraphs (before the next heading), joined by blank lines; a child's prose is the child's | structural head + the blocks' heads |
| `attribution(field, window)` | segments since `window`, merged when adjacent segments share actor, plan and dead-ness, snapped to `doclang.tokens` boundaries for display only | on read |
| `ink(f)` | human live segments on `f`'s blocks beyond the latest handoff frontier (unsent) or within it and beyond the realized frontier (sent) | |
| `ground(f)` | loop segments and loop-killed dead segments on `f` since the person last engaged with it: the later of the latest hand-off and their newest op on its blocks (in apply order). A feature the person never touched paints nothing: there is no "since". | |
| `plan(f)` | live segments with `plan` set whose plan is open, plus `suggest_del` marks | |
| `queued(f)` | the hand-off frontier is not covered by the realized frontier (`covered(handoff, realized)`, one definition for phase, the queue file and phantoms); a feature being edited or built right now is still queued | |
| `phase(f)` | `edited` (unsent ink) · `realizing` (lease, the one ephemeral input, read from `status.json`) · `queued` · `planned` (queued and unbound) · `proposed` (open plan on f) · `drifted` · `synced`, first that holds. Doc wins: the person's unsent edit shows even while an agent holds the lease. `drifted` means the newest op among `bind.refresh`/`bind.detach` on `f`, text ops on its blocks (any actor) and `feature.realized` is a binding change; a `bind.attach` is a relocation, not drift | |
| `directive(f)` | rendered from the all-rejected read mode of `f`'s text plus the person's ink, with the ops between the frontiers: Focus from `bold` marks a person turned on since the last realization (each by its own placing op), Consult from `link` marks, `Author asked` from the hand-off's `asked`; for a dead heading with a `delete_code` hand-off, "remove". `Edit only:` returns with comments (a `cite` inside an open comment span); until then the scope is the binding set | |
| `history(f)` | every op recorded against the heading or a block `f` currently dominates, by the block the op actually landed in, conflicted ops included with their code; ops on blocks `f` dominated at the time but no longer does are omitted | |
| `timeline(pos)` | un-apply from the live store over a bounded window, per block | |
| `dead_cites` | `cite` marks whose `(file, symbol)` is absent from the index | index head |
| `lang(block)` | `doclang.detect_prose_language` of its live text when it differs from the workspace language | per block |
| `export` | Markdown in the `rejected` read mode (an open plan is not the document), `## Title {#b-id}`, paragraphs, kind fences, marks as written with `* [ ] \ # \`` escaped where they would read as markup; comments omitted. `import(export(s))` rebuilds `s` | structural head + block heads, debounced |
| `projection` | per live block `{id, kind, level, rank, head, text, segs (live, plus dead inside the display window), marks, aria}`; `aria` is a per-segment description for screen readers | per block |

## 7. Sessions, order, authority, and the log

### 7.1 Sessions

A session is one daemon lifetime on one checkout, identified by a random id.
The daemon writes `log/<session>.jsonl`, appending each op after it is applied.
A session closes and a new one opens on: daemon exit, a git commit, or a change
of `HEAD` (branch switch, checkout, rebase); on a `HEAD` change the daemon first
rebuilds from the logs now on disk, since a checkout or pull may have changed
their set. One daemon per session file, many actors: the daemon stamps `actor`
on every op from the client's authenticated identity, so one file carries the
person, the loop and the agents in the order they wrote. A file with a
non-monotone `seq` or a malformed op is rejected at load with a report;
authority is checked per op at apply and again at replay.

### 7.2 Order

Live: apply order. Replay: for a single session file, `seq`. Across files, the
merge order is `(at, session, seq)`, and a rebuild after a git merge is
deterministic given the same set of files. The daemon advances its HLC past the
maximum `at` it has replayed, so an op typed after a pull is later than what it
anchors to. Only the applier stamps `at` (a client submission carries none),
and a log on disk is the truth, so nothing is refused at replay: a peer's fast
clock moves this daemon's clock forward too, and the order stays total. The
hub, which accepts session files from strangers, is where a stamp far ahead of
wall time is rejected, before the file is merged.

### 7.3 Authority

Roles are decided outside the calculus and recorded where every clone reads
them: the tracked `config.json` (`roles: {actor: role}`; the hub writes a
contributor's role there when they sign in, under `human:gh:<login>`, which no
local identity can collide with; the local checkout's `human:local` and
`human:import` are `owner` unless it says otherwise). The check happens when an
op **arrives**, and the answer is recorded on the op: a replay honours what was
recorded rather than re-asking, so `replay(log)` is the same store on every
machine and at every later state of the table (I6), and reducing someone's
access tomorrow never deletes what they wrote today. A log from a stranger is
judged at its own door instead: the hub validates a pull request's session
files against its table before merging them. The applier checks, per op and
with the store in hand, that the actor's role allows it: `owner` and `maintainer` may write everything; `machine` (agents,
the loop, translators, imports) may write the trunk and plans but no verdict
and no hand-off, with one exception, the loop's own `feature.realized`, which
is the daemon settling evidence; `suggest` may open plans and write ops tagged
with a plan the same actor opened (riding someone else's open plan would land
a stranger's words when the maintainer accepts it). A machine may write into
any open plan (an agent extending the loop's or a contributor's proposal is a
proposal still). A disallowed op is recorded with `conflict="authority"` and
not applied, so a PR that carries a forged `plan.accept` in its session file
has no effect on rebuild. The hub validates a PR's log files before merging.

### 7.4 Idempotency and provisional anchors

A client submits ops through its inbox file with a client-side id; the daemon
acks with the assigned `OpId`. A resubmitted client id is acked with the same
`OpId` and not applied twice.

A client cannot know the `OpId` of characters it inserted a moment ago and has
not yet seen acked, so a submission may anchor to them provisionally as
`["$cid", "<client id>", k]`, the k-th character of the insert that submission
minted. The daemon resolves these against every ack it has issued to that client,
in order; an unresolvable one fails as an unknown anchor. This is what lets a
burst of typing land as one chain of ops without a round trip per keystroke.

### 7.5 Snapshots

`codoc snapshot` writes `log/snapshot-<hlc>.jsonl`: one `block.add` per live or
dead block carrying its full `Text` (segments with actor and at, marks) plus the
bindings, the plans (with the ops they owned, now archived, and their verdicts)
and the frontiers (with the words the person asked for), and the `Frontier` it
folded. Earlier files move to `log/archive/`. Replay applies the snapshot
first — **without the authority check**, since a fold re-expresses ops that
were authorized when they were written and is not a new decision — and then
every op not inside its frontier, with the check on, regardless of `at`, so a
branch forked before the snapshot merges correctly. `state_fingerprint` is
equal across a fold. The archive stays readable for the timeline. A snapshot
is therefore as trusted as the log it replaces: the hub refuses a pull request
that introduces or changes one.

### 7.6 Privacy

`feature.handoff.asked` is the person's prompt and is committed with the log.
`config.json` `privacy.prompts = "text" | "hash"` chooses whether the log holds
the text or a hash whose text lives in the ignored cache; the default is `text`
for a private repository and `hash` when `init` detects a public remote.

## 8. Invariants

- **I1 One author per character.** A segment names one op and one actor.
- **I2 Suggestion and comment anchors do not grow.** A character typed at the
  edge of a `suggest_del`, `comment`, `link` or `cite` span is outside it.
- **I3 No cascade.** An op mutates only the blocks it names or its computed
  range; no apply changes a level it was not asked to.
- **I4 Unique block ids.** Split and add mint; a duplicate `block.add` is a
  recorded conflict. The editor's repair pass enforces this for ProseMirror's
  copy paths before submission.
- **I5 Anchors are never re-guessed.** An unknown character or block yields a
  recorded conflict, never a nearest match.
- **I6 The store is a function of the log.** `replay(log) == store` on every
  apply path.
- **I7 Documentation never writes code.** No op touches source files; a
  directive is derived; realization is a gesture.
- **I8 Binding uniqueness.** `(file, symbol_path)` has at most one live owner;
  `bind.attach` displaces and records.
- **I9 Frontiers are monotone.** Per feature, each watermark only grows.

## 9. Tests that pin the calculus

- `apply ∘ inverse = identity` for every op kind over random states.
- `replay(log) == store` after every apply path, including conflicts.
- Random op sequences preserve I1, I4, I8, I9.
- `ingest(new_text)` then `text == new_text`, with marks preserved by identity.
- `export → import → export` is a fixed point.
- `replay(A ++ B) == replay(B ++ A)` up to the `conflict` field when the two
  sequences' anchors are disjoint.
- Apply touches only the named blocks (counted).
- Python and TypeScript agree on scalar indexing and on projection rendering.
- `timeline` un-apply equals prefix replay over the window.
- Split and merge fuzzing with marks spanning the cut.
