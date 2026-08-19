# What the data can support, read against the thesis

Written 2026-08-18 after the first pilot came back, and revised 2026-08-19 when
the task was redesigned. Read it with `analysis-plan.md`, which says where each
measure comes from, and with
`docs/plans/2026-08-19-001-task-redesign-v2-reviewing-an-agent-session.md`, which
replaces the task half of the 2026-08-16 design.

The point of this file is to say, in advance, which claims the collected data can
carry and which it cannot — so the answer is not discovered while writing results.

## The task changed on 2026-08-19, and so did what is claimable

The participant now reviews a change an agent already made, rather than making
one. The reasoning is in
`docs/plans/2026-08-19-001-task-redesign-v2-reviewing-an-agent-session.md`. What
follows below about the pilot, the three measurement faults and the parity fix all
still holds. What the arms are compared on has moved.

**The claim is no longer that codoc helps you read code.** It is that codoc is
where a person decides and reviews. The better the models get, the less anyone
reads code, which makes a surface outside the code more necessary rather than
less. Say it that way, because "helps you understand the codebase" invites the
objection that comprehension aids are a shrinking market.

**The new primary outcomes are detection coverage and a durable written trace.**
Detection coverage is how many of the planted problems a participant found and
correctly attributed, out of a change every participant sees identically. A
project plants as many as its recorded session actually landed, which is three
for scribe rather than the four the design assumed. A durable written trace is
whether the record the participant finished with says what was decided about each
problem. Record truth is measured too, by probe and rated blind, and it is
pre-registered as predicted NOT to differ for the reason below. The thresholds
are frozen in `pre-registration.md`.

**What the change does not fix.** It is still a bundle against a bundle at twelve
participants, still one session, and still silent about the week scale. The false
alarm count is new and it is there so that a difference in detection cannot be
claimed without checking that the participant did not simply call everything
wrong.

## What each condition hands the participant, measured on both projects

Both projects were derived through both conditions and each condition's own
record pass was run over the result. The two projects did not behave the same
way, and the difference matters more than either result on its own.

**Both baselines end true, and both argue for the change.** scribe's `CLAUDE.md`
says "The share is 0.4, lowered from 0.6 because a running header that starts
after a title page or stops before the appendices appears on two pages of five",
and states the cost on long documents. tally's grew from 149 lines to 235 and
gained a section headed "Unknown merchants stop the run", with a paragraph on why
a bucket is worse than stopping. Every word of both is accurate. The maintenance
skill does its job.

**codoc ended the two projects differently, and we do not know why.** On scribe
its tree kept the sentences the change contradicts, still saying furniture
repeats on "at least 60% of pages" and that "a short list of prefixes keeps
theirs", and reported `code_drift` with seven proposals waiting for a verdict. On
tally its tree amended to match the code on nearly everything, including the
month and week split and the unmatched merchant stopping the run, and left two
proposals and one stale line about a test.

The obvious explanation is the amend gate, which is meant to preserve
human-authored prose more strictly than prose a loop wrote. It is NOT established:
`feature_writers` is unset on the features involved in both projects, so whatever
produced the difference, it was not that field. **Do not report a mechanism here.**
Report the two observations, say they differ, and say the cause was not
identified.

What this does to the measures is the same either way, and it is the part to act
on.

**Record truth at the end cannot be read on its own**, because the two conditions
do not start it from the same place. A baseline begins the review already true and
can stay true with the participant doing nothing. codoc began stale on scribe and
close to true on tally. So record truth is reported as a pair, the state at
handover and the state at the end, and the reading is the change between them
rather than the final value. Report the handover state per project, because it
differs per project.

**A record can be true and worthless.** In the baseline, "true" means the agent's
account of the agent's own decision, written over the sentence a person had
written, so nothing anywhere records that anyone ever chose the old value or that
anything changed. That is what the study means by asking whether the person
decided, and it is why the durable written trace and who-settled-it are the
measures to lead with rather than record truth.

**Where a condition normalises the contradiction away, detection gets harder**
without any information being hidden. On scribe's baseline the record no longer
holds the commitment the change contradicts, so a participant cannot find the
conflict by reading the record at all: they have to find it in the diff, the
transcript, or the README. Whether codoc's arm is easier depends on whether its
tree kept the conflict, which on this evidence it does sometimes and not always.
That is a real limit on the mechanism claim and the paper has to state it.

**Both conditions' machinery keeps the record current, and that was measured, not
assumed.** The scribe recording was derived through both conditions and each
condition's own record pass was run over it. The baseline's maintenance skill
produced a `CLAUDE.md` that states the changes plainly: "repeat_share moved from
0.6 to 0.5, and keep_hyphen starts empty rather than listing twelve prefixes",
and "a line counts as furniture when the same text repeats on at least half the
pages, and on no fewer than two of them whichever way the share falls". Both are
accurate. The agent that made the questionable choices then described them
correctly.

So **record truth may not separate the conditions at all**, and the study should
not be built on the expectation that it will. What is left that can separate them
is worth stating before any data arrives:

- **Finding.** The baseline states the change inside 163 lines of prose that also
  describe everything else. codoc states it as amended features plus three
  proposals attached to the parts of the tree they belong to.
- **Being made to decide.** A codoc proposal is a verdict somebody has to give:
  it sits in the tree until it is accepted or rejected, and the ledger records
  which. Nothing in the baseline demands a decision, so "the agent did it and they
  never noticed" and "they read it and agreed" look identical afterwards.

The second of those is the one to lead with, because it is the difference the
tool actually makes and it is recorded rather than rated. Report record truth
either way, including the likely result that both conditions keep it.

**The agent rewrites the project's own README, in both conditions, and nobody
asked it to.** Observed on both projects while recording. In tally it added 77
lines describing the new settings file, the ordering rule, and what the transfer
setting does, all of it accurate. In scribe it added 51 lines describing the
config file and the report.

That is a third record, and it is present identically in both conditions, so it
does not bias the comparison. What it does is compress the effect, because a
participant who reads the README diff has read an account of the change whether
or not their condition has a maintained description. Reading it is a legitimate
route and it has to be a strategy code of its own, beside reading the agent's
account and reviewing the change.

The README is not reliably true, which is the more useful half. scribe's says the
notes are listed "each against the `[^n]` to search for", which is precisely the
cross-reference that does not work, so the agent wrote the false promise into the
README as well as into the report. So a participant can catch the problem there,
and a participant who corrects only the description has left a second record
saying the wrong thing. Score record truth against the record the condition owns,
and note the README separately rather than folding it in.

**One thing the replay adds and one thing it costs.** It adds comparability,
because everybody reviews the same change, so detection counts can be pooled at
all. It costs realism, because nobody watched their own agent work and the
compression factor is not one. Report the factor from the manifest, and say that
the lag between an edit and the tree reacting was preserved in proportion rather
than removed.

## What the pilot actually showed

One pilot, two conditions (scribe as baseline, tally with codoc). Numbers below
are from that session, recovered with `logger/transcript.js` and
`scoring/ledger-actions.py`.

| | baseline | codoc |
| --- | --- | --- |
| Prompts the person wrote | 1 | 2 |
| Files the agent read | 11 | 12 |
| Files the agent edited | 11 | 10 |
| Code files the PERSON edited | 0 | 0 |
| Description edits by the person | 3 | 1 |
| Description edits by the agent/loop | 3 | 2 |
| Proposals accepted | — | 1 |
| Files the person opened in the editor | 1 | 2 |

The shape is the same on both sides: **the person prompts once or twice, the agent
does everything, and the person opens almost nothing.** That is not a measurement
failure. It is the finding, and it is the same finding the calibration runs
predicted — "the agent solves both tasks correctly no matter what it is asked"
(`experimenter-guide.md`). The study was already pre-registered as discriminating
at the human layer rather than the code layer.

## Three measurement faults the pilot exposed, and what was done

**1. The editor could not see the agent.** The interaction log is written by a VS
Code extension, and an extension only sees files that are open. The agent touched
12 files per condition; the log recorded 1 and 2. Every other read and edit left
no trace. → `logger/transcript.js` recovers them from the transcript, in the same
event schema, so one vocabulary maps both halves. Runs on sessions already
collected.

**2. The provenance figure was biased against codoc.** `figures/provenance.js`
calls itself "the figure the thesis lives or dies on". It counts who writes to the
description. In the baseline the description is `CLAUDE.md`, an ordinary file, and
its edits were recorded. In codoc the description is edited in a custom editor,
which changes no text document — so codoc's description edits were recorded as
**zero**, while the baseline's were counted. A tool whose whole claim is that the
description is co-written was being measured as a description nobody writes in. →
`scoring/ledger-actions.py` reads codoc's own change ledger, excluding seeding
(57 of 68 events on the pilot were bootstrap and translate).

**3. Reading the program's output was counted as reading code.** `fixtures/*.txt`
and the `.md` the program writes were classified `code`, inflating "did they open
the code before acting" — the measure meant to separate reading from trusting.
tally's `.csv` samples were dropped entirely, so the two projects were not even
counted the same way. → a distinct `output` surface.

None of the three is visible in a session that looks healthy, and all three run
against the tool or against comparability. They are the reason to look at pilot
data properly rather than only checking that files arrived.

## What the data can now support

**RQ1 — understanding.**

> **Out of date since 2026-08-19.** What follows calibrates the round of five
> questions that was asked before the task, open book and timed. That round has
> been removed from the session, so neither its score nor its elapsed time is
> collected any more, and the claim about FINDING stated at the end of this
> subsection has no measure behind it as things stand. What RQ1 can support has to
> be restated, and that is a decision for the study lead rather than an edit made
> here. `analysis-plan.md` records the removal and its reason. The closed-book set
> asked after the task is unaffected.

This is the strongest position, and it improved most.
The instrument's own stated open problem was that a blind model scored 9/12 while
the description scored 8/12 — "on this evidence the questions do not separate the
arms" (`projects/scribe/STUDY.md`, before this revision). After rewriting both
descriptions and both quizzes:

| | scribe | tally |
| --- | --- | --- |
| From the codoc tree | 5/5 | 5/5 |
| From `CLAUDE.md` (baseline) | 5/5 | 5/5 |
| Blind, no description at all | 2/5 | 2/5 |

Both arms can reach every answer; guessing cannot. That is what an instrument
needs to be worth running: the score is now free to move for reasons other than
how good the model is at multiple choice, and the elapsed time is the other half.

Note what this does NOT establish: both arms score the same *from the text*, by
construction — the two descriptions are now literally the same content
(`check-descriptions-match.py` is a build gate). So RQ1 cannot be answered by "one
description is better written". Any difference has to come from the mechanism —
how fast somebody can find the answer in a bound, navigable tree with search and
`/codoc:ask`, against the same words in a flat file. **The claim RQ1 can support is
about FINDING, not about CONTENT.** State it that way or a reviewer will say the
arms differed in their documents, which they no longer do.

**RQ2 — authored modification.** The primary outcome is one rating per planted
problem, 0 to 2 and blind, plus who settled each one. A project plants as many as
its recorded session landed, so coverage is reported as a proportion of that
project's own maximum. That is hand-rated and
unaffected by the instrumentation faults. What the fixes add is the *evidence* for
the who-settled-it coding: with the merged stream you can say, per decision,
whether the person read the relevant file before the agent changed it, whether
they wrote anything into the description, and whether a proposal was accepted
without the code ever being on screen. Before, that reconstruction was manual and
mostly unavailable.

**The honest expectation, from the pilot:** the modal answer to "who settled this"
will be *the agent did it and they never noticed*, in both arms. If that holds at
n=12 it is a real result and it is publishable — the design already commits to
publishing the shape where the baseline holds up. It is also the result that most
needs the provenance fix, because the interesting question becomes whether codoc
moves any decisions from "never noticed" to "accepted deliberately", and the
accept events only exist in the ledger.

## What the data cannot support, whatever comes back

- **That codoc produces better code.** Pre-registered as near-ceiling in both
  arms and confirmed by calibration. Do not report task success as a win.
- **That codoc is faster.** The baseline's maintenance skill spends agent turns by
  our own design; the codoc arm now has two extra affordances. Both directions are
  contaminated. Report agent-turn overhead, claim nothing.
- **Anything attributed to one mechanism.** It is a bundle against a bundle at
  n=12: bindings, verdicts, holds, search and `/codoc:ask` all move together.
- **Anything about the week scale.** One session. The description's value over
  months is the follow-up study, not this one.
- **A per-arm difference in the quiz score sourced from wording.** The two
  descriptions are now identical in content on purpose.

## Two things still open, and they are decisions rather than work

1. **Pre-registration is written and is `pre-registration.md`.** The v2 design's
   one frozen threshold was written against an abandoned research question and a
   codebase no longer used, so it has been restated in the units the redesigned
   task actually produces. Detection coverage and a durable written trace are the
   two confirmatory predictions, each with a sign test over the twelve paired
   participants. Record truth is pre-registered as predicted NOT to differ, which
   is what the measurement above found and what honesty requires writing down
   before the data rather than after. Everything else in the analysis plan is
   labelled exploratory. It still has to be posted to OSF.
2. **The session snapshots did not run on the pilot** (`session-log.sh` had to be
   started by hand and was not), so no 20-second history exists and "what kind of
   edits people make to the description" has no data for that session. FIXED for
   every session from here: the logger takes the snapshots itself
   (`logger/snapshot.js`), in both conditions, with nobody starting anything. The
   pilot's own gap is not recoverable — that session has no replay, and its entry
   in the plan stays MISSING.
