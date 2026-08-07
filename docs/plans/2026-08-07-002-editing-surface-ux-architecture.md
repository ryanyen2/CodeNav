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

## 4. Still open, in priority order

1. **Sibling reorder reverts silently** (T2.7 from the engine audit) — the move
   payload carries a parent but no ordinal, so a reorder within one parent is a
   no-op the UI still animates. Reordering must land in the data model before it
   is offered as a gesture.
2. **No drag-to-reorder.** Notion's signature gesture; nothing in the webview
   listens for drag at all. Deliberately ordered AFTER (1): offering a gesture
   whose data model silently discards the result is worse than not offering it.
3. **No editor placeholder / empty state.** A fresh tree opens as a blank page
   with no first action.
4. **Markdown affordance gap.** `> ` (steering) and `**bold**` (focus) are
   load-bearing signals per CLAUDE.md, but only `#` has an input rule, so two of
   the three give no feedback as you type them.

---

## 5. Verification

1324 pytest · 788 vitest · `tsc --noEmit` clean · esbuild clean.
New: `tests/serve/test_viewer.py` (3), `src/test/viewer-status.test.ts` (13),
`src/test/decoration-policy.test.ts` (13), `src/test/decoration-cost.perf.test.ts`
(2 — the measurement is recorded as a fact rather than asserted as a threshold,
because machines vary and a perf number pinned to this laptop is a future false
failure).
