# codoc v2 — workflows and gestures

Every workflow below is stated as what the person does, what ops the system
records, and what the person then sees. If a workflow needed an op that is not
in `01-calculus.md`, the calculus would be wrong, not the workflow; none did.

## 1. The two loops, restated on the calculus

**Code → document (reflect).** The index diff finds added, modified, removed
and relocated chunks. Deterministic reactions (`bind.refresh`, `bind.detach`,
relocation `bind.attach`) apply as ops with `actor=loop`. What needs judgment
goes to one LLM pass that returns, per feature, new text and structural
suggestions. New text enters through `ingest` against the text the LLM was
shown (`base`), as text ops with `actor=loop`: small repairs land on the trunk;
large rewrites, new features, moves and deletions open a plan (`plan.open` with
`actor=loop`) so their ops are tagged and await a verdict. "Small" is the
fraction of a block's live characters the ingest deleted, an exact number the
call records, tightened for blocks whose last live author is a person. A
feature with unsent ink or queued work never receives a trunk write from the
loop: the call opens a plan, and a structural loop plan on it waits for the
next pass (the document wins). A loop's "this feature lost all its code" is a
`kind=delete` plan: the title and prose are struck under `suggest_del`, and
accept emits the delete.

**Document → code (realize).** A person's trunk ops on a bound feature's blocks
beyond the hand-off frontier are a held draft, visible as ink. A hand-off
gesture records `feature.handoff(covers=head)`; the directive builder renders
the feature's current text plus what changed since `realized.covers` into the
agent's prompt. The agent implements, reflects through the code loop with
`caused_by` the hand-off op, and `feature.realized(covers, sha)` closes it — a
realization with nothing queued is refused `nothing_queued`, since there is no
request to settle. The document never writes code; a gesture does.

## 2. Editing gestures

The editor declares what happened at dispatch. The settle boundary flushes ops;
it repairs block-id uniqueness after paste and split, and nothing else.

| gesture | ops (one batch) | what the person sees |
|---|---|---|
| type in a paragraph | `text.insert` / `text.delete` | blue ink, pulsing until the daemon acks |
| Enter mid-paragraph | `block.split` | two paragraphs; comments and ink stay with their words |
| Enter, Enter, `## `, type a title | `block.split`, `block.split`, `block.set(kind=heading, level)`, `text.insert`… | a new feature; paragraphs typed below it are its body |
| `## ` at the end of a populated paragraph | `block.split` at the caret, `block.set(kind=heading)` | the paragraph stays with the previous feature; the caret is in the new title |
| type a paragraph, then insert a heading above it | `block.split` (empty first half gets the new id), `block.set(kind=heading)` | the paragraph now reads under the new feature; nothing about it changed |
| Backspace at paragraph start | `block.merge` | one paragraph; attribution follows the characters |
| Backspace/Delete across a heading boundary | none; the caret moves | the boundary refuses to merge title into prose (kept from today) |
| empty a title by deleting characters | `text.delete` | a feature with an empty title, flagged (`phase` is `edited`: a removal is pending intent, and the hand-off carries it as `removed:`); not a deletion |
| Delete feature (command, ⌘⌫ on a heading) | `block.delete` (its range is the heading and its own body, computed at apply; the applier detaches its bindings and records them), `block.set(level)` per promoted child | the feature and its prose are gone; History has them; a bound feature stays in the queue as a phantom and its removal directive is held until a hand-off with `delete_code` |
| select-all, Delete | the same per feature, one batch | reversible with one undo; refused only if it would empty the whole tree |
| Tab / Shift-Tab, drag a feature | `block.move(level_delta)` (the subtree range is computed at apply) | the subtree moves as one |
| cut a paragraph, paste elsewhere | `block.move` when the same id leaves and arrives within one settle; otherwise `block.add` + `text.insert` with the paster as author, `copied_from` in the op meta | |
| paste a subtree | `block.add` × n with fresh ids | the arriving node is the clone |
| undo | inverse ops, `actor` the person | an undo after ack is a new op; the ledger keeps both |
| bold a phrase | `text.mark(bold)` | Focus line in the next directive |
| paste an external link | `text.mark(link)` | Consult line in the next directive; underlined to say so |
| `@` cite code | `text.mark(cite)` | inline citation; `Edit only:` scope when inside a commented span |
| comment on a span | `block.add(kind=comment)` + `text.mark(comment)` | a margin card; `scope=code` makes it a steer |
| Build it (from a comment) | `feature.handoff` | the terminal runs the realize pass |
| edit inside a proposed span | `text.insert` with no plan tag | blue inside gray |
| Accept / Reject | `plan.accept` / `plan.reject` | gray becomes prose or vanishes; attribution keeps the agent |
| Restore mine (after a loop rewrite) | inverse of the loop's batch, `actor` the person | the words come back as the person's own edit |
| Catch up / Keep | none; the viewer's read cursor advances | the ground under that feature clears for this viewer; nothing is written to the log |
| switch language, translate | `ingest` per block with `actor=translator:<lang>` | a plan per batch if the block was human-authored, trunk otherwise; a block with an open translator plan is skipped by a re-run |

Two gestures are deliberately impossible rather than disambiguated afterwards:
a heading cannot be merged into prose by a keystroke, and a feature cannot
vanish by a heading vanishing. Both require the explicit delete gesture. That
is what removes the five-way "why is this heading absent" taxonomy.

## 3. Workflows

### 3.1 First run on a repository

`codoc init` indexes the code, runs bootstrap (per-file and organizing passes,
kept), and writes the resulting tree as `block.add` and `bind.attach` ops with
`actor=loop` in one batch. The person opens the tree and reads it. Nothing is
gray: bootstrap output is the trunk, because there is nothing for it to be a
suggestion against.

### 3.2 Daily: edit, hand off, review what was built

The person edits prose. Ink appears and stays as a held draft. When they want
code, they hand off (⌘S with a bound feature dirty, the toolbar, or Build it on
a comment). The queue is the ops since the watermark, rendered. The agent
works; the code loop reflects its changes with `caused_by`; the feature reads
`realizing` then `synced`, or `divergent` when the agent's reflect touched
features the hand-off did not name (kept: a pure function over `caused_by`).
The person reads the ground: green on what the reflection added, red on what
it cut, in the same paragraph as their ink.

### 3.3 The agent plans first

`/codoc:plan` opens a plan: `plan.open(actor=agent:…)` then `block.add` and
`text.insert` ops tagged with it. The plan is materialized in place as gray
text at the rank it would take. The person edits it in place (blue inside
gray), accepts or rejects. Accept keeps the agent as author of the surviving
words and hands the feature off in the same batch, since a plan is by
definition a request to build. **The sender of the verdict emits the hand-off,
not the applier**: the editor sends both bodies in one batch and `codoc accept`
does the same (`--no-build` takes the words without the work). A `kind=delete`
plan is the exception — removing code is its own decision, made with
`codoc handoff --delete-code`. Reject tombstones it; History still shows it.

### 3.4 Reviewing an agent session (the study task)

History scrubs by op id. Each batch is a stop: what changed, who, and the
`caused_by` chain to the directive, the prompt, the session, and the commit.
The per-word question ("did the build keep this clause") is the ground under a
planned run: a plan-tagged run that was accepted and later tombstoned by a loop
op is exactly "agreed and not kept", read straight off the tombstone's `by`.

### 3.5 Asking

`/codoc:ask` draws a walkthrough over existing features. It writes nothing
(kept as an ephemeral overlay outside the calculus, because it changes nothing
and must be usable mid-edit).

### 3.6 Collaboration on the hub

A contributor's browser emits the same ops; the hub stamps `actor=human:<github>`
from the session cookie and forwards them to the daemon's inbox. The applier
checks the actor's role (from the GitHub permission table) on every op: a
`suggest` role may only write plan-tagged ops and comments, so a contributor's
edit on the trunk is applied as a plan with that actor, which is today's
Suggesting stance without a stance. The maintainer accepts, which hands off;
the hub realizes on a worktree and opens a PR whose log files the hub validates
(one actor per session file, roles honoured) before merge. Verdicts are ops, so
the hub needs no separate verdict channel.

### 3.7 Branching and merging the repository

See `04-version-control.md`. The person branches, edits the tree and code,
merges. The tree's history merges with the code's because the logs are files
in the repository, one per session, and the store rebuilds from them.

### 3.8 Switching the authoring language

`codoc lang zh-Hans` sets the workspace language; new prose originates in it.
`codoc translate` ingests translated text per block with a translator actor;
citations and links are marks and are re-anchored by the ingest, which refuses
a block where one cannot be placed. A human-authored block's translation opens
a plan so the author keeps the strict gate they have today.

## 4. What the person sees, and from where

| surface | reads |
|---|---|
| ink, ground, plan opacity in the prose | runs and marks on the block; one encoding, three tags |
| margin marker per feature | `phase(f)` |
| tree rows, minimap rail | `phase(f)` through the same palette |
| History stance | `attribution` over the full window; `timeline(seq)` |
| provenance card | the `caused_by` chain from the op |
| status bar | the daemon's head, lease, and queue size |
| comment cards | comment blocks whose anchor mark still resolves; resolved ones age out |

Everything in the table is a read of the store. Nothing is a second model.

## 5. Trade-offs in the workflows

- **Delete instead of retire.** Retire kept a struck-through node in the tree
  forever and confused every participant who tried to remove something. Delete
  removes it, the ledger keeps it, undo restores it with its position and
  bindings, and a bound feature's deletion still asks before code goes. The
  loop's own "this feature lost all its code" is a plan to delete, not a retire.
- **No Suggesting stance.** Whether an edit is a suggestion is decided by who
  is editing what, not by a toolbar mode the person has to remember. A
  maintainer on their own tree writes the trunk; a contributor writes plans.
- **Hand-off stays explicit.** The study and the ledger both showed that
  inferring "this prose wants code" from English mood is wrong. A description
  edit is documentation until a gesture says otherwise.
- **Accept hands off.** Accepting a plan without building it left a `planned`
  placeholder whose only purpose was to wait; a plan is a request, so the
  verdict queues it. A person who wants the words but not the build edits the
  words instead of accepting.
- **Per-word attribution is recorded, paragraph summaries are shown.** The
  default History label on a paragraph is "written by you, repaired by the loop
  on <date>, one clause from an accepted plan"; the word runs show on hover.
