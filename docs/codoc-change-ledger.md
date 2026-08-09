# The codoc change ledger — one classification table for every change

codoc keeps a human-authored feature tree (the *codoc*) synchronized with a
codebase, in both directions, while humans and AI agents edit both sides
concurrently. The core algorithm is a **unified change ledger**: every change,
from either side, by any actor, is (1) detected, (2) classified, (3) reacted to
according to one explicit decision table, and (4) recorded with full provenance
so the IDE can render *who* changed *what*, *how*, and *because of which prior
change*.

The table is encoded in `codoc/loop/classify.py`; this document is its prose
counterpart.

## Provenance vocabulary

Every tree mutation is an `Event` row stamped with:

| field | values | meaning |
|---|---|---|
| `actor` | `human` · agent id (`claude-code`, `codex`, …) · `loop` | who made the change (`loop` = codoc's own deterministic machinery or its LLM pass) |
| `mode` | `pen` · `suggest` · `auto` | how it landed: direct authoritative edit / proposed, needs an accept verdict / machine-applied safe maintenance |
| `caused_by` | `d-…` directive · `e-…` event · suggestion id · `""` | the prior change this one implements — the causality chain |

Legacy events carry `""` and the model infers actor/mode from the event source
on load. Nothing downstream may *require* provenance; it only enriches.

## The decision table

| # | Side | Detected change | Origin | Reaction |
|---|------|-----------------|--------|----------|
| 1 | code | modified bound chunk | any | REFRESH binding (auto; `loop`/`auto`) |
| 2 | code | removed bound chunk | any | DETACH (auto); feature emptied **and not held** → RETIRE proposal (authority pass only) |
| 3 | code | relocated chunk (identical `tokens_hash` = move; unique same-file `types_hash` = rename) | any | deterministic ATTACH to the prior feature (auto — attribution never depends on an LLM) |
| 4 | code | added unbound chunk | any | ONE LLM placement pass → ATTACH (auto) or ADD/MOVE proposal (`suggest`); graph-neighbor coverage net as fallback |
| 5 | code | in-place modify on a realized feature with prose, **not held** | any | stale-description AMEND via the LLM (small → auto, large → `suggest`) |
| 6 | code | any change while a realize queue is open | agent | rows 1–5, ops stamped `caused_by=⟨directive id⟩` → the IDE groups them as a cascade under the user's edit |
| 7 | doc | AMEND / title edit | human/`pen` | apply + mint a **held-draft** directive `d-…` (`handed_off=False`): in the manifest + hold set (in-situ diff) but **not** in `realize.md`. No prose heuristic — the SYSTEM never guesses from English mood whether the edit "requests code" |
| 8 | doc | hand-off (commit / `codoc realize`) · plan ADD (`realized=False`) · RETIRE with bound code · steer (`> …`) · block `lower` | human | the EXPLICIT realize gestures — handed off the moment they are minted (or, for a held draft, when its feature appears in the one-shot `handoffs` channel) → `realize.md` is (re)built. `is_imperative` is **deleted**: intent is a typed gesture, never inferred from prose |
| 9 | doc | any edit in Suggesting stance | human/`suggest` | doc-ahead suggestion: registered as a pending *intent* (a hold) carrying the suggested text. **Applied by Loop B's intent drain** — the agent-side apply — through row 7/8 (`mode=suggest`, `caused_by=` the suggestion id). The human's only verb on their own suggestion is **Withdraw**, which removes the intent before the drain |
| 10 | doc | structural op via MCP | agent | pending proposal (`suggest`; renders code-ahead) |
| 11 | doc | safe op via MCP (reflect/attach/refresh) | agent | auto-apply, recorded in the sidecar changes feed as `actor=agent / mode=auto` → the IDE inks the prose as that agent's pencil |
| 12 | verdict | accept / reject from the IDE inbox | human | apply+consume / delete. RETIRE accept stays detach-only unless `delete_code` (destructive ops need explicit intent) |
| 13 | code | drift on a **held** feature (pending doc-ahead intent or queued directive) | any | bindings still maintained (rows 1, 3); AMEND/RETIRE/MOVE proposals **suppressed** until the hold releases — **doc always wins** |

Two structural properties make the table sound:

- **Bindings are attribution, not intent.** Rows 1/3 (and DETACH) always run —
  even on held features — so the code↔feature index never goes stale. Only
  *intent-level* ops (AMEND/RETIRE/MOVE/ADD) are subject to holds or review.
- **Documentation never writes code by itself.** A doc AMEND mints a held draft
  (row 7); code is realized only by an explicit, typed gesture (row 8) — never by a
  regex guessing whether prose "sounds imperative". The old `is_imperative` gate was
  deleted (2026-06): it false-fired on descriptive prose opening with a verb and
  re-fired on typo fixes. The USER decides what realizes, by handing off.
- **Destructive asymmetry.** A RETIRE can delete code only when a human typed
  `~` in the text or an agent explicitly passed `delete_code=True`. An accepted
  auto-raised retire merely untracks.

## The two channels

Provenance crosses the plain-text `tree.codoc` boundary through two small JSON
files (the Python loops never read the rich `tree.doc.json`):

**`.codoc/edits.json`** — written by the IDE host, schema v1:

```json
{"version": 1,
 "edits":   [{"feature_id": "f-…", "fields": ["description"],
              "actor": "human", "mode": "pen",
              "suggestion_id": "d-f-…", "ts": 0}],
 "intents": [{"id": "d-f-…", "feature_id": "f-…", "actor": "human", "ts": 0,
              "description": "Should validate the session token."}]}
```

- `edits` annotate settles ("this tree.codoc write was authored by X in mode
  Y"), written *before* the tree.codoc save so the daemon pass that the save
  wakes already sees them. Loop B **drains** them and stamps the matching user
  ops; an op with no annotation defaults to human/pen (a raw-text edit).
  Annotations are display provenance only — a stale one can mislabel an actor,
  never corrupt state.
- `intents` mirror the live doc-ahead suggestions and are **host-owned** (the
  loops never write the list): created on capture, removed on withdraw / once
  satisfied. An intent carries the suggested `title`/`description` (only the
  fields the suggestion changes); Loop B's **intent drain** applies it as the
  agent-side apply (row 9 → 7/8) and skips intents whose payload already
  matches the store, so the read-only drain stays idempotent. They are half of
  the doc-wins hold set; intents older than 7 days are ignored (an abandoned
  suggestion must not hold a feature forever).

**`.codoc/realize.json`** — written by Loop B next to `realize.md`:

```json
{"version": 1, "directives": [
  {"id": "d-…", "feature_id": "f-…", "kind": "amend",
   "caused_by": "<suggestion or event id>", "text": "<rendered directive body>"}]}
```

The machine-readable manifest of the queued directives: feature ids form the
other half of the hold set; directive ids are echoed as `⟨d-…⟩` in the
`realize.md` headings so the implementing agent can pass them back via
`codoc_reflect(caused_by=…)`. `kind` may also be `"steer"` (an inline `> …`
steering comment). `text` carries each directive's rendered body so a later
Loop B pass rebuilds `realize.md` as old + new — the queue is **appended to,
never clobbered** while a realization is in flight (closing the previously
deferred "wholesale rewrite can drop earlier queued directives" gap). Deleted
together with `realize.md` when the queue completes; a manifest with no
`realize.md` beside it is stale and ignored.

## Two parties, one feature: the arbitration table

The decision table above says what a change MEANS. This one says who wins when two
changes to the same feature reach the store without having seen each other — a person
typing while an agent amends, or two hub contributors on one description.

Each authored command declares `base_text`: the value the AUTHOR last knew for the field
it replaces (not a hash — the comparison then uses one normalizer, the daemon's own, so
there is no TypeScript/Python parity to drift). `loop_b._resolve_content` answers two
independent questions from it, which the predecessor fused into one boolean and got wrong
in both directions:

| overlap? | who wrote the current text | outcome | recorded as |
|---|---|---|---|
| base still matches | — | CLEAN — apply verbatim | an ordinary edit |
| disjoint edits | anyone | MERGED — both land, nobody reviews | `merged: … both edits kept` |
| same lines | this same editing session | MERGED — its rewrite wins its own words, other parties' disjoint edits ride along | `merged: … your version won` |
| same lines | someone this author outranks (`model.event.outranks`: a person beats an agent) | SUPERSEDED — the author wins; the agent's text stays in the event ledger | `codoc history` |
| same lines | a peer | DEFERRED — the whole edit goes up for review; nobody is overwritten in silence | a pending proposal |

The load-bearing part is the PROVENANCE of `base_text`, because every failure mode here is
silent: a `base_text` that happens to equal the store's current text reads as a clean
continuation, so the write lands verbatim with no merge, no arbitration and no record. It
therefore comes only from the author — the emitting host's own not-yet-echoed writes
(`state/known-store.ts`), else the projection baseline the settle CITES — and never from
"the newest projection the host has read", which the author may never have adopted. Where
the base cannot be established at all, the OLDEST retained baseline is claimed: an
under-claimed base makes the daemon cautious (it merges, or defers), an over-claimed one
makes it blind.

## The causality chain (surface-back)

A doc edit is *always underspecified* — the user edits the core intent and the
implementation touches more than the words said. The chain that keeps this
legible:

```
user edits doc (pen settle, or suggestion → Loop B's intent drain)
  → Loop B applies the op (event E, actor=human)        [rows 7/8]
  → directive d-X minted, caused_by = suggestion id or E
  → agent implements; codoc_reflect(caused_by="d-X")    [row 11]
  → epoch-close Loop A reflects the gaps; its ops also
    carry caused_by="d-X" while the queue is open        [row 6]
  → the IDE maps caused_by → "↳ from your edit" on the
    surfaced-back diffs, and pencil-inks agent prose
```

## What the IDE consumes (sidecar v4)

`tree.bindings.json` gains two ledger views:

- `changes` — the last ~50 *applied* events (`{event_id, at, kind, feature_id,
  actor, mode, caused_by}`, newest first). Drives the **pencil re-stamp**: when
  a description changed under the saved rich doc and the latest amend was an
  agent's, the fresh text is inked as that agent's pencil instead of resetting
  to plain.
- `holds` — the doc-wins hold set, for any per-feature "yours pending" cue.
- `proposals.*` entries additionally carry `actor` / `mode` / `caused_by`; a
  non-empty `caused_by` renders the inline cascade cue (`↳ from your edit`) on
  the existing diff card — color still encodes direction, ink still encodes
  authorship, no new UI surface.

## Incrementality invariants

The reaction cost of a change is bounded by the change, not the repo:

- Indexing is per-file memoized (cocoindex); chunk reads push the file scope
  down to LanceDB (`read_all_chunks(files=…)`) and never read embeddings in the
  loops; the full-index symbol table for graph resolution is a source-less,
  embedding-less projection.
- The graph cache updates per touched file (`update_graph`).
- One feature-table read per Loop A pass, threaded through subtree selection,
  title dedup, and placeholder adoption (`bound_feature_ids()` replaces
  per-feature binding lookups).
- ONE LLM call per pass, fed only the changed chunks + the file-locality ego
  subtree + every node title (the dedup context) — no per-node LLM work.
- State-based reconciliation (`reconcile_drift`) is the authority and is
  idempotent: a missed cycle is recovered by re-deriving divergence from
  current state, never by replaying history.
