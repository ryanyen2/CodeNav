# Doc-Attribution Robustness — Taxonomy, Invariants, and Plan

**Date:** 2026-08-01
**Goal:** the `tree.codoc` doc webview must survive how people *actually* edit —
char-by-char, messy, mid-thought, with transient malformed states — without ever
silently destroying a feature, detaching its bindings, or desyncing the doc body
from the left-nav tree. Today two design choices make the editor fragile:

1. **The doc body was ordered by `created_at`, not by the tree.** A child feature
   created after a later root rendered far from its parent, so scroll-spy jumped
   and the doc read nothing like the nav. (The reported bug: "Converged-document
   semantic analysis" floated to the top of the doc though it lives deep under
   "Semantic conflict analysis and resolution".)
2. **Retire was inferred from *absence*.** A heading that vanished from the settled
   doc — for any reason — emitted a `retire`, destroying the feature and detaching
   its bindings. But a heading vanishes constantly under normal editing.

This doc records the edit taxonomy that motivated the fix, the invariants we now
hold, how we *prove* them, and the phased plan (Phase 1 landed; Phases 2–3 scoped).

---

## Part 1 — The territory: how a keystroke becomes a command

The webview is a **projection consumer + identity-keyed command emitter** (U4). It
never persists `tree.doc.json` and never infers ops from a text diff. The path:

```
keystroke → ProseMirror doc mutates → (debounced) onSettle(doc)
  → host cites the baselineId of the projection it was showing
  → featureUnits(doc): walk headings → { fid, localId, title, description, parentId, retired }
  → settleCommands(history, baselineId, fallback, next, salt)
      → commandsForSettle(prev, next): identity-keyed diff → {add|set_title|set_description|move|retire}
  → append to edits.host.jsonl
  → daemon merge_host_ops → edits.json (under lock)
  → _command_to_op → apply_op → store
  → daemon re-projects → tree.doc.json + tree.codoc (derived artifacts)
```

Two orderings meet here and **must agree**:

| Artifact | Consumer | Order | Source |
|---|---|---|---|
| `tree.codoc` / left nav | `render_tree` | depth-first **pre-order** via `store.children` | authored tree |
| `tree.doc.json` / doc body | `build_doc_from_store` | *was* flat `created_at`; **now** pre-order | store projection |

Before Phase 1 the doc body used `list_features()` (`ORDER BY created_at`). The nav
used `store.children` recursion (pre-order). Any tree whose creation order differs
from its tree order desynced the two. This is a structural guarantee, not a
styling tweak — the fix reorders the projection to the nav's pre-order.

---

## Part 2 — The edit taxonomy (what "messy" actually means)

The old test set pinned clean, hand-picked scenarios. Real editing produces
*sequences* of transient, malformed doc states between keystrokes. The failure
class that matters lives in those transients. Enumerated:

**A. Structure-dissolving edits (the dangerous class — used to trigger retire):**
- A1. Backspace at heading start → heading merges up into the previous block.
- A2. Select-all + delete → every heading vanishes for one settle.
- A3. Cut a heading (intending to paste it elsewhere) → gone between cut and paste.
- A4. Delete a heading's text char-by-char → empty-title heading, then no heading.
- A5. Undo/redo storms → doc flips through intermediate shapes.

**B. Structure-forming edits (identity + parent must resolve mid-formation):**
- B1. Type prose first, then add a heading *above* it → prose momentarily orphaned.
- B2. Add lines under a paragraph, then insert a heading → which owner do they get?
- B3. Paste a multi-heading block → several un-minted nodes at once.
- B4. Promote/demote a heading (change level) → reparent, not add/retire.

**C. Identity-loss edits (a heading exists but lost its key):**
- C1. A heading with neither fid nor localId (paste from outside, malformed state).
- C2. Level jumps (h1 → h4 with nothing between) → parent-stack must clamp.
- C3. Zero paragraphs / empty title / duplicate titles.

**D. Concurrency edits (the daemon re-projects mid-typing):**
- D1. A settle cites baseline B0; the daemon already pushed B1 (renamed a sibling).
      Diffing against B1 would revert the daemon's rename → must diff against B0.
- D2. A settle fires again before the mint echoes back → the same `add` re-emits.

The old engine mishandled A (destroyed features), was untested on B/C (fragile
parent resolution), and D was only spot-checked.

---

## Part 3 — The invariants

Three load-bearing invariants, each now asserted by property tests over a large
random corpus (well-formed **and** deliberately malformed), not just fixtures.

- **I1 — No destruction by absence.** A feature is retired *only* by an explicit
  `retired` flag transition (false→true) on a node that STAYS in the doc — the
  `~ retire` gesture. A heading that merely vanished (taxonomy class A) is a
  **no-op**. Destruction is a gesture, never a geometry.

- **I2 — Identity-anchored prose (Phase 2 target).** A paragraph belongs to a
  feature by *identity*, not by "the nearest heading above it right now." Under
  class B edits, prose should not silently re-attribute to whichever heading it
  currently sits under mid-edit.

- **I3 — Structure from gestures, not geometry.** Reparent/add/retire come from
  explicit signals (level changes, new localId, retired flag), so a transient
  malformed shape (class C) cannot be misread as a structural op.

Supporting properties proven alongside them:
- **I-total** — `featureUnits` / `commandsForSettle` never throw on any doc
  (malformed included) and are deterministic (same inputs → same output).
- **I-idempotence** — a fully-minted projection diffed against itself yields zero
  commands (no churn); an un-minted node self-diffs to ONLY its deterministic-id
  `add` (`c-add-<localId>`), never a mutate/destroy (FIX B — ledger folds replays).
- **I-content-safe** — text-only edits emit only `set_title`/`set_description`,
  never `add`/`move`/`retire`.
- **P-preorder / P-parent / P-roundtrip** — the store→doc projection emits in nav
  pre-order, every parent precedes its descendants, and `parse_doc(build_doc_from_store)`
  recovers each feature's title/description/parent_id.

---

## Part 4 — The plan

### Phase 1 — Kill retire-by-absence + prove ordering and the diff (LANDED)

Faithful-tree ordering:
- `codoc/codoc_file/doc_render.py`: added `_preorder()` and wired it into
  `build_doc_from_store` (`features = _preorder(store.list_features())`). Reorders
  live features into the tree's depth-first pre-order, keyed by `parent_id`; a
  feature with a dangling parent is treated as a de-facto root and never dropped.
  Both the daemon (`loop_b.write_tree_doc`) and the hub (`serve/payload.py`) go
  through this one function, so the fix covers both surfaces.

Retire is explicit (I1):
- `vscode-codoc/src/state/commands-from-doc.ts`: `FeatureUnit` gained `retired`;
  `featureUnits` carries the heading's `retired` attr. `commandsForSettle` emits
  `retire` ONLY on the `retired` false→true transition (then `continue`). The
  absence-based retire loop is **deleted**. `settleCommands` no longer suppresses
  retires (the phantom-retire class is gone since retire is explicit), keeping the
  cited-baseline diff (D1).

Proofs:
- TS: `vscode-codoc/src/test/commands-from-doc.props.test.ts` — seeded mulberry32
  fuzzer, 400 seeds × 6 properties (I-total, I-idempotence ×2, I1, retire-iff-flag,
  I-content-safe).
- Python: `tests/codoc_file/test_doc_render_props.py` — seeded `random.Random`,
  60 seeds × 3 properties (P-preorder, P-parent, P-roundtrip).
- Regression fixtures added to `commands-from-doc.test.ts` and `test_doc_render.py`.

Status: all suites green — Python `tests/codoc_file/` 373 passed; vitest 663
passed (56 files); `tsc --noEmit` clean.

### Phase 2 — Identity-anchored prose (I2) — LANDED

`paragraph` gained an `ownerId` attr (the fid|localId of its feature) so prose
attribution follows identity, not "nearest heading above." Positional attribution
stays as the fallback for prose with no owner yet. This closes taxonomy class B
(type prose then add a heading above; add lines then insert a heading), where prose
used to re-attribute by geometry.

Two halves keep the anchor honest:
- **Owned from the first render.** `codoc/codoc_file/doc_render.py` stamps
  `ownerId = fid` on every projected description paragraph (`_paragraphs` /
  `_annotated_paragraphs` gained an `owner_id` param; `build_doc_from_store` passes
  `f.id`). The frozen text path `build_doc` stays owner-free → byte-identical to the
  pre-I2 shape. Both the daemon and the hub go through `build_doc_from_store`.
- **Crystallized for new prose.** `vscode-codoc/src/webview/tiptap/paragraph-owner.ts`
  adds the `ownerId` global attribute to `paragraph` and a keep-owner
  `appendTransaction` (mirroring `uniqueLocalIdPlugin`) that stamps any null-owner
  paragraph with its nearest heading's identity within one transaction. It only fills
  nulls — it never overwrites an owner — so a heading inserted above owned prose
  cannot steal it. Convergent (no transaction loop).

Attribution: `commands-from-doc.featureUnits` now buckets paragraphs by `ownerId`
(when it names a live heading) with a positional fallback — so when no paragraph
carries an owner (older docs / a pre-deploy projection) it degrades to the exact
pre-I2 walk, keeping every prior test green.

The flagged risk — "ProseMirror does not preserve custom block attrs across all
merge/split transactions for free" — is now *proven* rather than assumed:
`paragraph-owner.test.ts` drives a real `EditorState` through a split and asserts
both halves keep the owner, plus the anti-steal invariant (a heading above owned
prose does not re-own it) and convergence. Pure logic lives in
`pm-doc.paragraphOwnerFills`.

Proofs: `paragraph-owner.test.ts` (7), `commands-from-doc.test.ts` +3 I2 cases,
`commands-from-doc.props.test.ts` +1 property (owned prose lands in its owner
regardless of position, 400 seeds), `test_doc_render.py` +2 (projected paras carry
`ownerId`; text path omits it). Green: vitest 674, Python codoc_file/serve/loop 930,
`tsc --noEmit` clean.

### Phase 3 — Sequence-level fuzzing (the transients themselves) — LANDED

Phase 1 fuzzes *pairs* (prev, next). Phase 3 fuzzes *sequences*: a seeded fuzzer
drives a REAL ProseMirror `EditorState` — with the actual `uniqueLocalIdPlugin` +
`keepParagraphOwnerPlugin` appendTransactions firing on every transaction — through a
random script of taxonomy-derived edit operations (type, split, delete a
heading/paragraph, insert a heading above prose, erase a heading title, toggle
retire). After each step it serializes the live doc, runs `featureUnits` +
`commandsForSettle` against an evolving baseline (modelled as the daemon's last
projection — retired features excluded), and asserts the invariants at every
intermediate settle.

Invariants proven across sequences (`commands-from-doc.sequence.props.test.ts`,
150 seeds × 14 steps):
- **I1 crown jewel** — with NO retire gesture in the script, ZERO retire commands are
  emitted across the whole sequence. Messy editing cannot destroy a feature.
- **retire ⟺ flag transition** — with retire toggles allowed, retires correspond
  exactly to `retired` false→true transitions vs the baseline (no extra, no missing).
- **I-total** — `featureUnits`/`commandsForSettle` never throw on any settled doc.
- **no phantom identity** — every command names a live identity in the settled doc;
  every `add` is localId-keyed with a deterministic `c-add-<localId>` id and never
  carries an fid.

Anti-vacuous floors assert the fuzzer actually mutates the docs, exercises the
add/heading-above-prose path, and exercises real retires — so the invariants are
proven against genuine edit traffic, not a no-op sequence.

Green: vitest 676 (58 files), `tsc --noEmit` clean.

---

## Status — all three phases landed (2026-08-02)

The doc↔tree attribution is now robust against real char-by-char editing and proven
so at three levels: the store→doc projection lines up with the nav (P-preorder), a
single settle is safe for any transition (Phase 1 pairwise + I1/I2/I-content-safe),
and no *sequence* of messy edits can reach a destructive command (Phase 3). Retire is
a gesture, prose is identity-anchored, and structure comes from signals, not geometry.

---

## Appendix — Why the old design knew this was dangerous

`loop/loop_b.py` already tracked `soft_retired` / `unretired` counters — evidence
the authors knew absence-inference produced retires that had to be walked back. I1
removes the source instead of counting the damage.
