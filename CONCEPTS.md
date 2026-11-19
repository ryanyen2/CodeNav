# Concepts

Definitions are in `docs/model.md`; this is the glossary.

**Feature**: a live heading. Its *range* is the heading and every following
block up to the next heading at the same or a shallower level; its *body* is
the heading and the following non-heading blocks up to the next heading of any
level. A feature *is* its heading block: one id space.

**Block**: one unit of the document, with an id, a kind, a rank (a fractional
order key) and a level (headings and list items). Kinds: `heading`; the prose
kinds `paragraph`, `bullet`, `numbered`; the source kinds `table`, `diagram`,
`code`; the plugin kinds `image`, `screenshot`, `url`, `pdf`, `comment`.

**Segment / run**: a stretch of characters minted by one op, carrying its
actor and clock. A dead segment keeps its place. Runs are what the editor
paints and what History reads.

**Mark**: a range over character identity: `bold` (Focus), `code`, `link`
(Consult), `cite` (a code reference), `comment`, `suggest_del` (a plan's
proposed removal), `suggest_mark` (a plan's proposed mark change). Only `bold`
grows when text is typed at its end (I2).

**Op**: one of twenty operations, applied by one process, recorded with its
canonical payload, its inverse and, when it could not apply, a conflict code.
Ids are `session:seq`.

**Plan**: the changes an agent, the loop, a translator or a contributor
proposes, as ops tagged with a plan id that apply at once and show as gray text
until a verdict. Accept keeps the proposer as author; reject kills its words by
identity.

**Hand-off / realized**: per-feature frontiers (watermarks). A feature is
queued while its hand-off frontier is not covered by its realized frontier.
Ink outside the hand-off is unsent.

**Phase**: `edited`, `realizing`, `queued`, `planned`, `proposed`, `drifted`,
`synced`; the first that holds. One derived value for the marker, the tree
rows and the agent.

**Ingest**: the one place where whole new text for a field becomes ops through
a diff, against a stated base, with marks kept by identity.

**Machine gate**: where a machine's ingest lands when it named no plan: the
trunk, or a plan when the feature is held or the rewrite deletes too much.

**Projection**: the daemon's per-block view for the editor (text, runs, spans,
phases, claims, watermarks), adopted per block.
