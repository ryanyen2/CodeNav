# Task redesign, after pilot 1

Status: **awaiting sign-off.** Nothing is built from this yet.

## Why

The first pilot could not run. A participant with a general CS background, given a
few minutes on 2,000 lines across 15 files, could not form enough of a view to
make a change or answer a question about it. Both conditions floored, so nothing
was compared.

The useful part of that finding is not "make it easier". It is that the
bottleneck was in the wrong place. Finding where drafts belong in hearth is a
**search** problem, and an agent searches instantly, so the human was a
spectator and the two arms looked alike.

**The task should be easy to implement and hard to decide.** The agent writes the
code either way — that was already pre-registered. What the person has to supply
is judgment: is this the right behaviour for this codebase? And that depends on
knowing why the existing code decided what it decided, which is the thing codoc
claims to carry and `CLAUDE.md` carries worse.

## The research questions

Restated from the source, and they supersede the earlier three.

**RQ1 — understanding.** Can codoc help users understand the codebase when
collaborating with AI agents? What is the purpose of the codebase, why is it
designed this way, why were certain changes made? *(Building the theory of the
program.)*

**RQ2 — authored modification.** Can codoc help users author modifications with
control maintained when collaborating with AI agents?

Everything below is chosen to serve one of those two. Anything that serves
neither is cut.

## What a study codebase has to be

A **pile of policies, not a pipeline of mechanisms.** A pipeline forces you to
trace it end to end before you can act; a pile of policies can be sampled, and it
is exactly where a record of intent earns its keep, because the code says what
was decided and never why.

| Property | Why |
| --- | --- |
| One-sentence purpose, understood in 60 seconds | RQ1's first question must not be the hard part |
| ~500 lines, ~6 files, all readable | 2,000 was the floor effect |
| 9 independent policies, 5–20 lines each | Many small decisions, each samplable |
| Every policy has a defensible alternative | If there was only one sensible choice, there is no rationale to record |
| Every reason is absent from the code | The description is the only place it can live |
| One pair of policies secretly coupled | The hazard: a change that looks local and is not |

The two projects must match on all of these, so a difference between them is
domain and not difficulty.

## The two projects

Same deep shape — read messy input, apply nine policies, write something tidy —
in two domains far enough apart that nobody meets the same problem twice.

### `scribe` — text pulled out of a PDF, into clean Markdown

Purpose: *"It takes the text layer out of a PDF and writes readable Markdown."*

| # | Policy | The alternative it could have taken |
| --- | --- | --- |
| 1 | Join words hyphenated across a line break | Keep the hyphen: some are real compounds |
| 2 | A single newline continues a paragraph | Treat every newline as a break |
| 3 | Headings by leading numbering | By short line, or by ALL CAPS |
| 4 | Drop the line repeated on every page | Keep it: a one-off document wants its header |
| 5 | Drop trailing page numbers | Keep them as anchors |
| 6 | Bullets from `-`, `•`, `*` | Only from `-`, so a real bullet character is literal |
| 7 | Footnote markers collected at the end | Left inline where they appeared |
| 8 | Normalise ligatures and curly quotes | Leave them, for fidelity |
| 9 | Collapse runs of blank lines to one | Keep them, as deliberate spacing |

**Coupled pair (the hazard):** 4 and 3. A repeated running header that also looks
like a heading gets dropped by the furniture rule before the heading rule ever
sees it. Any change to either has to account for the other.

### `tally` — a bank export, into a monthly summary

Purpose: *"It reads a CSV of transactions and writes what you spent, by month and
category."*

| # | Policy | The alternative it could have taken |
| --- | --- | --- |
| 1 | Category from the first matching merchant rule | From the last, or refuse on ambiguity |
| 2 | Refunds net against the category | Reported separately |
| 3 | Transfers between your own accounts are excluded | Included, since money did move |
| 4 | A transaction belongs to the month it was *made* | The month it *posted* |
| 5 | Duplicates by date, amount and merchant | By the bank's own id only |
| 6 | Round at the summary, not per row | Round each row |
| 7 | Unmatched goes to `uncategorised` | Refuse and report the row |
| 8 | Recurring detected by same amount, monthly | By merchant name alone |
| 9 | A negative amount is money out | Money in, as some banks export it |

**Coupled pair (the hazard):** 3 and 5. A transfer between your own accounts is
two rows that look exactly like a duplicate. Excluding transfers and dropping
duplicates cannot both be applied naively without losing one leg or keeping both.

## The tasks

One rule added, with four decisions the card deliberately leaves open. The agent
implements in a minute; the participant decides.

**scribe:** *"Support block quotes."*
1. What marks a quote in extracted text — indentation, or a leading character?
2. Does de-hyphenation apply inside a quote?
3. Does a quote end the paragraph before it?
4. What happens when a quote runs across a page break, where the furniture rule is?

**tally:** *"Support split transactions"* — one purchase across two categories.
1. How is a split written in the CSV?
2. Does a split count as one transaction or two?
3. Does the duplicate rule see the two halves as duplicates?
4. If one half matches no rule, does the whole thing go to `uncategorised`?

Decision 4 in each is the coupled one, so the hazard is reached by deciding
rather than by tripping over it.

## What is measured

**Gate.** The change runs and the existing tests pass. Not reported as a result;
a session that fails the gate has no decisions worth rating.

**Primary — RQ2.** Each of the four open decisions rated **0–2 for consistency
with what the codebase already believes**, blind to condition. A participant can
produce working code that contradicts the codebase, and that is the finding.
Alongside it, who settled each decision: they decided, the agent proposed and
they accepted, or the agent did it and they never noticed.

**Primary — RQ1.** A twelve-question multiple-choice quiz, four options each, one
right. Asked closed-book after the task. Four questions per band:

- *Purpose* — what is this program for, what is in and out of scope
- *Rationale* — why is a policy the way it is, rather than its alternative
- *Change* — why was a particular past change made
- *Extension* — to add a named further feature, what would have to be decided

The bands map onto RQ1's own wording. Options are written so that the wrong ones
are plausible to somebody who read only the code, which is what makes it a test
of theory rather than of reading speed.

**Secondary.** Confidence and its grounds; the questionnaire; and from the
interaction log, where the time went and whether the description sat in the loop.

## What this costs, plainly

Two codebases, two seeded codoc trees, two matched `CLAUDE.md`, two task cards,
two quizzes, two scorers, and re-calibration of both. It is the largest single
piece of work in this project, and it discards the calibration already done on
hearth and ember.

The reason to spend it: pilot 1 says the current pair cannot produce a
measurement at all, so the alternative is not cheaper, it is nothing.

## Open, and worth deciding before building

1. **Warm-up.** Five guided minutes in the same codebase before the task, or
   straight in? Straight in measures cold reading; guided measures what the
   description does once you are oriented. The pilot suggests cold is too cold.
2. **Quiz timing.** Closed-book once after the task, or before and after so the
   change is the measure? Before-and-after is stronger and costs ten minutes.
3. **Is nine policies the right number**, or does it want to be six?
