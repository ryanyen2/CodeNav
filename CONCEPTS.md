# Concepts

Shared domain vocabulary for this project — entities, named processes, and status concepts with project-specific meaning. Seeded with core domain vocabulary, then accretes as ce-compound and ce-compound-refresh process learnings; direct edits are fine. Glossary only, not a spec or catch-all.

## Feature Tree

### Feature
A named unit of human-authored intent, distinct from the code it describes — one feature can bind to many code chunks across many files, and one file's chunks can belong to several features.

Lifecycle: `planned` (an accepted plan placeholder with no code yet) → `active` (a real, code-bound feature; the first Binding promotes it out of `planned`) → `retired` (tombstoned, no longer live). Lifecycle is a named state machine, not a derived projection — it is the authoritative status, separate from any in-flight Directive or hold state a feature might also carry.

### Binding
A link between one Feature and one code symbol in one file, identified by a file path and a qualified symbol path. A feature has as many Bindings as code symbols it covers; a symbol has at most one Binding (so a file with several bound symbols can map to several different Features).

## Realize Pipeline

### Directive
A single queued unit of code-implying work, targeting exactly one Feature. Directives are the machine-readable form of what the realizing agent is asked to do; each carries the kind of change (e.g. amend, retire, steer) and the Feature it targets.

### Hand-off
The state distinction on a Directive between **held draft** (constructed but not yet sent to the agent — visible in-situ as a pending diff/hold, but not queued for realization) and **handed off** (actively queued for the live agent session). An AMEND or block-edit Directive is born a held draft by default; it flips to handed-off only through an explicit gesture (a steer, a retire, a plan flag, or an explicit hand-off action) — and once handed off, it is sticky and never reverts to a draft.

### Activity Epoch
The lifecycle window of one agent working session, tracked as open (an agent is actively touching code) or closed (the session ended). While an epoch is open, file touches accumulate against it; when it closes, the touched-file record is preserved for reconciliation, not cleared. An epoch's origin distinguishes an interactive session from one owned by Loop B's own realization pass.
