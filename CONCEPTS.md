# Concepts

Shared domain vocabulary for this project — entities, named processes, and status concepts with project-specific meaning. Seeded with core domain vocabulary, then accretes as ce-compound and ce-compound-refresh process learnings; direct edits are fine. Glossary only, not a spec or catch-all.

## Feature Tree

### Feature
A named unit of human-authored intent, distinct from the code it describes — one feature can bind to many code chunks across many files, and one file's chunks can belong to several features.

Lifecycle: `planned` (an accepted plan placeholder with no code yet) → `active` (a real, code-bound feature; the first Binding promotes it out of `planned`) → `retired` (tombstoned, no longer live). Lifecycle is a named state machine, not a derived projection — it is the authoritative status, separate from any in-flight Directive or hold state a feature might also carry.

### Binding
A link between one Feature and one code symbol in one file, identified by a file path and a qualified symbol path. A feature has as many Bindings as code symbols it covers; a symbol has at most one Binding (so a file with several bound symbols can map to several different Features).

## Concurrent Editing

### Projection
The read-only rendering of the store that an editing surface displays. Each one carries a
**baseline id** so a later edit can name the exact projection it was typed against. A
projection is authoritative about what the store holds and says nothing about what any
particular author has seen — the two diverge whenever a projection is read but not adopted.

### Baseline Citation
The baseline id an edit names as the state its content was computed from. The EDITOR owns
it (stamped when a projection is actually adopted, not when one arrives), so an edit
flushed by an incoming projection still cites the state its author was looking at. The
diff that turns a settled document into commands runs against the cited baseline, so
another party's in-flight change is never read as a user edit that reverts it.

### Base Text
The value an authored edit declares it is REPLACING for one field, as the author last knew
it. Its two legitimate sources are the emitting surface's own writes that no projection
has echoed back yet (an *optimistic overlay*, per field, which only its own emissions may
extend and only a confirming projection may retire) and the cited baseline. Never the
newest projection: that reads as "the author saw this", and an edit whose declared base
matches what the store already holds is applied verbatim, with no merge and no record.

### Arbitration
The resolution of two edits to one feature that never saw each other, decided from the
base text by two independent questions — do they overlap, and does the author outrank
whoever wrote the current text. Outcomes: clean, merged (disjoint, both land), superseded
(the higher-ranked edit wins the contended words; the losing text stays in the event
ledger), deferred (peers, so the whole edit goes up for review).

## Realize Pipeline

### Directive
A single queued unit of code-implying work, targeting exactly one Feature. Directives are the machine-readable form of what the realizing agent is asked to do; each carries the kind of change (e.g. amend, retire, steer) and the Feature it targets.

### Hand-off
The state distinction on a Directive between **held draft** (constructed but not yet sent to the agent — visible in-situ as a pending diff/hold, but not queued for realization) and **handed off** (actively queued for the live agent session). An AMEND or block-edit Directive is born a held draft by default; it flips to handed-off only through an explicit gesture (a steer, a retire, a plan flag, or an explicit hand-off action) — and once handed off, it is sticky and never reverts to a draft.

### Activity Epoch
The lifecycle window of one agent working session, tracked as open (an agent is actively touching code) or closed (the session ended). While an epoch is open, file touches accumulate against it; when it closes, the touched-file record is preserved for reconciliation, not cleared. An epoch's origin distinguishes an interactive session from one owned by Loop B's own realization pass.
