# codoc v2 — the editor

Status: design, 2026-09-17. The webview is a projection of the store plus an op
emitter (spec §1). This note says exactly how a ProseMirror document maps onto
the calculus, what the editor declares at dispatch, and what it never infers.

## 1. What changes from v1

v1 handed the host the whole settled document and the host diffed it against a
cited baseline to recover commands; six decoration families each re-derived
"who is ahead" from text diffs. In v2 the editor **records** what happened:

| v1 | v2 |
|---|---|
| settle → diff → `set_description` | steps → text ops on character identities |
| `ownerId` pins prose to a feature | membership is position; a heading inserted above prose re-parents it |
| `proposed` attr, `plan-materialize`, four guards | `plan` marks carried by the projection; a person's typing inside is untagged (I2) |
| settlement's three baselines and the two-hop diff | runs in the projection: ink, ground and opacity are read, not computed |
| `known-store`, `edit-baseline`, `doc-gate` per feature HLC | per-block `head` and the set of unacked local ops |
| `inline-blame` word-diff replay | runs |
| `revision-model` un-apply from `revisions.json` | un-apply of the projection's per-block op window, whose inverses the daemon recorded |
| `commands-from-doc` absence taxonomy | impossible gestures (heading merge, heading vanish) are refused at the keymap; the rest are explicit commands |

Lines deleted from `vscode-codoc/src/state/`: roughly 8k of 10k.

## 2. Document model

```
doc      := block*
block    := heading(bid, level, head) inline*
          | paragraph(bid, head) inline*
          | kindBlock(bid, kind, head, content)          # NodeView per kind, read-only unless the kind edits Text
inline   := text | hardBreak
marks    := bold | link{href} | cite{file, symbol} | comment{block} | suggest_del{plan} | plan{pid}
```

- **A citation is a mark, not an atom.** v1's `codeRef` atom made the label
  non-editable and a single position, which is why display-space arithmetic
  existed. A `cite` mark over ordinary text keeps the label attributed like any
  other characters, so a person can fix a typo in a label and the ledger says
  who did.
- **`plan` is a mark in the editor only.** In the store a plan tag lives on
  segments. The projection renders it as a non-inclusive mark so ProseMirror
  itself does not extend it over typed text (I2); the recorder never sends it.
- **No `author` mark.** Attribution is read from runs, never stored in the doc.
- **No `ownerId`, no `localId`, no `proposed`, no `retired`.** `bid` is the only
  identity; a heading with no `bid` yet holds a provisional one (§4).

Every block node carries `head`: the store position of the last op that touched
it, from the projection. It is the adoption gate (§6).

## 3. The shadow

For each block the editor keeps a **shadow**: the store's live characters for
that block in order, each with its `CharRef`, built from the projection's runs
(`{op, k0, len}` per live segment). The shadow is what turns a ProseMirror
position into a character identity. It advances on the editor's own ops before
they are acked, with **provisional refs** for characters it minted:

```
CharRef  := [op, k]  |  ["$cid", cid, k]
```

`["$cid", cid, k]` is the k-th character of the insert this client submitted as
`cid`. The daemon resolves it against every ack it has issued to that client
(spec §7.4), and the ack rewrites the shadow's provisional refs to real ones.

The shadow holds live text only; tombstones stay in the store. An anchor the
editor needs is always a live character or START, because the editor only ever
inserts after something it can see.

## 4. Recording: steps to ops

A ProseMirror transaction is a list of steps. The `Recorder` plugin turns each
user-origin transaction into op bodies at dispatch, per step, using the shadow
to name characters:

| step shape | ops |
|---|---|
| `ReplaceStep` inside one textblock, slice is inline content | `text.delete(from..to)` on the shadow refs the range covered, then `text.insert(after = ref before `from`, text)` for the inserted text; marks in the slice become `text.mark` on the minted range |
| `AddMarkStep` / `RemoveMarkStep` | `text.mark(on/off)` over the refs |
| the split command (Enter) | `block.split(at = ref before the caret, new = fresh bid)` |
| the merge command (Backspace at start, Delete at end, paragraphs only) | `block.merge(into, bid)` |
| `## ` input rule, set kind command | `block.set(kind)` (+ `block.set(level)`) |
| Tab / Shift-Tab, drag | `block.move(after, level_delta)` |
| delete feature command | `block.delete` × own body, `block.set(level)` × promoted children, `bind.detach` × bindings |
| paste of several paragraphs | `block.add` per paragraph after the caret's block, then `text.insert` per block |

Two rules make this exact rather than heuristic:

1. **Structural steps are never inferred from a `ReplaceStep`.** Enter,
   Backspace-at-start, `## `, Tab, drag and delete are keymap or command
   handlers that emit the op and then perform the equivalent ProseMirror
   change themselves with `origin=recorded` so the Recorder skips the
   transaction. A `ReplaceStep` whose slice or range crosses a block boundary
   and did not come from one of these (a paste, a drop, an unknown command) is
   handled by the one **fallback**: the recorder replaces it with a
   `block.add`/`text.insert` sequence for inserted blocks and a per-block
   character comparison for blocks whose inline content changed, and marks the
   batch `via: "fallback"`. The fallback is confined, named, and never decides
   who wrote anything: the actor is the person either way.
2. **A heading never merges into prose and prose never merges into a heading.**
   The keymap refuses both (v1's `block-boundary.ts`, kept). A heading
   disappears only through the delete command. The five-way "why is this
   heading absent" question therefore never arises.

IME composition emits nothing between `compositionstart` and `compositionend`
(F3 in the design review); the composed text lands as one insert.

Batches: one debounced settle of typing is one batch; each command gesture is
one batch. Batches are the undo unit. Undo emits the recorded inverses as new
ops (the projection carries the inverse of each op in the window), and the
editor's own ProseMirror history is disabled: two histories of one document
is how a deleted heading resurrects.

## 5. Submitting and acking

Ops are appended to `.codoc/inbox/ide.jsonl` as submissions `{cid, kind,
target, payload, batch, plan?}` by the extension host (the webview cannot
write files; it posts to the host). The host watches `inbox/ide.ack.jsonl`
and forwards acks to the webview, which resolves provisional refs in the
shadow and removes the ops from the block's unacked set. A conflicting ack
(`conflict` set) is shown on the block and its op is dropped from the shadow
by re-adopting that block's projection.

On the hub the browser posts submissions to `/api/submit`, which appends to
`inbox/hub-<login>.jsonl` under the user's identity; acks arrive over SSE.

## 6. Adopting a projection

The daemon publishes `projection.json` after every batch. The host posts it to
the webview; the webview adopts **per block**:

- a block whose `head` is unchanged is left alone;
- a block with unacked local ops is left alone (the person is typing in it);
- otherwise its node is replaced by the projection's, and the caret, if it was
  inside, is mapped through the replacement by character identity (the shadow
  before and after share refs);
- blocks added, removed or re-ranked by the projection are inserted, removed
  or moved in the document.

There is no baseline citation and no whole-document diff: the projection is
the truth for every block the person is not holding.

## 7. Drawing

Everything drawn is a decoration computed from the projection's per-block
data plus the shadow; nothing is stored in the doc.

| surface | source |
|---|---|
| ink (blue, pulsing unsent / steady sent) | runs with a human actor whose op is outside the realized frontier; unacked local ops are unsent by definition |
| ground (green added, red removed) | loop runs since the viewer's cursor; removed text drawn as a widget at the tombstone's live offset (`claims` in the projection) |
| opacity (gray) | `plan` marks; `suggest_del` marks as strike |
| margin marker, tree rows, rail | `phases[fid]` |
| comment cards | comment blocks whose anchor mark resolves |
| History stance | `attribution` runs per block; the timeline un-applies the block's op window locally |
| Catch up | a viewer-local read cursor (`localStorage`) over loop batches newer than it |

One palette on every surface, as in v1: blue is the person, gray a plan, green
and red the code. `aria-description` per run for screen readers.

## 8. Toolbar and gestures kept

Hand off (⌘S), Accept all / Reject all, History, the language switcher, Find
and Replace, the palette, drag handles, the walkthrough overlay, the
placeholder, `@` citation picker, `/` block menu, comments with Build it. Cut
per `06-cut-list.md`: glance, reveal, ribbon, presence avatar, the Suggesting
stance, Keep/Restore on auto-edits (Catch up covers it), retire.

## 9. Module plan

```
src/calc/           pure, vitest-covered, no DOM
  projection.ts     the projection.json types and a parser
  shadow.ts         per-block live text with CharRefs, provisional refs, ack resolution
  recorder.ts       ProseMirror steps → op bodies (given a shadow and a position→block map)
  adopt.ts          projection → per-block replacement plan given unacked sets
  claims.ts         runs + marks + phases → decoration ranges and palette classes
  window.ts         per-block op window → un-apply for the timeline
src/editor/         ProseMirror/TipTap
  schema.ts         heading, paragraph, kind blocks, marks
  recorder-plugin.ts, commands.ts (split/merge/set/move/delete), keymap.ts
  decorations.ts    ink/ground/opacity/markers from claims
  adopt-plugin.ts   applies adopt plans, maps the caret
  toolbar.ts, comments.ts, find (kept), palette (kept), timeline (rewritten on window.ts)
src/host/           extension host
  provider.ts       reads projection.json, watches inbox acks, posts both; writes inbox/ide.jsonl
  extension.ts      commands per the cut list; daemon supervision (kept)
```

## 10. Trade-offs

- **Recording vs diffing.** Recording is exact and cheap and it is what makes
  "who wrote this" trustworthy; it costs a keymap that owns every structural
  gesture and one confined fallback. Diffing at settle would have kept the
  fallback as the whole design.
- **Cite as a mark vs an atom.** A mark loses the chip look unless styled, and
  a person can break a label into two by typing in the middle. A mark gains
  attributed, editable labels and removes a coordinate system. Style it as a
  chip; refuse nothing.
- **Disabling ProseMirror history.** Undo through inverses is exact and shared
  with the timeline; ProseMirror's history is positional and would diverge
  from the store the moment a projection adopts a block. Cost: undo granularity
  is the batch, which Upwelling's study argued is the granularity people want.
- **Per-block adoption vs a whole-document gate.** A whole-document gate held
  back the entire projection while any feature had an unsent edit, which is
  why v1 needed the per-feature HLC gate on top. Per-block is the natural
  unit now that blocks have identity, and it is what lets an agent's plan
  appear in one paragraph while the person types in the next.
