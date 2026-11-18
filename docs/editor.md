# The editor

The editor is a ProseMirror page that shows the daemon's projection and records each
gesture as ops. It holds no model of the document: the page is drawn from
`projection.json`, and every change leaves as a submission to the daemon's inbox. This
file describes `vscode-codoc/src` as it is. The projection and the ops are defined in
[model.md](model.md); the inbox, acks and the holding rule in
[concurrency.md](concurrency.md#13-the-editors-side).

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
doc      := (heading | paragraph | bullet | numbered | source | kindBlock)+
heading  := text*              attrs: bid, head, level
paragraph:= text*              attrs: bid, head
bullet   := text*              attrs: bid, head, level        a list item; level is the indent depth
numbered := text*              attrs: bid, head, level        its number is drawn, not stored
source   := text*              attrs: bid, head, kind (table | diagram | code), lang, derived
                               raw text, newlines allowed, no marks
kindBlock:= atom               attrs: bid, head, kind, fields; a card (card.ts)
marks    := bold | code | link{href} | cite{file, symbol} | comment{block} | suggest_del{plan} | plan{pid}
```

- `bid` is the block id. `head` is the store position of the last op that touched the
  block, from the projection. Adoption compares it.
- A heading, a paragraph and a list item are one line. There is no hard break: Enter and
  Shift-Enter both start a new block, and the applier refuses a newline there.
- A numbered item's number is computed when drawn: consecutive numbered items at the same
  level count 1, 2, 3; a deeper item between them does not restart the count.
- A `source` node edits the store's `source` field. Enter inside it types a newline.
- Every other projected kind except `comment` is a `kindBlock`: the kind's name, a text
  area per Text field its layout names, and every other field read-only.
- A comment block is not in the document. It is a card in the margin (section 7).
- The person sets `bold`, `code`, `link` and `cite`, and the recorder sends them. The
  projection sets `comment`, `suggest_del` and `plan`; the recorder never sends them. Every
  mark except `bold` is non-inclusive, so typing at its end is outside it.
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
| Enter, Shift-Enter in a heading, paragraph or item | `block.split` at the caret. A heading's tail becomes a paragraph; an item's tail is an item at the same level. |
| Enter in an empty list item | `block.set(kind = paragraph)`. |
| Enter in a source block | `text.insert` of a newline on `source`. |
| Mod-Enter | `block.add` of a paragraph after the block. The way out of a code block at the end of the page. |
| `#`..`######` and a space at a block's start | `text.delete` of the hashes, `block.set(kind = heading, level)`. |
| `- `, `* `, `1. ` at a paragraph's start | `text.delete` of the marker, `block.set(kind = bullet or numbered, level = 1)`. |
| ```` ``` ````, ```` ```lang ```` or ```` ```mermaid ```` as a paragraph's whole text, or one row `\| a \| b \|` as its whole text, then Enter | `block.delete` of the paragraph, `block.add` of a code block (with `lang`), a diagram or a table (with that row). A row becomes a table only when the viewer wrote every character of it: unacked, applied for this page, or in a projected run with the viewer's actor (`status.actor`). Another actor's row, and prose such as `\|x\| is the absolute value`, split as usual. |
| `/` menu | Heading 1-3, Paragraph, Bullet list, Numbered list, Table, Code block, Diagram: `text.delete` of the typed `/query` and `block.set(kind)` or a new block. In a heading, also Delete feature. |
| Backspace at a list item's start | Above level 1, `block.set(level - 1)`; at level 1, `block.set(kind = paragraph)`. |
| Backspace at a heading's start | `block.set(kind = paragraph)` when the heading owns no bound code, has no child heading, has no hand-off its realized frontier does not cover, is not the target of an open plan or named in one's `handoff`, and whose phase is `edited`, `synced` or not yet projected, so a stray `# ` is undone in one key; its body joins the feature above. Any other heading (one with code, a theme with children, a feature queued or proposed) is left alone, with the notice "Delete feature from the / menu". |
| Backspace at a paragraph's start, Delete at its end, next to a paragraph or item | `block.merge`. When the other block is empty, `block.delete` of it instead, so the block with words keeps its id and kind. |
| Backspace or Delete in an empty paragraph next to a heading, source or plugin block | `block.delete` of the paragraph. |
| Typing, Backspace, Delete, Enter or cut over a selection across blocks | `text.delete` at each end, one `block.delete` naming every block wholly inside (so the applier reads each block's feature before any dies), `block.merge` of the two ends when both are heading or prose. Refused with a notice when it crosses a heading that is not wholly selected, or would remove every block or every feature. |
| What is deleted at the ends of a selection | Backspace, Delete or cut: an end block wholly selected is deleted too, when a block outside the selection remains. Typing, paste, Enter or an IME composition (text follows): a prose end is emptied and kept, a wholly selected source end is deleted, and the text lands in the first kept prose end (the last when the first end is a heading); with none kept, in a new paragraph where the selection was. A heading is never where text lands. |
| Tab, Shift-Tab on a list item | `block.set(level ± 1)`. |
| Tab, Shift-Tab on a heading | `block.move` of the heading after the block before it, `level_delta ± 1`: the subtree moves with it. |
| Tab in a paragraph | Nothing; focus stays in the editor. |
| Mod-B, Mod-E | `text.mark` bold or code, on or off, one per block the selection covers. |
| Mod-K | An inline field for the URL. Enter sends `text.mark` link; an empty field turns the link off. An address with no scheme gets `https://`. |
| Paste (`paste.ts`) | The selection is deleted first. Plain text with structure (a block that is not a paragraph, or an inline mark) is read as Markdown (`calc/markdown.ts`): the first heading or prose piece joins the caret's block, every further piece is `block.add`, `text.insert`, `text.mark`. Other text splits into one block per line. Ids in pasted Markdown are ignored; a pasted `codoc:<kind>` fence becomes a code block. |
| Composition (an IME) | Nothing while composing; one `text.insert` of the composed text when it ends. |
| Drop a drag handle (`drag.ts`) | `block.move`, `level_delta` 0. A heading moves with its range. |
| `deleteFeature(fid)`, Delete feature in the `/` menu | `block.delete` of the heading, `block.set(level)` per descendant heading, one level up. Refused when it would empty the document. |

Two plugins in `guards.ts` keep the page and the shadows in step:

- `structureGuard` refuses a transaction that adds, drops, duplicates or un-ids a
  top-level block unless it is tagged `RECORDED`. The page applies a transaction first and
  records what it took, so a refused gesture leaves no ops.
- `nfcGuard` composes text that a transaction inserted in decomposed form.

**Batches.** Ops wait 350 ms after the last one and leave together as one batch. A verdict,
a hand-off, an undo and an adoption flush the queue first. The batch is the undo unit.

**Undo.** ProseMirror's history is not installed. ⌘Z asks the host for `history.json` and
submits the recorded inverses of the newest batch this page sent that is not undone, newest
op first, as batch `u-<batch>`; ⇧⌘Z and ⌘Y undo that batch (`u-u-<batch>`)
(`calc/window.ts`, `nextUndo`). It waits while any submission is unacked. A batch is this
page's when the page submitted it, not when its actor is the viewer's: a teammate's batch
merged through git and the person's own `codoc accept` in a terminal are never offered.
The page keeps its batch ids in the webview's state (`setState`; on the hub, the tab's
`sessionStorage`), so a reload still undoes them; an editor opened fresh has nothing to undo.

**The viewer.** `status.actor` is the person the page writes as: `human:<git config
user.email>` on this machine (`human:local` without one), or `human:gh:<login>` on the hub
(docs/concurrency.md, section 5). Only the viewer's own words are inked.

## 5. Submitting and acks

A submission is `{cid, batch, kind, target, payload}`. The `cid` of a `text.insert` is the
one its provisional refs were minted under. The page holds every block an op names (its
`bid`, `into`, `fid` and `new`) and every block a gesture added or removed, until the ack
for that `cid` arrives.

An ack `{cid, op, conflict}` releases the hold. An applied op resolves the shadow's
provisional refs and becomes the block's last applied op. A conflicted op is dropped and
the conflict is reported at once. The block keeps its other unacked words, and the page
re-adopts it from the projection in hand once none of its ops is in flight.

## 6. Adopting a projection

`adopt` in `adoption.ts`, with the plan from `calc/adopt.ts`:

1. A projection from another `store` (the daemon rebuilt it: a commit, a checkout, a
   fold) forgets every last-applied op and re-reads every block that has no op in flight.
   Queued and unacked ops stay, and the new store answers them. Within one `store`, a
   projection whose `head` is lower than the one shown is dropped.
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
| `ink-unsent`, `ink-sent` | Runs by the viewer with no plan, outside `realized`; sent when inside `handoff`. Another person's words are not inked. | Blue. |
| `plan-open` | Runs of an open plan. An accepted plan's words are ordinary text; History shows who proposed them. | Gray. |
| `plan-cut` | `suggest_del` spans. | Struck gray. |
| `plan-mark`, `plan-unmark` | `marked`, `unmarked` claims. | A dashed gray underline. |
| `code-added`, `code-removed` | `ground` claims, where no ink overlaps. Removed words are a widget at their place. | Green, red. |
| `author-*` | In History mode, merged runs per actor kind. | One colour per kind. |
| Phase marker | `phases`, at each heading that is not `synced`. | A glyph with an `aria-label`. |

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
| Accept all (n), Reject all (n) | `plan.accept` or `plan.reject` per open plan, in one batch. Accept also sends `feature.handoff` for each feature in the plans' `handoff`. |
| Hand off (n), ⌘S | `feature.handoff` per `edited` feature, with no `covers`. |
| Build now | Asks the host to run `codoc realize`. Shown when a feature is `queued` or `planned`. |
| ⌘F, ⌘⌥F or ⌘H | Find; Find and Replace (`find.ts`). Matches stay inside a block; a replacement is ordinary typing. |
| `/` | The block menu (section 4). |
| Mod-B, Mod-E, Mod-K | Bold, code, link (section 4). |
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
  markdown.ts               block and inline Markdown -> specs and spans; the same cases as
                            codoc/calculus/block_md.py and inline_md.py
src/web/editor/             ProseMirror (TipTap schema)
  schema.ts, keymap.ts, commands.ts, paste.ts, guards.ts, outbox.ts, adoption.ts,
  decorations.ts, drag.ts, find.ts, menus.ts, comments.ts, timeline.ts,
  editor.ts (mountEditor), app.ts (the page)
```

## Not built

- Editing plugin kind blocks (image, url, pdf, screenshot) in place. An agent writes them
  through `codoc_write_block`.
- A context menu; Delete feature is in the `/` menu only.
- Pasting HTML lists beyond a basic `li` rule.
- A viewer-local read cursor that clears `ground` once seen.
- The `/codoc:ask` walkthrough inside the editor.
