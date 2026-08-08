# Editing surface UX — measured audit and architecture

**Date:** 2026-08-07 · **Branch:** `fix/edit-tracking-robustness`
**Scope:** both editing surfaces — the VS Code webview and the `codoc serve` hub.
**Predecessor:** `2026-08-07-001-edit-tracking-robustness-architecture.md` (the
transaction engine). That one made edits *survive*. This one is about whether the
person making them can *tell what is happening*.

---

## 0. The one structural fact that shapes everything

`codoc/serve/static.py` serves **the same esbuild bundle** the VS Code webview
runs. There is one editor and two transports, selected at runtime by
`acquireHostApi()` (`host-bridge.ts`). So "design both surfaces well" is not two
design jobs. It is one editor, plus an honest accounting of what differs:

| | VS Code | hub |
|---|---|---|
| transport | in-process messages | HTTP POST + SSE |
| latency | file-system | network, retried, sometimes offline |
| authority | always the maintainer | GitHub permission: none / suggest / handoff |
| theming | host `--vscode-*` vars | `body.codoc-standalone` palette |

Two of those four — **latency and authority** — were invisible to the client.
That is where the hub's editing experience actually breaks, and it is not a
styling problem.

---

## 1. What was measured, including the hypothesis that died

The opening hypothesis was that per-keystroke decoration cost made typing janky.
Every decoration plugin is written identically:

```ts
if (tr.getMeta(X_UPDATED) || tr.docChanged) return buildEverything(newState.doc, …)
return old.map(tr.mapping, tr.doc)
```

`tr.docChanged` is true for every character, so the cheap `map` branch — the one
ProseMirror provides for exactly this — runs only when the document did *not*
change. Nine plugins do this. The arithmetic suggested ~67ms per keystroke.

**The arithmetic was wrong, and `decoration-cost.perf.test.ts` says so.** Cost
tracks the number of Decorations *created*, not the document walk, and most
layers early-return `DecorationSet.empty` when they have nothing to show:

| layer | active when | measured, 300 features |
|---|---|---|
| hold / pending | **while you type** | 0.03–0.11ms — fine |
| blame | History toggled on | **7.3ms rebuild vs 2.4ms map** |
| glance | Glance toggled on | same shape |
| blocks, threads, phases | content present | empty → free |

So the real finding is narrower and worth stating precisely: **the layer that is
active precisely while you type is cheap; the expensive ones are behind toggles.**
Turning History on makes typing cost ~7ms/keystroke at 300 features and worse
above that, because blame decorates *every* feature to redraw *none* of them —
typing a character changes no blame fact. Mapping there is not merely faster, it
is the correct answer. Real, bounded, worth fixing; not the catastrophe claimed
before it was measured.

---

## 2. The hub defect — enforcement without legibility

`dispatch.py` gates every command on a capability, correctly. `host-bridge.ts`
drops any 4xx from the outbox, also correctly — a capability you lack never
succeeds on retry, and keeping it would wedge every later message behind it in
the FIFO. Both halves are right. Together they are a silent loss:

1. `protocol.ts` had **no capability field**, so the client drew the maintainer's
   affordances for every viewer.
2. A read collaborator edits a description. The settle POSTs. The hub answers 403.
3. The outbox drops it. Nothing is shown.
4. Their prose stays on screen looking saved, until the next projection replaces
   it with the version they never changed.

This is the same failure class the engine work just closed, re-entering through
the transport. And the cause is structural, not cosmetic: **the client cannot
show what it does not know.**

Three facts were missing, and they are the same shape — all answer *what will
happen to what I type?*

- **Capability** — what am I allowed to do?
- **Delivery** — did my edit actually reach the hub?
- **Connection** — is the hub still there?

### Landed

**`viewer` on the payload** (`payload.viewer_block`, `protocol.ViewerInfo`).
Attached **per connection** in `app.py` / `push.event_source`, deliberately *not*
inside `build_browser_payload`.

> *I might break this if…* the viewer block were built into the shared payload.
> `PayloadStream` computes one payload and de-dupes it across every connected
> viewer, so the capability would be whichever viewer's connection happened to
> populate the cache — handing a contributor a payload claiming HANDOFF and
> unlocking a button the server then refuses. Worse than not knowing. Pinned by
> `test_the_shared_payload_carries_no_viewer`.

> *I might break this if…* the two booleans came from a second table. They are
> `cap.can_suggest()` / `cap.can_hand_off()` — the capability's own answers, the
> same ones the routes enforce with, so they cannot drift apart. Pinned by
> `test_the_block_derives_from_the_capability_not_a_parallel_table`.

**`Delivery` on the bridge** (`host-bridge.ts`). `state` is `live` / `queued` /
`offline`, plus the last refused message.

> *I might break this if…* the indicator were wired to SSE. EventSource
> reconnects on its own, so it would flicker "offline" through every routine
> reconnect while saying nothing about whether the user's edit is safe. Delivery
> is derived from the **POST path**, which is the one that answers the question
> being asked.

**One surface** (`viewer-status.ts`) rather than role checks scattered through
the shell. It renders nothing in VS Code, where the answer never varies.

Design choices, each stated as a consequence rather than a privilege:

- The role reads **"Suggesting" / "Editing" / "Read only"**, not "read
  collaborator". Your GitHub permission is not the thing you need to know while
  you are typing; what happens to your words is.
- The delivery half is **absent entirely when everything has landed**. A
  permanent "connected" badge is chrome people stop seeing, which makes it
  useless on the one day it matters.
- Colour goes only to **Read only** and **Offline** — the two states where
  waiting does not help and the reader may need to act. Colouring Suggesting vs
  Editing would rank collaborators by permission for no actionable purpose.
- A refusal is announced **once**. The bridge reports the last rejection as
  *state*, not an event, so it is present on every later delivery change;
  re-announcing would turn one refusal into a notice on every subsequent
  keystroke. `noticeFor` is pure and pinned by four tests.
- Refusal text always contains **"not saved"**, including for unrecognised status
  codes. Whatever else is unclear, that part must never be.

Anti-vacuity floors drive the real bridge against a hub answering 403 (dropped,
reported) and a dead network (kept, reported as offline).

---

## 3. The decoration policy — LANDED

One policy (`tiptap/decoration-policy.ts`) replacing nine copies of the wrong
one. A structure-keyed layer is invalid only when its *state* changes or the
**heading sequence** changes; otherwise it maps. `structureChanged` is memoised
per transaction in a `WeakMap`, so the nine layers share one computation.

> *I might break this if…* the shared answer were published as plugin state
> instead. Reading another plugin's state during `apply` returns its OLD value
> unless that plugin happens to be registered first — a bug that appears only
> when someone reorders the extension list. A `WeakMap` keyed on the transaction
> has no ordering to get wrong.

> *I might break this if…* every layer were forced through it. `hold`, `captured`
> and `reveal` derive decorations from the TEXT (a changed-range underline, a
> per-word animation), so for them a keystroke really is invalidating and mapping
> would show a stale span — and they are cheap anyway, decorating only the feature
> being edited. Applied to blame, phases, blocks and threads; **not** to glance,
> which reads heading text and is therefore not structure-keyed.

The risk in this change is the opposite failure — mapping when a rebuild was
needed, trading jank for stale decorations, which is worse. So the tests pin both
directions: split/delete/identity-change/re-own/level-change all still rebuild,
and a mapped edit is asserted to actually *reposition* its decorations.

**`glance-decorations` widget key** — fixed separately, since glance cannot use
the policy. It was the one widget layer with no `key`, so ProseMirror tore down
and rebuilt one `<div>` per feature on every keystroke while Glance was on. The
key includes the pitch, because keying on fid alone would make ProseMirror treat
a changed pitch as the same widget and keep the stale text.

## 4. Ordering and drag-to-reorder — LANDED

The tree had no representation of order at all: siblings came back
`ORDER BY created_at`, so a reorder emitted a `move` whose `parent_id` had not
changed, `apply_op` wrote the parent it already had, and the next render put the
node back. The gesture animated and then reverted.

**`rank`, a fractional key** (`codoc/model/rank.py`, schema v6). Base-62 and
ASCII-ordered, so Python `<` and SQLite `ORDER BY` agree with no collation; keys
never end in the zero digit, so equal keys mean equal positions rather than an
encoding accident.

> *I might break this if…* order were a dense integer `ord` renumbered on every
> reorder. Moving one node would write every sibling row — and every write stamps
> `feature_writers`, so dragging one node would mark all its siblings as freshly
> written and the author's next edit to any of them would read as a conflict with
> a stranger (`loop_b._resolve_content`). Moving one node must touch one row.
> Pinned by `test_a_reorder_touches_exactly_one_row`.

**Position is NEIGHBOUR IDENTITY, never an index.** `NodeOp.after_id/before_id`,
resolved to a key at the write boundary by `Store.rank_between`.

> *I might break this if…* the command carried "third child". By the time it
> drains, Loop A may have added or retired a sibling and the index means
> something else. "After A, before B" still means what its author meant — and
> when A vanished the other half still applies; when both vanished it appends
> rather than guessing; when A is no longer a child of that parent, its key is a
> position in a different list and is ignored.

**The client emits the minimum.** `reorderTargets` takes the complement of a
longest increasing subsequence, so one drag is one command. Anchoring is
asymmetric on purpose: `afterId` is the immediate predecessor (commands are
emitted in document order, so a predecessor is always already placed), `beforeId`
is the nearest following sibling *that is not moving* (a following mover has not
been placed yet). Giving both bounds is what makes the result independent of
which longest subsequence was chosen — with one bound, a run of adjacent movers
can satisfy every anchor it was given and still land wrong. A 500-permutation
fuzz replays the emitted commands and asserts they reproduce exactly the order
the user saw.

The concurrency property falls out of restricting the comparison to ids both
sides know: the agent inserting or retiring a sibling leaves every surviving
neighbour relationship intact, so it produces no phantom moves.

**The gesture is a document edit.** `feature-drag.ts` moves a *slice* — the
heading plus its prose plus its nested features — in one transaction, so undo
restores it in one step. It writes no command itself: the settle pipeline sees
the changed order and emits the move. Slice boundaries use the CLAMPED depth, the
same one that decides parentage everywhere else, so a drag can never grab a
different subtree than the indentation shows.

`drag-handle.ts` uses pointer events (the native drag image is unstylable across
hosts and `dragover` stutters the drop line) and event delegation (binding a
closure per handle would mean re-creating every handle on every keystroke — the
churn `decoration-policy` exists to remove). Escape abandons a drag mid-flight; a
press that never travelled 4px is a click, not a zero-length move. **⌥⌘↑/↓ moves
a feature by sibling**, because a drag is mouse-only and restructuring a tree must
not be — and the grip reveals on `:focus-within` as well as hover, so the
affordance is visible to the people who depend on that path.

**Migration preserves what users already see**: ranks backfill per parent in
`created_at` order, so upgrading does not reshuffle anybody's tree.

## 5. Authoring cues and empty states — LANDED

Both remaining items, and one of them turned out to be a documentation bug.

**The markdown-signal gap was half the size I recorded, and differently shaped.**
CLAUDE.md described *three* signals feeding Loop B directives. Checking the code
rather than the doc:

| signal | live? | feedback before |
|---|---|---|
| `**bold**` → `Focus:` | yes (`_signal_lines`, command path) | yes — StarterKit input rule |
| `[label](https://…)` → `Consult:` | yes (`_signal_lines`) | **none** |
| `> …` → `STEER` | **no** — retired in U7 | n/a |

`loop_b` step 2.7 is explicit: once the webview stopped writing `tree.codoc`, the
text-comment → STEER loop was dead and was deleted. Steers arrive through the
inline-comment surface via `edits.json`. So typing `> ` in a description has been
ordinary prose for some time, while CLAUDE.md told every reader — human and agent
— that it queued a directive. Corrected there.

> *I might break this if…* I had shipped the cue this plan originally called for.
> Styling `> ` lines as steering callouts would have made a retired path look
> live, which is worse than the silence it replaced: the author would believe the
> agent had been told something nobody sent.

**Consult links are now marked** (`consult-decorations.ts`). A decoration, not a
schema mark: the description round-trips through `inlineRunsToText` to the exact
markdown the daemon parses, and a Link mark would insert a serializer between the
author's text and that parser — one more place for the two to disagree. The
pattern mirrors `parse._LINK_RE` character for character, and nine table-driven
cases assert the TypeScript and Python matches agree, including the ones that
must NOT match (`codoc:` refs, bare URLs, unclosed brackets). A cue that
over-matches is a lie about what the agent was told.

**Placeholders** (`placeholder.ts`). Two, because they answer different
questions: the empty *document* offers the first action unconditionally, and an
empty *description* prompts only on the block holding the caret — prompting every
empty block at once turns a document into a form. Both name the affordances that
existed but were undiscoverable (`/` for a block, `@` for a code reference, ⌘K
for a feature); the tree pane's existing message says "run `codoc init`", which a
hub contributor cannot do and which mentions neither.

Placeholder text lives in a data attribute rendered by CSS `::before`, so nothing
enters the document — nothing to serialise into `tree.codoc`, nothing to settle,
nothing a select-all can copy. Pinned by a test.

## 6. Nothing further open

The §1–§5 work closes every item this audit opened. Known limits, recorded rather
than pending:

- `blame`/`glance` still cost more per keystroke than a mapped layer when their
  toggles are on. Measured, bounded, and correct — the remaining cost is building
  decorations that genuinely changed.
- Drag-and-drop is verified by unit tests over the geometry and by `tsc`/esbuild;
  the pointer choreography itself (drop-line tracking, the grab cursor) is
  DOM-behaviour that this suite cannot execute, since it runs node-env with no
  jsdom by design.

## 7. Verification

1364 pytest · 845 vitest · `tsc --noEmit` clean · esbuild clean.
New: `tests/serve/test_viewer.py` (3), `src/test/viewer-status.test.ts` (13),
`src/test/decoration-policy.test.ts` (13), `tests/model/test_rank.py` (25),
`tests/loop/test_reorder.py` (15), `src/test/reorder-commands.test.ts` (18),
`src/test/feature-drag.test.ts` (19), `src/test/authoring-cues.test.ts` (20),
`src/test/decoration-cost.perf.test.ts`
(2 — the measurement is recorded as a fact rather than asserted as a threshold,
because machines vary and a perf number pinned to this laptop is a future false
failure).
