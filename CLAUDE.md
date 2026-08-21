# CLAUDE.md

Guidance for Claude Code working in this repository. Deep internals (data model,
loop phases, bootstrap, storage, control-file schemas, env vars, the hub) live in
`docs/architecture.md`.

## Project Overview

**codoc** maintains a human-intent-level view of a codebase as a navigable
*feature tree*, synchronized to the underlying code. Each node is a **feature**: a
named unit of intent that binds to many code chunks across many files (and one
file's chunks may belong to several features). The tree is **first-class authored
intent** (not LLM-derived); code attribution (bindings) is a secondary index kept
fresh by the reflective pipeline.

The repo has the Python core (`codoc/`) and the VS Code extension (`vscode-codoc/`).
It was rewritten clean-slate in 2026-05 (see `docs/architecture.md` for what was
deleted vs kept).

## Commands

```bash
pip install -e .            # Python 3.11+ (pip or uv); project venv is .venv
pip install -e '.[serve]'   # + the deployed hub (fastapi/uvicorn/sse-starlette)

# Five core CLI commands
codoc init                  # index repo, propose initial tree, write .codoc/tree.codoc
codoc watch                 # the daemon: run both loops as you edit code / tree.codoc
codoc status                # feature count, pending proposals, recent activity, and what
                            #   the prose gate has been finding in prose CODOC wrote
codoc sync                  # one-shot: apply tree edits (Loop B), then reflect code (Loop A)

# Plumbing (agents / no-IDE workflows)
codoc accept <e-id>         # CLI verdict path — mirrors the IDE Accept (then runs Loop B)
codoc reject <e-id>         # CLI verdict path — mirrors the IDE Reject
codoc history <feature>     # one feature's blame timeline (who/when/why, by id or title fragment)
codoc reflect               # recovery-grade state reconciliation (used by the Stop hook)
codoc propose <kind>        # author a plan proposal from the shell (humans/tests); --reflects
                            #   marks one that restates code that already changed
codoc install-hooks         # (re)install the CC hooks + MCP registration
codoc realize               # implement the realize queue NOW, foreground (SDK or CLI engine)
codoc migrate               # one-time idempotent workspace heal (migrate tree.doc.json comments into the store + converge duplicate features + track config.json + install /codoc:* commands that shipped after this workspace was wired); also runs on daemon startup
codoc lang [<bcp47>]        # show / set the language the TREE is authored in (en, zh-Hans, ja, …) — see "Authoring language" below
codoc translate             # rewrite an EXISTING tree's prose into that language (--dry-run, --limit N)
codoc voice                 # read what codoc learned about how YOU write, from your own
                            #   rewrites of its prose; `forget <v-id>` / `keep <v-id>` / `why <v-id>`
codoc serve                 # the deployed hub: serve the tree to remote users (docs/serve-deployment.md)

# watch flags: --dry (apply tree edits, don't queue realization), --no-realize
# (never queue this session), --auto-realize (unattended fallback when no session)

python3.11 -m pytest tests/   # Python tests
```

## The `tree.codoc` surface

The only human surface is `.codoc/tree.codoc`. You edit titles/descriptions
directly. Structural proposals render as an **in-place overlay** — ADD/MOVE as
ghost hunks at their destination, RETIRE/AMEND as decorations on the live node —
accepted/rejected with the IDE's inline **Accept / Reject** (which write verdicts
to `.codoc/inbox.json`; there is no accept/reject *syntax*). Feature ids
(`⟨f-id⟩`) stay on disk for stable identity but the IDE hides them. Code is cited
inline with markdown links: `[label](codoc:file.py#symbol)`.

Two markdown-native signals in descriptions feed Loop B directives:
- `**bold**` is **focus** — newly-bolded spans ride in as a `Focus:` line; an
  imperative bolded span queues a directive even when the prose reads descriptive.
- `[label](https://…)` external links become `Consult:` lines — the realizing
  agent WebFetches them before implementing. The editor underlines them so an
  author can see the link registered as an instruction.

**Comments are the unit of requested work.** An inline comment on a span of prose
becomes a `STEER FEATURE` directive through `edits.json` (`drain_steers`). Typing a
`> …` line into a description does NOT create one: the webview stopped writing
`tree.codoc` in U6, and U7 retired the text-ingest path that used to read `> ` lines
out of it (see `loop_b` step 2.7), so a `> ` line is now ordinary prose.

A comment carries four things beyond its text, and together they turn a sticky note
into a request an agent can act on precisely:

- **the code it means** — `codoc:` citations inside the commented span become the
  directive's `Edit only:` scope (`comment-model.codeRefsIn`). No picker and no new
  syntax: a description already cites its code inline, so the sentence you select
  usually names what you mean. A comment that cites nothing keeps the feature's whole
  binding set, which is what "no code in particular" honestly means.
- **the sentence it replies to** (`anchor_text`) — a note reads as a correction, and
  without the claim it corrects the agent has to guess.
- **its scope** — `code` (the historic steer: change the implementation, leave the
  author's prose alone) or `both` (also bring the description in line).
- **its outcome** — the thread records the directive it produced and reaches
  `resolved` when that directive lands, so it can then show the code it caused.

Threads are **durable**, in the store's `comments` table and the sidecar's `comments`
slice; they used to live in extension-host memory and vanish with the tab. A resolved
thread lingers about an hour and then leaves the margin (`annotation.in_margin`) — long
enough to read what your request produced, and no longer; the record itself is never
deleted. **The document never moves for a comment**: the margin cards hang in the
whitespace that already exists and, where it is too narrow to hold one without covering
the prose, the anchor opens the thread as a popover instead. (It used to slide the whole
prose column sideways — on arrival as well as authoring, and three times over when you
wrote one.) Hovering either the commented words or their card lights both, which is what
ties a card to its sentence once the stack has pushed it off its own line. **Build it**
in the composer sends the note, asks for the prose to follow, and runs `codoc realize`
in a terminal — the one place codoc lets an agent write to your source files, so it is
visible and interruptible rather than a background spawn. It is offered in the
extension only; a hub contributor has no working tree to run against.

**Reading the tree** (surfaces that change nothing):
- `/codoc:ask <question>` answers a question by drawing a numbered *walkthrough* — a
  reading path over features that already exist (the `codoc_walkthrough` MCP tool →
  ephemeral `.codoc/ask.json`; see `codoc/loop/ask.py`). It writes nothing to the
  store or the change ledger, so it is safe at any point in an edit. Prefer it to a
  chat paragraph when the tree already covers the answer; `codoc_walkthrough_read` /
  `codoc_walkthrough_clear` observe and dismiss.
- In the Codoc Tree editor, `Cmd+F` / `Cmd+Alt+F` search and replace across feature
  titles and descriptions (the raw `tree.codoc` is a read-only export, so this is the
  only place to search/rename the tree).
- The **History** toolbar stance answers two questions in place: **who wrote this
  sentence** (inline authorship, marked per span — not per node; see below) and
  **what did this page say before** (a **timeline scrubber** above the document; dragging
  it left renders the tree as it read then, with that moment's change marked in the prose
  where it happened). See "Reading the tree's own past".
- **What a change to this would reach** is a count at the end of a feature heading,
  revealed on hover and opening the dependents by name with the symbols that reach in
  (`webview/tiptap/impact-decorations.ts`, off the `feature_impact` sidecar slice; the
  same answer rides every MCP `read_context` row). It is a DERIVED index and must never
  enter a description: a paragraph listing its own callers is the inventory-of-machinery
  defect the altitude rule bans, and it goes stale on the next caller. The companion
  answer — *how are these related* — is the diagram block, lifted from the same graph.
  See `docs/architecture.md`, "Drawing how things relate, and what a change would reach".

## Reading the tree's own past (the timeline)

Turning on **History** reveals a scrubber over every moment the tree has been in.
Dragging back replaces the page with a **read-only reconstruction** — never the live
editor with old text in it, because the editor is wired to a settle→command pipeline
whose job is to report document changes, and pushing Tuesday's prose through it would
record the author reverting the whole tree. The past page is tinted and the tree pane
dims, so it is never mistakable for today.

The chain behind every change is one hover away (`state/provenance.ts`, rendered by
`webview/provenance-card.ts`): **what changed → the directive that asked for it → the
prompt a person typed → the session they typed it in → the commit the code work started
from → the code diff itself**. Every link already existed in the ledger; nothing showed
them together. The same card hangs off the History stance's per-feature label, so the
question can be asked at a paragraph as well as at a moment.

A chain is not a **warrant**, and the card carries both. Every link above says what
happened *before* a claim; none says why to believe it. So a prose-writing op records
what its stated why RESTS ON (`loop/warrant.py` → `NodeOp.warrant`) and the card quotes
it as a `Rests on` row. The describing pass cites ids from the evidence it was given
(`why.py.stamp_ids` puts `c1`/`d1`/`p1` on each entry; `author_intent` carries `a1`…) and
codoc resolves each id back to what that source actually said — **the quote never comes
from the model**, and an id naming nothing is dropped, so a fabricated citation leaves the
op unwarranted rather than falsely warranted. Most descriptions have no warrant and should
not: an observation about what code achieves makes no claim needing evidence, so nothing
is drawn (see the assertion register, Rule 7).

**Blame is per SPAN, not per feature** (`state/inline-blame.ts`). A feature is several
paragraphs written by a person, a loop pass and an agent in turn, so "claude-code edited
this 3h ago" answers a question nobody asks — the reader is deciding whether to trust one
CLAIM. Replaying the revision window's word diffs forward attributes each surviving span
to the party that introduced it, using data already on the wire. A span the ledger cannot
account for stays **unattributed**: crediting it to the nearest editor is precisely the
error the per-node version made, and doing that per word would multiply it. Where one
author owns a whole description nothing is drawn at all — the heading's own label already
says so, and underlining every word to report it would be the node-level signal again.

Two rules make it trustworthy:

- **It reconstructs backwards from the live document**, locally. A scrubber cannot
  afford a round trip per frame and the webview has no request channel — so the daemon
  ships a bounded window of applied events carrying the text each one DISPLACED
  (`.codoc/revisions.json`, `codoc/loop/revisions.py`), and `state/revision-model.ts`
  un-applies them. Recording what an op destroys is `loop/apply._record_displaced`, at
  the one write boundary.
- **It says what it cannot reconstruct.** An event written before codoc recorded the
  displaced value knows a feature changed and not what it changed from. There is no
  backfill and there must not be one: such a change is reported as unreconstructible
  rather than diffed against invented words.

## Authoring language

The tree can be authored in any language; `codoc/doclang.py` owns that (NOT
`codoc/lang/`, which is *programming* languages — the two are orthogonal, a Python
repo can have a Mandarin tree). The setting lives in **`.codoc/config.json`**
(`{"doc_language": "zh-Hans"}`), the one non-export file in `.codoc/` that is
**tracked in git** — it has to travel with the repo, or a contributor's daemon
writes English prose into somebody's Chinese tree. `CODOC_DOC_LANGUAGE` overrides it
per-process; `codoc init --doc-language` sets it before bootstrap so the first tree
is already in the language.

Prose is translated; **addresses are not** — identifiers, symbol paths, and
`codoc:` link targets stay verbatim, and the code an agent writes keeps the
language its neighbours use. Four prompts carry the directive via a `{{doclang}}`
marker (expanded into the cached prefix; empty for English, so English prompts are
unchanged), and the MCP reads return a `doc_language` block because a coding agent
is the one writer with no prompt in front of it.

**The tree may be bilingual, and that is not a defect.** The setting says what codoc
*originates* prose in; what the tree contains is observed per node
(`doclang.detect_prose_language`). Originating → the workspace language. Editing
existing prose → the language that prose is already in, because an author who wrote
one node in English inside a Chinese tree meant to. Chinese prose carrying English
library and API terms is correct writing and nothing flags it. The sidecar and the
MCP feature rows carry a `lang` tag only for nodes that differ from the tree's
language; the webview stamps those on the DOM so the browser gets fonts and
line-breaking right per element, and the toolbar switcher (read-only on the hub)
writes `.codoc/config.json`. `codoc lang` reports what the tree actually contains,
not just what is set.

Switching the language does not retranslate anything, so a tree already built in
English is migrated with **`codoc translate`** (`loop/translate.py`): one LLM pass per
batch of features, rewriting titles and descriptions while copying every `codoc:`
citation, external link, and `**bold**` focus span through unchanged. It REFUSES a
node whose translation dropped one of those, or whose title would collide with a
sibling's, and it preserves each node's `feature_writers` role so a translated
human-authored node keeps the strict amend gate. Previous wording stays in the change
ledger (`codoc history <feature>`). Idempotent — selection is by detected language,
so an interrupted run is safe to re-run.

Setting it also changes what the lexical heuristics do, because every one of them
was written for a spaced Latin script — see the table in `docs/architecture.md`
("Doc language"). The short version: `norm_key` NFKC-folds titles so an IME can't
mint a duplicate node, `terms`/`tokens` segment per script instead of dropping every
non-ASCII character, and `clause_chars` ports the amend gate's 24-character
"preserved clause" into whatever a clause costs in the script at hand.

## Learning how the author writes

Codoc generated a description, a person rewrote it, and until now that rewrite
taught nothing: the ledger recorded it and the next pass wrote in the register it
had just had corrected. `codoc/loop/voice.py` closes that gap, following PRELUDE /
CIPHER (Gao et al., NeurIPS 2024 — `papers/02-continual-learning-from-user-edits.md`):
infer a NAMED preference from the draft→revision gap, keep it, retrieve the ones
whose context resembles the node being written, put them in the prompt.

Deliberately **no fine-tuning**, for that paper's reasons plus one of codoc's own: an
author can read a sentence of English and tell us it is wrong, and that correction
channel (`codoc voice forget`) is the only thing that makes a learned preference safe.

Three properties are load-bearing, and each answers a way this goes wrong:

- **A lesson is about the WRITING, never the content.** An author who fixed a false
  claim taught us nothing about voice, and generalizing from that edit is how a
  memory learns to assert one specific fact everywhere. The inference pass classifies
  every rewrite (`style` / `content` / `mixed` / `noise`) and only the style half of a
  rewrite survives into a lesson. Two guards sit in front of the model as well: a
  rewrite of prose a PERSON wrote is never read (they are changing their mind, not
  correcting us), and a change too small to carry a preference is dropped.
- **A lesson is provisional until a second edit corroborates it.** One rewrite is a
  hypothesis, so it is recorded but NOT injected; it starts shaping prose at
  `ACTIVE_AT` (2) agreeing edits, or when a person promotes it with `codoc voice keep`.
- **A lesson remembers where it was learned** (`scope_path`, `scope_files`), because
  preferences vary by region of the tree. Retrieval ranks on that overlap and sends
  **at most one lesson per axis** — two lessons on one axis are either paraphrases
  (which merging should have caught) or a contradiction, and sending both means the
  model follows whichever it read last. That is also how a changed mind takes effect:
  the better-corroborated lesson displaces the older one at the point of use.

Where it runs: `harvest` is at the head of Loop A's prose pass, not in Loop B. Loop B
is the interactive path the author is waiting on, while Loop A already makes a model
call and is where prose gets written — and the harvest only has to have run before the
next WRITE. It costs nothing on a pass with no new rewrites (it returns before calling
anything), and it is gated `learn_voice=False` for bare callers on the `embed_fn`
precedent, so a unit test never makes an unasked-for call. **Retrieval is
unconditional** — reading already-learned lessons out of the store needs no gate.

Two prompt keys, not one: `author_voice` is the author's own paragraphs ("sound like
this") and `voice_lessons` is the learned instructions ("do this"). Kept apart because
a model handed both in one list follows neither reliably, and because the samples alone
are the weak form of style transfer (the EMNLP 2025 imitation result in the notes).
The prompt states the two limits that outrank every lesson: they say HOW to write and
never WHAT is true, and they do not override the assertion register or the human-prose
amend gate.

The ledger cursor for the harvest is the events table's **insertion order, not the HLC
stamp** — `HLC.now()` pins `logical_time` at zero, so every event Loop B applies inside
one millisecond carries an identical `at`, and paging on `at > since` silently dropped
whatever shared the watermark's millisecond (`tests/loop/test_voice.py` pins this).

`codoc voice` also reports PRELUDE's metric, `edit_cost_trend`: the normalized edit
distance between what codoc wrote and what the author left, in buckets oldest to
newest. A falling series is the claim that this works; a flat one says it is doing
nothing, which is the finding worth having. It refuses to call a trend under 8
observations.

## Checking what codoc wrote

`prompts/style.txt` states the rules every prose pass is held to, and until now
nothing enforced them: a sample that opened on a mechanism reached the tree and the
author fixed it by hand. That is worse than it sounds, because the style memory above
reads that hand-fix as a preference — so a defect we could have caught upstream is
laundered into a lesson. `codoc/loop/prose.py` is the check: a **deterministic** critic
(no model) that names defects — opens on a mechanism, restates the title, no rule
given, nothing beyond the identifier names, banned register, decorated punctuation,
clipped sentences, rhetorical question, overlong sentence, altitude too low for a broad
node, and four title rules.

**Check and score sit in different places, deliberately.** A repair needs a rerun, so
`prose.gate` runs at the three passes that generate prose (`agent/tree_update.py`,
`agent/bootstrap_agent.py`). The RATE is recorded at `apply_op` instead
(`loop/apply.py::_record_prose`), because a per-caller counter measures whichever
caller remembered to call it, and `apply_op` is the one boundary every writer crosses.
`codoc status` prints it once something has been checked.

Four properties are load-bearing:

- **A repair is the same call with the critique appended to the VOLATILE tail.** No
  second prompt file: the rules are already in the frozen prefix and the reply is what
  changed. Appending to the tail keeps the cache prefix byte-identical, and it also
  makes the second request differ, which `run_agent`'s response memo requires.
- **A repair can only be an improvement.** The rewrite is kept only if it covers the
  same nodes with the same bindings and scores strictly better. A dropped node, a
  re-attributed binding, a worse draft, or a rerun that raised all keep the first
  draft, defect recorded — a gate that can lose a write is worse than no gate.
  `severity` weights a missing fact above a punctuation slip, so a rewrite cannot win
  by deleting the content it was asked to fix.
- **A person's prose is never checked and never scored.** An author who writes a dash
  is not committing a defect, and averaging their words into the rate would improve
  codoc's own score every time somebody typed one.
- **Every rule was calibrated against this repo's own prose**, swept over 412
  docstring paragraphs, with the surviving false-positive share recorded per rule in
  the module docstring. A rule that fires on a fifth of good writing has stopped
  describing a defect: `altitude-too-high` was removed for exactly that reason (good
  leaf prose often names no symbol and no number, because the BINDINGS already tie the
  node to its code), and `decorated`'s share is annotated as a corpus mismatch rather
  than a false-positive rate, since style.txt exempts the notes-to-a-developer
  register these docstrings are written in.

**Altitude is part of the contract, on both sides.** A node's register depends on
where it sits — a parent is read by somebody choosing which child to open, a leaf is
the last stop and has to carry the detail — so `loop/subtree.py` puts `depth`,
`children` and `spans_files` on every subtree row, `prompts/style.txt` states what
each altitude asks for, and `tree_update.txt` Rule 9 reads the fields by name. The
gate's `altitude-too-low` marks the same line the prompt draws (`prose._BROAD_FILES`
= 3 files, or any children, or depth 0), and `tests/agent/test_altitude.py` pins the
number in both places so it cannot drift. The depth is computed over the WHOLE
feature list rather than the sent window, because a window's top is not the tree's
top and a depth counted locally would pitch a mid-tree node as a theme. The two
bootstrap passes get no per-node altitude — the file pass runs before anything exists
to parent its nodes to — so there the register is fixed by which pass you are in.

Two implementation invariants, both about not lying to a rule: every punctuation and
identifier check searches a **length-preserving masked copy** (citations, links and
code spans replaced character-for-character) and quotes the ORIGINAL at the same
offsets, so a defect's quote is the author's text; and the lexical rules are skipped
for non-Latin prose, where a word count is meaningless (`doclang.clause_chars` supplies
the script's own floor instead).

## Architecture

### Core idea — two loops

- **Loop A — code → codoc** (`loop/loop_a.py`): snapshot-diff the index →
  auto-apply safe ops (refresh/attach/detach/small-amend) → if anything needs
  judgment, ONE LLM pass (`agent/tree_update.py`) returns minimal node ops;
  structural ops (add/move/retire) are logged as pending proposals.
- **Loop B — codoc → code** (`loop/loop_b.py`): drain identity-keyed **commands**
  from `.codoc/edits.json` (add/set_title/set_description/move/retire — the webview
  emits them; an idempotency ledger guards crash-replay). The webview never writes
  `edits.json` directly (no shared lock across processes): it APPENDS ops to
  `edits.host.jsonl`, which the daemon merges into `edits.json` under the lock at each
  Loop B pass (`edits.merge_host_ops`). Plus proposal verdicts →
  apply via `apply_op` → for edits implying code change, build a directive and
  **queue it for the live session** in `.codoc/realize.md` (status `awaiting_impl`).
  Edits are NOT inferred from a text/doc diff (that path was retired). The session
  implements via `/codoc:sync`; the Stop-hook reflection / watch-daemon Loop A then
  closes the loop. No headless `claude -p`.

A single LLM pass with full change + whole-tree-title context (plus the
`UNIQUE(file, symbol_path)` binding constraint) is what prevents duplicate nodes —
no move/fracture/coalesce detectors, no post-hoc dedup gates. The five Loop-A
phases, the Loop-B drain order, bootstrap, and the realization triggers are
detailed in `docs/architecture.md`.

### Package layout (`codoc/`)

```
model/       # Pydantic: Feature, Binding, Event/NodeOp/NodeOpKind, HLC; ids.py;
             #   annotation.py (Mark, CommentThread — store-authoritative rich state),
             #   voice.py (StyleLesson + EditKind/LessonStatus/LessonAxis)
store/       # db.py — Store over the SQLite tables (features/bindings/events +
             #   blocks/marks/comments + style_lessons/store_meta + applied-command
             #   ledger) + 1 derived graph cache (WAL)
graph/       # code dependency graph (derived, rebuildable): extract.py, query.py
             #   (query.py also holds feature_impact: which features would feel a
             #   change to each one — the standing group-4 answer, NOT the same as
             #   loop_a._compute_impacted, which is changeset-scoped)
loop/        # the two loops + pieces: classify.py (decision table), phase.py (the
             #   single feature-phase projection — holds/drift/resolution are views),
             #   diff.py (compute_changeset — and a removal from a file that no
             #   longer PARSES is held rather than read as deleted code, so a
             #   mid-edit save cannot detach a binding), apply.py,
             #   loop_a.py / loop_b.py, edits.py
             #   (edits.json + realize.json), payload.py (the per-PASS prompt
             #   budget: how much of each chunk's source a call gets to see,
             #   conceded members-first so one generated module cannot make a
             #   258,000-char call; a set that fits is passed through unchanged,
             #   and a set that does NOT fit at full allowance is split across
             #   several calls instead of spent down to 60 chars a method —
             #   by top-level owner, so no pass is shown half a class),
             #   why.py (grounded rationale for
             #   descriptions: commit messages / realized directives / recorded
             #   rationale — each entry id-stamped so a claim can cite it),
             #   warrant.py (resolving those citations back to what the source
             #   actually said; an id naming nothing is dropped, never invented),
             #   inbox.py, status.py, fsio.py (atomic
             #   IO), subtree.py, bootstrap_hier.py, title_dedup.py (opt-in semantic
             #   title dedup), migrate.py (one-time store-authoritative workspace
             #   heal), sdk_realize.py / autorealize.py, watch.py,
             #   revisions.py (the timeline transport: applied events + the text each
             #   displaced + the directives they cite + the warrant they rest on),
             #   gitref.py (the commit a
             #   directive's code work started from — fails soft to ""),
             #   voice.py (the style memory: harvest human rewrites → lessons →
             #   retrieve by context → inject; see "Learning how the author writes"),
             #   prose.py (the prose GATE: a deterministic critic over a title or
             #   description + one repair pass; see "Checking what codoc wrote" —
             #   NOT blocks/prose.py, which is the prose BLOCK plugin)
blocks/      # typed-media blocks + plugin codecs (agent-native notebook protocol):
             #   base.py (Capability LIFT/LOWER/CONSULT + BlockPlugin), registry.py,
             #   builtins.py, prose.py (plugin-zero; the block codec, not loop/prose.py's
             #   critic), diagram.py (graph→mermaid lift, both directions, grouped by
             #   the owning feature and bounded; edge-delta lower),
             #   screenshot.py (transient + url/image consult media),
             #   refresh.py (Loop A lift pass), conformance.py (host parity harness)
agent/       # base.py, tree_update.py (the incremental LLM call), bootstrap_agent.py,
             # paths.py, hook.py / install_hooks.py, propose.py,
             # voice.py (classify a rewrite, name the preference — policy-free)
mcp/         # codoc MCP server (FastMCP, stdio): tools.py + server.py (codoc-mcp script)
serve/       # the deployed hub (codoc serve) — see docs/architecture.md + serve-deployment.md
codoc_file/  # render.py (store → tree.codoc + sidecar), parse.py, diff.py (→ user ops)
lang/        # tree-sitter adapters: python.py + typescript.py  [KEPT]
             #   + notebook.py — a .ipynb IS Python with the cells still visible, so
             #   it is an ADAPTER (not a second reader like settings_files.py): the
             #   cells become one synthetic Python document, a markdown heading names
             #   the statement run under it (nb.ipynb::load-the-data, its defs as
             #   members), sections are FLAT, the prose rides in as raw string
             #   literals so it reaches both the chunk and its identity, and outputs
             #   never do — so a re-run is not a change and a reworded step is
             #   they decide what gets an ADDRESS — see docs/architecture.md,
             #   "What gets an address": every definition in the namespace gets
             #   one (a `def` in an `except` branch binds the module, so it is
             #   file.py::loads), one name gets exactly one (overloads and a
             #   property's accessors are JOINED, not last-wins), the guard a
             #   definition exists under is part of the definition, and
             #   __module__ is the glue between declarations, not the whole file
             #   (PROGRAMMING languages — not doclang.py, see below)
doclang.py   # the AUTHORING language of the tree: profiles + the prompt directive,
             #   the .codoc/config.json setting, and the script-aware text helpers
             #   (norm_key / terms / tokens / clause_chars / char_budget) that the
             #   loop's lexical heuristics use instead of Latin-only regexes
settings_files.py  # a SETTINGS file as addressable chunks (toml/yaml/json/ini), so a
             #   decision that moved out of the code can still be named: a section is
             #   a chunk and a nested section is its member (rules.toml::periods.week),
             #   its comments come with it, and identity is the parsed key/value pairs
             #   so a formatter cannot wake Loop A. Not a lang/ adapter — no functions.
core/        # tree_walk.py — tokens_hash/types_hash identity signals  [KEPT substrate]
pipelines/indexing/  # cocoindex_app.py, update_index(), read_all_chunks()  [KEPT]
             #   settings_scan.py — WHICH settings files are indexed: the ones some
             #   indexed source file's text names (a decision the code reads), not a
             #   glob. runner.py caches the cocoindex App + environment because
             #   cocoindex has one environment per process — see docs/architecture.md
             #   "One workspace per process".
             #   gate.py — whether a LARGE file holds intent, decided by its parse
             #   and not its size: past 1.5 MB a file gets a hearing (read it, parse
             #   it, index it if the median definition is the size a person writes)
             #   instead of the silent drop that made altair's 1.6 MB schema module
             #   invisible while its 1.2 MB sibling was indexed
             #   survey.py — what the walk never saw (unreadable languages,
             #   over-cap files, unfollowed symlinks), so the index-coverage
             #   figure in `codoc status` cannot read 100% over code codoc
             #   never saw; reports codoc's OWN limits only, never intentional
             #   excludes. See docs/architecture.md "What the walk never saw"
prompts/     # tree_update.txt, realize.txt, bootstrap_file.txt, bootstrap_org.txt,
             #   voice_infer.txt (each carries a {{doclang}} marker, expanded into
             #   the cached prefix)
             #   bootstrap_settings.txt — the settings pass: a config file's sections
             #   ATTACHED to the features whose code reads them, and those
             #   descriptions AMENDED to name the value in force. Not a
             #   Configuration node, which is what asking a settings file what it
             #   is FOR produces
             #   style.txt — the shared writing guide, pulled into the five prose
             #   prompts by {{include:style}} so every pass that writes a title or
             #   a description is held to one register (abstract first, then the
             #   rule, then the case; no dashes, no decoration)
cli/main.py  # Typer app; config.py — LLM config
```

The data model, NodeOp kinds, storage (the SQLite tables + the cocoindex/LanceDB
index), and the `.codoc/` control-file schemas are documented in
`docs/architecture.md`; the change ledger is `docs/codoc-change-ledger.md`.

### VS Code extension (`vscode-codoc/`)

The local extension is file-based: no HTTP server, no port. `WorkspaceState`
watches the `.codoc/*` control files, reparses on change, and drives the status
bar off `status.json`. The **`Codoc Tree` webview** (`providers/tree-editor.ts`)
is the default editor for `tree.codoc`; both it and the raw-text editor render
**every** proposal type inline, with `✓`/`✗` Accept/Reject on the node itself. An
ADD is MATERIALIZED into the document at the rank it would take (not a widget
beside it) so the reader can judge how the tree would READ with it in; a RETIRE
strikes the words it proposes to remove; an AMEND shows old and new together. Only
a MOVE keeps a ghost, because the node itself stays put until the verdict lands and
both ends have to be visible at once.

**Three channels, one grammar** (`state/settlement.ts`; design docs
`docs/plans/2026-08-19-003-settlement-three-channels.md` and
`docs/plans/2026-08-20-001-plan-channel-and-one-palette.md`). Every unsettled span is
a *claim* — a range, a channel, a stage — and each channel owns a different
property of the text, so they stack on the same words without a legend:

- **human → the INK.** Blue; pulsing while it is still yours to send, steady once
  handed off. Ink ONLY — your own removals are not drawn, because you are the one
  who removed them; the other two channels do report theirs. It walks TWO hops, never
  one (`humanBase → projected → … → live`), because a single diff to `live` swallows
  every word a materialized plan put on the page and inks the agent's proposal as
  yours.
- **plan → the OPACITY.** Faded gray, solider once accepted. A `proposed` plan is
  materialized into the doc, so a `proposed` node attr guards three paths that would
  otherwise author the agent's words as yours: `featureUnits`, `renderTreeFromDoc`,
  `inlineRunsToText`. An `accepted` plan comes from the queued DIRECTIVE
  (`hold_detail.origin == "plan"`), because accepting deletes the proposal it would
  otherwise be read from.
- **code → the GROUND.** Green behind what the codebase added, red behind what it
  cut, at sentence granularity.

Composition is the specification. Planned wording with a red ground under it means
*this was agreed and the build did not keep it* — the thing the model exists to say.
Blue ink on a green ground is **impossible** and is kept so by construction: the
code claim yields wherever the author also claims. The full matrix is in
`settlement.ts`'s header and pinned by `settlement.test.ts`.

**The same palette on all four surfaces** — the prose, the margin marker, the tree
rows and the minimap rail. Blue is always the author, gray is always a plan, green
and red are always what the code did. The rows and the rail used to have ramps of
their own in which "sent" was green (the code's colour, on a promise the code had not
kept) and "proposed" was blue (the author's colour, on words nobody in the room had
written); `railState` now delegates to `featureState` rather than paralleling it.

Nothing forces a verdict: claims are DERIVED, never stored, so an unanswered
proposal simply stops being offered. The margin marker (`state/node-status.ts`)
ACCUMULATES along fixed slots rather than ranking — a node that was planned, then
built, and built differently carries three facts a rank would reduce to one — and
is computed from the same claims as the prose, so the two cannot disagree.

Store-authoritative editing model (2026-06 refactor — see
`docs/plans/2026-06-26-001-refactor-store-authoritative-coordination-plan.md`):
the **SQLite store is the single source of truth**. The webview is a pure
*projection* of the store (it consumes the daemon-written `tree.doc.json`) plus an
identity-keyed *command emitter* — editing actions emit `{id, kind, fid|localId,
baseRev, payload}` commands (add/set_title/set_description/move/retire) by APPENDING
to `edits.host.jsonl` (the webview holds no cross-process lock, so it never writes
`edits.json` directly); the daemon merges that append log into `edits.json` under the
lock and applies via `apply_op`; nothing is inferred from a doc diff. A settle cites
the `baselineId` of the projection it was computed from so the host diffs against that
exact baseline (not an in-flight one — no phantom retire). Both `tree.doc.json` AND
`tree.codoc` are daemon-written **derived artifacts** (`tree.codoc` is a read-only
export; the daemon is their sole writer).
A per-feature HLC version gate keeps a returning projection from clobbering a newer
local edit, and a **draft / hand-off** gate keeps code-implying edits
safe-by-default. Two provenance rules make concurrent editing safe: the EDITOR owns the
baseline citation (stamped at the end of an adopt, so a settle flushed by an arriving
projection still cites what its text was typed against), and a command's `base_text`
comes from this host's own unechoed writes (`state/known-store.ts`) else the cited
baseline — never from a projection the author may not have adopted. On the hub the
BROWSER emits the commands (`webview/command-emitter.ts`) through those same modules,
since it is the only party there that sees a projection. The editing-model details + key
source files are in `docs/architecture.md`.

### The deployed hub (`codoc serve`)

An optional Tier-1 web surface (`codoc/serve/`) that serves the tree to
GitHub-authorized **remote** users from the maintainer's own machine: contributors
*suggest* edits, a maintainer hands them off, and the hub realizes them on a git
worktree → code PR. It is a **separate process** that supervises the daemon and is
a file-channel client (reads `.codoc/*`, writes only the authored-edit + verdict/draft
channels of `edits.json`/`inbox.json` — never `tree.codoc`, and never `tree.doc.json`,
which is the daemon's own projection). See `docs/serve-deployment.md` (setup) and `docs/architecture.md`
(modules). README "Flow 3" is the user-facing overview.

## Tests

- `tests/` — Python unit + integration (`loop/`, `store/`, `graph/`, `codoc_file/`,
  `agent/`, `mcp/`, `cli/`, `serve/`). Ledger suites: `test_classify.py` (the
  decision table), `test_edits.py` (annotations + manifest + hold_set),
  `test_reader.py` (scoped reads).
- `tests/bdd/` — Given/When/Then code↔tree round-trip (deterministic via an injected
  `propose`) + a subprocess-isolated real-LLM E2E position report.
  `tests/loop/test_end_to_end.py` and `tests/bdd/test_e2e_userflows.py` are gated on
  `OPENAI_API_KEY`; everything else is deterministic.
- vscode-codoc: `npx vitest run` from `vscode-codoc/`; `npx tsc --noEmit` + esbuild
  must stay clean. The TS parser is parity-tested against `parse.py`.
- Fixtures: `tests/fixtures/` (self-contained Python + TypeScript samples) and `test/`
  (real-world corpora for bootstrap/E2E: `requests/`, `altair/`, `nanochat/`, …).

Accepted review residuals live in `docs/residual-review-findings/`; superseded
plans/brainstorms from prior feature work are archived under `docs/archive/`.
Documented solutions to past problems (bugs, best practices, workflow patterns),
organized by category with YAML frontmatter (`module`, `tags`, `problem_type`),
accrete in `docs/solutions/`; shared domain vocabulary lives in `CONCEPTS.md`.
