# Settlement: three channels, one model

Written 2026-08-19. Replaces the drawing half of `captured-decorations`,
`auto-edit-decorations`, and the agent-proposal tracked-change marks with one
model. Built and shipped in `vscode-codoc/` — this document is the reasoning, not
a proposal.

## What was wrong

Six decoration families independently answered the same question about the same
character range — *whose claim is this, and how far along is it?* — and each one
answered it with its own baseline, its own diff granularity, and its own hue.

| family | baseline | granularity | encoding |
|---|---|---|---|
| captured-decorations | a local frozen baseline | word | blue underline + amber caret |
| hold-decorations | the daemon's hold set | node | sage chip |
| agent-proposals | sidecar proposals → engine marks | sentence | ins/del, per-author tint |
| ghost rows | sidecar proposals → widget | node | dimmed block beside the doc |
| auto-edit-decorations | sidecar `auto_edits` | sentence | margin rail + underline |
| inline-blame | the revisions window | span | per-author underline |

Because they were six layers and not one model they could not **compose**. A
feature that had been rewritten by the loop, then edited by its author, then
proposed against by an agent wore three marks that each claimed the whole
paragraph, drawn in whatever order the extension list happened to register.
"Which one wins" was settled by z-index and by stand-down flags
(`getLocallyEdited`) bolted onto whichever layer noticed the collision last.

Three underlines, in three hues, meaning three unrelated things. And none of them
could say the thing a reader most needs after an agent works: *this sentence was
planned, and what got built differs from the plan here.*

## The model

Every unsettled span is one **claim**: a range, a CHANNEL (who is ahead), and a
STAGE (how far along). Three channels, orthogonal — a span can carry one from
each, and each owns a *different property of the text*, which is what lets them
stack without a legend:

```
human → the INK.      blue text.  Pulsing while it is still yours to send.
plan  → the OPACITY.  faded, as unbuilt things should look.
code  → the GROUND.   green behind what the codebase added, red behind what it cut.
```

Planned wording that the build then altered therefore reads exactly as it should
with no rule written for the combination: the plan's own faded words, with a red
ground under the part that did not survive. Composition is the design.

`state/settlement.ts` is the whole of it — pure, no DOM, no TipTap.
`webview/tiptap/settlement-decorations.ts` puts it on screen and is deliberately
thin: build the live text, ask for the claims, draw them.

### Where the coordinates come from

The channels do not share a baseline — pretending they did is what forced the
stand-down flags. They share an **origin**, the projection the daemon wrote, and
each is a diff on one side of it:

```
code.prev ──diff──▶ projected ──materialize──▶ planned ──type──▶ live
           (code)                (plan)                 (human)
```

Everything is resolved into LIVE block coordinates up front by chaining
content-based paragraph pairings backwards, so no claim is ever computed in one
paragraph's coordinates and drawn in another's. Offsets are in **display space**
(`state/display-space.ts`): every inline atom is one object-replacement char, so
char *i* in a textblock at *pos* is doc position *pos + 1 + i*, citations
included.

Two rules do the interesting work:

- **`mapSpan` carries a whole span, keeping only what survived.** Mapping two
  endpoints and calling the result a span is wrong in the case that matters: an
  insertion in the middle silently joins it, and the mark covers words the channel
  never wrote.
- **The two channels get opposite drop rules, for opposite reasons.** A code claim
  is ALL OR NOTHING — it reports what the codebase says at the granularity of a
  sentence, and once the author edits inside that sentence it is not the sentence
  the report was about. A plan claim SPLITS — a proposal is text you are meant to
  edit in place before accepting, so typing inside one is ordinary use and the mark
  must survive it, tightened around the author's words rather than swallowing them.

### `humanBase`, and why it is not `projected`

The case that forces it is the ordinary one: you edit, you press ⌘S, the daemon
applies the edit and projects it straight back. `projected` now EQUALS what you
typed, so a human diff taken against it is empty — and the blue ink saying "this
is yours, the code has not caught up" vanishes at the exact moment it starts being
true. The daemon ships the pre-edit text for precisely this
(`hold_detail.baseline`); before hand-off the editor's own frozen baseline plays
the same role.

It is the base for the human *claims* only. Carrying plan and code spans forward
still walks `planned → live`, because that is the transformation the text actually
underwent and positions have to follow the text.

## A plan is written into the document

A proposed ADD used to be a widget: a dimmed block pinned near the parent, outside
the document. It was honest about being a proposal and dishonest about everything
else — it did not sit at the rank it would take, it did not participate in the
surrounding prose, and the reader could not tell how the tree would READ with it
in. Which is the only question a verdict actually asks.

Now the node goes where it will go, marked `proposed`, drawn in the plan channel's
opacity. Amends already worked this way; adds and retires join them, so "planned"
is one idea in the surface instead of two that share a verdict button.

**The safety rule that makes it possible.** The instant an agent's words are in the
document, every path that projects the document back to authored state can author
them as the human's. `commands-from-doc` sees a heading with a localId and no fid
and emits `add` — the settle after that would write the machine's proposal into the
store under the reader's name, with nothing in the ledger to say where it came
from. The `proposed` attr is the guard, and it is deliberately the same device as
`retired`: a flag on a node genuinely in the document. Three call sites honour it —
`featureUnits` (no commands), `renderTreeFromDoc` (not exported to `tree.codoc`),
and the baseline-aware `inlineRunsToText` (insertion-marked runs already excluded).
`plan-materialize.test.ts` pins all three.

A plan node may only be inserted at a heading boundary. Prose routes to its feature
by `ownerId` where one is stamped and by POSITION where one is not, so a node
dropped mid-description would capture the paragraphs below it — and since a planned
node's contents are discarded, those paragraphs would vanish from the real
feature's `set_description`. Silent prose loss, from a rendering decision.

**A third edit kind: `cut`.** A proposal is materialized as old-AND-new, because the
tracked-change engine keeps the displaced sentence so the reader can compare. So the
text a plan wants removed is on screen, and drawing it as a deletion POINT would
print the sentence a second time beside the copy already there. `cut` is a range
over text that is still present and proposed to go; `del` stays a point carrying
words that are already gone.

## The node marker accumulates

`feature-state.ts` ranks a lifecycle and picks ONE state, and for what it models
that is right: `working` / `proposed` / `sent` / `staged` are stages of one
progression, and showing four at once made the row a legend nobody had.

The settlement channels are not stages of each other. A node that was planned, then
built, and built DIFFERENTLY carries three facts a rank reduces to one — dropping
precisely the two that make the reader look. So the marker has fixed slots, at most
three glyphs, each in the ink its channel already owns in the prose:

```
●   whose it is           blue    outline = waiting, filled = the code says it now
○   whether it was planned gray    fainter = proposed, solider = accepted
±   whether it drifted     ± sign  what the build added or dropped
```

Both rings fill on the same event and it means the same thing in both: the claim
reached the code. An outline is a promise, a fill is a fact.

Crucially the marker is computed from the **same claims** the prose is drawn from.
A badge computed from its own inputs is a badge that will eventually disagree with
the text under it, and no reader can tell which is lying.

## Nobody has to decide

There is no forced verdict and there must not be one — an author who ignores a
proposal has still told you something. Every claim is DERIVED, never stored, so
"what if they never answer" needs no mechanism: the next payload recomputes from
whatever is true then.

- A proposal the daemon stops offering stops producing claims. Superseded,
  withdrawn and applied all look the same from here — none of them is still a
  question.
- A REPLACEMENT proposal is computed against the store's current text, because that
  is what the daemon proposed against. No trace of the unanswered one survives.
- Unanswered TYPING keeps its marks, because `humanBase` moves only when the feature
  adopts a projection. This is the one place the "assume they meant what is on
  screen" reading has teeth, and the one place getting it wrong loses work.

**Fulfilment is the exception**, and structurally so: it is precisely the moment the
difference DISAPPEARS. If the marker were a pure function of the claims, the only
thing the surface could ever show for a realized edit is its mark silently ceasing
to be drawn — the one outcome you were waiting for is the one it cannot report. So
`state/fulfilment.ts` watches the transition and remembers it for 30 minutes: long
enough to catch on the next read-through, short enough that the margin is not a
changelog. Nothing else expires on a timer; a condition that fades out is a surface
lying about what is still true.

## The past, in the same grammar

The timeline could already put Tuesday's words on screen. What it could not do was
say what changed in the same terms the live document uses: it had `changesAt`, a
before/after per FEATURE, and a whole-node before/after is not a mark.

`state/history-claims.ts` projects a moment's changes through the same `claimsFor`,
at the same sub-sentence granularity, producing the same `Claim` shape the same
layer renders. The channel comes from the ledger's own `actor`: a moment the author
made is theirs; everything else reached the document by way of the code. The stage
is fixed — everything in history is settled, so a human moment is `committed` and
never `open`, because a pulse is a prompt to act on something finished.

The past page had a private encoding (`ce-past-add` / `ce-past-del`, an underline
and a strike in nobody's ink). It is gone: a reader dragging the scrubber should
not have to translate.

**It still says what it cannot reconstruct.** A change whose displaced text the
ledger never recorded yields NO claims — not an empty diff, and not a diff against
the current words. A mark drawn over invented text is worse than no mark, because
the reader cannot tell which one they are looking at.

## What was deleted

- `captured-decorations.ts` — the decoration half. Its baseline bookkeeping survives
  as `state/edit-baseline.ts`, which was never about decorations.
- The auto-edit diff, and the two workarounds that went with it: a `locallyEdited`
  stand-down and an `arrivedAs` memo of each rewrite's first render. Both were
  compensating for a diff that could not tell the loop's words from the author's once
  they shared a paragraph. The model tells them apart by construction.
- The ADD ghost row and its module-level draft store. "Edit before accepting" is now
  editing the document, and the accept payload is read off the node.
- The engine's `ins`/`del` per-author tint. The marks stay — they are what keeps a
  proposal out of `tree.codoc` and what a reject deletes — but they no longer paint.
  Author identity moved to the provenance card and the marker's hover, where it can
  be read rather than decoded.

## Known gap

An **accepted ADD produces no fulfilment marker**. An add proposal is filed under its
own event id (it has no feature yet), and once it resolves that id is gone from the
document either way: accepted, it returns under a freshly minted fid; rejected, it is
simply gone. The tracker emits silence rather than a wrong ring. Telling the two apart
needs the ledger's `caused_by`, which the sidecar records but the payload does not yet
carry to the webview — the fix is to ship the `changes` slice and match `add_node`
events citing the layer. Pinned in `fulfilment.test.ts`.

## Tests

`settlement`, `node-status`, `plan-materialize`, `settlement-stages`, `fulfilment`,
`history-claims`, `edit-baseline` — plus the re-pointed `decoration-grammar`,
`display-text`, `auto-edits` and `v7-workflow-surfaces` suites. 1350 tests, `tsc
--noEmit` and the esbuild bundle clean.
