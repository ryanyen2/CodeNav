# codoc v2 — architecture

This is the working map of the code. The design and its justification live in
`docs/substrate/` (`01-calculus.md` is normative; `02-workflows.md` walks the
user flows; `03-extensions.md` says what a new block kind is;
`04-version-control.md` says what git sees; `09-editor.md` says how ProseMirror
maps onto the calculus). `CLAUDE.md` is the one-page version; `CONCEPTS.md` the
glossary.

## 1. Thesis

codoc keeps a document of **features** (a heading plus paragraphs, citing code
inline) in step with a codebase in both directions while three parties edit it
concurrently: a person, a coding agent, and codoc's own reflective loop. Every
surface (the editor, the export, the agent's prompt, the status bar) is a
function of one append-only **op log**. Every character carries the op that
inserted it, so "who wrote this word" is read, never reconstructed.

The one rule that binds the design: **a diff may draw; it never decides what
happened.** The single exception is `ingest`, the boundary where a whole-text
write (an LLM repair, a translation, an import) becomes ops, once, under
stated rules, and whose result is a fixed point (`markdown(field) == new_md`).

v1 recovered edits by diffing a settled document against a cited baseline and
needed ten channels and a dozen heuristics (settle, resurrect, phantom retire,
three-baseline settlement) to stay upright. v2 records instead.

## 2. The calculus (`codoc/calculus/`)

### 2.1 Types (`ids.py`, `text.py`, `doc.py`, `ops.py`)

| type | shape | notes |
|---|---|---|
| `OpId` | `"<session>:<seq>"` | unique because a session id is random and one daemon writes it; never renumbered |
| `CharRef` | `(op, k)` | the k-th Unicode scalar the op inserted; on the wire `["op", k]`, or `"START"` |
| `Seg` | `op, k0, chars, actor, at, plan?, dead?` | a run of characters one op minted; a dead segment keeps its place forever (a positioned tombstone) |
| `Mark` | `kind, attrs, start, end, actor, op` | a range over identity; `bold` grows after its end (Peritext "after"), `link`/`cite` are exclusive, `comment`/`suggest_del` toggle whole only |
| `Text` | `segs, marks` | one field; every mutation is by identity, offsets exist only at the edges (`ref_at`, `offset_of`) |
| `Block` | `id, kind, rank, level, content, dead?` | `heading` / `paragraph` / a plugin kind; `rank` is a fractional order key; content maps field → `Text` or JSON |
| `Op` | `id, at, actor, batch, kind, target, payload, base, plan?, caused_by?, inverse, conflict?` | `at` is an HLC string; `payload` is canonical after apply; `inverse` is a list of op bodies applied in order |
| actor | `human:<who>` · `agent:<which>` · `translator:<lang>` · `loop` · `import` | a closed grammar; `actor_kind()` is what policy and the loops decide by |

A feature *is* its heading block: it dominates every following block until a
heading at the same or a shallower level; its **body** is itself plus the
paragraphs before the next heading. Membership is position, so a heading typed
above prose re-parents it and there is no `ownerId`.

### 2.2 The twenty ops

```
text.insert / delete / restore / mark
block.add / set / split / merge / move / delete / restore
bind.attach / detach / refresh
plan.open / accept / reject / reopen   (reopen is the inverse of a verdict; it reaches the log only through undo)
feature.handoff / realized
```

- **Plannable** (may carry `plan=pid`): insert, mark, add, set, split, move,
  and `bind.attach` (a proposed node's bindings are part of the proposal).
  A plan never destroys: its deletions are `suggest_del` marks.
- **Verdicts** (`plan.*` verdicts, `feature.handoff`, `feature.realized`):
  owner or maintainer only; the loop may write its own `feature.realized`.
- `block.delete` covers a block's own body; descendant headings survive (the
  editor's delete gesture promotes them in the same batch). A heading never
  dies by merge.
- `feature.handoff` / `feature.realized` carry **frontiers** (per-session max
  `seq`); queued work is `not covered(handoff, realized)`.

### 2.3 Apply (`apply.py`, `store.py`)

`apply(store, op)` is total and two-phase: it resolves every anchor, block and
mark before the first mutation, so a conflict never leaves a partial change.
On success the op leaves with its canonical payload and its inverse; on
failure with `conflict` set (`unknown_anchor`, `dead_block`, `authority`,
`merge_heading`, `plan_not_open`, …) and the state untouched. Every op is
recorded either way; `replay(log) == store` (I6) is a property test.

The `Store` is a cache of the log with the indexes the views need: `by_id`,
`position`, `char_index` (minting op → holders), `mark_index`, `ops_by_block`
(block → positions of the ops recorded against it, the canonical holder of an
applied op), `rank_head`, `block_head`, `structural_head`, plus `bindings`,
`plans`, `watermarks_by_fid`. `state_fingerprint()` is the observable state.

Verdicts act by identity: `plan.reject` kills a plan insert wherever a split or
merge moved its characters, removes marks by their placing op, lets a block
the plan added die if it is now empty, reverts a set, restores a move unless
someone re-ranked the block since, and detaches a planned attach. `plan.accept`
takes a `subset` closed under anchor dependency, undoes the rest, and
tombstones the words under its `suggest_del` marks. Both record exactly what
they changed so `plan.reopen` restores it.

### 2.4 Ingest (`ingest.py`, `inline_md.py`)

`ingest(text, base_text, new_md, actor, plan?) → Ingest(bodies, deleted_fraction, refused?)`:

1. `base_text` must equal the field's current live text, else `stale_base`.
2. A paragraph break in `new_md` is refused: callers address blocks.
3. Tokens are words and whitespace runs (one character per token for unspaced
   scripts); whitespace is diff junk, so an appended word re-authors nothing
   and a rewritten sentence is one hunk. Each hunk is one `text.delete` and
   one `text.insert` anchored after the last surviving character.
4. Marks survive by identity and the call is a fixed point: existing marks are
   widened (`on` over the new span, merging into the old mark and keeping its
   placer) or shrunk (`off` over survivors no longer marked); a bold an insert
   lands on is taken off the inserted words; new spans are minted. A trunk
   write by `loop`/`translator` that would remove a person's bold, link or
   cite from every character it covers is refused (`would_drop_human_*`).
5. `deleted_fraction` feeds the caller's gate.

`inline_md` is the exact grammar of prose Markdown: `**bold**`,
`[label](https://…)`, `[label](codoc:file#Symbol)`, with `\` escapes for
`* [ ] \ # \``; bold may sit inside a label; `parse(render(t, spans))` is the
identity for every state the store can hold.

### 2.5 Derived views (`derive.py`, `export.py`)

Pure over the store: `markdown(text, mode)` in `live` / `rejected` / `accepted`
read modes; `description`, `title`, `tree`; `attribution` (runs);
`ink` (human segments beyond realized: `unsent` past the hand-off, `sent`
between, merged by stage); `ground` (loop adds/removes since the person last
engaged with the feature); `plan_claims`; `phase` (`edited · realizing ·
queued · planned · proposed · drifted · synced`, first that holds, doc wins);
`directive` (the realize prompt item: rejected-mode text, "Changed since",
Focus from a person's new bolds, Consult from links, "Author asked");
`history` (ops on the blocks the feature currently dominates, conflicts
included); `projection` (what the editor renders). `export_markdown` renders
`tree.codoc` in the `rejected` mode (`## Title {#b-id}`, paragraphs, kind
fences) and `import_markdown` is its inverse on a fresh store.

### 2.6 Invariants and the tests that pin them

I1 one author per character · I2 suggestion and comment anchors do not grow ·
I3 no cascade · I4 unique block ids · I5 anchors are never re-guessed · I6 the
store is a function of the log · I7 documentation never writes code · I8
binding uniqueness · I9 frontiers are monotone. `tests/calculus/` holds the
property tests (inverse ∘ apply, replay, random op soups, split/merge fuzz,
ingest fixed point fuzz, export round trip); `tests/substrate/test_review2.py`
pins the second review's findings (rebuild through verdicts and undo,
authority table, inbox durability, one plan per amend).

## 3. The substrate (`codoc/substrate/`)

**One applier per checkout: the daemon.** Everything else writes through the
inbox, or applies itself when no daemon runs (`client.write` / `client.ingest`
take the same path as the daemon's drain).

| module | role |
|---|---|
| `clock.py` | hybrid logical clock; the string sorts lexicographically in Python, SQLite and JS; `observe` advances past every replayed stamp and refuses nothing (a committed log is the truth) |
| `log.py` | `log/<session>.jsonl` (header line, then ops as recorded), validated at load (monotone `seq`, well-formed non-decreasing `at`); `replay_order` merges files by `(at, session, seq)`; the inbox: `inbox/<client>.jsonl` submissions and `inbox/<client>.ack.jsonl` acks, whole lines only, malformed lines acked with an error |
| `workspace.py` | the façade: `rebuild()` (snapshot, then every op outside its frontier), `submit(bodies)`, `plan_ingest` / `commit_ingest` / `ingest` (the gate that turns a large trunk write into a plan), `drain_inbox(identity)` (provisional `["$cid", cid, k]` anchors resolved against acks in the same drain), `rotate_session`. `Roles.policy(store, op)` is the authority check: owner/maintainer everything; machines the trunk and plans but no verdicts (the loop's own `feature.realized` excepted); suggesters `plan.open` and their own plans |
| `daemon.py` | per debounced batch: rebuild + rotate the session when `HEAD` moved; drain inboxes; read the agent epoch (`activity.json`: while a session is open, code changes are held; when it closes, one scoped reflect pass runs and settles the hand-offs it cited); else reflect the changed files; publish unless nothing changed |
| `publish.py` | `projection.json`, `history.json` (the timeline window: the last 200 ops with each touched block's text after them and their recorded inverses), `tree.codoc`, `realize.md`, `status.json`, phases computed once; the realizing lease lives in `status.json` and decays by mtime |
| `snapshot.py` | fold the logs into `log/snapshot-<hlc>.jsonl` recording the frontier it folded; older files move to `log/archive/` |
| `import_v1.py` | a v1 `codoc.db` becomes one import session in the person's name |

### Files under `.codoc/`

| file | role | git |
|---|---|---|
| `log/<session>.jsonl`, `log/snapshot-*.jsonl`, `log/archive/` | the truth | tracked |
| `tree.codoc` | Markdown export, rejected mode; a merge conflict in it is meaningless (regenerate) | tracked |
| `config.json` | `doc_language` and other settings that must travel with the repo | tracked |
| `projection.json` `history.json` `status.json` `realize.md` `activity.json` `ask.json` | derived or ephemeral | ignored |
| `inbox/*.jsonl` | client channels | ignored |
| `cache/graph.json`, `cocoindex.db`, `lancedb/`, `index.meta.json` | the code index and graph | ignored |

## 4. The loops (`codoc/loops/`)

### 4.1 Code → document (`reflect.py`)

Given a `ChangeSet` from the index (`codoc/loop/diff.py`):

1. **Deterministic binding maintenance**: `bind.refresh` for modified bound
   chunks, `bind.detach` for removed ones, `bind.attach` for relocations
   (same fingerprint moved; or a unique AST shape among everything removed
   matched with a unique shape among what arrived).
2. **Emptied features** open `delete` plans (the loop proposes, never deletes).
   The rule is state-based, not edge-triggered: a live feature that once had
   code, has none now, and has never been proposed for deletion, unless it is
   held (`edited`/`queued`/`planned`), in which case the next quiet pass
   offers it. A rejected proposal is an answer, so it is never asked twice.
3. **One LLM pass** (`agent/tree_update.py`, `prompts/tree_update.txt`) over
   the added and modified chunks with an ego-graph context
   (`context.select_context`). Its answer is validated (`validate_raw`) before
   any write. `attach` lands on the trunk (attribution, not intent); `add_node`
   and `move_node` open plans; `amend` is block-keyed (`{id, text}` repairs,
   `{id, delete: true}` strikes, `{text}` adds a paragraph, omission means
   unchanged) and lands on the trunk with `actor=loop` when every part is a
   small repair to a feature with nothing pending, else becomes **one plan**;
   the gate is per block (`GATE_HUMAN` 0.15 when a person last wrote the block,
   `GATE_MACHINE` 0.5 otherwise).
4. **Coverage net**: an unplaced chunk lands with the feature owning most of
   its graph neighbours, else in a plan for a node named after its file.

When the pass runs because an agent session closed (`under_epoch`), every op on
a feature with queued work carries `caused_by` its latest hand-off; a pass
triggered by a hand edit cites nothing.

### 4.2 Document → code (`realize.py`)

No edit inference. `handoff` records `feature.handoff` (the frontier drained,
the base commit, the person's own words, `delete_code` for a removed feature).
`queued` is the watermark fact. `write_queue` renders every queued directive
into `realize.md` (`prompts/realize.txt`), which `/codoc:sync` or `codoc
realize` implements. `settle(ws, res)` records `feature.realized` when a pass
under a closed epoch cited the hand-off on a binding op; `codoc done` is the
owner's escape hatch for a request that moved no bound symbol. `withdraw`
applies the hand-off's recorded inverse as the person's op.

### 4.3 Bootstrap and translate

`bootstrap.py`: per-file LLM calls propose features (with a project brief from
the README and commit rationales), an organization pass nests them; every
paragraph enters through `ingest` so bootstrap prose is attributed to `loop`.
`translate.py`: one LLM pass per batch rewrites titles and paragraphs into the
workspace language through `ingest`, so citations, links and a person's bolds
are kept by identity or the block is refused; previous wording stays in the
log.

## 5. The agent (`codoc/agent/`, `codoc/mcp/`, `codoc/plugin/`)

Claude Code hooks (`agent/hook.py`) open and close an **epoch** in
`activity.json` around each session and record the files it touched; the
daemon holds code changes during an epoch and reflects once when it closes
(a hard-killed session is recovered after `EPOCH_STALE_SECONDS`). The MCP
server (`mcp/server.py`, FastMCP over stdio) exposes `codoc_context`,
`codoc_tree`, `codoc_status`, `codoc_history` (reads), `codoc_reflect`
(attach/amend through the inbox as `agent:claude-code`), `codoc_propose`
(plans), `codoc_realize_progress`, and the walkthrough trio. `/codoc:sync`
reads `realize.md`, implements, and reflects; `/codoc:plan` proposes.

Agent writes are ops like anyone else's: the actor is `agent:<name>`, the
policy denies verdicts, and a prose repair goes through `ingest` under the same
gate as the loop's.

## 6. The editor (`vscode-codoc/src/`)

The webview is a projection of the store plus an op emitter (`09-editor.md`).

- `calc/` is pure and vitest-covered: `projection.ts` (types), `shadow.ts` (a
  block's live characters with their `CharRef`s, advanced by unacked local ops
  with provisional refs), `recorder.ts` (ProseMirror steps → op bodies; one
  confined fallback for steps it cannot name), `adopt.ts` (per-block
  replacement plan given unacked sets and `head`), `claims.ts` (runs, marks,
  phases → paints: ink, ground, opacity, authorship).
- `editor/`: `schema.ts` (heading, paragraph, marks; `plan` and `suggest_del`
  are painted, never sent), `commands.ts` (split, merge, makeHeading,
  makeParagraph, relevel, moveBlock, deleteFeature; each emits its op and
  performs the equivalent ProseMirror change tagged `RECORDED`), `editor.ts`
  (mount, record, submit in debounced batches, ack, adopt per block, conflict
  re-adoption), `decorations.ts`, `find.ts`, `menus.ts` (`/` blocks, `@`
  citations), `drag.ts`, `app.ts` (tree pane, toolbar, hand-off ⌘S, verdicts,
  History, language switcher).
- `host/provider.ts` reads `projection.json` and `status.json`, appends
  submissions to `inbox/ide.jsonl`, tails `inbox/ide.ack.jsonl`, opens cited
  code, writes `config.json`; `extension.ts` keeps the daemon running as a
  reconciled invariant.

ProseMirror history is disabled: undo is the recorded inverse applied as a new
op. A heading never merges with prose and disappears only through the delete
gesture, so "why is this heading absent" never arises.

## 7. Extensibility (`03-extensions.md`)

A new block kind is a `Kind` registration: a JSON schema for its non-Text
content, `text_view` (which fields are attributed Text), optional `lift` (code
→ content, run by the loop), `lower` (content → code, run at realize),
`consult` (what the agent reads), `export`/`import_` (its fence in
`tree.codoc`), `write_back` (what `codoc_write_block` may change). Text fields
get attribution, marks, plans, translation and History for free; comments are
a kind whose anchor is a `comment{block}` mark.

## 7.1 The hub (`codoc/serve/`, `docs/serve-deployment.md`)

`codoc serve` claims the repo (`serve.lock`), supervises one daemon, and
serves the same editor bundle over HTTP: a GitHub session maps the visitor's
collaborator permission to a role in the tracked `config.json`, their edits
become submissions in `inbox/hub-<login>.jsonl` (the daemon stamps
`human:<login>` and applies them under that role), and one SSE feed carries the
projection, status, their acks and the timeline window. `worker.py` turns each
uncovered hand-off into a pull request from a sandboxed agent on a worktree
(`sandbox.py`, `realize_agent.py`, `consult.py`: no shell, edits confined to
the feature's bound files, secrets unreadable, tokens scrubbed, a post-run
scope gate), and `ratelimit.py` bounds writes per login.

## 8. Version control (`04-version-control.md`)

One session file per daemon lifetime; a session closes on every commit and
change of `HEAD`, so two branches never share a file and git merges by adding
both. Replay order is `(at, session, seq)`; ids survive merges; ops naming
characters a store never minted record `unknown_anchor` and History says so.
`tree.codoc` is committed for review and for people without codoc; it is
derived, so regenerate on conflict.

## 9. Environment variables

| variable | effect |
|---|---|
| `CODOC_PROVIDER`, `CODOC_MODEL`, `CODOC_MODEL_FAST`, `CODOC_BASE_URL`, `CODOC_TEMPERATURE`, `CODOC_MAX_TOKENS`, `CODOC_REASONING_EFFORT`, `CODOC_LLM_TIMEOUT` | the LLM used by reflect, bootstrap and translate (`config.py`); `claude` reuses the Claude Code login, `openai` needs `OPENAI_API_KEY` (`ANTHROPIC_API_KEY` for the SDK engine) |
| `CODOC_LOG_PROMPTS`, `CODOC_VERBOSITY` | debugging |
| `CODOC_EMBED_CHUNKS`, `CODOC_EMBEDDER_PROVIDER`, `CODOC_EMBEDDER_MODEL`, `CODOC_LANCE_PATH`, `CODOC_INDEX_SOURCE`, `CODOC_INDEX_RETENTION_S` | the code index (`pipelines/indexing/`) |
| `CODOC_AGENT`, `CODOC_EPOCH_ORIGIN`, `CODOC_NO_STOP_REFLECT` | how the hooks name the agent and whether the Stop hook reflects |
| `CODOC_DOC_LANGUAGE` | per-process override of `config.json`'s `doc_language` |

## 10. Tests

`python -m pytest tests/` (calculus, substrate, loops, cli+mcp, graph,
doclang; all deterministic). `cd vscode-codoc && npx vitest run && npx tsc
--noEmit && node esbuild.config.mjs`. Adversarial reviews of each stage and
their probe files are under `docs/substrate/` (`07`, `08`, `10`).
