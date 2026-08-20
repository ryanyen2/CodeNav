# The plan channel, and one palette for every surface

Written 2026-08-20. Built. Continues
`docs/plans/2026-08-19-003-settlement-three-channels.md`, which introduced the three
channels; this fixes four things that were wrong with them and closes the hole under
the plan-first loop that made the whole grammar unobservable after a verdict.

## Four reports, one root

The four symptoms were reported together, and three of them turn out to be the same
missing fact:

1. A plan that AMENDS an existing description was drawn as the reader's own blue ink
   instead of the plan's gray.
2. An agent blocked on `codoc_await_verdicts` did not move after the user accepted.
3. Leaving a comment lit up green "edit" decorations over prose nobody had touched.
4. There was no way to see "this was planned, and the build came out different",
   which is the composition the three-channel grammar exists to produce.

(2) and (4) are one bug: **an accepted plan had no representation at all.** (1) has
two independent causes, both in the coordinate plumbing. (3) is a decoration family
that survived the rewrite it was supposed to be replaced by.

---

## 1. A plan amend read as the author's typing

### Cause A — the paragraph split ran in the wrong coordinate space

`buildStages` projected a description into **display space** (where every newline
collapses to one object-replacement char, so a citation and a line break are each one
position) and then handed the whole string to `planRuns`, which split it on `\n\n`.
After the projection there are no newlines left to split on. A two-paragraph amend
therefore produced ONE planned block against the editor's two.

`alignParas` then paired live paragraph 0 with the single planned block and left every
later paragraph unpaired — so paragraph 2 diffed against `''` and came back as text the
author had just typed. In blue, with a ⌘S prompt, on prose nobody had touched. The plan's
own claims for that paragraph did not exist at all.

**Fix.** Split first, project each piece second. `plan-materialize.descParas` is now the
single place that answers "where do this description's blocks divide", it takes RAW text
by construction, and `planRuns` takes paragraph ARRAYS so the mistake cannot be made
again through its signature. `settlement-stages.textOf` already did it in the right
order, which is what made the discrepancy findable.

### Cause B — a hold baseline outranked the plan

`blockStages` took `humanBase ?? planned` as the base for the human diff. `humanBase` is
the queued directive's pre-edit wording, and it knows nothing about a proposal
materialized after it — so on a feature that was BOTH holding an edit of the author's and
carrying an agent's amend, the human diff ran straight past the plan and swallowed every
word the agent had put on the page.

**Fix — two hops, never one.** The chain the text actually walked has a stage in the
middle, and the human channel now follows it:

```
humanBase ──(a) applied edit──▶ projected ──plan──▶ planned ──(b) typing──▶ live
```

(a) is what the author already handed over — the daemon applied it, so it is in
`projected` — carried forward into live coordinates. (b) is what is on screen and not in
the store yet. Neither can pick up the plan's words, because the plan lives strictly
between them. With no hold baseline `humanBase === planned`, (a) is empty, and the
ordinary case is byte-for-byte what it was.

---

## 2. Accepting a plan queued nothing

`/codoc:plan` tells the agent to **default to `codoc_propose_amend`** — "most tasks
change what an existing feature does rather than introducing a new unit of intent" — and
`loop_b` classified every accepted AMEND as the tree catching up to code that already
changed. So accepting a plan made of amendments:

- minted no realize directive,
- wrote no `realize.md`, never reached `awaiting_impl`,
- never armed the extension's stalled-queue offer,
- and left `codoc_plan_status` answering `all_realized: true` over work nobody had
  started.

An agent that ended its turn at the await had nothing to resume it. The IDE's own button
already said **"Accept & build"** for a plan-tagged amend (`grammar.consequenceOf`), so
the two halves of the surface disagreed about what the click would do.

**Fix.** `realized=False` already meant "describes code that does not exist yet" on an
ADD. It means the same on an AMEND, and now says so:

- `codoc_propose_amend(..., builds=True)` and `codoc propose amend` (the plan-authoring
  CLI, which stamps `source=plan`, so `builds` defaults to True there and `--reflects`
  is the opt-out).
- `render.proposals_payload` reports `writes_code: "build"` for it, so the button and
  the loop are computed from one flag.
- `loop_b._is_plan_op` covers both kinds; an accepted plan node mints a directive.

A plain reflection amend is untouched, and must be: asking the agent to build one would
mean rewriting code to match a description derived from that very code.

**The second deadlock.** On a `--dry` / `--no-realize` pass the daemon defers a
code-implying accept, leaving the verdict in the inbox AND the proposal in the store —
which is exactly the state `await_verdicts` reads as "the user has not clicked yet". It
spun to the 24-hour timeout. It now reports those as `deferred`, with a note naming the
flag, after one confirming poll.

---

## 3. A comment fired the old green decorations

`webview/tiptap/hold-decorations.ts` survived the settlement rewrite. Beside its chip it
drew a margin RAIL down every held paragraph and an UNDERLINE over the text the author
had changed — computed by its own `changedRange` word diff against
`hold_detail.baseline`, in the sage "staged" hue.

That underline is the settlement model's human channel, drawn a second time, from a
different baseline, in the code channel's colour:

- The author's pending edit wore two marks that agreed only by luck; they used different
  diffs (a contiguous word-snapped region vs per-sentence spans), so on any real edit
  they disagreed about *where* the change was.
- Green now means "the codebase added this". Painting the author's unbuilt words in it
  said the codebase had already seen them.
- **With no baseline the underline fell back to marking the author's `**bold**` runs.**
  A comment becomes a steer directive, and a steer carries no baseline — so leaving a
  note lit up emphasis across the feature as though it were a pending change. That is
  the reported symptom, and there was nothing to explain about it: it was marking text
  nobody had touched.

**Fix.** The diff half is deleted; the ACTION half — the chip, its gloss, the withdraw ✕
— stays, which is the same split `auto-edit-decorations` made when its own diff moved
out. The chip is now inked by *whose* words are waiting.

---

## 4. An accepted plan is a state, and it had nowhere to live

Every plan claim came from the pending-proposal list, and accepting **deletes that row**.
So the moment the reader agreed, the plan's wording became ordinary prose:

- `Stage = 'accepted'` was unreachable. `stagedProposals` filled it from an `accepted`
  flag on the suggestion that nothing ever set.
- The fulfilment tracker fired on the proposal disappearing, so the ring meaning "this
  was planned, and it has been built" filled **at the moment of the click**, before
  anything was built. And it fired on REJECT too, for every proposal whose node stays in
  the tree (all amends, all retires) — the surface telling the reader their declined
  proposal had shipped.
- The composition the grammar exists for — the plan's gray with the build's green and red
  under the parts that came out different — could never be drawn, because by the time the
  build landed there was no plan left to draw.

**Fix — the queue is the source.** A `Directive` now records `origin` (`human` | `plan`),
set when the directive is minted by accepting an agent's proposal, and surfaced through
`hold_detail`. That one field carries three things:

- **which channel draws the hold.** `origin: "plan"` routes the directive's `baseline` to
  a new `FeatureLayers.accepted` (gray, stage `accepted`) instead of to `humanBase`
  (blue). Nothing else could still tell them apart: both are "applied to the store,
  waiting on the code", and the row that said "an agent wrote this" is gone.
- **hand-off.** Accepting IS the explicit gesture, so a plan directive is handed off on
  mint rather than waiting for a ⌘S that is not coming. A plan amendment's `kind` is the
  same `"amend"` a held prose draft has, so `origin` is the only thing that separates them.
- **who gets credited when it lands.** The hold set holds both kinds of work; without
  this, an accepted plan leaving it credited the reader with words an agent wrote.

`FeatureLayers.accepted` is a separate field from `plan` because it has a different
GEOMETRY, and that is not a detail: accepting APPLIES the proposal, so the plan's words
are in `projected` and the words they displaced are gone from the page. A materialized
proposal has both sides present; this has one. Reusing `plan.runs` would print the
displaced sentence over text that is not there.

**Fulfilment moves to where the build is.** The tracker now watches the AGREED set, not
the offer:

| transition | means | fires |
|---|---|---|
| proposal → agreed | accepted | nothing (nothing is built) |
| proposal → gone | rejected / withdrawn / superseded | nothing |
| agreed → gone, node stands | the directive closed | plan fulfilment |
| hold → gone, not agreed | your own edit was built | human fulfilment |

This also closes the gap the old design documented as a deliberate silence: an accepted
ADD produced no marker, because its proposal id vanished identically on accept and on
reject. The agreed set is keyed by the id the STORE minted, which only an accept
produces, so the question is now answered by where the plan went rather than by what
disappeared.

---

## The composition matrix

Read a span as its INK (who wrote it) over its GROUND (what the codebase did):

| ground \ ink | none | blue (human) | gray (plan) |
|---|---|---|---|
| none | settled prose | you wrote it, not built yet | planned; nothing built yet |
| green (add) | the codebase added this | **✗ impossible** | planned, and the build put these words in |
| red (cut) | the codebase dropped this | **✗ impossible** | **planned, and the build did not keep it** |

The bottom-right cell is the point. Nothing is written to produce it — it falls out of
two channels drawing two properties of the same words.

The ✗ cells are contradictions, not rarities: "you wrote this" and "the codebase wrote
this" cannot both be true of one sentence, and blue-on-green gives the reader no way to
tell which half is lying. Three rules keep them empty, and `settlement.test.ts` pins
them:

1. Human and code claims are both diffs INTO `projected`, so they can name the same
   sentence. The code claim yields wherever the human also claims — the author is the one
   party who can be asked, the same rule `model.event.outranks` states on the Python side.
2. A code claim is ALL-OR-NOTHING through the author's later typing.
3. Human claims are the INSERTED runs of a diff, and inserted text is by construction
   absent from the `same` regions every other channel maps through.

Human and plan cannot collide either, by (3) in both directions.

---

## One palette, four surfaces

The channels were a palette for the prose. The tree rows had their own ramp and the
minimap rail had a third, and two of the three named the wrong party:

| surface | proposed | sent | staged | loop rewrite |
|---|---|---|---|---|
| prose (before) | gray | blue | blue | green/red |
| tree row (before) | review-**blue** | sage **green** | blue | *not shown* |
| rail (before) | review-**blue** | sage **green** | blue | **amber** |
| all three (now) | gray | blue | blue | green |

Sage green was the same colour the code channel uses for "the codebase added this", so a
promise the codebase had not kept yet and a fact about what it did wore one hue. Review
blue was the author's ink on a proposal nobody in the room had written.

- The channel tokens (`--st-human` / `--st-plan` / `--st-code-*`) moved to the
  design-system block at the top of the stylesheet, because four surfaces read them now.
- `featureState` gained two states, and both are splits rather than additions: `agreed`
  (a plan you accepted — same lifecycle position as `sent`, different author, and the
  colour is the thing reporting the author) and `rewritten` (the loop changed this; the
  rail always showed it and the row did not, so the two panes disagreed about a feature
  the codebase had just changed under the reader).
- `railState` now DELEGATES to `featureState` and only decides its own two extra states.
  It used to be a parallel copy, and the copy had already drifted — delegating is what
  stops it drifting again.
- `working` spends no channel hue: an agent being somewhere right now is not a claim
  about who is ahead. Neutral plus motion, in both panes.
- Inline blame joins the same three inks. It is the same three parties seen from a
  different question, and History stance draws blame BESIDE settlement claims — the one
  view where a fourth palette was worst.

## A derived projection that was one pass stale

The sidecar's `holds` / `hold_detail` slices are computed from the realize queue, and
`_apply_edits` writes the queue AFTER its `write_tree`. The watch daemon re-renders on
its own afterwards, so this never showed there — but every other caller (the daemonless
verdict drain, `codoc sync`, the Stop hook, the hub) left the surface a full pass behind:
an accepted plan queued its work and the document went on showing nothing until something
unrelated happened to re-render. `LoopBResult.queue_changed` now says when the queue
moved and `run_loop_b` re-projects the sidecar — the sidecar only, since `tree.codoc` is
prose and rewriting it there would re-open the H1 question for no gain.

## Tests

`settlement` (the matrix + the two-hop human channel), `settlement-stages` (the
paragraph split, the hold-plus-plan case, origin routing and its back-compat default),
`fulfilment` (accept ≠ built, reject ≠ built, the accepted ADD), `plan-materialize`
(`descParas`), `classify-surface` + `display-text` (the chip marks no prose),
`decoration-grammar` (no status surface reports a channel in another channel's hue),
`feature-state`; and on the Python side `tests/mcp/test_server.py` (plan amend queues,
reflection amend does not, deferred accepts are reported) and `tests/agent/test_propose.py`.
1400 TS tests, 1905 Python tests, `tsc --noEmit` and the esbuild bundle clean.
