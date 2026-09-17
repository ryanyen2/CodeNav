# Review of the v2 calculus (docs/substrate/01–04)

Reviewer stance: hostile, PL/distributed-systems. Sections cited are those of
`01-calculus.md` unless prefixed (`02 §`, `04 §`).

Verdict up front: the thesis (identity minted at the write, attribution in
runs, one applier, log as truth) is right and is the first design in this repo
that can make "blue on green is impossible" a property of the encoding. As
written it is not buildable, for three reasons that each break several sections
at once: `OpId` is a local sequence number but every watermark, subset, anchor
and `base` uses it as a global identity, which the merge story in 04 then
reassigns; tombstones are said to be compacted while every anchor, split and
merge depends on them; and I3's clamp is a hidden O(document) cascade with an
unrecoverable inverse. A fourth, smaller but product-visible: the ingest lost
the ledger's arbitration rule (`base_text`) and diffs against the live text.
Details follow.

---

## A. Soundness holes in the calculus

**A1. `OpId := int assigned by the applier` (§2) cannot survive 04 §2.**
`CharRef = (op, k)`, `base`, `handoff.upto`, `realized.upto`,
`plan.accept.subset`, `timeline(seq)` and `ink(f) = op > handoff.upto` all
treat the applier's sequence number as identity. 04 §2 says logs are per
session and rebuilt in `(HLC, session, seq)` order after a merge. Two branches
both have an op 1042. After `codoc rebuild`, char `(1042, 3)` is ambiguous, and
any op recorded before the merge that names it points at the wrong character.

Failing case: branch A hands off feature f at applier position 100. Branch B,
unmerged, edits f at an HLC earlier than A's handoff HLC. Merge. Rebuild
interleaves B's op before the handoff. `ink(f)` reads B's edit as sent, and if
`realized.upto` has also passed it, as built. The directive is silently lost,
which is the one thing the design promises not to do.

Fix: `OpId := (session, seq)`, globally unique and stable; the applier's
position is a separate `pos` used only for local ordering and never persisted
in a payload. Watermarks cannot be "op > N": a handoff must record the explicit
frontier (per-session max seq) of ops it drained, and `queued(f)` is frontier
difference. The same frontier is needed by the snapshot rule in E7.

**A2. Tombstone compaction (§2 "compacted once nothing references them and
they fall out of the display window") contradicts every anchor rule.**
`text.insert.after`, `block.split.at`, `text.delete.from/to`, and the merge
story all rely on a deleted character still being locatable. "Nothing
references them" is undecidable: an unmerged branch on another machine may
anchor after any character. Once compacted, the op arrives with a missing
anchor and §3 says "applied as far as its anchors allow", which for an insert
with no `after` means not applied. A whole paragraph typed after a
since-deleted word disappears with a conflict flag.

Also `Tomb := {op, k0, len, by}` carries no position, so the inverse of
`text.delete` ("re-insert from tomb") does not know where to put the text
back, and "a split at a tombstoned char" has no position to split at.

Fix: tombstones are positioned (one sequence of live and dead segments per
block, Peritext style) and are never removed from the truth. See D3 for how
to keep live-text ops O(live) anyway.

**A3. `text.delete` "inclusive range in current order" is ambiguous three
ways.** (a) Current order of live chars, or of live plus dead? If a tombstoned
char inside the range is later resurrected by an undo of the earlier delete,
is it inside this delete or not? (b) `from` in one block and `to` in another
after a concurrent `block.split`: the op targets `(bid, path)` and half its
characters are no longer in `bid`. (c) `from` after `to` cannot happen within
a block, but the range can span chars from several inserting ops, and the
inverse "re-insert from tomb" then has to re-insert several tomb segments in
the right relative order, which `Tomb` without position cannot express.

Rule: the range is the wire form only; `apply` expands it to an explicit list
of `(op, k0, len)` segments over the sequence at apply time and stores that as
the canonical payload (the inverse needs it anyway). Text ops should resolve
characters by `CharRef` and treat the target `bid` as advisory, since a char's
block is a lookup; then a split or merge under a concurrent delete costs
nothing and case (b) vanishes.

**A4. `text.mark` inverse "the opposite toggle" (§3 table) is wrong for
exclusive kinds and for partial toggles.** `link{href}` and `cite{file,
symbol}` cannot nest in Markdown; turning a link on over an existing link with
another href must displace it, and the inverse is "restore the displaced
marks", not toggle. `on=false` over a sub-range splits a mark into two;
whether the halves share `Mark.id` is unspecified, and `comment{block}` and
`suggest_del{plan}` must never be partially removed (a half-resolved comment
has no meaning). State: bold merges; link and cite are exclusive and record
displaced marks in the inverse; comment and suggest_del are toggled whole or
refused. Also state whether `Mark.id` stays unique after `block.split` cuts a
mark in two (it cannot, if the comment card is to show once and its anchor
spans both halves).

**A5. `block.add` with content containing several Text fields collides in
`k`.** An image block with `alt="ab"` and `caption="cd"` minted by one op
gives `(op, 0)` twice. Rule: `k` is one counter across all Text fields in
schema order, or `CharRef` includes the path. The same issue hits the
snapshot's "one `block.add` per block with runs preserved" (04 §5): those runs
reference archived op ids, and `Run := {op, k0, len}` carries the actor "by
reference (the op)", so after archiving, blame cannot name an author. The
brainstorm's run had `author`; the calculus dropped it. Restore it: a run
carries `actor` and `at` (interned), Etherpad style. It is cheap and makes the
snapshot self-describing.

**A6. `block.add`/`block.move` with `after: BlockId` have no landing rule when
`after` is deleted.** There are no block tombstones; a deleted block has no
rank to land after. Merge case: A deletes paragraph P; B adds Q after P.
Rule: deleted blocks keep their rank as block tombstones (the inverse of
`block.delete` already needs the rank), and "after a dead block" means "at
that rank". Related: the inverse of `block.move` ("move back") is itself
anchored to a block that may be gone by undo time; record the old ranks
`[(bid, rank)]` and make the inverse a rank restoration.

**A7. I3's clamp is a hidden cascade, O(document), with a broken inverse.**
Sequence: H1 a, H2 b, H3 c. `block.move(a, count=1, after=END)` (the payload
lets the editor send count=1). Now b is the first heading with no
predecessor; the applier clamps b to 1, then c must clamp to 2, and so on
until a heading of lower level. That is not O(block), it is represented by no
op, and the recorded inverse "move back, `-level_delta`" does not restore b
and c. Same with `block.set(kind=heading, level=4)` under an H1, and with
`block.delete` of an H1 whose H2 children the batch forgot to promote.

Drop I3. Org-mode and mdast tolerate H1 followed by H3; `tree` is a fold that
treats any deeper heading as a child; `level` is display. Then no op ever
mutates a block it did not name, and `block.delete`'s "promote descendants
with `block.set(level)`" becomes optional cosmetics rather than a correctness
requirement. Level clamping to `[1, 6]` at apply is fine because it touches
only the named block.

**A8. `count` in `block.move`/`block.delete` is a payload that can lie.** A
count that stops short of, or runs past, the dominated range splits a feature
or swallows a sibling, and the applier has no way to tell. Make the range
computed at apply (heading: itself plus the blocks it dominates; paragraph:
itself) and recorded in the inverse. Select-all delete is a batch of feature
deletes. This also answers "range crosses a plan-tagged block": the plan's
ghost block moves with the range (fine) and the plan's later `reject` finds
it by id, which it can.

**A9. `block.split` cases not covered.** `at: CharRef` is ambiguous (split
before or after `at`?). Split of an empty block: "an empty half never keeps
the id" leaves no half to keep it. Split of a heading at START (Enter to make
room above) gives the heading's id to the second half, which is right for
bindings; at END gives an empty new block, which must become a paragraph; in
the middle truncates the title and the tail must become a paragraph in the
same batch (02 §2 only lists paragraph splits). Split of a plan-added block by
the human: the new half has `Block.plan=None` but every char plan-tagged;
`plan.reject` then empties it and leaves a ghost empty paragraph.

Rule: `at` means "the new block starts with the char after `at`"; START is
allowed and makes an empty first half with a fresh id. Drop `Block.plan`
entirely; "a block belongs to a plan" is derived (added by a plan op and all
live chars plan-tagged), and reject deletes blocks it has emptied that a plan
added. Marks spanning the split point are split (and rejoined by merge).
Splitting a heading always yields `heading + paragraph`, never two headings.

**A10. `block.merge` needs an explicit `at`.** Backspace at the start of an
*empty* paragraph is the commonest merge and records no character, so the
inverse "split at the recorded char" has nothing to split at; record `at:
CharRef | END` (the survivor's last live char, or END if empty). "Kills the
later block's id" plus `into` is redundant; say the survivor is `into` and the
editor chooses. Forbid at apply any merge involving a heading (the gesture
already refuses it; the op must too, or an MCP writer deletes a feature and
its bindings by the back door, and "merge two headings" would silently drop
one feature's bindings). After a merge `base` per block is meaningless; make
`base` the client's global head, which is all a single applier needs.

**A11. `block.delete` of a bound feature "queues a removal directive" (02 §2)
is unimplementable from the listed views.** `directive(f)` and both watermarks
key on `fid`, which is now a deleted block, and `description(f)` is empty.
Rule: a feature that had bindings at deletion time stays in the queue view as
a phantom until `feature.realized` names it, and `directive(f)` for a dead
heading renders "remove". The ledger's `delete_code` asymmetry (row 12: an
accepted retire untracks unless a human said delete) has no home; put
`delete_code: bool` on `feature.handoff`.

**A12. Binding uniqueness is missing from the invariants.** `UNIQUE(file,
symbol_path)` is called the load-bearing dedup constraint in CLAUDE.md and
does not appear in I1–I7. After a merge, A attaches S to f1 and B attaches S
to f2. Rule (I8): an attach that would violate it implicitly detaches the
prior owner and records that in its inverse; the later HLC wins.
`bind.detach`'s inverse ("attach") needs the hashes recorded in the payload.

**A13. `plan.accept` with a subset.** A subset that accepts `text.insert`
into block X while rejecting the `block.add(X)` puts accepted text in a
tombstoned block. Rule: the subset must be closed under anchor dependency
(the minting ops of every anchor); the applier computes the closure and
either expands or records a conflict. Per-block subsets (accept everything
the plan did to these blocks), which is what the UI actually offers, keep
closure mostly local.

**A14. A plan cannot contain `block.delete`.** Plan ops "apply to the live
document immediately, tagged" (§3). A loop plan to delete a feature (02 §5:
"the loop's own 'this feature lost all its code' is a plan to delete") would
remove it before the verdict. There is `suggest_del` for text only. Rule: a
plan marks `suggest_del` on all Text of the feature (title struck) and
`plan.accept` emits the delete; or add a block-level suggest_del. Plan moves
and adds are fine as live tagged ops with inverses.

**A15. Loop plan on a feature the human is editing (ledger row 13, doc
wins).** Nothing in the calculus refuses `plan.open` on a feature with unsent
ink, and nothing stops a *trunk* loop ingest (a "small repair") into a block
the human is mid-edit. The latter is the worse one: see B2. Rule: while
`ink(f)` is non-empty or f is queued, loop ingests on f open a plan, and loop
plans on f are deferred to the next pass.

Also `ground(f)` is defined (§6) as "loop runs and loop tombstones on f since
the human's last op on the block": one keystroke by the human after a loop
rewrite erases all red and green before anyone reviewed it. Define ground
against the human's last hand-off (or a `feature.seen` watermark), not their
last op.

**A16. `feature.handoff` / `feature.realized`.**
(a) Two overlapping hand-offs (100 then 120 while the first is realizing):
`realized(100)` then `realized(120)` is fine, but a slow second agent
producing `realized(100)` after `realized(120)` moves the watermark
backwards. State monotone max.
(b) Who emits `realized` and on what evidence? If the agent via
`codoc_reflect`, it is forgeable and unverifiable exactly as today.
(c) `phase.realizing (lease)` reads a status-file mtime that is not in the
store, so `phase` is not a pure function of the log as §6 claims. Admit the
one ephemeral input rather than pretend.
(d) A hand-off while a loop plan is open on f: `description(f)` is the live
view, which includes the plan's gray text, so the prompt contains an
un-vetted loop rewrite as if the person wrote it. State which read mode
`directive(f)` uses (all-rejected plus the human's ink is the only honest
one).

**A17. Undo and the batch.** Undo is batch-wise (§3.1). A human's undo of a
batch that a later loop op anchored into leaves the loop's text after a
tombstone, which is fine and visible. Undo of the *accept* is the natural
unit; undo of an op inside an accepted plan is just a new human delete. Say
the undo stack contains only the person's own batches plus verdicts, and
"Restore mine" is the explicit inverse of a loop batch; otherwise ⌘Z after
`codoc init` undoes the bootstrap.

**A18. Translator plan on a block the human then edits.** I2 makes the
human's new chars trunk, outside the suggestion, so after accept they sit
inside a translated paragraph in the other language. Acceptable if reported.
Not acceptable: `codoc translate` re-run while that plan is open selects the
block again by detected language and stacks a second full-block
translation. Rule: skip blocks with an open translator plan.

**A19. Causality across machines.** "hybrid so a skewed machine cannot
reorder a causal chain" (04 §2) holds only if a daemon advances its HLC past
the maximum HLC of every op it has replayed (from a clone, pull or merge).
`HLC.advance(observed)` on `main` does this per observed op, but nothing in
04 says rebuild feeds the max into the daemon's clock. Failing case: B's clock
is 5 minutes behind; B pulls A, rebuilds, types after A's character; B's op
gets an HLC earlier than A's; the next rebuild anywhere replays B's op before
its anchor exists. State the rule and add a bound on wall-clock skew (an op
minutes ahead of local wall time is recorded and quarantined), or one machine
set to 2030 poisons every later `at` forever.

---

## B. The ingest heuristic (§3.2)

The claim in §7 ("a wrong word pairing costs a run boundary, never a wrong
author") is false in one important case, and the rule "marks re-anchored by
content match" contradicts I5. Enumerated:

1. **Moved sentence** (the loop reorders two sentences): the diff yields
   delete plus insert; the surviving words are re-minted with `actor=loop`;
   the human's own sentence reads as loop-written (green) with a red copy
   above. Wrong author for surviving text. Needs a rule: detect block-local
   moves at sentence granularity before the word diff, or classify the
   ingest as a rewrite and open a plan.
2. **Ingest against the wrong base** (the biggest one): the LLM reads the
   block at head 100, answers at head 130, and the human typed meanwhile.
   Diffing against the *current* text makes the loop tombstone the human's
   fresh words, drawn as red ground over the person's own unsent ink. That is
   the ledger's arbitration table (`base_text`), and the calculus lost it.
   Rule: `ingest` takes the head the text was read at; the diff runs against
   that text; the resulting ops anchor by char id; any op that would delete a
   char minted after that head is dropped and the whole write is demoted to
   a plan.
3. **Unicode normalization**: NFD from an IME versus NFC from the model makes
   every char differ; the block reads as 100% rewritten (opens a plan,
   re-authors all runs). Normalize NFC at every text boundary (insert and
   ingest); state it once.
4. **Multi-paragraph return**: the LLM returns a blank line inside one block.
   Ingest is per block; either it emits `block.split` (so ingest must produce
   structural ops) or it refuses. Same for a newline in a title. Unspecified.
5. **Empty return**: all chars deleted, the block persists empty. Say whether
   that is a `block.delete`.
6. **Marks in `new_text`**: bold, link and cite are carried by the Markdown,
   so "re-anchor by content match" is unnecessary for them and dangerous (a
   nearest-match guess, which I5 forbids). The correct rule is identity, not
   content: an existing mark survives iff a mark of the same kind and attrs
   in `new_text` covers at least one of its surviving chars; otherwise the
   old mark dies with its chars and the new one is minted by this op. Then
   "who bolded this" (a directive input, since bold is Focus) stays with the
   human's op when the loop merely rephrased around it.
7. **Human-authored mark that cannot be placed** (the loop rephrased a bolded
   imperative): today `translate` refuses the node. Make it general: an
   ingest with `actor=loop` that would drop a human bold, link or cite
   refuses the block; with a plan tag it records a conflict on the plan.
8. **Comment anchors and `suggest_del`** are not in Markdown and need no
   re-anchoring: they ride on char identity and survive on whatever chars
   survive. Acceptable, given A2 (positioned tombstones), because a fully
   rewritten span still reports what it commented on from tombstones. But an
   *open steer* whose span the loop fully rewrote loses its `Edit only:`
   scope; state that the directive renders scope from tombstoned cites too.
9. **CJK and unspaced scripts**: word diff degenerates to char diff, runs
   fragment. Acceptable; use `doclang.tokens` as the tokenizer and say so.
10. **Diff nondeterminism across library versions**: harmless, the output is
    recorded; note it so nobody "fixes" it by re-running.
11. **Whole-block ingest of a heading title** via `codoc_write_block`: a
    title is Text, fine, but sibling title collisions (today's `norm_key`
    refusal in translate) are unenforced anywhere. See C.
12. **Ingest into a block with an open plan by another party**: the loop's
    trunk repair interleaves with the agent's gray; accept then produces a
    paragraph three parties wrote. Sound, but ugly; the row-13 hold (A15) is
    the rule.

Acceptable without a rule: 9, 10, and odd run boundaries from equal-word
mispairing. Need a rule: 1 through 8, and 12.

---

## C. Missed use cases

Status per item: covered / needs a rule / cut.

- **Hub roles and capabilities** (`serve/payload.viewer_block`, `canSuggest`,
  `canHandOff`): needs a rule. 02 §3.6 says "the actor's role decides" but
  never where roles live or which ops each role may emit; and 04 §6 makes the
  hub a second daemon on a worktree, a second applier merged through git,
  which is fine only if authority is checked at apply (F1).
- **`codoc translate` idempotence**: needs a rule (A18). Selection by
  detected language plus "skip blocks with an open translator plan".
- **Dead-ref flagging** (`render._resolve_ref`; the IDE decorates dead
  cites): not mentioned. Add a derived view `dead_cites(f)`: cite marks whose
  `(file, symbol)` is absent from the index. Cheap.
- **Walkthrough (`ask`)**: covered (02 §3.5). Decide that steps cite block
  ids, which the brainstorm left open.
- **Bootstrap**: covered (02 §3.1). Needs a rule for re-init on a workspace
  whose `log/` already exists, and a note that the bootstrap batch is not
  undoable (A17).
- **`codoc history <feature>`**: needs a rule. A feature's body membership
  changes over time; "ops on blocks f dominated at the time" is a fold per
  op. Define it as structural ops on the heading plus text ops on currently
  dominated blocks, and say what it omits.
- **`Author asked` prompt capture**: needs a payload. `directive(f)` cites
  "the session's captured prompt" but no op carries it; put `asked` on
  `feature.handoff`. Then see F2 on privacy.
- **`realized.jsonl` outcomes**: cut, if `feature.realized` carries `{sha,
  files, summary}`. Say so explicitly.
- **Realizing lease**: needs a rule; keep it outside the store and admit that
  `phase` reads it (A16c).
- **Epoch/activity model** (session open/close, Stop-hook reconciliation
  deciding when Loop A's LLM pass may run): not covered. Marker ops
  `session.begin/end` next to the proposed `commit(sha)` marker would keep it
  in the log; otherwise say it stays outside.
- **Drift and divergence**: covered via `bind.refresh` and `caused_by`,
  provided the loop knows which hand-off it is reflecting for, which is the
  epoch again.
- **`Edit only:` scope**: covered (B8 caveat about tombstoned cites).
- **doclang helpers**: `norm_key` is unhoused; there is no sibling-title
  uniqueness invariant anywhere. Cut as an invariant (ids are identity now)
  and keep as a lint on `tree`. `terms`/`tokens`: needs a rule (B9).
  `clause_chars`: cut; the gate is now a deleted-character fraction, which is
  script-invariant.
- **Semantic title dedup** (opt-in embeddings): covered, outside the
  calculus.
- **Coverage net**: covered (`bind.attach`, actor=loop).
- **MCP reads returning `doc_language`**: covered (config.json).
- **Per-node `lang` tag**: needs listing as a derived view
  (`detect_prose_language` per block).
- **Comment outcome** (thread resolves when its directive lands;
  `annotation.in_margin` ageing): needs a rule linking a comment block to the
  hand-off that drained it and to the `feature.realized` that covers it.
- **Suppress loop proposals on held features** (row 13): needs a rule (A15).
- **`delete_code` asymmetry** (row 12): needs a payload (A11).
- **Study replay harness** (`docs/study-materials/replay/play.py` reads
  `status.json`, `edits.json`, frames): cut from v2's obligations, but say
  explicitly that the recorded study frames are unplayable on v2, so any
  re-analysis runs on `main`.
- **Migration of existing workspaces** (`codoc migrate`): not covered. See
  F6.
- **`codoc propose <kind>` from the shell**: covered if there is a CLI front
  to `ingest` with a plan tag.
- **Cmd+F replace across the tree**: covered (text ops per match).
- **Sentence-level ground**: still open in the brainstorm; fine.

---

## D. Performance

- **Tree fold and parent lookup**: `tree` and `parent(f)` are O(N blocks)
  folds. If the cache is keyed by `head` (§6), every keystroke invalidates
  it. Rule: a structural cache keyed by a structural head that only
  add/delete/move/set(kind, level) advance. Keep a heading-only ordered list
  (K ≪ N) for parent lookup.
- **`timeline(seq) = replay(log[..seq])`** is O(history) per scrubber frame,
  which is exactly what today's bounded `revisions.json` window exists to
  avoid. Since inverses are recorded, define it as un-apply from the live
  store over a bounded window, per block: O(window).
- **Tombstones in the hot path**: a 2,000-word block rewritten fifty times
  holds 100k dead chars. If live and dead share one sequence, every op on the
  block is O(live + dead). Eg-walker's shape applies: the cache holds live
  text plus runs; tombstone positions are rebuilt per block from the log only
  when a merge, a timeline frame or a missing-anchor lookup needs them. That
  also dissolves the compaction question in A2: nothing is ever compacted,
  and the truth is the log.
- **Runs**: a word-diffed LLM rewrite yields a few hundred runs; each typed
  char splits one; `attribution` merges adjacent same-actor runs at read.
  Fine. `Mark.from/to` lookups need a per-block `op → run indices` map or
  every mark render is O(runs).
- **Rank**: string compare is O(key length); growth is one char per 62
  inserts between the same neighbours, and typing paragraphs at the end of a
  feature is exactly that pattern (between the last paragraph and the next
  heading). Ten-character keys after years; fine. `ordinal_keys` on a moved
  range is O(count).
- **Export and projection**: O(N) per export; must be debounced to settles
  and structural changes, never per op. Initial projection load for 5k
  features with runs and marks is a few MB; exclude tombstones outside the
  display window.
- **Rebuild after merge**: k-way merge of per-session files sorted by HLC
  (monotone per daemon) then O(total ops) applies at O(block) each; a year is
  seconds. Fine.
- **Ingest**: O(n·d) word diff per block; fine. Move detection (B1) adds a
  sentence LCS; still fine.
- **`directive(f)` and watermarks**: need an `op → block` index plus the fold
  to feature; with A1's frontier-based queue the lookup is a set difference,
  not a range scan.
- **Binding uniqueness** (I8): needs an index; trivial.

---

## E. Version control (04)

1. **OpId reassignment** (A1) breaks every watermark, subset and `base` on
   the first merge. Fatal until fixed.
2. **Clock skew** (A19): the causal claim needs the advance-past-replayed-max
   rule and a drift bound.
3. **A session spanning a branch switch**: the session file is untracked
   until committed, so `git checkout other` carries it across; the daemon
   keeps appending ops against blocks that exist only on the first branch
   while `.codoc/log` now holds the other branch's files and `cache/` is
   stale. Rule: the daemon watches HEAD (it has `gitref`), closes the session
   file, rebuilds, and opens a new session on every HEAD change; ops typed in
   the gap are conflicts, reported.
4. **A session file committed mid-session, then the branch forks**: same
   session id, same file, two divergent tails. That is a real git conflict in
   a jsonl, the exact thing per-session files were meant to prevent. Rule: a
   session closes at commit (post-commit hook) or the session id embeds the
   HEAD sha at open.
5. **Rebase and squash**: the log files survive (they are tree content), but
   `base_sha`, `sha` and `commit(sha)` markers dangle. Acceptable; the
   provenance card must say "commit no longer in history" rather than fail.
6. **Two daemons on one checkout**: two session files, both mutating one
   `cache/`, each assigning positions. Violates "one applier". Keep today's
   watch singleton as a lock on `cache/`, and have the cache record the set
   of `(session, seq)` it has applied.
7. **Snapshot then merge of a pre-snapshot branch**: B forked before the
   snapshot; B's ops have HLC earlier than the snapshot's `block.add`s, so
   replay applies them first, every anchor is missing, and the entire
   branch's editing is conflicts. Fatal as written. Rule: a snapshot records
   the frontier of `(session, seq)` it folded; any op not in that frontier
   replays after the snapshot regardless of HLC. B's inserts anchored after a
   pre-snapshot tombstone also need the tombstone, which the snapshot
   dropped; positioned tombstones (A2) and keeping the archive readable fix
   that.
8. **Hand-edited or forged log**: validate schema, monotone seq per file,
   one actor per session, HLC sanity; reject with a report. See F1 for
   authority.
9. **git-lfs**: the merge driver receives pointer files unless it fetches;
   note it. Snapshots are the only large files and should not be in LFS.
10. **Monorepo with several `.codoc`**: one daemon and one log dir each;
    cross-package cites are out of scope. Say so.
11. **The merge driver requires codoc on every machine at merge time**; the
    fallback leaves a tracked derived file dirty after every daemon start,
    today's known annoyance. Accept, but document.
12. **"Two people merging the same two branches get the same tree"** holds
    only for the same set of log files; an uncommitted local session
    differs. Fine, but the sentence overclaims.

---

## F. Unknown unknowns

1. **Security: authority is trusted from the log.** The daemon stamps
   `actor` for webview and MCP writers, but the log is the truth and a PR
   carries session files. A hub contributor appends `plan.accept` and
   `feature.handoff` with `actor=human:<maintainer>` to their session file;
   the PR merges; the maintainer's daemon rebuilds, reads an accepted,
   handed-off plan, and the hub realizes it. Rule: a session file has one
   actor and every op's actor must equal it; verdicts, hand-offs and trunk
   writes are checked against the role table at apply, and a disallowed op
   is applied as a recorded conflict; the hub validates a PR's logs before
   merging. Signing session files is optional on top.
2. **Privacy.** `asked` prompts, GitHub logins in `actor`, and every
   keystroke's history are committed to a repository that may be public. A
   contributor's typo history is permanent. Decide: a `private` field set
   stripped by a git clean filter, or prompts stored as hashes with the text
   in the ignored cache, and archive-as-scrub for keystroke history.
3. **IME composition and character identity.** Flushing ops mid-composition
   mints Latin garbage in a Chinese tree, then deletes it; emit no ops
   between `compositionstart` and `compositionend`. Define `k` as a Unicode
   scalar index; the TS side indexes UTF-16, so every astral character
   (emoji, some CJK) shifts anchors by one between webview and daemon unless
   converted. Grapheme clusters: deleting half a cluster by id is legal and
   renders a broken glyph; acceptable, but say it. NFC at the boundary (B3).
4. **RTL and bidi**: decorations by char range work; the ingest tokenizer
   for Arabic needs `doclang.tokens`. No design change.
5. **Accessibility**: all three channels are colour-only. The run model
   makes an `aria-description` per span trivial; add it to the projection
   contract now, not later.
6. **Migration.** Existing workspaces have events with `prev_description`
   but no char identity. Replaying old AMENDs through a diff to reconstruct
   runs would be a diff deciding attribution, which the thesis forbids.
   Recommend: migrate as one bootstrap-like batch with the per-feature actor
   from `feature_writers`, mark it `migrated_from`, and keep the old ledger
   readable only through legacy `codoc history`.
7. **5k features**: the calculus is fine; the LLM pass still receives every
   title (dedup context), which is not new but scales with the tree, not the
   change.
8. **Offline and hub disconnect**: char-anchored ops queue offline and land
   on reconnect. Better than today. Two hub contributors in one block stack
   plans; fine.
9. **Client op ids versus applier ids**: the inbox needs an idempotency key
   per client op and an ack mapping it to the assigned `(session, seq)`;
   unstated.
10. **Testing strategy** (what pins what): `apply ∘ inverse = id` per op over
    random states; `replay(log) == store` on every apply path (stated);
    random op sequences preserve I1, I4, I8; `ingest(new_text)` then
    `description == new_text` with marks preserved; `export → import_ →
    export` fixed point; `replay(A ++ B) == replay(B ++ A)` up to the
    `conflict` field where anchors are disjoint; O(block) pinned by counting
    touched blocks per apply; Python/TS parity on code-point indexing and on
    projection rendering; timeline un-apply equals prefix replay; split/merge
    fuzzing with marks spanning the cut.

---

## G. Verdict and the five changes that matter most

The core is worth building. The design text is not yet a specification: it
is unsound at the merge boundary, at tombstones, and at I3, and it lost the
ledger's one arbitration rule in the ingest. Ranked edits:

1. **Make `OpId` global and watermarks frontier-based** (01 §2; §3 table rows
   16–17; 04 §2). Replace `OpId := int` with `OpId := (session: str, seq:
   int)`; add `pos: int` as the applier's local order, never persisted in a
   payload. Replace `upto: OpId` on `feature.handoff`/`feature.realized` with
   `covers: frontier` (per-session max seq), define `queued(f)` as frontier
   difference, and make `plan.accept.subset` a list of `(session, seq)`
   closed under anchor dependency.
2. **Tombstones are positioned and permanent in the truth; the cache stores
   live text plus runs** (01 §2 "Tombstones"; §4; 04 §5). Delete "compacted
   once nothing references them". Add: `Tomb` carries a position (a segment
   in the block's sequence); tombstone positions are rebuilt per block from
   the log on demand; a split or insert at a tombstoned char is legal and
   lands there; only an unknown `CharRef` is a conflict. Add `actor` and
   `at` to `Run`.
3. **Delete I3 and `count`** (01 §3 rows 8–9; §5). Hierarchy tolerates level
   gaps; `tree` folds by "deeper than the preceding heading"; no op mutates
   a block it did not name. `block.move`/`block.delete` compute their range
   at apply and record it, plus old ranks, in the inverse. Forbid
   `block.merge` on any heading and `block.delete` inside a plan (plans use
   `suggest_del` and accept emits the delete).
4. **Ingest against a recorded base, never the live text** (01 §3.2).
   `ingest(block, path, base_head, new_text, actor, plan?)`: diff against the
   text at `base_head`, anchor by char id, drop any delete of a char minted
   after `base_head` and demote the write to a plan; NFC at the boundary; no
   ops during IME composition; marks survive by identity (same kind and
   attrs over a surviving char), never by content match; a loop ingest that
   would drop a human bold, link or cite refuses the block; multi-paragraph
   text emits `block.split`.
5. **Authority at apply, and sessions that close on commit and branch
   switch** (04 §2, §6; 02 §3.6). One actor per session file; verdicts,
   hand-offs and trunk writes checked against the role table by the applier,
   with disallowed ops recorded as conflicts; the hub validates PR logs. The
   daemon advances its HLC past the max replayed HLC, bounds wall-clock
   skew, and opens a new session on every commit and HEAD change. Snapshots
   record the frontier they folded, and ops outside it replay after the
   snapshot.

Two smaller edits worth making in the same pass: define `ground(f)` against
the last hand-off rather than the human's last keystroke (A15), and state
which read mode `directive(f)` renders from when a plan is open on the
feature (A16d).
