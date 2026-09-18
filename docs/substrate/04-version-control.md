# codoc v2 — version control

## 1. What is committed

```
.codoc/
  config.json                 # doc language, kinds enabled; tracked
  log/<session-id>.jsonl      # the op log; one file per writing session; tracked
  tree.codoc                  # Markdown export; tracked, derived, regenerable
  cache/, lancedb/, cocoindex.db, index.meta.json   # the code graph and index; ignored
  inbox/<client>.jsonl        # client → daemon op submissions; ignored
```

The log is the truth and it travels with the code. A clone runs `codoc rebuild`
(or the daemon does it on first start) and has the whole history, attribution
included. Today only `tree.codoc` is committed; blame, plans and the ledger die
with the checkout.

## 2. Why one file per session

Two branches that both append to one log file conflict in git on every merge.
Two branches whose sessions wrote different files never conflict: git merges
by adding both. A session is one daemon lifetime on one checkout and **closes
on every commit and on every change of `HEAD`** (the daemon watches it), so a
committed session file is never appended to again and two branches forked from
one commit never share a file. Within a file the order is `seq`; across files
it is the op's HLC, then session, then `seq`. Op ids are `(session, seq)` and
survive the merge unchanged, so every anchor, frontier and plan subset recorded
before the merge still names the same thing after it.

The daemon advances its clock past the highest HLC it has replayed, so an op
typed after a pull is later than the characters it anchors to. A committed log
is the truth and nothing in it is refused at replay, a peer's fast clock
included: the local daemon's clock simply moves forward past it. The hub, which
accepts session files from strangers, rejects a file stamped far ahead of wall
time before it is merged.

After a merge, `codoc rebuild` replays all logs. Ops from the other branch land
on the merged state through their character anchors: an insert after a
character that still exists, live or dead, lands there; a delete of characters
already dead is a no-op; an op naming a character this store never minted
records a conflict and is shown in History as "could not apply after merge",
naming the block. Nothing is silently lost and nothing is guessed. Given the
same set of log files, the rebuild is deterministic; an uncommitted local
session is the one thing two people merging the same branches may differ by.

Each log file is validated at load: schema, monotone `seq`, well-formed and
non-decreasing HLC stamps. A file carries every actor the daemon stamped in
that session (the person, the loop, the agents); authority is checked per op.
A file that fails mid-way is reported and skipped, never partially applied; a
torn *last* line (a crash mid-write) keeps the prefix, since that is a state the
daemon was in, and the next daemon start cuts the torn line so every later
rebuild agrees.
Only one daemon may own a checkout's `cache/`; the existing watch singleton
lock is kept for exactly that reason.

## 3. The export in git

`tree.codoc` is committed so that a pull request shows the intent change
beside the code change, and so a person without codoc can read the tree. It is
rendered in the `rejected` read mode: an open plan is a proposal, not the
document, so an un-vetted repair never enters git as fact; accepting it is what
commits it. Because it is derived, a merge conflict in it is meaningless. A `.gitattributes`
merge driver (`tree.codoc merge=codoc`) resolves it by regeneration:

```
[merge "codoc"]
    name = codoc export
    driver = codoc export --merge %O %A %B %P
```

The driver rebuilds the store from the merged logs and writes the export. When
codoc is not installed, git falls back to a textual conflict the person
resolves by hand; the next daemon start regenerates the file from the logs
regardless, so a wrong manual resolution cannot corrupt anything.

## 4. Commits as anchors

The code side has commits; the document side has op ids. The join is recorded
twice, both existing: `feature.handoff` carries `base_sha` (HEAD when the work
was requested) and `feature.realized` carries `sha` (HEAD when the reflection
landed). History's provenance card opens the code diff between the two. A
`post-commit` hook appends a `commit(sha)` marker op so the timeline can also
be scrubbed by commit ("what did the tree say at v0.3"), which today needs a
timestamp guess.

## 5. Snapshots and size

A log grows by one op per settle, LLM write or verdict. At a few hundred bytes
per op, a year of daily editing is tens of megabytes: acceptable in a
repository, and compactable. `codoc snapshot` writes the store as a single
`log/snapshot-<hlc>.jsonl` (one `block.add` per live or dead block with its
segments, actors and marks preserved, plus bindings, plans and frontiers) and
records the frontier of ops it folded; earlier logs move under `log/archive/`.
A rebuild applies the snapshot first and then every op outside its frontier
regardless of HLC, so a branch forked before the snapshot merges correctly and
its inserts after pre-snapshot tombstones still land, because the snapshot
keeps dead segments. Attribution survives because segments carry their actor;
the archive stays readable for scrubbing before the snapshot. Snapshots and
logs should not be in git-lfs: the merge driver would receive pointer files.

## 6. What the hub adds

Nothing to the format. The hub's daemon writes its own session logs; a
contributor's ops carry their actor; a realized PR carries the logs its
session wrote, so the maintainer's merge of the PR merges the tree's history
with it. The hub's worktree is a checkout like any other. Authority is checked
at apply, not at the hub's door: a session file in a PR that carries a verdict
or hand-off by an actor whose role does not allow it is applied as a recorded
conflict on rebuild, and the hub refuses to merge a PR whose logs fail
validation.

## 6.1 Privacy

The log is committed, so a hand-off's `asked` prompt and every actor's
GitHub login are in the repository's history. `config.json`
`privacy.prompts = text | hash` decides whether prompt text or only its hash
is logged (the text then lives in the ignored cache); `init` defaults to
`hash` when the remote is public. Keystroke-level history is inherent to the
design and is what `snapshot` plus archive removal is for when a repository
must shed it.

## 6.2 Rebase, squash, history rewrites

Log files are tree content and survive any rewrite. `base_sha`, `sha` and
`commit` markers may then name commits no longer in history; the provenance
card says so rather than failing. A monorepo with several `.codoc` directories
runs one daemon and one log directory per directory; citations across them are
out of scope.

## 7. Trade-offs

- **Committing the log vs only the export.** Only the export is what exists,
  and it loses everything the product is about at every clone. The log costs
  disk and a rebuild step; both are bounded.
- **Per-session files vs one file.** One file is simpler to read and conflicts
  on every merge. Per-session files never conflict and cost a directory.
- **Merge by replay vs merge by text.** Replay is deterministic and records
  what it could not place. Text merge of the export invents a resolution and
  has no way to say what it lost.
- **HLC order across branches.** Two branches' ops interleave by time, which
  is not causally meaningful across branches but is the only total order
  available without a central sequencer, and character anchors make the result
  independent of the interleaving wherever the ops do not touch the same
  characters. Where they do, the later HLC wins and the earlier is a recorded
  conflict, which is the least surprising rule available and is reported.
