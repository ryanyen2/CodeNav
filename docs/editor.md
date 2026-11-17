# The editor

The editor is a ProseMirror page that shows the daemon's projection and records each
gesture as ops. It holds no model of the document: the page is drawn from
`projection.json`, and every change leaves as a submission to the daemon's inbox. This
file describes `vscode-codoc/src` as it is. The projection and the ops are defined in
[model.md](model.md); the inbox, acks and the holding rule in
[concurrency.md](concurrency.md#13-the-editors-side).

The editor edits headings and paragraphs. Editing of list items, tables and code blocks is
being added.

## 1. Processes

| part | where | does |
|---|---|---|
| The page | `src/web/editor/app.ts`, one bundle | Tree pane, toolbar, document, comment margin, status line. Driven by the host's messages. |
| The VS Code host | `src/host/provider.ts`, a custom editor on `.codoc/tree.codoc` | Posts `projection.json` and `status.json` when they change. Appends the page's submissions to `inbox/ide.jsonl` and posts back the acks from `inbox/ide.ack.jsonl`. Answers a `history` request with `history.json`. Writes `doc_language` to `config.json`. Opens the code a citation names. Runs `codoc realize` in a terminal. |
| The hub host | `src/web/hub.ts` in the browser, `codoc serve` on the server | The same messages over `POST /api/message` and the SSE stream at `/api/events`. Submissions go to `inbox/hub-<login>.jsonl`. |
| The extension | `src/extension.ts`, `src/host/` | Registers the editor and the commands, keeps a daemon running for the repository, shows its status, runs setup. |

The messages between page and host are in `src/wire.ts`. `tree.codoc` is only the file VS
Code opens; the page never reads it.

## 2. Document model

```
doc      := (heading | paragraph | kindBlock)+
heading  := inline*            attrs: bid, head, level
paragraph:= inline*            attrs: bid, head
kindBlock:= atom               attrs: bid, head, kind, fields; drawn read-only
inline   := text | hardBreak
marks    := bold | link{href} | cite{file, symbol} | comment{block} | suggest_del{plan} | plan{pid}
```

- `bid` is the block id. `head` is the store position of the last op that touched the
  block, from the projection. Adoption compares it.
- Every projected block that is not a heading, a paragraph or a comment is a `kindBlock`:
  the kind's name, then one row per field with a value. This includes `bullet`,
  `numbered`, `table`, `code` and `diagram`.
- A comment block is not in the document. It is a card in the margin (section 7).
- The person sets `bold`, `link` and `cite`, and the recorder sends them. The projection
  sets `comment`, `suggest_del` and `plan`; the recorder never sends them. `plan` and
  `suggest_del` are not inclusive, so typing at their edge is the person's own text.
- The store's `code` mark has no editor mark yet and is not drawn.
- A citation is a mark over ordinary text, so its label is editable and attributed.
- Attribution and plan membership are not in the document. They are read from the
  projection's runs and drawn as decorations.

## 3. The shadow

For each heading and paragraph the page keeps a shadow (`src/web/calc/shadow.ts`): the
block's live characters in order, each with its `CharRef`, built from the projection's
unmerged runs. It turns a ProseMirror position into a character identity.

- Offsets in the shadow are Unicode scalars. ProseMirror counts UTF-16 units;
  `scalarOffset` and `unitOffset` convert.
- The shadow advances with the page's own ops before they are acked. A character the page
  inserted is named `["$cid", cid, k]` until its ack arrives; `resolve(cid, op)` then
  rewrites it to `[op, k]`.
- The shadow holds live characters only. The page anchors only on characters it can see,
  or `START`.

## 4. Recording

`src/web/editor/outbox.ts` turns every transaction the page applied into ops.

| source | ops |
|---|---|
| A transaction tagged `RECORDED` by a gesture (`commands.ts`) | The gesture's own ops. |
| A `ReplaceStep` inside one textblock (`calc/recorder.ts`) | `text.delete` of the identities under its range, then `text.insert` after the identity before it; each mark in the inserted slice becomes `text.mark` over the minted range. |
| `AddMarkStep`, `RemoveMarkStep` | `text.mark` on or off over the identities. |
| A step the recorder cannot name | The fallback, `reconcile`: the touched block is compared with its shadow and the difference sent as `text.delete` and `text.insert` by the person. |

The gestures (`commands.ts`, bound in `keymap.ts`):

| key or command | ops |
|---|---|
| Enter | `block.split` at the caret. A heading's tail becomes a paragraph. The half with live text keeps the id. |
| Enter over a selection inside one block | The delete as recorded text, then the split: two dispatches. |
| `#`..`######` and a space at a block's start | `text.delete` of the hashes, `block.set(kind = heading, level)`. |
| `/` menu: Heading 1..3, Paragraph | `text.delete` of the typed `/query`, `block.set(kind)`. |
| Backspace at a paragraph's start, Delete at its end, next to a paragraph | `block.merge`. |
| Backspace or Delete at a heading boundary | None. The caret moves across. |
| Backspace, Delete or Enter over a selection across blocks | `text.delete` per block, `block.delete` per block wholly inside, `block.merge` of the two ends. Refused when it touches a heading or a block with no shadow. |
| Tab, Shift-Tab on a heading | `block.set(level)`. |
| Drop a drag handle (`drag.ts`) | `block.move`, `level_delta` 0. A heading moves with its range. |
| Paste of several blocks (`pasteBlocks`) | The first block's text joins the caret's block; `block.add` and `text.insert` per further block. Text is normalised to NFC. |
| `deleteFeature(fid)` on the editor handle | `block.delete` of the heading, `block.set(level)` per descendant heading, one level up. Refused when it would empty the document. No control on the page calls it. |

Two plugins in `guards.ts` keep the page and the shadows in step:

- `structureGuard` refuses a transaction that adds, drops, duplicates or un-ids a
  top-level block unless it is tagged `RECORDED`. The page applies a transaction first and
  records what it took, so a refused gesture leaves no ops.
- `nfcGuard` composes text that a transaction inserted in decomposed form.

No key toggles `bold` or `link`. Marks reach the page in pasted text and through the `@`
picker.

**Batches.** Ops wait 350 ms after the last one and leave together as one batch. A verdict,
a hand-off, an undo and an adoption flush the queue first. The batch is the undo unit.

**Undo.** ProseMirror's history is not installed. ⌘Z asks the host for `history.json` and
submits the recorded inverses of the viewer's newest batch that is not undone, newest op
first, as batch `u-<batch>`; ⇧⌘Z and ⌘Y undo that batch (`u-u-<batch>`)
(`calc/window.ts`, `nextUndo`). It waits while any submission is unacked.

## 5. Submitting and acks

A submission is `{cid, batch, kind, target, payload}`. The `cid` of a `text.insert` is the
one its provisional refs were minted under. The page holds every block an op names (its
`bid`, `into`, `fid` and `new`) and every block a gesture added or removed, until the ack
for that `cid` arrives.

An ack `{cid, op, conflict}` releases the hold. An applied op resolves the shadow's
provisional refs and becomes the block's last applied op. A conflicted op drops the block's
local state: the page re-adopts the block from the projection in hand and reports the
conflict.

## 6. Adopting a projection

`adopt` in `adoption.ts`, with the plan from `calc/adopt.ts`:

1. A projection whose `head` is lower than the one shown is dropped.
2. Queued ops are flushed.
3. A block is held while one of its ops is unacked, or while its last applied op is
   outside the projection's `frontier` (`Outbox.held`). Held blocks keep their node, their
   place and their shadow.
4. Every other block whose `head` changed is replaced by the projection's; blocks the
   projection added, dropped or re-ordered are inserted, removed or moved.
5. When every step is a replacement, the changed blocks are replaced in place. Otherwise
   the document is rebuilt in the projection's order, keeping held nodes.
6. The caret goes back after the character it sat after, found by identity.

After every ack the projection in hand is adopted again, so a block released by the ack is
read when the projection already contains its op.

## 7. Drawing

Everything drawn is a decoration from the projection (`calc/claims.ts`,
`editor/decorations.ts`). `Paints` keeps the paints per block and redraws only the blocks
whose paints can have changed.

| paint | from | drawn as |
|---|---|---|
| `ink-unsent`, `ink-sent` | Runs by a `human:` actor with no plan, outside `realized`; sent when inside `handoff`. | Blue. |
| `plan-open`, `plan-accepted` | Runs with a plan. | Gray. |
| `plan-cut` | `suggest_del` spans. | Struck gray. |
| `plan-mark`, `plan-unmark` | `marked`, `unmarked` claims. | A dashed gray underline. |
| `code-added`, `code-removed` | `ground` claims, where no ink overlaps. Removed words are a widget at their place. | Green, red. |
| `author-*` | In History mode, merged runs per actor kind. | One colour per kind. |
| Phase marker | `phases`, at each heading that is not `synced`; `realizing` from `status.json`. | A glyph with an `aria-label`. |

Each painted run carries its meaning as `title` and `aria-description`. One palette on
every surface: blue is the person, gray a plan, green and red the code.

**Comments** (`comments.ts`). Threads are grouped by `thread` and drawn as cards in the
margin beside their anchor block. New thread: select words, press Comment, write.
A card offers Reply; Resolve, to a viewer who decides or wrote the first message; and Build
it, which hands off the anchor's feature with the thread's words as `asked` and asks the
host to run `codoc realize`.

**History** (`timeline.ts`). The History toggle turns on author paints and opens a scrubber
over the window in `history.json`. A position shows every block as it read after that op
(`textAt`), in the order and with the blocks it had then (`shapeAt`), with that op's
change marked by a word diff. The scrubber holds an op id: at now it stays at now as the
window grows; on a past op it finds that op again, and returns to now when the op has left
the window. It never writes to the live editor.

## 8. Toolbar and keys

| control | does |
|---|---|
| Tree pane | One row per heading, indented by level, with a phase glyph. A click scrolls to the heading. |
| History | Toggles section 7's History. |
| Language | Sets `doc_language`. |
| Comment | Starts a thread on the selected words. |
| Accept all (n), Reject all (n) | `plan.accept` or `plan.reject` per open plan, in one batch. No hand-off. |
| Hand off (n), ⌘S | `feature.handoff` per `edited` feature, with no `covers`. |
| Build now | Asks the host to run `codoc realize`. Shown when a feature is `queued` or `planned`. |
| ⌘F, ⌘⌥F or ⌘H | Find; Find and Replace (`find.ts`). Matches stay inside a block; a replacement is ordinary typing. |
| `/` | The block menu (section 4). |
| `@` | The citation picker: up to 8 bound symbols from the projection's bindings, filtered by the typed query. A pick replaces `@query` with the symbol's name under a `cite` mark. |
| Click on a citation | Opens the code. |

A viewer whose role is `suggest` (from `status.json`) is not offered Accept, Reject, Hand
off or Build now. Their typing becomes a plan in the drain.

## 9. Modules

```
src/wire.ts                 page <-> host messages
src/extension.ts            activation, commands, daemon reconcile
src/host/                   provider.ts (relay), workspace.ts, daemon-manager.ts, pidfile.ts,
                            serve-manager.ts, setup/ (provisioning the Python core)
src/web/hub.ts              the browser's host connection on the hub
src/web/calc/               pure, no DOM
  projection.ts             projection.json types, op bodies
  ids.ts                    block, batch and client ids
  shadow.ts                 section 3
  recorder.ts               steps -> op bodies; reconcile
  adopt.ts                  projection block -> node; the adoption plan
  claims.ts                 paints from runs, plans, claims, phases
  window.ts                 history.json; nextUndo, textAt, shapeAt, wordDiff
src/web/editor/             ProseMirror (TipTap schema)
  schema.ts, keymap.ts, commands.ts, guards.ts, outbox.ts, adoption.ts,
  decorations.ts, drag.ts, find.ts, menus.ts, comments.ts, timeline.ts,
  editor.ts (mountEditor), app.ts (the page)
```

## Not built

- Editing list items, tables, code blocks and other kind blocks (being added).
- A key or button for bold and links, and drawing the `code` mark.
- A control for deleting a feature.
- A viewer-local read cursor that clears `ground` once seen.
- The `/codoc:ask` walkthrough inside the editor.
