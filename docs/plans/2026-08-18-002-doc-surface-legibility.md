# The document surface is illegible — diagnosis and the rule that fixes it

*Design decision, 2026-08-18. Written against five audits of the shipped surface; every
claim below is cited to code in those audits.*

## What users actually said

> "unclear workflow and unclear inline decorations — sometimes the accepted changes are
> planned nodes? some are the code changes surface, some are the users edits, some are
> the modification before code edits… they are too tangled"

> "those node level decorations should be more inline level, more granularity, those
> extra UI should be designed more in-situ"

> "all the design should have a purpose and should be easy to understand"

These are three statements of one problem, and the audits found its structural cause.

## The diagnosis

**codoc already contains the correct architecture, and applies it to two surfaces out of
three.**

`state/feature-state.ts` exists because this exact failure was diagnosed once before —
its header describes "six markers at once… a legend nobody had". Its answer is a single
ORDERED projection: rank every signal a feature can carry, take the winner, draw one
glyph and one sentence. The tree rows use it. The minimap ticks use it. The module even
promises that "a row badge and its rail tick can never tell different stories".

The document pane never got the collapse. It still runs the pre-collapse model: seven
independent decoration plugins, each free to mark the same heading and the same
paragraphs, none aware of the others. The consequences the audits measured:

- **"The agent is working here" renders up to nine ways at once** — heading glyph,
  ribbon, presence avatar, presence label, presence trail, body ghost-dim, busy shimmer,
  rail tick, tree badge — all off one `sync.phase` value.
- **"Queued for the agent" renders six ways**; **"staged, not sent" renders five.**
- **Four different left rails claim the same `::before`** on description paragraphs
  (pending, captured, auto-edit, blame). They do not compose — the last one in the
  stylesheet wins. Turning the History stance on therefore *replaces* the "recorded, not
  sent" and "queued" rails with an authorship rail. A signal about work owed vanishes
  because the reader asked who wrote something.
- **Two `::after` claims collide** on the heading: the save flash silently deletes the
  agent-activity glyph while it plays.
- **Underline means nine different things**, five of them dotted, three of them blue.
  **Strikethrough means six**, including both "this IS retired" and "retiring this is
  PROPOSED" — fact and proposal in the same visual.
- **Colour discipline is stated and broken.** The stylesheet's rule is one structural
  accent plus two directional hues, "at most these TWO saturated hues in view at once",
  with hue meaning DIRECTION and ink meaning AUTHORSHIP. In practice blue means five
  things (code-ahead direction, your staged edit, structural you-are-here, loop
  authorship, finished-status) and the common case — blame on, one held feature, one
  proposal, a find query — puts five hues on screen.

So "too tangled" is not a matter of taste. It is a surface where one fact is drawn up to
nine times, four signals fight for one pixel column, and one visual primitive carries nine
meanings.

### The second structural cause: two state machines

The daemon computes a per-feature `Phase` (`loop/phase.py`) explicitly to be "ONE home for
'where is this feature mid-flight?'". It ships in the sidecar as `feature_phase`. The
webview parses it — and **nothing consumes it**; `phaseForFeature` has zero call sites.
The webview instead re-derives its own projection from raw signals, with a different state
set (Phase has `divergent` and `drifted`; the webview's has neither) and different names
for the states they share (`drafting/queued` vs `staged/sent`).

Two "single sources of truth" for one question is why the vocabulary fractured: the audit
found **six words for one state** — saved / recorded / captured / staged / draft / held —
and **six for the next** — sent / pending / queued / committed / awaiting / ready. And it
found single words carrying two meanings: "recorded" is both your edit and your verdict;
"pending" is both what you owe the agent and what the agent owes you.

## The rule

> **A node-level signal may be drawn ONCE, in one place, chosen by rank. Everything
> finer than a node is drawn in the prose, at the span it is about.**

Concretely, for the document pane:

1. **One chip per heading**, from the same ranked projection the tree rows use — so the
   two panes cannot disagree, which is already the stated invariant and is currently
   false for two states. One glyph, one short label, one sentence on hover.
2. **The four rails go.** Every one of them duplicates, at node level, something the
   prose already says at span level: captured has its underline, hold has its
   `ce-intent-underline`, auto-edit has its struck words, blame becomes part of the
   chip's card. They were also destroying one another.
3. **Span marks stay and are the granularity of record** — this is the user's
   "more inline level" directly. The diff underlines, the comment anchor, the consult
   span, the find match, the walkthrough quote: each marks the words it is about.
4. **Fact and proposal must not share a visual.** A retired feature and a
   *proposed*-retire feature are both struck today. Proposal is a *provisional* state and
   already has a texture convention in the stylesheet (dashed = not in the code yet);
   apply it, and fix the plan-ghost texture rule that is currently dead CSS.
5. **One word per concept.** Pick one term for each state and use it in every surface —
   chip, tree row, rail legend, status bar, tooltip, button. Two candidate spines, both
   already partly in use: *recorded → sent → done*, or *staged → queued → landed*. Choose
   one and delete the other four synonyms.

## What this is not

It is not "fewer signals because fewer is prettier". Every signal the audits found has a
real fact behind it. The claim is narrower and testable: **a fact drawn twice is not drawn
twice as clearly**, and four rails that overwrite each other draw it zero times.

---

# Version control: what it is actually for here

The user asked the right question — *what do they want version control for, and what is
the blame-mode equivalent in codoc?* The Google Docs answer does not transfer, because in
codoc **the document is not the artifact**. The code is. The tree is a view of intent kept
synchronized with it, edited by three parties, one of whom works while you are asleep.

Four questions, in the order people actually ask them:

1. **"What changed while I was away?"** — the most frequent, and the worst served. An
   agent works when you do not, so the normal state on opening the tree is "things moved".
   Today this is split across three unrelated counters (the catch-up pill covers only loop
   rewrites; proposals are a separate count; queued directives a third) and there is no
   "since I last looked" anywhere.
2. **"Why does this sentence say this?"** — served now, by the provenance card: change →
   the directive that asked → the prompt someone typed → the session → the commit the work
   started from → the code diff.
3. **"Who wrote this claim?"** — served at NODE level ("You edited · 3h ago"), which is the
   wrong granularity for the question. Nobody asks who last touched a feature; they ask
   who wrote *this sentence*, because that decides whether to trust it.
4. **"What did it say before?"** — served by the timeline scrubber.

### The blame-mode equivalent is inline, not per-node

git blame is per LINE. Google Docs colours per RUN. codoc's blame is per FEATURE, which is
why it reads as decoration rather than information: a feature is a paragraph or five, and
"claude-code edited this 3h ago" tells you nothing about which of its claims the agent
wrote.

The data for the finer answer is already shipped. The revisions window carries, per
applied event, the text it wrote and the text it displaced. Replaying those word diffs
forward attributes every surviving span to the party that introduced it. That is real
per-sentence authorship, computed locally, with no new transport — and it is exactly the
"more inline level, more granularity" the user asked for.

### One asymmetry the surface must never hide

Undoing a change to the document does **not** undo the code that change caused. A tree
edit mints a directive; an agent writes code; scrubbing the prose back does not unwrite it.
This is why the timeline is READ-ONLY and must stay so: a "restore this version" button
would silently promise something the system cannot do. Recovery from the past is by
*reading* it and re-authoring deliberately — which is also why the provenance card offers
the code diff rather than a revert.


---

# Does each control earn its place?

The user asked directly: *what is the purpose of bold? of highlight? are the retire and
plan buttons needed? do users need glance mode?* The audit answered them from the code,
and the answers split into three groups.

## Cut — they lose the user's work

**Italic** and **Highlight** produce marks that `pm-doc.inlineRunsToText` discards on
serialize; the next daemon projection then wipes the visual too. You style something, you
watch it style, and it is gone. (`highlight-mark.ts`'s own header claims it persists in
`tree.doc.json` — stale since U4, when the host stopped writing that file.) A control that
silently discards work is worse than no control. Both are removed, from the toolbar and
the bubble.

## Fix — the purpose is real and the wiring is broken

**Bold** is not typography here. `**bold**` in a description becomes the `Focus:` line on a
realize directive — the highest-priority part of the intent, and the documented flagship of
the "markdown-native signals" design. It has never worked from the editor: the B button
makes a mark that is dropped on serialize, and typing literal `**` is eaten by an input
rule and converted to the same doomed mark. So the one authoring signal codoc advertises
most is unreachable from the only human surface. **Fixed by making the mark round-trip to
`**…**`**, and the tooltip now says what bolding causes.

**Plan** (`◇`) promises "the agent implements it on commit" and cannot keep it: `realized`
is dropped between `FeatureUnit` and the `add` command payload, so `NodeOp.realized` stays
`None`, defaults to `True`, and `classify.edit_mints_directive` never fires. The button
produces an ordinary feature whose placeholder title says "(plan)". **Fixed by wiring
`realized` through the command path** — plan-before-code is a documented core workflow,
so the answer is to make it true rather than to remove the button.

## Keep — with an honest label

**Retire** is the human's only destruction verb, and it is *safer* than its reputation:
from this button it is detach-only and never queues code deletion (that path exists only
for an agent's `delete_code=True` proposal, where the verdict button already says "Accept
& delete code"). But it destroys a feature and its whole binding index about two seconds
after the click, with no confirmation and no easy undo, while its tooltip says only
"Toggle retire on this feature". The button stays; the tooltip must say what it costs.

**Glance** collapses every feature to its first sentence. It is decoration-only, correctly
labelled, cheap, and reachable from the palette as well. It is also arguably redundant with
the left tree pane, which is the navigation surface — the difference is that glance shows
the derived *pitch* rather than the title, which is genuinely more informative. **No change
without evidence.** Cutting a harmless, honestly-labelled feature on a hunch is a different
kind of mistake from cutting one that loses work, and we have usage data for neither. It
does, however, occupy one of nine slots in a top bar that is over budget — if something has
to go from that bar, this is the candidate, and the palette keeps the capability.

## The meta-finding

There is **no in-product explanation of any codoc-specific semantic**. Not that bold steers
the agent, not that a comment is a work request, not that an external link becomes an
instruction the agent fetches, not that ⌘S dispatches rather than saves. The one onboarding
document that exists taught `> …` steering — a mechanism retired in U7, which never worked
for anyone who followed it. That text is now corrected. The larger gap stands: every
consequence in this product is currently learned by surprise.
