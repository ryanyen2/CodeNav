# Concepts

**Feature** — a heading block and the paragraphs before the next heading
(its body); it dominates every following block until a heading at the same
or a shallower level. A feature *is* its heading block: one id space.

**Block** — a heading, paragraph or plugin kind, with a rank (fractional
order key), a level (headings), and content whose Text fields are attributed.

**Segment / run** — a stretch of characters inserted by one op, carrying its
actor and clock; dead segments are positioned tombstones. Runs are what the
editor paints and what History reads.

**Mark** — a range over character identity: bold (Focus), link (Consult),
cite (a code reference), comment, suggest_del (a plan's proposed removal).
Suggestion and comment anchors never grow (I2).

**Op** — one of twenty operations with a canonical payload, a recorded
inverse and, when it could not apply, a conflict. Ids are `session:seq`.

**Plan** — an agent's or the loop's proposal: ops tagged with a plan id that
apply live as gray text until a verdict. Accept hands the feature off.

**Hand-off / realized** — per-feature frontiers (watermarks). Queued work is
the difference; ink past the hand-off is unsent.

**Phase** — synced · edited · queued · realizing · proposed · drifted ·
planned; one derived enum for the marker, the rows and the rail.

**Ingest** — the one boundary where a whole-text write becomes ops through a
diff, against a stated base, with marks kept by identity.

**Projection** — the daemon's per-block view for the editor (text, runs,
spans, phases, claims, watermarks). Adopted per block.
