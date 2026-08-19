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

**The new primary outcomes are detection coverage and record truth.** Detection
coverage is how many of four planted problems a participant found and correctly
attributed, out of a change every participant sees identically. Record truth is
whether each claim in the description they finished with is true of the code they
finished with, measured by probe and rated blind. Both are new, neither is
calibrated yet, and a pilot has to run before the thresholds are written down.

**What the change does not fix.** It is still a bundle against a bundle at twelve
participants, still one session, and still silent about the week scale. The false
alarm count is new and it is there so that a difference in detection cannot be
claimed without checking that the participant did not simply call everything
wrong.

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

**RQ1 — understanding.** This is the strongest position, and it improved most.
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

**RQ2 — authored modification.** The primary outcome is four decisions per
session, rated 0–2 blind, plus who settled each one. That is hand-rated and
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

1. **Pre-registration has not happened**, and the one frozen threshold in the v2
   design is written against the abandoned RQ1 and a codebase that is no longer
   used. It needs restating against RQ1-understanding and RQ2-authored-modification
   before participant 1, or the study is exploratory and should say so.
2. **The session snapshots did not run on the pilot** (`session-log.sh` had to be
   started by hand and was not), so no 20-second history exists and "what kind of
   edits people make to the description" has no data for that session. FIXED for
   every session from here: the logger takes the snapshots itself
   (`logger/snapshot.js`), in both conditions, with nobody starting anything. The
   pilot's own gap is not recoverable — that session has no replay, and its entry
   in the plan stays MISSING.
