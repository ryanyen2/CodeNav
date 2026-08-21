# codoc architecture (detailed reference)

The deep internals behind the summary in `CLAUDE.md`. Read `CLAUDE.md` first for
the overview, commands, the `tree.codoc` surface, and the package map; this doc
is the reference for the data model, the two loops in detail, storage, the
control files, environment variables, and the deployed hub.

> **History:** this repo was rewritten clean-slate (2026-05). The old
> reflective/planning/realize/health pipelines, the transaction/constraint model,
> the FastAPI server, and the 20-command CLI were deleted. The cocoindex+LanceDB
> index, the tree-sitter adapters, and `core/tree_walk.py` were kept as substrate.

## Data model

- **`Feature`** (`model/feature.py`): `{id, title, description, parent_id, lifecycle,
  created_at, updated_at}`. `id` = stable `f-xxxxxxxx`, rendered as `⟨f-id⟩`. ONE
  prose field. **`lifecycle`** (`Lifecycle` enum: `planned | active | retired`) is
  the single authoritative named state (Proposal A1); `retired`/`realized` remain as
  derived read-only `@computed_field` views, and the legacy `retired=`/`realized=`
  kwargs still construct via a validator. A `planned` placeholder (an accepted
  `/codoc:plan` node with no code) is promoted `planned→active` by `feature.realize()`
  / `store.mark_realized` on its first binding — the one explicit transition point
  (guarded to `planned` rows). The DB keeps the `lifecycle` column authoritative +
  the `retired`/`realized` columns in sync, and a pre-A1 db backfills `lifecycle`
  from the bools on open.
- **`Binding`** (`model/binding.py`): `{id, feature_id, file, symbol_path,
  fingerprint, types_hash, updated_at}`. Anchor `(file, symbol_path)` is the index
  join key; `fingerprint` = the chunk's `tokens_hash`; `types_hash` = its
  name-invariant AST shape (drives rename detection; backfilled when missing).
- **`NodeOp`** (`model/event.py`): `{kind, feature_id?, parent_id?, title?,
  description?, bindings, rationale, realized?}`.
- **`Event`** (`model/event.py`): `{id, at, source, op, applied, accepted_at,
  actor, mode, caused_by}`. A *proposal* is an Event with `applied=False`. The last
  three are the **change ledger** (`docs/codoc-change-ledger.md`): `actor` ∈
  human | agent | loop; `mode` ∈ pen | suggest | auto; `caused_by` = the directive
  / event / suggestion id this change implements. A validator infers actor/mode
  from `source` when absent.
- **`HLC`** (`model/hlc.py`): Hybrid Logical Clock; monotonic, lexicographically
  sortable; the `created_at`/`updated_at`/`at` clock.

### NodeOp kinds

- Safe, auto-applied: `ATTACH DETACH REFRESH AMEND` (AMEND only when the edit is
  small — `AMEND_SAFE_RATIO`, the sole threshold).
- Structural, accepted/rejected via `inbox.json`: `ADD_NODE MOVE_NODE RETIRE_NODE`.
  **Rendering is an in-place overlay**: ADD/MOVE emit a ghost hunk at the
  destination parent; RETIRE/AMEND emit no text and ride in the sidecar
  `proposals` map (the IDE decorates the live node in place), keeping the live
  node byte-identical to a clean render so the round-trip stays a no-op.

## Storage

SQLite WAL at `.codoc/codoc.db` — **3 authoritative tables + 1 derived graph cache**:
`features`, `bindings` (`UNIQUE(file, symbol_path)`), `events` (append-only;
`applied=0` = pending; schema v3 lifts `feature_id` out of the op payload into an
indexed column — backfilled on migrate — so per-feature blame is one lookup:
`store.events_for_feature`, surfaced as the `codoc_history` MCP tool and the
`codoc history` CLI; a directly-applied ADD pre-mints its id in `apply_op` so the
creation event is findable by feature), plus `code_edges` (derived from
`references_in_chunk`, safe to drop and rebuild). No
transactions/constraints/obligations tables.

The chunk index is owned by **cocoindex**, outside `codoc.db`: AST chunks +
identity hashes (tokens_hash / types_hash) in `.codoc/lancedb/code_chunks.lance`;
cocoindex memoization in `.codoc/cocoindex.db/`. Indexing is durable, incremental,
crash-resumable. `update_index(root, codoc_dir)` runs the pipeline once (memoized
per-file); the loops then read via `read_all_chunks(...)`, pushing file scope down
as a LanceDB predicate and dropping the heavy columns (source, embedding) they
don't need. Reads share a process-lifetime background loop + cached connection.

**Embeddings are opt-in.** Nothing in the loops, bootstrap, or graph reads chunk
vectors today, so `CODOC_EMBED_CHUNKS` is OFF by default: the `CodeChunkLite`
schema omits the column and the pipeline never imports sentence-transformers (a
multi-second cold cost). Turning it on (for future semantic search) is recorded in
`.codoc/index.meta.json` and rebuilds the index under the vector schema.

**One workspace per process.** cocoindex holds a single environment per process, and
where the index LIVES is read from `CODOC_LANCE_PATH` / `COCOINDEX_DB` once, when that
environment enters its lifespan. So the App and the environment are cached in
`runner._app_for` and reused every pass — three separate failures otherwise: an App
rebuilt per pass keeps its name taken by any FAILED pass a traceback still references
("An app named 'CodocIndex' is already registered", permanently, from one transient
error); a second workspace indexed in the same process wrote its rows into the FIRST
one's index and left its own empty with nothing raised; and `.codoc` deleted from a
shell left the environment writing through handles to an unlinked directory. A switch of
workspace, an embed-flag wipe, and a `.codoc` that stopped existing all close the
environment, and the next pass rebinds it. Per-file memo state lives in each workspace's
own `cocoindex.db` and survives that, so the cost is the walk, not the files.

**LanceDB upkeep.** Lance is copy-on-write — every pass appends a version + new
fragments and cocoindex never prunes them (they piled to 4k versions / 256MB for
this repo before this). `update_index` ends with
`optimize(cleanup_older_than=30min)` (~40ms steady-state, 30-min retention per
lancedb#3086) to keep the index at live-data size.

### What gets an address (`lang/python.py`)

`(file, symbol_path)` is the whole system's address: the LanceDB chunk id, the
`compute_changeset` key, and the `UNIQUE(file, symbol_path)` binding key. A named
entity with no address cannot be cited, bound, or described; two entities sharing
one address silently become one. So the Python walk owes two guarantees, and used
to give neither.

- **Every definition in the namespace gets one.** `if` / `try` / `with` / `for` /
  `while` / `match` do not open a Python scope, so a `def` inside one binds the
  *enclosing* namespace: `loads` in an `except ImportError:` fallback is
  `file.py::loads`, full stop. The walk used to read direct children only, which
  left every compat shim, optional-dependency fallback, `if TYPE_CHECKING:`
  protocol and version branch with no address of its own — swallowed whole into
  `__module__`, where nothing could cite them. `_TRANSPARENT` names those
  statements and `_same_scope_defs` descends them, stopping at each definition.
- **A declaration is an entity too.** A public module-level name — `X = …`,
  `X: T = …`, and PEP 695's `type X = …` — gets its own address, because a
  description cites it by name. The 695 spelling was the one falling into glue.
- **One name, one address.** Keying by qualified name alone is not enough when a
  name is defined more than once: `@overload` stubs, a property and its setter, a
  version-branched pair. Whichever piece the walk emitted last won, so an
  overloaded function was routinely indexed as its first empty stub and described
  as doing nothing. Pieces are now collected per name and **joined** — the sources
  concatenated, `start_byte`/`end_byte` spanning them. `test/altair` had 517
  definitions landing on an occupied address before this and has none now.

Two consequences worth stating, because both are load-bearing:

**The guard IS the definition.** When a transparent statement's definitions all
carry one name, the *statement* is the chunk — `try: from fast import loads /
except ImportError: def loads…` is one entity whose source includes both branches.
"Only when `fast` is missing" is not a detail a faithful description may lose, and
handing the branches out separately would have made two entities where the code
has one.

**Join, don't span.** A property's accessors often sit either side of an unrelated
method. Spanning first→last would put that method inside the property's chunk, so
editing the neighbour would report the property as changed. Concatenation keeps
the chunk to what the entity actually is; `tokens_hash`/`types_hash`
(`core/tree_walk.py`) are computed from a chunk's `source`, so a joined source is
a legitimate input.

`__module__` follows from the same rule: it is the **glue** — the concatenated
top-level runs that define nothing — not the span from the first statement to the
last. Spanning made it the whole file, so a one-line edit to any function changed
the module feature's fingerprint too: one edit reported as two changes, the second
in the feature with least to do with it. (`lang/typescript.py` had the identical
defect and the identical fix; only the Python adapter needed the walk rewritten.)
`resolve_symbol_path` now delegates to `extract_chunks` rather than re-deriving
spans, so a `codoc:` link cannot resolve somewhere the index does not know.

**Existing workspaces see a one-time fingerprint wave.** `__module__` and every
merged name change hash, so the first pass after this re-reflects prose that was
written from an incomplete view of the code — which is the point. A file that is
*entirely* one guarded definition (`test/altair/jupyter/__init__.py`) loses its
`__module__` address, and its binding survives by relocation: the old `__module__`
source equals the new named chunk's, so `_detect_relocations` pairs them on
`tokens_hash` and re-attaches.

### What a prompt gets to see (`loop/payload.py`)

Every pass that shows the model code did it the same way: `symbol_path` plus the
first 600 characters of `source`. That makes the prompt a function of how many
chunks the pass happens to be holding — fine for a 20-symbol file, and **258,000
characters** for one generated module in `test/altair` (`channels.py`, 786 chunks;
`core.py` would be 308,000). The cost is the smaller problem. A single call asked
to name a coherent feature set over 786 symbols returns a junk drawer, and the rule
the prompt itself states (at most ~12 bindings to a node) cannot be honored.

So the budget is **per pass, not per chunk** (`BUDGET`, 72,000 chars), spent down a
ladder of named concessions:

- **A member gives way before a top-level definition.** The prompt's own rule binds
  a method to the feature its class owns, and no amount of the method's body changes
  that, so the partition is decided at the top level. (The TypeScript adapter
  addresses only top-level declarations, so a `.ts` file has no members and the
  first concession never fires there.)
- **A settings section is never a member**, however dotted its name. The concession
  above is earned by the class stating what its method is for, so a shortened body
  still lands the reader in the right place; `[tool.pytest.ini_options]` has no such
  parent — it names a path through a DOCUMENT, and the keys under it are the decision
  itself. Compressing those first would spend the budget on exactly the lines the tree
  cannot otherwise say, which is the whole reason the file is in the prompt.
- **The rung is chosen against what the set would actually cost once cut**, not
  against allowance times count — most definitions are shorter than their
  allowance, and charging for source nobody will send concedes two rungs the set
  can afford.
- **A cut is a whole-line cut, it keeps the signature, and it says it cut.** A
  byte-boundary slice hands the model a function that appears to end where the
  slice did; `head` keeps whole lines, guarantees the signature (`_BODY_OPENS`
  handles Python `:`, TypeScript `{`, and the `: ...` stub bodies generated Python
  is made of), and marks the elision.
- **Nothing drops to nothing.** The lowest rung still carries a signature, so every
  symbol arrives named and typed and a partition over all of them stays possible —
  which folding uncovered chunks into the largest node can never recover.
- **Where the signatures alone exceed the budget, the budget yields.** Cutting them
  would hand the model a list of names without parameters, which is the one thing
  it cannot work from.

A set that fits at 600 chars a chunk is passed through unchanged, so the ordinary
case is byte-identical to before: every file in `test/requests`, `codoc/store/db.py`
at 95 chunks, `doc-view.ts` at 141. On the crowded ones `channels.py` goes 258k →
77k and `api.py` 90k → 63k. Both call sites share it — `bootstrap_hier` per file,
and Loop A across `added` + `modified`, so a commit that lands a generated module
cannot turn the incremental pass into a half-megabyte call either.

**Conceding is the floor, not the goal** (`passes`). A pass spent down to 60
characters of each method is still being asked to name a feature set it can barely
read, so such a set is split across SEVERAL calls instead — and **the criterion is
the budget itself**, which is why there is no threshold here to argue about: split
iff the set does not fit at full allowance. A set that fits is one pass and one
call, exactly as before; a set that does not was going to be shown a fraction of
itself. Over the 813 files in this repo and its corpora that is 810 unchanged and
three split — `core.py` (923 definitions → 4 passes), `channels.py` (786 → 4),
`api.py` (219 → 2) — and after the split not one pass has to concede at all.

- **Split by top-level owner, never mid-class.** The prompt binds a method to the
  feature its class owns; a pass shown half a class would be asked to name a feature
  over evidence another pass is holding. An owner too large to share a pass gets one
  to itself and the budget concedes within it — the honest answer for a 200-method
  class, which is one feature no split makes two.
- **Owners pack in the order the caller presents them**, and `bootstrap_hier`
  presents a file in ITS OWN order (`_in_file_order`, by `start_byte`) rather than
  the index's alphabetical one. The order decides what a pass sees together: by
  name, `channels.py`'s four passes each span nearly the whole 1.2 MB file and
  overlap one another (3 of 3 neighbouring pairs); in file order they are four
  contiguous regions of it and overlap nowhere. It also fixes what the prompt reads
  even on a file that is not split — a module's constants no longer land between
  its classes, and `Store.__enter__` no longer precedes `Store.__init__`.
- **A file's groups run in SEQUENCE, threading titles forward** (`bootstrap_hier`).
  They are slices of one namespace, so a group blind to what its predecessor named
  will name it again — and avoiding that duplicate is what a single whole-file call
  was buying. Concurrency is unaffected: it is across files, and one file's groups
  were one call's worth of work.
- **A group that fails costs its slice, not the file.** Its symbols fall to
  `_ensure_file_coverage` like anything else the model left out; the warning names
  which part was lost, and `BootstrapResult.skipped` still names the file, once. The
  fatal "broken setup" guard therefore counts CALLS — every call failing is a
  missing key, whereas one part of one file failing is not, however few files the
  repo has.

### Whether a large file holds intent (`pipelines/indexing/gate.py`)

The indexer skipped any file over 1.5 MB, reasoning that a minified bundle, a
generated blob, or a vendored single-file lib is not authored intent and that
parsing and embedding one stalls the loop. The reasoning is right and the measure is
a proxy, and it came apart on the file that matters most: `test/altair` ships two
modules from one generator — `channels.py` at 1.20 MB, 786 definitions, indexed, and
`core.py` at 1.60 MB, 923 definitions, **invisible**. Same directory, same generator,
same shape, and what decided between them was 400 KB. `core.py` is the Vega-Lite
schema surface: the single file a reader of altair most needs described.

What distinguishes a blob is its **shape once parsed**, and the parse says so
plainly. Over the 826 Python and TypeScript files in this repo and its corpora the
largest median chunk any of them has is 16,097 bytes (a re-export `__init__.py` that
is one chunk). A one-line data module parses to one chunk with a median of 1,248,897;
a vendored file of four enormous generated functions has a median of 105,792. Two and
a half orders of magnitude of daylight, so `ORDINARY_DEFINITION_BYTES` (40 KB) sits
inside the gap and does not have to be delicate.

So size decides one thing — whether to **read** the file at all
(`READ_CEILING_BYTES`, 4 MB; parse and hash measured at 0.36 s/MB, so ~1.3 s once).
Being a cost bound and not a claim about intent, it is per **kind** of file
(`read_ceiling`): a notebook's bytes are mostly the outputs of having run it — a base64
PNG per plot — and none of them is ever parsed, hashed, or shown to a prompt, so a
`.ipynb` is held to `NOTEBOOK_READ_CEILING_BYTES` (20 MB) instead. Under one number,
whether codoc could see a notebook's code would be decided by whether somebody ran it.
Past `HEARING_BYTES` (1.5 MB) a file gets a *hearing* instead of a silent drop: read
it, parse it, index it if the parse found definitions of the size a person writes.
The hearing is **free** — the parse it judges is the one `_process_file` was going to
do in the next breath.

- The test is the **median** definition, not the count and not the largest. Count
  says nothing (a blob can parse to many pieces, a hand-written module to a few) and
  the largest says nothing either — `store/db.py` carries a 55 KB chunk among 95
  without ceasing to be readable code. The median asks the question that matters: *is
  this file made of units a person could read one at a time?*
- A parse that found **nothing** is not addressable, which is also honest: indexing
  it would be a no-op that reads as coverage.
- Below the hearing threshold nothing is consulted, so a small module that happens to
  be one enormous dict is indexed exactly as before. The hearing only ever gives a
  big file a chance; it never takes one away.

The ceiling is now a bound on what the **describing** pass can carry rather than a
guess about the author. `loop/payload.py` is what made that bound honest — it keeps
one crowded file's prompt finite by conceding down to its signatures, and the
newly-admitted `core.py` makes a 94,165-character bootstrap call instead of a
308,434-character one. Raising the ceiling further waits on splitting a crowded
file's bootstrap pass, so the number stays where the downstream can honor it, and the
survey **reports every file the gate turns away** rather than letting the tree quietly
not mention them.

### What the walk never saw (`pipelines/indexing/survey.py`)

`codoc status` checks that every indexed chunk is attributed to a feature. That is
a coverage report over **the index**, and it reads 100% on a repo whose Go half
codoc cannot parse, whose generated module the gate turned away, or whose monorepo
packages are directory symlinks the walk refuses to follow. A tree that describes a
third of a codebase and calls itself in sync is the one failure a faithful view of
the code must not have, so the coverage figure now carries the bound on itself.

`survey_repo` walks the repo **the way the indexer walks it** — the same matcher,
patterns and large-file gate, *imported* rather than restated, because a survey that
disagrees with the walk is worse than no survey. Sharing the gate means the survey
does for a file past the hearing threshold what the indexer does: reads and parses
it. That is a handful of files in a large repo and none in most, and it is the price
of the report being about the walk that actually happened. It reports four kinds of
blindness, each on its own `render_survey` line because each is answered differently:

- **another language** — source with no adapter (`.go`, `.js`, `.sh`, …), counted
  per extension. A standing bound on what the tree can ever say.
- **over the read ceiling** — parseable files past `READ_CEILING_BYTES`, **named**:
  the threshold is somebody's to revisit and they cannot weigh it without knowing
  which file it cost them.
- **large and not addressable** — read, parsed, and made of no definitions a feature
  could bind (a one-line data module, a few enormous generated functions). Named for
  the same reason, and separate from the line above because the answer is different:
  there was nothing here to describe, so the threshold is not the thing to revisit.
- **an unfollowed symlink** — a directory the patterns *allow* and the walk's loop
  guard refuses. Usually a monorepo layout codoc should be pointed at directly.

It reports only codoc's **own** limits. A file under `node_modules/`, `dist/`, or a
path the repo's `.gitignore` names was excluded by somebody's decision; saying so
on every `codoc status` would bury the facts that are about codoc. That is why the
survey uses a plain `PatternFilePathMatcher` instead of the walk's
`_SymlinkAwareMatcher` and tests exclusion *before* the symlink guard — it has to
tell "excluded on purpose" from "codoc declined to follow a link", and only the
second is news. The extension set is source-only for the same reason: `.md`,
`.json` and lock files are not a gap in codoc's view of the *code*.

The whole thing is advisory. It is wrapped in a bare `except` at the call site in
`cli/main.py`, has a `max_entries` runaway guard, and a partial result is still a
lower bound — a survey must never be what breaks `codoc status`.

## Loop A in detail (code → codoc)

`compute_changeset` (`loop/diff.py`) diffs two index snapshots keyed by
`(file, symbol_path)` on `tokens_hash` → `ChangeSet{added, removed, modified}`.
`apply_changeset` has five phases: (1) **auto-ops** — modified-bound → REFRESH,
removed-bound → DETACH; (2) **correspondence** — `_detect_relocations` pairs
removed↔added that are the same code relocated (move via `tokens_hash`; rename via
same-file unique `types_hash`, then **cross-file** when the `types_hash` is
*globally 1:1-unique* — D3) → deterministic ATTACH, no dropped attribution;
(3) **LLM pass** — for unbound additions / a feature that lost its last binding /
a stale-prose modification, safe ops apply and structural ops become proposals;
(4) **may-impact** — surface upstream dependents for the prompt; (5) **coverage
net** — `_cover_uncovered_adds` attaches to the best `neighbor_feature` or surfaces
a pending ADD, so nothing is silently dropped.

**A file that does not parse is not evidence of deletion.** tree-sitter recovers
what it can from a damaged file and drops the rest, so its chunks are a lower bound
on what the file contains — and the index has no column for "I could not read
this". A removal reads as deletion and DETACHES the binding, so a save in the
middle of an edit would strip a feature's attribution and the repaired file would
come back as an unbound addition for the LLM pass to guess at again.
`diff._hold_unparseable_removals` drops removals whose file is **still on disk and
demonstrably unparseable** (`lang.parses_cleanly`, which walks only the damaged path
via `has_error`); a file that is GONE removes its chunks for real, and a file no
adapter reads is not second-guessed. Only removals are held: an addition or a
modification out of a damaged file is at worst a spurious refresh that the next
clean pass corrects. A file that never parses (a templated `.py`, Python 2) keeps
stale bindings until it does, which is why the hold is logged rather than silent.

**Merge-edge robustness (D1–D4).** The LLM-pass apply folds a re-proposed node by
exact title (`_unbound_features_by_title`), by `(normalized_title, parent_id)` for
binding-less theme parents (the D2 identity guard the `UNIQUE` binding constraint
can't give), and — opt-in via `CODOC_SEMANTIC_DEDUP` — by *embedding* near-duplicate
title (`loop/title_dedup.py`, D1, fails-safe inert without the embedder).
`reconcile_drift` opportunistically backfills empty `types_hash` on bound chunks
(D4) so rename detection isn't permanently blind for legacy/MCP binds.

Two caller-set authority flags gate the LLM pass: `allow_retire` and
`amend_on_change`. `run_loop_a` (mid-edit diff) passes both `False`;
`reconcile_drift` (authoritative full-state, the daemon path) both `True` — so a
RETIRE only ever surfaces from the authoritative view. **Stale-proposal GC** runs
first each pass (drops ADDs already bound elsewhere, RETIREs whose feature still
owns code), so a no-op `codoc sync` converges to `in_sync`. **Doc-wins holds:**
`held` features (live doc-ahead intents ∪ queued directives) suppress code-side
AMEND/RETIRE/MOVE — doc wins; binding maintenance is never suppressed. The hold
check is the SINGLE `phase.is_held` predicate (D5), shared by all three guards (the
`emptied` detection, `suppressed_by_hold`, and `_compute_drift`) so they can't drift
apart. While `realize.json` exists, applied ops are stamped `caused_by=⟨directive⟩`.

### The amend gate, and the amend that changes nothing (`loop/apply.py`)

A code-side AMEND rewrites prose a person may have written, so it is not applied on
the strength of being newer. `is_small_amend` measures how much of the old
description SURVIVES the new one — `preserved_ratio` counts contiguous runs at least
`clause_chars(old)` long, so a reflow scores high and a rewrite scores low — and
holds human-written prose to `PRESERVE_RATIO_HUMAN` (0.85) while loop-written prose
answers to `PRESERVE_RATIO_MACHINE` (0.50). Which bar applies is read from
`feature_writers`, and failing the bar is not a refusal: the op becomes a pending
proposal for a person to accept.

That makes `feature_writers` load-bearing, and `apply_op` reassigns it to **whoever
wrote last** on any applied op carrying a title or description. So an amend that asks
for the prose the feature ALREADY HAS is not the harmless no-op it looks like:

- **it takes the paragraph over.** A restatement moves a human-written node to the
  loop, and the gate then holds the author's NEXT rewrite to the machine bar instead
  of theirs. One op that changed no words is enough to unlock somebody's prose — a
  mid-band rewrite (preserved between the two ratios) that was correctly held for
  review auto-applies after it.
- **it spends a moment of the timeline on nothing.** The scrubber and the per-span
  inline blame read applied events, so a restatement is a change a reader can open and
  find nothing in, and it re-attributes spans the author wrote to whoever restated
  them. A ledger of changes that did not change anything is how a diff stops being
  worth reading.

`restates_current` therefore drops such an op at both doors that reach `apply_op`
outside the authored path — Loop A's `apply_changeset` op loop (the single funnel for
`run_loop_a` and `reconcile_drift` alike, counted as `LoopAResult.restated` and named
in `summary()`) and `mcp/tools._apply_single` (which answers the agent `noop: True`
and records nothing). It is dropped rather than proposed, because asking a person for
a verdict on nothing is the same cost in a different place. Comparison is
`normalize_description`, so whitespace the reader cannot see does not count as a
change here either.

Two symmetries are deliberate. The authored side has refused this since merge3
landed, for the same reason and in nearly the same words
(`loop_b._resolve_content` → `Resolution(NOOP)` when the merge result equals what is
stored). And a **plan** amend (`realized is False`) is exempt however familiar its
words: prose that already says what the feature WILL do, marked not yet built, is a
request to build it — the words being unchanged is the point, not a sign there is
nothing to do. `codoc translate` meets the same hazard from the other side and
handles it by restoring the writer role after each apply (see "Authoring language").

### Warranting a stated why (`loop/why.py` + `loop/warrant.py`)

The describing pass is handed prose the repo already wrote about its own decisions —
commit messages that touched the changed files, realized directives, previously recorded
rationale — plus the author's live prompt. That gives a description the material for a
*why*; it does not record which of it the why rested on, and a description grounded in a
commit read identically on the page to one that outran every source in the block. A
**warrant** closes that: `NodeOp.warrant` is a list of `Warrant{kind, ref, quote}`, and
the provenance card renders each as a `Rests on` row.

Three rules make it worth trusting:

- **Every entry is citable, and ids are per-prompt.** `why.stamp_ids` puts `c1`/`d1`/`p1`
  on the commit / directive / prior entries and `loop_a` stamps `a1`… on `author_intent`
  (at the call site, not inside `relevant_intent`, because Loop B's directive builder
  consumes that same function's output as plain strings and has no citation to make).
  Stamping happens AFTER `_fits` trims the block, so a citation can never name an entry
  dropped for size. Ids are a handle for one prompt and are never stored — what is stored
  is the resolved warrant.
- **The quote never comes from the model.** The reply carries ids only
  (`"warrant": ["c1", "a1"]`); `warrant.resolve` looks each up in the index built from the
  block that was actually sent (`warrant.index_evidence`) and stores what that source
  said, clipped. Asking the model to repeat the evidence back would let a paraphrase drift
  toward the claim it is meant to check. Rows are ordered by directness — intent,
  directive, commit, prior — not by the order cited, mirroring `why.py`'s own hierarchy.
- **Fail open, never fail false.** An id that resolves to nothing — a hallucinated sha, a
  stale id from a previous pass — is dropped silently: no error, no retry. A missing
  warrant is a *normal* state (most descriptions report what code achieves and assert no
  reason at all, per Rule 7), so a pass must not be spent on the formality. The failure
  mode is losing a real citation, never gaining a fabricated one. Only prose-writing ops
  carry one; a citation on an `attach` has no claim to support.

It travels on both surfaces a reader asks from — `revisions.py::_entry` for the timeline
and `render.py::_history_feed` for the sidecar, each omitting the key entirely when there
is nothing to say — and `codoc history` echoes it as a `rests on …` line under the reason.

## Loop B in detail (codoc → code)

`run_loop_b` (1) drains the `edits.json` **`commands`** channel — the
identity-keyed authored edits (add/set_title/set_description/move/retire) the
webview now emits (U3/U4) — and applies each via `apply_op` against the store,
gated by an applied-command-id ledger (idempotent under a daemon-down replay) and a
`(normalized_title, parent_id)` dedup guard so a re-sent `add` can't duplicate-mint;
a minted `fid` is echoed back keyed to its submitted `localId`. Authored edits are
**no longer inferred from a `tree.codoc` / `tree.doc.json` text diff** — once the
daemon became the sole writer of both files (U4/U6), reading either back as input
was the daemon diffing its own output, which re-minted nodes and resurrected
deletions; that inference (`doc_presence` / `doc-fids.json`) is retired (U7), and a
deletion is now an explicit `retire` command. It then (2) drains `inbox.json`
verdicts, (3) applies legacy annotation ops stamped with the annotating actor/mode
(default human/pen), (4) drains live **payload intents** (doc-ahead suggestions,
`mode=suggest`), (5) re-renders `tree.codoc` **and** `tree.doc.json`
(`write_tree` + `write_tree_doc` — the store projection the webview re-reads), and
(6) builds a directive from each code-implying op. Each gets a `d-…` id (in its
`### N.` heading + the `realize.json` manifest) and is written to `realize.md` with
status `awaiting_impl` for the live session — the queue **appends, never clobbers**.
`**bold**` focus and external links drain here into `Focus:` / `Consult:` lines.
Steering notes arrive as `steers` on `edits.json` (`drain_steers`), written by the IDE's
inline-comment surface — a `> …` line typed into a description is ordinary prose, since
the text-ingest channel it used to ride was retired with the rest of the doc-diff
inference (U7).

**RETIRE is path-asymmetric:** accepting an auto-raised RETIRE from the inbox is
**detach-only** (never queue code removal — a Loop-A retire off transient drift
could be a false positive); only a human `~` retire in the text queues a
`RETIRE FEATURE` removal directive.

**Realization trigger.** Primary path is in-session: `/codoc:plan` proposes nodes
then calls the blocking `codoc_await_verdicts` MCP tool, so the same turn
implements + binds. Fallbacks reuse `realize.md`: the `UserPromptSubmit` hook (no
daemon) and `codoc watch --auto-realize` (no session), via `loop/sdk_realize.py`
(preferred) or a blind `claude -p /codoc:sync`. The unified `/codoc:sync` reads
`status.json` and dispatches direction.

**Reading, not writing — `/codoc:ask`.** A separate, read-only slash command:
the session reads the tree + code and calls `codoc_walkthrough` to lay a numbered
reading path over the features that already hold the answer (`.codoc/ask.json`;
see the control-files list). It changes nothing — no store write, no directive, no
realize queue — so it needs no verdict and no trigger; it is the *understanding*
counterpart to plan/sync's *change* loop. Companion tools `codoc_walkthrough_read`
/ `codoc_walkthrough_clear` observe and dismiss.

## Blocks — typed media + plugin codecs (agent-native notebook protocol)

The feature node generalizes to a typed, bindable, lifecycled **block**. Prose stays
the implicit *block-zero* (`feature.description`), so an existing feature owns zero
block rows and is unchanged; non-prose media (diagram, image, latex, url, screenshot)
are rows in the new `blocks` table (`codoc/store/db.py`). The whole subsystem lives in
`codoc/blocks/`.

A **plugin** (`blocks/base.py:BlockPlugin`) declares which of three capabilities a
medium supports — `LIFT` (code→block, ungated attribution, read-only on code),
`LOWER` (block→code directive, hold-gated, lossy→held draft), `CONSULT` (passive
context, no round-trip) — plus binding mode (`bound` inherits the feature's binding
set; `ambient` has none), lifecycle (`persistent`/`transient`), and per-direction
dispatch (`deterministic`/`agent`). The registry (`registry.py`) validates that each
declared capability has its method and is the loops' **dispatch table**.

Two invariants make this robust and deterministic:
- **KTD1 — binding stays feature-level.** A block's binding view derives from
  `bindings_for_feature`; `UNIQUE(file, symbol_path)` is untouched (no per-block key).
- **KTD8 — structure is deterministic, only transformation is the LLM.** Every block
  carries a stable `id` (`ids.new_block_id`) that survives arbitrary host edits (move =
  `ord` change, delete+undo, type-change); the loops diff the settled block-id set
  against a baseline, so the LLM never tracks identity — it only transforms content.

Loop integration (no parallel pipeline — block directives ARE directives):
- **Loop A** (`loop_a.run_loop_a` → `blocks/refresh.py:refresh_lift_blocks`) re-derives
  persistent `LIFT` blocks from the fresh graph after `apply_changeset`. It is doc-wins:
  a block on a *held* feature is skipped so a human's in-progress edit is never clobbered.
- **Loop B** (`loop_b._apply_edits` step 2.9) drains the `edits.json` `block_edits`
  channel, dispatches each by declared `LOWER` capability, and feeds the result into the
  same manifest → `realize.md` queue (inheriting the draft gate, append-never-clobber,
  filelock). A `remove` drops only the projection (block row), never code/bindings; an
  ambiguous `lower` returns a `draft` held for confirmation; a `move` emits nothing.

Reference plugins: prose (`prose.py`, plugin-zero), diagram (`diagram.py` — deterministic
`lift` from the dependency graph, deterministic edge-delta `lower`; see "Drawing how
things relate" below), and the consult media
(`screenshot.py` — transient screenshot riding the steers channel + url/image). Hosts
render blocks from the sidecar `blocks` slice (v6); the VS Code webview and the `codoc serve`
hub (read-only) are two hosts on the one protocol, kept in parity by
`blocks/conformance.py:canonical_block_view` (mirrored by `bindings-model.ts:blocksForFeature`).

### Drawing how things relate, and what a change would reach

Sillito's third and fourth question groups — *how are these related?* and *what happens
if I change this?* — are the two a description cannot answer without becoming an
inventory of the machinery it exists to spare the reader. Both are answered from the
dependency graph instead, and both stay out of the prose.

**Group 3 — the diagram lift** (`blocks/diagram.py`). For a feature's bound symbols it
walks the graph in BOTH directions (in-edges that start inside the feature are skipped:
a node's own internals are not external pressure), ids each node by its whole symbol
path (a leaf-collapsed id merged `a.py::save` with `b.py::save` and drew a dependency
that does not exist), labels it with the leaf, and groups neighbours into a subgraph per
OWNING feature — the reader's own first, a neighbour no feature covers labelled as
outside the tree. A hub symbol is bounded at `MAX_NEIGHBOURS` by degree then symbol path
(deterministic, so a re-lift is idempotent), and the cut is drawn as a real node
(`+N more related symbols`) because a silently truncated diagram reads as the whole
story.

The round trip carries **no legend**: nothing in the mermaid content records which box
is which symbol, and both halves rebuild that from the feature's bindings plus their
one-hop neighbours — so an author editing the picture cannot delete the mapping. On
`lower`, an EDGE DELTA always yields a directive (a box with no code behind it is a
request to create it, and the author's own label is the best name anyone has for it);
what drafts is a change that moved no edge — a lone new box, a relabel, or content the
codec cannot read (KTD2).

**Group 4 — `graph/query.py:feature_impact`.** `{subject → [{feature_id, title, count,
via}]}` over `call`/`import`/`inherit` edges, one entry per DEPENDENT feature, ranked by
how many of its symbols tie it to the subject and then by title so the answer is stable
between passes. Self-coupling and retired dependents are dropped; `via` is capped at
five and `count` is not. This is a STANDING property, deliberately separate from
`loop_a._compute_impacted`, which answers the neighbouring question (who was affected by
what just happened) off a changeset and feeds the LLM pass — a reader asking what a
change would reach has changed nothing yet.

It reaches three surfaces: the `feature_impact` sidecar slice (sharing `write_sidecar`'s
one edge-table read with `feature_see_also`), `impact` / `impact_total` on every
`read_context` row (an agent about to edit is asking exactly this and is the reader least
able to look around), and a chip on the feature heading that is invisible until the
heading is hovered (`webview/tiptap/impact-decorations.ts`) — the fact is worth a great
deal in the seconds before an edit and nothing at all while reading. The count is the
chip; clicking opens the dependents by name with the symbols that reach in, each a link
to go read.

## Bootstrap in detail

`run_bootstrap` → `bootstrap_hier_from_chunks`, three phases: (1) a per-file
`propose_file_features` LLM call (sees only that file's chunks → structurally can't
make a cross-file junk drawer; `_ensure_file_coverage` folds stragglers into the
file's largest node); (1b) a settings pass over the config files the code reads, after
every code file (below); (2) an org pass (`organize=True`) grouping file-features under
3–6 broad theme parents. Temp ids ("n1"/"t1") resolve to real ids before apply,
enabling within-call nesting.

### Phase 1b — the decisions a settings file holds

A settings file is held back from the per-file pass and gets one of its own
(`prompts/bootstrap_settings.txt` → `propose_settings_features`). Not for tidiness: the
per-file pass is asked what a file is FOR, and a settings file answers that with a
Configuration node — a junk drawer holding every unrelated decision in the repo, which
is the shape a feature tree exists to avoid. The right answer is usually **no new
node**. `[periods]` belongs on the feature whose code reads it, and that feature's
description should say `month = "made"` where it used to say "read from rules.toml". So
this pass returns `attach` plus `amend`, and mints a node only for a section nothing
accounts for.

Two properties carry it:

- **It runs after every code file**, serially, at the end. The ordering IS the
  mechanism: a pass that cites `f-…` needs those features to exist and needs their
  prose, since an amend that cannot see what it is amending either repeats it or throws
  it away.
- **Candidates are chosen by evidence, not proximity** (`_settings_readers`). The same
  rule that admitted the file to the index — a source file whose text names the
  basename — applied per SYMBOL, so the pass is offered the function that opens
  `rules.toml` rather than every feature in the module containing it. An `attach` or
  `amend` citing a feature it was never shown is dropped (`_only_offered_features`);
  `_ensure_file_coverage` then gives that section a node of its own, which is a visible
  gap instead of prose on the wrong node. The same fallback covers a pass that fails —
  one raised call costs the values and not the code files' tree.

The per-file calls run in concurrent **waves** of `CODOC_BOOTSTRAP_CONCURRENCY`
(default 8): all store reads happen before dispatch and all writes after the wave,
on the calling thread in deterministic file order (workers only make the LLM call),
so a wave shares one titles snapshot — an identical prompt prefix that hits the
prompt cache across the wave — while cross-wave dedup context still accretes.

## LLM calls — model tiers + prompt-cache alignment (`agent/`, `config.py`)

Every completion funnels through `config.complete(prompt, config, prefix_parts=…)`.
`prefix_parts` are the STABLE prompt segments (frozen instructions, then the
whole-tree title outline) that precede the volatile change; templates carry
`<<<CACHE_BREAK>>>` markers that `agent/base.split_prompt` cuts **before**
substitution (so a marker inside repo content is inert). The provider layer turns
the prefix into Anthropic `system` blocks with `cache_control` breakpoints / an
OpenAI stable prefix + `prompt_cache_key`, so consecutive passes over an unchanged
tree pay cache-read (~0.1×) for everything but the change. The keyless `claude`
path runs each completion with a minimal `--system-prompt-file` (codoc preamble +
prefix) and `--disallowedTools "*"` — dropping the ~37K-token default agent context
to ~1–2K, with cross-spawn cache hits. Structured-extraction calls (per-save tree
update, per-file bootstrap) default to the fast model tier (`fast_llm_config`,
`CODOC_MODEL_FAST`); `run_agent` memoizes parsed responses (bounded LRU) so a
crash-replayed / re-issued identical pass doesn't re-bill.

## Settings files (`codoc/settings_files.py`)

A codebase moves a constant out of a module and into `rules.toml` precisely BECAUSE the
value matters to somebody who is not reading the source — the same reason a feature tree
exists. So the moment a decision becomes worth configuring is the moment codoc goes quiet
about it: the walk covers `**/*.py` and `**/*.ts`, so the pass that described the feature
had the code that READS the setting and never the file that SETS it, and it wrote the
mechanism because the mechanism was all it was shown ("The month threshold is read from
rules.toml").

A settings file has no functions, so this is not a tree-sitter adapter and does not live
in `codoc/lang/` (which is *programming* languages). What it shares with an adapter is the
only thing the pipeline needs — a file becomes named, addressable pieces:

- **A chunk is a section; a nested section is its member.** `[periods]` is
  `rules.toml::periods`, `[periods.week]` is `rules.toml::periods.week` — the same
  owner/member relation a class and its methods have, so everything downstream that reads
  a dotted symbol path (the prompt's binding rule, the per-pass budget's split by
  top-level owner in `loop/payload.py`) works on settings unchanged. Keys before the first
  section are the file's own, under `::__module__`, the name the code walk already uses.
- **The comments come with the section.** In code the reasoning is in the docstring; in a
  settings file it is the `#` run directly above the key, and it is the part a description
  most wants to quote. The run is floored by the previous header, so no run is claimed
  twice and a reason is never attributed to the decision above the one it explains.
- **Identity is the parsed key/value pairs, sorted** (`hashes`), not the text.
  Fingerprinting decides whether Loop A wakes at all, so a formatter that reorders two
  keys or reflows a comment must not read as a policy change, while `month = "made"`
  becoming `"posted"` must. The two signals keep the meaning they have for code:
  `tokens_hash` covers keys AND values, `types_hash` the key paths alone — the file's
  shape, which a rename moves and a re-tuning does not. A fragment that does not parse
  falls back to its words, exactly as `core/tree_walk` does, because a half-written
  settings file is a state a person passes through.
- **A line is the unit.** Minified JSON, whose keys share one line, is therefore not
  addressable by section and comes back as the whole file — naming it after its last key
  would be worse than naming it after the file.

Four formats, because they are what a Python repo's decisions live in. TOML, JSON and INI
come from the standard library; YAML needs PyYAML, which codoc does not depend on, so a
repo without it has no YAML support rather than a broken import. `available_formats`
reports that, since a file the walk skipped and a file that does not exist must not look
the same.

`NOT_INTENT` (`pyproject.toml`, lock files, `tsconfig.json`, …) is a **backstop, not the
selection rule**: those files are machinery, generated or dictated by a tool, and a tree
that described them would spend its first nodes on the build. Which settings files enter
the index at all is decided by the code that READS them.

### Which settings files enter the index (`pipelines/indexing/settings_scan.py`)

A glob would be the wrong rule in both directions: a repo's YAML is mostly CI matrices
and fixtures the tree should never mention, and a name-based allowlist cannot know that
`tally/rules.toml` is where this codebase's policy lives. The rule that matches what the
tree is for is **a settings file holds a decision if the code reads it** — the same
evidence a binding stands on, and it makes an unread file a non-event rather than a
judgment call.

The evidence is a mention of the file's BASENAME in the text of some indexed source
file: `open("rules.toml")`, `Path(__file__).parent / "rules.toml"`, `"config/rules.toml"`,
or a comment that names it. Text, not an AST, because the mention is what matters and a
path is assembled a dozen ways. Two bounds follow and are stated rather than hidden: a
name built at runtime (`f"{env}.toml"`) is not found, and a file the code names only in
prose is found — the first costs a chunk that should be there, the second one that could
have been left out, and of the two failures the second is the cheap one.

The scan reads each source file once and stops as soon as every candidate name is
accounted for; a repo with no candidate settings files opens nothing at all. Its result
carries three things, because they are three different situations: `read_by_code` (index
these), `unreadable` (referenced, but this process has no parser — a YAML file with no
PyYAML must not read like a file nobody wants), and `unreferenced` as a COUNT, since a
repo has many of those and none of them is news.

The indexer then walks the settings candidates as a second mount
(`cocoindex_app._process_settings_file`) and passes the selection in as an ARGUMENT
rather than looking it up: the per-file function is memoized, and the decision is about
the repo, so a settings file the code stopped reading has to re-run while every code
file's memo stays untouched. Rows carry the format in the `language` column and identity
comes from `settings_files.hashes`, so nothing downstream needs to know a chunk came
from a settings file.

### What had to stop asking `codoc.lang`

Three places asked "does this file parse" by asking the tree-sitter adapters, which was
the same question until a second kind of readable file existed. `lang.parses_cleanly` now
answers **None** for a file no adapter claims, because "nothing read it" and "it is
damaged" are opposite answers a bool cannot hold — it used to say False, and every caller
had to remember that False meant "cannot tell".

- **`loop/diff._hold_unparseable_removals`** holds a removal only where a reader that
  claims the file says it is broken, and asks whichever reader claims it
  (`diff._reads_cleanly`). Otherwise a deleted `[merchants]` section — from a file that
  parses perfectly — was held forever and the tree kept citing a section that was gone.
  Mid-edit is still mid-edit in any format: an unterminated string holds the removal.
- **`agent/hook._symbol_scoped_features`** narrows an agent's edit to the SECTION it
  touched, so one changed decision scopes Loop A to the feature that owns it rather than
  to every feature bound to the file. An edit to the comment run above a header is an
  edit to that section, which is the same rule the chunker uses.
- **`pipelines/indexing/survey`** reports one settings answer, the only one that is news:
  a file the code READS that no parser here can open (a YAML file with no PyYAML). The
  ones nobody reads are excluded by design, so listing them would bury it.

`graph/extract` needed no change: it skips a row whose file has no adapter, which is
exactly the plan's answer that a settings file cites no symbols and the dependency graph
should not grow edges it cannot justify. `codoc status` coverage needed none either — a
settings section is an indexed chunk, so an unbound one is a real gap and counts as one.

`FORMAT_NAMES` (the format names, as against `FORMATS`, which is keyed by EXTENSION) is
what a caller holding an indexed ROW asks: the `language` column carries `"toml"`, not
`".toml"`, so testing a row against `FORMATS` matches nothing and does it silently. It is
static rather than `available_formats` because a YAML row indexed on a machine that had
PyYAML is still a settings row on one that does not.

### Clicking a cited setting (`vscode-codoc/src/state/settings-sections.ts`)

Once a description says `month = "made"` and cites `codoc:tally/rules.toml#periods`, the
citation has to land on the section. Nothing that finds CODE can: VS Code ships no TOML
or INI document-symbol provider, and `openRef`'s fallback regex looks for `def` /
`class` / `name =` where the file's declarations are `[periods]` — so the click opened
the file, revealed nothing, and read as a broken citation rather than a missing parser.
The leaf rule is wrong here too, which is why this is a module and not another branch:
`symbolLeaf` takes the last `.`-segment because `file.py::Class.method` declares
`method`, while a settings path nests in a document and
`pyproject.toml::tool.pytest.ini_options` is declared by its whole dotted name.

It mirrors the chunker's header grammars — `_section_starts`, `_uniquify`'s
`servers`/`servers[1]` repeat rule, and JSON's depth scan (a per-line regex cannot tell
a nested `"month":` from a top-level `"periods":` and would send the reader to whichever
came first). Headers only, never values, so the two can disagree about nothing but where
a section starts. It is consulted AHEAD of both code paths and they are then skipped: a
hit from them inside a settings file would be coincidence, and landing a reader on an
arbitrary line is worse than the honest no-op. A ref naming no section moves nothing, for
the same reason — showing `[periods]` to somebody whose sentence claimed `[retries]`
states something the file does not say.

## Notebooks (`codoc/lang/notebook.py`)

A notebook is where a lot of a Python codebase's *reasoning* is written down, and it is
the one place the author already wrote the description: `## Load the data`, then a
paragraph on why the export arrives monthly, then the code. Unindexed, that is the
richest intent in the repo sitting outside the tree — and worse, a notebook that calls
into the package is a caller the dependency graph does not know about.

The call this stage makes, and the reason it looks nothing like the settings-file one:
**a notebook goes behind the `LanguageAdapter` Protocol.** A settings file needed a
second reader because it has no symbols; a notebook HAS symbols, because a notebook is
Python with the cells still visible. So `detect_language(".ipynb") → "notebook"` plus
`get_adapter("notebook")` makes diff, the hook's symbol scoping, the graph, `status`
coverage, `payload`'s budget split and bootstrap work with **zero** changes each.

The adapter's whole job is to answer the Protocol in terms of a **synthetic Python
document**: the cells in order, one blank line between them, with a per-line index back
into the file. Everything else is delegated — each section's text goes to
`PythonAdapter().extract_chunks`, so name-merging, decorator peeling and the transparent
wrappers come for free and only the ADDRESSES differ.

**The prose rides in as raw string literals, not comments** — the load-bearing decision.
The pipeline-wide rule is that a comment is not part of a chunk (module-level glue is
collected in runs that exclude comment nodes) and not part of its identity
(`core/tree_walk`'s `_COMMENT_TYPES` is hard-coded, so an adapter cannot opt out). For
code that rule earns its keep: a reflowed comment is not a change. A notebook inverts the
case, and prose-as-comments would have failed twice over — the markdown would reach no
chunk and therefore no prompt, a prose-only section would produce nothing to bind, and a
rewritten paragraph would go unnoticed by Loop A. As `r"""…"""` statements the prose is
in the chunk source AND in the identity, which is right: in a notebook the markdown *is*
the authored intent, and a reworded step is exactly the change a fresh description should
follow. The literal is raw and quoted with whichever triple-quote the prose does not
itself use, so the paragraph is carried through byte-for-byte; a block using both styles
falls back to comments, which loses that block's prose and nothing else.

**A heading names the statement run under it, and sections are FLAT.** `## Load the data`
makes `nb.ipynb::load-the-data`, and a `def` under it is a member — `load-the-data.tenure`
— the same owner/member relation a class and its methods have, which is what lets the
dotted-path readers downstream work unchanged. `### Load` under `## Data` is `load`, not
`data.load`: headings are a **reading order**, not a namespace, and nesting them would
mint addresses that move when an author demotes a heading. A repeated `## Train` is a
second section (`train`, `train[1]`), and its members go under the name that section
actually got — the section's name is settled before its chunks for exactly that reason.
Before the first heading, and in a heading-less notebook, the addresses are a script's own
(`::__module__`, bare names), identical to the equivalent `.py` file, so nothing has two
names.

**Identity is the cells' source and nothing else.** Outputs and `execution_count` never
enter the synthetic document, so re-running a notebook is not a change — the alternative
is a tree that churns every time somebody presses Run All.

**IPython that is not Python is tolerated rather than fatal.** `!shell` and `%magic`
lines and `?`-help lines are commented out; a `%%cell-magic` cell and any cell that still
fails to parse is commented out whole. One `!pip install` must not make the file read as
*damaged*, because damage is what holds Loop A's removals — a tree citing symbols that
are gone. Two costs come with it and are accepted: a half-typed cell reads as clean, and
a section whose only cell is shell contributes no code chunk (its prose still binds).
Broken JSON is the opposite case and must report damage, which is why the fallback
document is `"(\n"` — raw JSON is itself a valid Python expression, so parsing the file
as Python would report a corrupt notebook as clean.

`synthetic_source` takes a notebook **or a chunk of one**: the identity and reference
walks are handed a chunk's own source, which is already synthetic Python. Routing that
back through the cell reader would give every notebook chunk the same never-moving hash
and no graph edges, so a text that is not a notebook is parsed as Python unless it opens
like a JSON document.

Byte offsets are **anchors, not slices** — nothing re-reads a notebook chunk from the
file, since the bytes between two chunk lines are JSON punctuation and base64. One
monotone forward search per line locates each synthetic line's text in the raw file
(trying both `ensure_ascii` escape forms). They serve the two callers that need a
position: bootstrap's file ordering by `(start_byte, symbol_path)`, and the hook's
approximate line number.

### Clicking a cited cell (`vscode-codoc/src/state/notebook-cells.ts`)

A citation into a notebook is the case where the existing paths are worse than a no-op.
A `.ipynb` opened as a TextDocument is a wall of JSON; the document-symbol provider has
nothing to say about it; and `openRef`'s fallback regex matches `"name":` inside a base64
output and reveals a line of PNG — so the reader ends up looking at machine noise where a
sentence promised them the code, which costs more trust than a click that did nothing. So
the notebook branch comes FIRST and owns the whole open (`openNotebookDocument`, then
`showNotebookDocument` with the cell as the selection), rather than being consulted after
the file is already open as text the way the settings branch is.

It reads the cells of the OPEN document rather than the file's bytes, so a citation still
resolves against a cell the author has edited and not saved — which is exactly when
somebody clicks one. Resolution mirrors the chunker's grammar (`_slug`, `_uniquify`'s
`train[1]`, flat sections) and lands a section on its HEADING and a member on the line
that DECLARES it. It resolves by longest section prefix rather than by `symbolLeaf`
alone: the leaf names the declaration to find, but only the prefix says which section to
look in, and two `## Train` sections can each declare a `fit`. A section whose member is
gone lands on the heading — the step exists, and the reader can see for themselves that
the name is not in it; a name that matches no section at all moves nothing.

### Telling the bootstrap pass whose words those are (`prompts/notebook_note.txt`)

Getting the paragraphs into the chunks is necessary and not sufficient: the pass on
the other end still reads them as a script that happens to contain long strings, and
then makes three mistakes it cannot make on a `.py` file. It paraphrases sentences a
PERSON wrote about their own work; it collapses the sections that person named into one
node, because the coarse-grouping rule is right for a module and wrong for an author's
own decomposition; and it reports the shell lines codoc commented out as code somebody
disabled — a claim about the author's file that the file does not make.

So `_notebook_note(file)` adds one instruction block for a `.ipynb` and returns `""`
for everything else. It is a per-file block rather than a standing "if this file is a
notebook" paragraph in the frozen instructions, because the path already settles the
question and a paragraph in the prefix would spend tokens on a case that is usually
absent. It rides in the **volatile tail**, for the same reason `why` does: a bootstrap
wave shares one cached prefix across every file it reads, and a per-file block in the
prefix would split that cache for every notebook in the repo — and for the `.py` files
sharing the wave with it.

## Doc language — the language the tree is AUTHORED in (`doclang.py`)

Two different things are called "language" here, and confusing them costs an
afternoon: `codoc/lang/` is **programming** languages (tree-sitter adapters);
`codoc/doclang.py` is the **authoring** language of the tree — what titles,
descriptions, and realize directives are written in. Orthogonal: a Python repo can
have a Mandarin tree.

**Where the setting lives.** `.codoc/config.json` (`{"doc_language": "zh-Hans"}`),
which is the one file in `.codoc/` besides the exports that is **tracked in git**
(`bootstrap.TRACKED_IN_CODOC`; `migrate.heal_gitignore` appends the exception to a
pre-existing `.gitignore`). It has to travel with the repo: if it lived only in
`CODOC_DOC_LANGUAGE`, a contributor running `codoc watch` without that export would
start appending English descriptions into a Chinese tree. Resolution is env var >
workspace config > `en`. Set it with `codoc init --doc-language zh-Hans` (before
bootstrap, so the first tree is already in the language) or `codoc lang zh-Hans`.
Any BCP-47 tag resolves — `en`/`zh-Hans`/`zh-Hant`/`ja`/`ko` have bespoke profiles
(title shape, punctuation register, embedder), everything else gets a generic one.

**How it reaches a model.** Four prompts carry a `{{doclang}}` marker, expanded by
`agent/base.load_prompt` at load time — *before* `split_prompt`, so the directive
lands in the cached prefix and cannot be displaced by a marker-shaped value. The
English directive is empty and the marker collapses, so an English repo's prompt
text does not move. The load-bearing line is *never translate code*: identifiers,
symbol paths, and `codoc:` link targets stay verbatim, or a translated tree quietly
loses its attachment to the code. `realize.txt` gets the `for_code_agent` variant —
its reader writes source files, so the tree's language must not leak into
identifiers or comments.

The coding agent is the one writer with no prompt in front of it, so the MCP reads
(`codoc_context`/`codoc_tree`/`codoc_status`) return a `doc_language` block, and the
writes (`propose_*`/`codoc_reflect`) attach an advisory `warning` when submitted
prose is in the wrong *script* — advisory because refusing would discard work
already done and leave the tree describing changed code.

**What had to change downstream.** Every lexical heuristic in the loop was written
for a script that puts spaces between words and needs ~6 characters to say one.
`doclang` answers both questions per-string rather than per-repo, deliberately: a
tree mid-migration holds English and Chinese nodes side by side, so one setting
would be wrong for half of them.

| Site | Was | Now |
|---|---|---|
| `loop_a`/`loop_b._norm_title` | `.lower()` + whitespace | `norm_key` — NFKC + casefold, so IME full-width forms don't mint a second node |
| `intent._terms` | `[^A-Za-z0-9]+` split | `terms` — n-grams per script; a Chinese prompt used to yield **zero** terms and fall back to recency |
| `divergence._tokens` | `[a-z0-9]+` | `tokens` — two empty sets compared as *identical*, so a Chinese realization always scored 1.0 and could never be flagged |
| `apply.preserved_ratio` | 24-char preserved run | `clause_chars` — 24 chars is a whole Chinese sentence, so every real amend scored ~0 and queued as a rewrite (returns 24 for Latin, unchanged) |
| `loop_a._placeholder_owner` | substring of the symbol leaf | + a *uniquely* matching term set, since a translated description contains no identifier; ambiguity declines rather than guesses |
| `why.py` char budgets | fixed char caps, `json.dumps` sizing | `char_budget` per script, `ensure_ascii=False` sizing (escaped, one CJK char measured as six) |
| `title_dedup.make_loop_embedder` | `all-MiniLM-L6-v2` | multilingual default for a non-English tree (an explicit `CODOC_EMBEDDER_MODEL` still wins) |
| `phase.intent_gloss` | English string | per-language table — this is codoc explaining the author's own edit back to them |
| control files + prompt payloads | `ensure_ascii=True` | `False` — readable diffs, and ~6× fewer tokens for CJK prose in a prompt |

### Bilingual by design — the setting says what codoc *originates*

The workspace setting is not a constraint on the tree; it is the language codoc
writes NEW prose in. What the tree actually contains is observed per node:

- **Originating** prose (a new node, a fresh description) → the workspace language.
- **Editing** existing prose → the language that prose is already in. An author who
  wrote one node in English inside a Chinese tree meant to, and an amend that
  translates it back is an unrequested rewrite of their words.
- **Mixed prose is correct writing.** Chinese prose carrying English library, API,
  and jargon terms is how bilingual authors actually write, and nothing flags it.

`doclang.detect_prose_language` decides, by script, after `strip_code_spans` removes
inline code, link targets, and `codoc:` refs — those stay in the code's language by
rule, so counting them measures the wrong thing. The floor is deliberately
**asymmetric** (`UNSPACED_FLOOR` 0.10 vs `SCRIPT_FLOOR` 0.30): borrowing runs one
way, and a Han content word is one or two characters where the English term beside
it is ten, so character share systematically overweights the Latin.
`使用 tree-sitter 解析 Python 与 TypeScript。` is 16% Han and unmistakably a Chinese
sentence — a symmetric floor called it English, which is backwards. Kana and Hangul
are checked separately because they are *decisive* where Han is shared between
Chinese and Japanese.

Detection rather than a stored per-feature column, for the same reason the script
helpers take no config: a column records what the language was when someone last set
it, goes stale the moment the author rewrites the node, and needs a migration to add.

Consumers: the MCP write advice compares an AMEND against the *target node's* prose
and only an ADD against the workspace default; `phase.intent_gloss` captions each
node in that node's language; the sidecar's `features{}.lang` and the MCP feature
rows carry a tag **only when it differs** from the tree's, so a monolingual tree
pays nothing and the field's presence is itself the "this node is the exception"
signal.

### Migrating an existing tree (`codoc translate`, `loop/translate.py`)

Because the setting only governs prose codoc *originates*, a tree already authored in
another language needs an explicit conversion. `codoc translate` is the one operation
that rewrites every description at once, so its design is about being safe to run:

- **Validation before application** (`check_translation`, pure and LLM-free). A node
  is REFUSED — left in its original language and reported — when its translation
  dropped a `codoc:` citation (a dead binding reference), an external link (a lost
  `Consult:` line), or a `**bold**` span (a lost `Focus:` line), when the prose came
  back in the wrong script, or when the new title would **collide with a sibling's**
  — two features translated into the same words become one to the soft
  `(normalized_title, parent_id)` key, and `migrate.dedup_features` would then
  converge them.
- **Applied, not proposed.** A whole-description rewrite fails the amend gate on
  every node, so routing through `should_auto_apply` would produce one proposal per
  node. The author asked for the rewrite by running the command — but only a command
  does this, never the language switch.
- **The writer role is restored** after each apply. `apply_op` reassigns
  `feature_writers` to whoever wrote last, so translating would re-stamp every
  human-authored node as loop-written, dropping it from `PRESERVE_RATIO_HUMAN` to the
  loose machine gate. The *event* still honestly records `actor=loop`.
- **No realize directives.** Directives are minted only in Loop B's channel drain, so
  a direct apply never queues code work — pinned by test, because the regression would
  ask an agent to reimplement the whole codebase.
- Resumable and idempotent: selection is by *detected* language, batches are applied
  as they land, and a failed batch is reported rather than rolling back the good ones.
- **Live progress channel** (`.codoc/translate.json`, v7): written at start, per
  batch, and (in a `finally`) at the end. Carries `running/target/total/translated/
  skipped` plus `pending` — the fids still awaiting their batch, which is exactly the
  IDE's per-node **skeleton set**. Each batch also re-renders the derived artifacts
  (`tree.codoc`/`tree.doc.json`/status) so the editor replaces each skeleton with its
  translated prose as batches land instead of snapping over at the end. Readers are
  lease-guarded (`TRANSLATE_LEASE_S`): a crashed run's stale `running: true` reads as
  dead, so no skeleton outlives the run that drew it.
- **Bidirectional.** English is a target like any other. An early guard refused to run
  whenever the target was English, on the reasoning that English is the default and so
  means "unset" — which made the language a one-way door: switching a translated tree
  back worked, and the one command that could act on it declined. Whether there is
  anything to do is decided by what the nodes say (`already`), never by which language
  was asked for.

### Display (`vscode-codoc/`, `codoc serve`)

`tree.bindings.json` carries `doc_language` (the tree's) plus per-node `lang`
exceptions. The webview stamps the tree's tag on `<html lang>` and each differing row
individually (`webview/doc-lang.ts`), because `lang` is what the *browser* reads for
per-element font fallback, line-breaking (a CJK line may break between any two
characters; a Latin one may not), and quotation conventions — a class would let us
style and leave the layout wrong. `doc-view.css` then keys on `:lang()`: zero
letter-spacing (the Latin tracking is tuned for Inter and collides full-width
glyphs), 1.85 line-height, `overflow-wrap: anywhere` with `word-break: normal` so a
long identifier inside CJK prose can break while English words stay whole, and
explicit CJK font fallbacks after the bundled Latin faces so the pairing is
deterministic per OS instead of accidental.

The toolbar carries the switcher (endonym-labelled — 简体中文, not "ZH"), which posts
`set-doc-language`; the host writes `.codoc/config.json` (`state/doc-language.ts`).
The host may read-modify-write that file, unlike `edits.json`, because the daemon
only ever reads it. The payload's `docLanguage` is read from **config.json, not the
sidecar** (`readDocLanguage`): the sidecar copy is daemon-written, so sourcing it
there made the switch write the right value and appear to do nothing until the next
render pass — and never at all with no daemon running. `config.json` is also in
`WorkspaceState`'s watch list, so a `codoc lang` run in the terminal repaints the
view, and `applyTreeLang` runs on *reconcile* as well as first mount — `renderAll`
happens once, so without that the root `lang` kept whatever the workspace had when
the editor opened and every `:lang()` rule stayed wrong for the session. The
config read itself lives in `state/codoc-config.ts`, deliberately free of any
`vscode` import so it is unit-testable: a switch that writes the right value but
keeps reporting the old one is indistinguishable, from the author's seat, from a
switch that did nothing. On the **hub** the host sends no `docLanguageChoices`, so the
same control renders as a read-only label: a remote contributor suggesting edits has
no business changing the language a maintainer's repo is authored in.

**Not covered (deliberately):** the extension's own UI strings are still English —
there is no `l10n` bundle, and `hold-decorations.ts` frames the (localized) gloss in
English text. Content language and interface language are separate pieces of work;
this is the first.

## Render + sidecar (`codoc_file/render.py`) and the `.codoc` control files

`write_tree` writes `tree.codoc` + the sidecar; the sidecar is **pure derived
state** and is re-emitted on every pass even when the text render is held back
(so an accept/reject is never a dead click). All `.codoc` files use atomic writes
(tmp → rename) + tolerant reads (missing/corrupt → default) via `loop/fsio.py`.

- **`tree.codoc`** — the human-authored tree. `- Title  ⟨f-id⟩` (id hidden);
  free-prose multi-paragraph descriptions; inline `[label](codoc:file#symbol)`
  citations. Bindings are *not* printed (they ride in the sidecar). Pending ADD/MOVE
  render as ghost hunks; RETIRE/AMEND emit no text.
- **`tree.bindings.json`** (v6) — the IDE/browser sidecar: `by_feature`/`by_file`
  bindings, `features{}` (each carries `lifecycle` + the legacy `realized`),
  `proposals` (drives in-place overlays + Accept/Reject), `changes` (recent applied
  events), and derived reading slices (`pitch`, `feature_kind`, `feature_see_also`,
  `feature_impact`). The last two are opposite directions of one graph and both stay
  out of the prose: `feature_see_also` is what a feature depends ON,
  `feature_impact` is which features would feel a change TO it — Sillito's group-4
  question, drawn as the hover-revealed chip on the heading
  (`webview/tiptap/impact-decorations.ts`) and handed to an agent on every
  `read_context`. Absence means nothing depends on the feature; one edge-table read
  serves both slices.
  The **mid-flight slices** — `feature_phase` (the single per-feature Phase),
  `holds`, `hold_detail`, `feature_drift`, `feature_resolution` — are ALL thin views
  of ONE `loop/phase.py:compute_phases` pass (Proposal B), off one source of truth.
  Doc-wins is resolved in the primary `feature_phase` slice (a held feature reads
  `drafting`/`queued`, never `drifted`/`divergent`); the `feature_drift`/
  `feature_resolution` slices keep the former `_live_*` filters unchanged. The TS
  reader (and the hub's `payload.py`) key on field presence, so older sidecars
  still parse.
- **`status.json`** — `{state ∈ in_sync | code_drift | tree_dirty | awaiting_impl |
  realizing, pending, at, …}`; drives the status bar + header CodeLens (and the
  hub's restart-safe payload version via its `at` HLC). A non-empty `realize.md` is
  a floor (reports `awaiting_impl`, never clobbered to `in_sync`). `realizing` is a
  **lease**, not a flag (WS1.5): trusted only while the file was written within
  `REALIZING_LEASE_SECONDS` (300 s) AND `realize.md` is still present. Live passes
  renew it per directive (`codoc_realize_progress` / sdk_realize); `refresh_status`
  preserves a fresh lease *without rewriting the file* (a rewrite would blank the
  pass's `detail`/`pending` and renew the mtime the lease is keyed to), and a stale
  lease decays back to ground truth on the next recompute. Similarly, activity.json
  epoch/phase liveness is leased by its readers (`epoch_alive` 90 s UI TTL /
  `EPOCH_STALE_SECONDS` 900 s daemon TTL; per-feature phases 120 s on their `at`).
- **`realize.md`** + **`realize.json`** — the realization queue (directive prompt
  for `/codoc:sync`) + its machine-readable manifest `{id, feature_id, kind,
  caused_by, text, handed_off}`. `text` lets a later pass rebuild the queue as
  old + new.
- **`realized.jsonl`** — durable directive outcomes: when the queue drains
  (`read_manifest` sees no `realize.md`), each handed-off directive is appended
  here `{id, feature_id, kind, caused_by, text, completed_at, ts}` (idempotent by
  id, bounded tail) before its manifest entry vanishes — join against
  `events.caused_by` for the code changes it produced. Read via
  `edits.read_realized`.
- **`intent.jsonl`** — captured author prompts: the `UserPromptSubmit` hook
  appends `{session_id, at, ts, prompt}` (slash commands skipped, bounded tail);
  Loop A's `recent_intent` threads the fresh epoch-owning-session tail into the
  tree-update prompt as `changes["author_intent"]`, and `codoc_status` /
  `read_status` expose it as `recent_intent` so a fresh session can resume where
  the author left off. Gitignored with the rest of `.codoc/`.
- **`ask.json`** — the `/codoc:ask` walkthrough overlay (`loop/ask.py`): a single
  slot holding `{version, id, question, answer, at, steps[{label, feature_id, note,
  quote?, group?, file?, symbol?, line?}]}`. A walkthrough is a numbered *reading
  path over features that already exist* — the `codoc_walkthrough` MCP tool writes
  it, the IDE draws it (chip + note + quote wash), and it is deleted to dismiss. Like
  `activity.json` it is **ephemeral**: no loop reads it, nothing derives from it, it
  writes nothing to the store or the change ledger, and it is safe to delete at any
  time — which is what lets an ask happen at any point in an edit. mtime-TTL'd (8 h)
  so a restart never resurrects yesterday's question; `label` (`1a 1b / 2a`) is
  computed by the writer so an LLM cannot emit a broken sequence. It rides the hub
  payload read-only; only a HANDOFF viewer may dismiss it (`serve/dispatch.py`).
- **`revisions.json`** — the TIMELINE transport (W8, `loop/revisions.py`): a bounded
  newest-first window (150) of *applied* events, each carrying what it wrote AND what it
  displaced (`prev_title` / `prev_description` / `prev_parent_id`, recorded at the
  `apply_op` write boundary), plus a `directives` map joining every `caused_by` to its
  request — `asked` (the captured author prompt), `session_id`, `base_sha`. Derived and
  rebuildable like the sidecar, and written from the SAME `recent_events` scan
  `write_sidecar` already does, so it costs one JSON dump rather than a database read;
  it self-skips when the window has not moved, so an event-free pass writes nothing.
  Its own file rather than a sidecar slice because it is the one view that carries
  PROSE, and the sidecar is re-read on every pass by everything. `REFRESH` is excluded
  (fingerprint churn a reader cannot see would bury everything that matters).
  The editor reconstructs past states from it LOCALLY, backwards from the live
  projection (`state/revision-model.ts`) — a scrubber cannot afford a round trip per
  frame, and the webview has no request channel to the daemon.
- **`tree.index.json`** — cross-reference registry (features/bindings/refs) for
  dead-ref flagging + hover; `refs[].resolved` is leaf-tolerant.
- **`drift.json`** — `{fid: "questioned" | "binding-lost"}`, re-emitted as the
  sidecar `feature_drift` slice (excludes held + unrealized).
- **`inbox.json`** — `{verdicts:[{event_id, accept}]}`, written by the hub/CLI
  (`inbox.append_verdict`, lock-held), drained by Loop B — the SOLE verdict
  applier; `codoc_await_verdicts` only observes outcomes from the event ledger
  (accepts are stamped `caused_by=<proposal e-id>`) and, with no daemon alive,
  drains by running the same Loop B pass. Read-modify-write is `filelock`-guarded.
  A verdict may carry optional `title`/`description` **accept-time edits** (v7):
  ghost proposals are editable in the IDE before acceptance, and Loop B applies
  the proposal with the edited text (`_amended_verdict_op`) — the plan directive
  minted from an edited plan ADD speaks the edited text too. Absent fields mean
  "as proposed", so old writers/readers are unchanged.
- **`inbox.host.jsonl`** — the IDE's **verdict append-log** (one
  `{event_id, accept}` JSON line per Accept/Reject click). The extension host
  holds no cross-process lock, so its old read-modify-write of `inbox.json`
  could land inside `drop_verdicts`' locked window and erase a click; appends
  can't erase anything. Folded into `inbox.json` under the inbox lock on first
  read (`inbox.merge_host_verdicts` — every `read_verdicts` merges first; last
  line wins per event id). Same pattern as `edits.host.jsonl` below.
- **`edits.json`** — the authored-edit + provenance channel: `commands` (the
  identity-keyed add/set_title/set_description/move/retire Loop B applies), `edits`
  (authorship annotations), `intents`/`drafts` (live doc-ahead suggestions + held pending
  edits — the doc-wins hold set), `handoffs` (the one-shot positive realize signal),
  `cancellations`/`steers`, `block_edits` (v6 — typed-media block edits drained by
  Loop B's `lower` dispatch), and `comment_resolves` (W8 — threads the author closed;
  its own one-shot channel because resolving queues no work, and routing it through
  `steers` would mint a directive to do nothing). Read-modify-write is `filelock`-guarded
  (the hub is a second writer). The set of channels is `_LISTS`, and the serializer
  derives its optional-key loop FROM it — a hand-maintained second list beside it is how
  a channel gets merged in memory and silently dropped on write.
- **`edits.host.jsonl`** — the IDE→daemon **append-only op log**, and the only thing the
  extension host writes. It is a separate process that does not hold the `edits.json`
  lock, so a read-modify-write from there could clobber the daemon's or the hub's; instead
  it appends one `{fn, arg}` op per line (O_APPEND is atomic per small write, so two IDE
  windows may append concurrently) and the daemon MERGES the log into `edits.json` under
  the lock at the start of every Loop B pass (`edits.merge_host_ops`, crash-safe via a
  `.merging` sidestep). The hub has no such log: it holds the lock itself, so
  `serve/dispatch.py` writes `edits.json` directly.

## Environment variables

| Var | Default | Description |
|---|---|---|
| `CODOC_PROVIDER` | inferred | `claude` / `openai` / `anthropic` / `ollama`. Unset → `openai` if `OPENAI_API_KEY`, else `anthropic` if `ANTHROPIC_API_KEY`, else **keyless `claude`** (Claude Code login) |
| `CODOC_MODEL` | per-provider | default `gpt-5.4-mini` / `claude-sonnet-4-6` / `sonnet`; cross-family value ignored |
| `CODOC_MODEL_FAST` | per-provider | model for the high-volume extraction calls (tree update, per-file bootstrap): default `gpt-5.4-mini` / `claude-haiku-4-5` / `haiku`; an explicit `CODOC_MODEL` overrides both tiers |
| `CODOC_LLM_TIMEOUT` | `300` | seconds before an LLM call (any provider, incl. the `claude` CLI spawn) is abandoned |
| `CODOC_EMBED_CHUNKS` | — | `1` → compute + store chunk embeddings in the LanceDB index (OFF by default — nothing reads them today; flipping the flag rebuilds the index under the other schema) |
| `CODOC_BOOTSTRAP_CONCURRENCY` | `8` | per-file bootstrap LLM calls run in concurrent waves of this size (`1` = serial) |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | — | API keys (also select the provider) |
| `CODOC_BASE_URL` | — | custom OpenAI-compatible base URL |
| `CODOC_TEMPERATURE` | `0.2` | sampling temperature |
| `CODOC_MAX_TOKENS` | `16000` | completion budget |
| `CODOC_EMBEDDER_PROVIDER` / `CODOC_EMBEDDER_MODEL` | `sentence-transformers` / `all-MiniLM-L6-v2` | embedder (dedup/similarity). The model default follows the doc language — a non-English tree gets a multilingual model, since the English one cannot compare its titles |
| `COCOINDEX_DB` / `CODOC_LANCE_PATH` | `.codoc/cocoindex.db` / `.codoc/lancedb` | index state paths (auto-set) |
| `CODOC_LOG_PROMPTS` | — | `1` → log LLM prompt+response to stderr |
| `CODOC_SEMANTIC_DEDUP` | — | `1` → enable D1 embedding near-duplicate title dedup in Loop A (off by default; needs a corpus-tuned threshold) |
| `CODOC_EPOCH_ORIGIN` | `interactive` | `loop_b` marks an agent-owned epoch |
| `CODOC_AGENT` | `claude-code` | role id of the coding agent driving codoc (`claude-code`/`codex`/`gemini`/`cursor`/…); stamped on the activity epoch (W1) so presence/ribbon/blame attribute to the real agent |
| `CODOC_NO_STOP_REFLECT` | — | disable the Stop-hook recovery reflection |
| `CODOC_DOC_LANGUAGE` | — | BCP-47 tag to AUTHOR the tree in, overriding `.codoc/config.json` for this process only. Prefer the committed setting (`codoc lang`) — an export that only one machine has is how a tree ends up half in each language |

## VS Code extension internals (`vscode-codoc/`)

**Editing model.** Store-authoritative (2026-06 refactor —
`docs/plans/2026-06-26-001-refactor-store-authoritative-coordination-plan.md`,
origin brainstorm under `docs/brainstorms/`). The **SQLite store is the single
source of truth**; the webview is a pure *projection* of the store plus an
identity-keyed *command emitter*. Both `tree.doc.json` and `tree.codoc` are
daemon-written derived artifacts — the daemon (`loop_b.write_tree_doc` /
`write_tree`) is their sole writer, `tree.codoc` is a read-only export, and there
is no shared-writer file. The webview *consumes* the daemon-written `tree.doc.json`
projection (built by `codoc_file/doc_render.build_doc_from_store`, which carries
each feature's `localId` + per-feature `updated_at` HLC, plus marks/comments lifted
from the store tables); editing actions emit identity-keyed commands `{id, kind,
fid|localId, baseRev, payload}` (kinds: add/set_title/set_description/move/retire)
on `edits.json`, applied to the store via `apply_op` with an applied-command-id
ledger for idempotency — nothing is inferred from a doc diff (the old
`doc_presence` / `doc-fids.json` deletion-inference is retired). A **per-feature
HLC version gate** (replacing the old `rev`/`docAhead` exact-text equality)
prevents a returning projection from clobbering a newer un-acked local edit on a
different feature.

Two provenance rules make concurrent editing safe, and both exist because getting them
wrong loses text in silence rather than loudly:

- **The editor owns the citation.** A settle names the `baselineId` of the projection its
  content was ADOPTED from (`whole-doc-editor.setDoc` stamps it at the end of the adopt),
  and the host diffs against that exact baseline. An arriving projection FLUSHES unsent
  typing, so that flush carries pre-adoption content — citing the arriving payload instead
  made every feature the daemon had just changed read as a user edit that reverted it.
- **`base_text` comes from the author, never from a projection they may not have seen.**
  A command's `base_text` is this host's own emitted-but-unechoed write for that field
  (`state/known-store.ts`, an optimistic overlay advanced only by successful appends and
  *pruned* — never seeded — by a projection that confirms them), else the text of the
  baseline the settle cites. The host reads projections the webview may never adopt (the
  gate defers on IME/composer and keeps a feature local while its edit is unsent), and a
  `base_text` taken from one of those equals the store's current text — which reads as a
  clean continuation, so the daemon applies over the other party with no merge and no
  record. When a citation cannot be resolved at all, the OLDEST retained baseline is used:
  under-claiming the base makes the daemon cautious, over-claiming makes it blind. Every unsettled span — the author's, an agent's proposal,
and what the codebase itself now says — surfaces through ONE model, the
**settlement** model (`state/settlement.ts`; see the section below). A
**draft / hand-off** gate keeps code-implying edits safe-by-default: the host marks
pending edits as `drafts` in `edits.json`, the daemon holds their directives, and
"Hand to agent" clears the drafts → queues `realize.md`. Marks (tracked-change
authorship ink) and inline comment threads now live in the store `marks` /
`comments` tables (`model/annotation.py`) so they survive a reload; the projection
re-emits them onto the inline runs. A one-time idempotent migration
(`loop/migrate.py`, run by `codoc migrate` and once on daemon startup) heals
workspaces that predate the refactor: it lifts pre-existing `tree.doc.json`
comment threads into the store and converges re-minted duplicate features onto the
binding-owner. It also installs **slash commands that shipped after the workspace
was wired up** (`heal_plugin_commands`): `install-hooks` writes one file per
command and nothing re-ran it on upgrade, so a workspace wired before
`/codoc:ask` existed kept `/codoc:plan` and `/codoc:sync` for good — the command
was in the package, the docs and the MCP, and absent for everybody who had already
installed codoc. Adds only, and only where `.claude/commands/codoc/` already
exists; a workspace without one never asked for the plugin.

**Settlement — the three channels (2026-08).** Six decoration families used to
answer the same question about the same character range (*whose claim is this, and
how far along is it?*), each with its own baseline, granularity and hue — so they
could not compose, and "which one wins" was decided by z-index plus stand-down
flags. They are now ONE model. Design doc:
`docs/plans/2026-08-19-003-settlement-three-channels.md`.

A **claim** is a range + a CHANNEL (who is ahead) + a STAGE (how far along). The
three channels are orthogonal and each owns a *different property of the text*,
which is what lets them stack on the same words without a legend:

| channel | axis | meaning |
|---|---|---|
| `human` | INK — blue | you wrote it; the code does not say it yet. Pulsing while unsent (`open`), steady once handed off (`committed`). **Ink only — no diff view:** your own removals are not drawn (you removed them), while the other two channels do report theirs. The claim is still emitted, so a deletion-only edit still reaches the marker. |
| `plan` | OPACITY — faded | an agent's words. `proposed` = awaiting your verdict, materialized old-AND-new. `accepted` = solider, and sourced from the queued DIRECTIVE rather than the proposal, because accepting deletes the proposal row (see below). |
| `code` | GROUND — green / red | it surfaced back from code that exists (`landed`), at sentence granularity. |

**The palette is the same on all four surfaces** — the prose, the margin marker, the
tree rows and the minimap rail. It was not: the rows and the rail each had a ramp of
their own in which "sent" was the code channel's green and "proposed" was the author's
blue, so two of three states named the wrong party. `feature-state.STATE_CHANNEL` maps
every row state to its channel, `railState` DELEGATES to `featureState` (it used to be a
parallel copy and had drifted), and `working` spends no channel hue at all — an agent
being somewhere right now is not a claim about who is ahead, so it is neutral plus
motion. Inline blame reads the same three inks, since it is the same three parties seen
from a different question and History draws it beside settlement claims.

**What may stack, and what may never.** Composition is the design, so the combinations
are the specification. Ink over ground:

| ground \ ink | none | blue (human) | gray (plan) |
|---|---|---|---|
| none | settled prose | you wrote it, not built yet | planned; nothing built yet |
| green (add) | the codebase added this | **✗ impossible** | planned, and the build put these words in |
| red (cut) | the codebase dropped this | **✗ impossible** | **planned, and the build did not keep it** |

The bottom-right cell is what the model exists for. The two ✗ cells are contradictions —
"you wrote this" and "the codebase wrote this" cannot both be true of one sentence, and
the reader cannot tell which half is lying — kept empty by three rules: human and code
claims are both diffs into `projected` and the CODE claim yields wherever the human also
claims (`outranks`, in the surface); a code claim is all-or-nothing through later typing;
and human claims are the inserted runs of a diff, which are by construction absent from
the `same` regions other channels map through.

Modules: `state/settlement.ts` (the model, pure), `state/settlement-stages.ts`
(the host half that rides the payload as `stages`), `webview/tiptap/settlement-
decorations.ts` (draws it), `state/node-status.ts` +
`webview/tiptap/node-marker.ts` (the margin marker, which ACCUMULATES rather than
ranking — see below), `state/fulfilment.ts`, `state/history-claims.ts`,
`state/edit-baseline.ts`, `state/plan-materialize.ts`, `state/display-space.ts`,
`state/para-align.ts`.

Load-bearing details:

- **Coordinates.** The channels share an ORIGIN (`projected`, what the daemon
  wrote), not a baseline: `code.prev →(code)→ projected →(plan)→ planned
  →(human)→ live`. Everything resolves into LIVE block coordinates up front by
  chaining content-based paragraph pairings backwards (`state/para-align.ts`), so
  a claim computed in one paragraph's coordinates can never be drawn in
  another's. Offsets are in **display space** (`state/display-space.ts`).
- **`humanBase` is not `projected`.** After ⌘S the daemon applies the edit and
  projects it straight back, so a human diff against `projected` is empty and the
  blue ink would vanish at the exact moment it becomes true. The base is
  `hold_detail.baseline` once held, the editor's frozen baseline before that.
- **The human channel walks TWO hops, never one.** `humanBase → live` in a single diff
  is wrong the moment a plan is on screen: the plan's sentences are in `live` and not in
  `humanBase`, so they came back as the author's own typing — the agent's proposal inked
  blue and offered for ⌘S. It follows the chain instead: (a) `humanBase → projected`,
  what the author already handed over, carried forward; (b) `planned → live`, what is on
  screen and not in the store. The plan lives strictly between them.
- **Paragraph splits happen in RAW text, never in display space.** Display space
  collapses every newline to one atom char, so a `\n\n` split taken after the projection
  can never match — a multi-paragraph amend produced ONE planned block against the
  editor's several, every later paragraph came back unpaired, and the whole of it was
  reported as text the author had just typed. `plan-materialize.descParas` is the one
  place that answers where a description's blocks divide, and `planRuns` takes paragraph
  ARRAYS so the mistake cannot be re-made through its signature.
- **An accepted plan is sourced from the QUEUE, not the proposal.** Accepting deletes
  the proposal row, so `stage: 'accepted'` was unreachable and the plan's wording became
  ordinary prose at the click. A `Directive` records `origin` (`human` | `plan`,
  surfaced via `hold_detail`), and a plan-origin hold routes its `baseline` to
  `FeatureLayers.accepted` (gray) instead of `humanBase` (blue). `accepted` is a
  separate field because its GEOMETRY differs: accepting APPLIES the wording, so the new
  side is on screen and the displaced side is gone — one side, like the code channel,
  where a materialized proposal has both.
- **Opposite drop rules, opposite reasons.** A `code` claim is ALL-OR-NOTHING: it
  reports what the codebase says at sentence granularity, so once the author edits
  inside that sentence it is not the sentence the report was about. A `plan` claim
  SPLITS around the author's typing: a proposal is text you are meant to edit in
  place before accepting.
- **A third edit kind, `cut`.** A proposal is materialized as old-AND-new (the
  engine keeps the displaced sentence so the reader can compare), so plan-removed
  text is ON SCREEN — a range to strike, not a `del` point, which would print the
  sentence a second time beside the copy already there.
- **Nobody has to decide.** Claims are derived, never stored, so an unanswered
  proposal needs no mechanism: it stops being offered and stops producing claims.
  A replacement proposal is computed against the store's current text. Unanswered
  TYPING keeps its marks, because `humanBase` moves only on adoption.
- **Fulfilment is the exception** (`state/fulfilment.ts`) and structurally so: it
  is the moment the difference DISAPPEARS, so a pure function of the claims could
  only ever show a realized edit's mark silently ceasing to be drawn. Watched as a
  transition and remembered for 30 minutes. It watches the AGREED set, not the offer:
  proposal→agreed is an accept (nothing built, no ring), proposal→gone is a rejection
  (likewise), agreed→gone with the node standing is the directive closing, and a feature
  leaving the hold set is the author's own edit unless that hold was plan-origin. Firing
  on the offer disappearing was wrong twice over — it filled the ring at the click, and
  it filled it on a REJECTED amend or retire, whose node stays in the tree. It also
  closes the old known gap: an accepted ADD is now reported, because the agreed set is
  keyed by the id the STORE minted and only an accept produces one.

**A plan is written INTO the document** (`state/plan-materialize.ts`). A proposed
ADD used to be a widget beside the doc: honest about being a proposal, dishonest
about how the tree would READ with it in, which is the only question a verdict
asks. Now the node goes where it will go, flagged `proposed`, drawn in the plan
channel's opacity; a RETIRE strikes the words it proposes to remove. **The safety
rule:** the moment an agent's words are in the document, every path projecting the
document back to authored state can author them as the human's. Three call sites
honour the flag — `featureUnits` (no commands), `renderTreeFromDoc` (not exported
to `tree.codoc`), and the baseline-aware `inlineRunsToText`. A plan node may only
be inserted at a HEADING BOUNDARY: prose routes by `ownerId` where stamped and by
POSITION where not, so a node dropped mid-description captures the paragraphs below
it, and a planned node's contents are discarded — silent prose loss from a
rendering decision.

**The node marker accumulates** (`state/node-status.ts`). `feature-state.ts` ranks
a lifecycle and picks one state, and for what it models that is right. The
settlement channels are not stages of each other: a node that was planned, then
built, and built DIFFERENTLY carries three facts a rank reduces to one, dropping
precisely the two that make the reader look. Fixed slots, at most three glyphs —
`●` whose it is (blue), `○` whether it was planned (gray), `±` whether it drifted.
Both rings fill on the same event: the claim reached the code. Computed from the
SAME claims as the prose, so the margin cannot disagree with the page.

**The past reads in the same grammar** (`state/history-claims.ts`). A moment's
changes go through the same `claimsFor` at the same sub-sentence granularity, in
the channel of the ledger's `actor` — the author's moments are `human`/`committed`
(never `open`: a pulse is a prompt to act on something finished), everything else
`code`/`landed`. It still refuses to diff against words the ledger never recorded.

**Workflow-legibility surfaces (v7).** Four surfaces make the messy, non-linear
workflow readable in place:

- **Busy skeletons** (`webview/tiptap/busy-decorations.ts`): a section being
  rewritten under the reader — a `codoc translate` batch pending
  (`payload.translation.pending`) or the agent applying (activity phase
  `reflecting`) — wears a shimmer skeleton in BOTH panes and drops user
  transactions inside its span (`filterTransaction`; REFLECT-tagged programmatic
  updates pass, or the rewrite could never land). Per-section, never
  whole-document: the skeleton set shrinks as batches land. `editing` (agent in
  the feature's CODE) is deliberately not busy — the doc text isn't moving then.
- **Two-stage language switch** (`doc-view.renderLangSwitch`): picking a language
  is stage 1 (config.json only — new prose); a standing "Translate N nodes?"
  offer is stage 2, spawning `codoc translate` from the host
  (`tree-editor.startTranslate`) with progress via `.codoc/translate.json` →
  per-node skeletons → incremental reveal. The offer's count comes from the pure
  `doc-lang.pendingForTarget`.
- **The minimap rail as a status strip** (`whole-doc-editor.rebuildRail` +
  `feature-state.railState`): one tick per feature in the SAME ordered projection
  the row badge uses (busy > working > proposed > rewritten > sent > staged >
  planned), same hues as the in-document decorations, a contiguous `in-view` band
  as the viewport indicator, and a legend pinned under the ticks. Tick titles name
  the state in words (`RAIL_STATE_LABEL`) so nothing rests on hue alone.
- **Loop rewrites as a review surface** (`webview/tiptap/auto-edit-decorations.ts`):
  an unasked Loop-A AMEND carries an explicit **Keep / Restore** verdict — the v6
  dwell-to-clear model is retired (a record that evaporates when looked at cannot
  be disagreed with). Keep acknowledges; Restore re-authors the previous wording
  through the ordinary command channel (`tree-editor.resolveAutoEdit` →
  `set_description`), where the daemon classifies it and, if code-implying, HOLDS
  it for hand-off like any other edit. The DIFF it is a verdict on is the
  settlement model's code channel (below); this module draws only the decision.
- **The `/codoc:ask` walkthrough** (`webview/tiptap/ask-decorations.ts` +
  `webview/ask-bar.ts`, state in `state/ask-model.ts`): a numbered reading path over
  existing features, read from `.codoc/ask.json`. Deliberately the one layer with
  **no hue** — every other decoration's colour carries lifecycle meaning, and a
  walkthrough is a reading order, not a state — so it reads as structure: an ordinal
  chip in the margin, the stage named once, a one-line note, a graphite quote wash
  (suppressed on any feature already carrying a diff, so two highlights never
  overlap). It writes nothing and blocks nothing, which is why an ask is safe
  mid-edit. The header carries the answer + a stepper; dismiss deletes the file.
- **The timeline — the tree's own past, in place** (W8). The History stance grows a
  scrubber (`webview/timeline-bar.ts`) over every MOMENT the tree has been in — a run of
  events by one author, for one cause, within two minutes, because a raw event list is
  not a timeline (bootstrap alone mints dozens of adds in one second). Dragging back
  renders `webview/revision-view.ts`: a READ-ONLY reconstruction, deliberately not the
  live editor with old text in it. The editor is wired to a settle→command pipeline whose
  job is to notice document changes and report them, so pushing a past revision through
  it would record the author reverting the whole tree; a separate surface makes that
  class of accident structurally impossible rather than carefully avoided. It is still
  in situ — same pane, same measure, the editor's own classes — and `body.history` tints
  it so it can never be mistaken for today.

  `state/revision-model.ts` does the reconstruction, BACKWARDS from the live projection:
  `liveSnapshot` reads the doc (depth on the heading → parent links), `snapshotAt` undoes
  every entry newer than the chosen moment (newest first — two amends to one feature only
  compose correctly unwound in reverse), and `changesAt` reports the before/after a reader
  perceives rather than each intermediate step. What it cannot recover it says: an event
  predating the `prev_*` fields lands in `Snapshot.unresolved`, and the page prints that
  instead of diffing against invented words. Diffs are DECORATIONS/plain DOM built from
  `wordDiff`, never engine marks — the same reason `auto-edit-decorations` gives: the text
  is already applied, and marked prose serializes back to the previous text.

  **Blame is per SPAN** (`state/inline-blame.ts`, W9). The stance used to label a whole
  feature with its last editor, which answers a question nobody asks: a feature is several
  paragraphs written by a person, a loop pass and an agent in turn, and the reader is
  deciding whether to trust ONE claim. `blameDescription` replays the revision window's
  word diffs oldest-first — each event's `prev_description → description` says which words
  it introduced, and kept words carry their owner forward — so every surviving span is
  attributed to the party that wrote it, from data already on the wire. Three refusals keep
  it honest: a span the ledger cannot account for stays **unattributed** (crediting it to
  the nearest editor is the exact error the per-node version made, and per word it would
  multiply); a recorded `prev` that disagrees with the replay **drops** the attribution
  rather than sliding every later offset by the difference; and where one author owns the
  whole description **nothing is drawn**, because the heading's label already says so and
  underlining every word to report it would be the node-level signal again.

  This replaced the per-paragraph blame rail, which was also one of FOUR rails competing
  for the same `::before` on a description block — so turning the stance on used to erase
  the "recorded, not sent" and "queued for the agent" cues, and a reader asking who wrote
  something lost the signals about work they owed.

  **Provenance is one card** (`state/provenance.ts` + `webview/provenance-card.ts`),
  reached from two ends — a moment on the timeline, or the History stance's per-feature
  label (which became a BUTTON: a native `title` could show who and when and could never
  be acted on). It walks the chain the ledger has always held and never displayed
  together: change → directive → the prompt someone typed → the session → the commit the
  work started from → **the code diff**, opened through the read-only `codoc-past:` scheme
  (`providers/past-content.ts`, `git show <sha>:<path>`) against the working tree. Rows
  with no value are omitted rather than shown empty; a cause whose directive has aged out
  of the bounded logs says so, because "we know this had a reason and no longer have it"
  is a different fact from "this had no reason".

  It also carries the **warrant** — see "Warranting a stated why" below. Every link in
  that chain says what happened *before* a claim; a `Rests on` row is the only one that
  says why to believe it, and it quotes the source rather than restating it. The row comes
  from the same entry that supplied the `Why` above it (`provenance.ts:warrantRows`),
  because one timeline moment can hold several ops and pairing a reason with a neighbour's
  evidence would offer that evidence for a claim it was never offered for.
- **Comments as requested work** (W8). The document never moves for one: margin cards hang
  in the whitespace that already exists and, where it cannot hold a card without covering
  the prose (`marginFits` — the surface is not the viewport, so the old `max-width: 1100px`
  media query keyed on the wrong box), the anchor opens the thread as a popover instead.
  The prose-shifting column it replaces fired on ARRIVAL as well as authoring — somebody
  else's comment yanked your page sideways mid-read — and three times over when you wrote
  one; the `transition` meant to soften it named a property nothing ever changed, so all of
  it landed as an unanimated jump, and at most real widths it was not a slide but a full
  re-wrap. Cards are now REUSED across a re-layout (keyed by thread id) instead of rebuilt
  per keystroke, so `top` finally has an element to animate on. Hovering either the
  commented words or their card lights both — the tie that lets a card pushed down the
  stack still name its sentence. A thread is durable (store `comments` table →
  sidecar `comments` slice → `comment-model.storedThreads`, merged local-wins with this
  host's un-drained ones); it scopes its directive to the `codoc:` citations inside the
  span it is anchored to (`codeRefsIn` — no picker, because a description already cites
  its code); it can ask for the prose to follow the code (`scope: both`); and it records
  the directive it produced, reaching `resolved` when that directive lands so it can then
  show the code it caused. **Build it** additionally runs `codoc realize` in a terminal
  (`codoc.realize`) — visible and interruptible, because this is the one action that lets
  an agent write to the user's source files. Extension only; the hub hides the verb.
- **Find & replace** (`webview/find.ts` pure logic, `webview/find-view.ts` widget,
  `webview/tiptap/find-decorations.ts`): ⌘F / ⌘⌥F over the tree doc's titles and
  descriptions (`tree.codoc` is a read-only export the daemon overwrites, and a
  webview receives no native Find widget, so the one document you can actually edit
  had no search). Highlights are decorations, never marks; **replace routes through
  the ordinary settle→command path**, so a replace is indistinguishable from typing
  and its `base_text` provenance stays trustworthy. Replace-all runs back-to-front
  (original positions stay valid) and skips sections the agent is mid-rewrite. The
  `codoc.find` / `codoc.replace` commands cover focus outside the iframe.

Key source files: `state/workspace-state.ts` (root/reload/status, `writeVerdict`),
`state/tree-model.ts` (TS port of `parse.py`, parity-tested), `state/bindings-model.ts`,
`state/edits-channel.ts` (edits.json contract), `state/agent-proposals.ts`,
`state/translate-model.ts` (lease-guarded translate.json reader),
`providers/{decoration,inlay,completion,doc-links,code-lens,code-actions}.ts`,
`providers/tree-editor.ts` (+ inline-comment lifecycle), `webview/host-bridge.ts`
(the VS Code/network transport seam), `webview/tiptap/*`, `daemon/{lockfile,daemon-manager}.ts`,
`extension.ts` (activation + command registration).

## The deployed hub (`codoc serve`, `codoc/serve/`)

A Tier-1 web surface that serves the intent tree to GitHub-authorized remote users
**from the maintainer's own machine** (a separate process that supervises the
daemon; a file-channel client that reads `.codoc/*` and writes only the
verdict/draft channels). Remote contributors *suggest* edits; a maintainer hands
them off; the hub realizes them on a git worktree and opens a code PR. See
`docs/serve-deployment.md` for setup. Modules:

- `supervise.py` — atomic single-owner claim + daemon supervision (peer to the
  extension's `daemon-manager.ts`).
- `app.py` — the composition root (FastAPI): static SPA, `/api/payload`,
  `/api/events` (SSE), `/api/whoami`, the CSRF-guarded `POST /api/command`.
- `payload.py` / `push.py` — derive the browser `DocPayload` by re-shaping the
  sidecar (no re-derivation); SSE snapshot + version-guarded re-push with an
  idempotency (no-broadcast-storm) guard. Version = the daemon's HLC stamp on
  `status.json` (restart-safe, not a per-process counter).
- `auth.py` — GitHub collaborator permission → capability (read→suggest,
  write→hand-off); server-side sessions (the browser holds only an opaque cookie).
- `dispatch.py` — capability-gated routing of browser commands to the file channels;
  remote `commit`/settle is held (only an explicit hand-off crosses to execution, and
  `hand-off` writes `handoffs` — the positive signal Loop B reads — not just clearing
  `drafts`). A settle carries no content: the browser emits identity-keyed **commands**
  itself (`webview/command-emitter.ts`, the same `settleCommands` + `known-store` modules
  the extension host uses), so nothing here writes a derived artifact. It used to persist
  the posted doc to `tree.doc.json`, which the daemon has owned since U4 and nothing has
  read as input since U7 — the remote author's prose was overwritten at the next render,
  and the write made `safe_write_tree` skip re-rendering both exports.
- `ratelimit.py` / `tunnel.py` — per-identity token bucket; cloudflared launcher.
- `sandbox.py` — the enforced realize sandbox (allowed tools, path allow/denylist,
  secret-read exclusion, out-of-scope gate).
- `realize_trigger.py` / `realize_pr.py` — fire only on handed-off directives;
  worktree → sandboxed agent (no token) → `gh pr create` (never push to `main`).
- `budget.py` / `consult.py` — Denial-of-Wallet caps; SSRF-hardened Consult-URL
  allowlist.

The browser reuses the same webview bundle via `acquireHostApi()` (the VS Code
path is unchanged); the standalone shell is `vscode-codoc/web/index.html` (strict CSP).
