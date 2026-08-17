# What we record, and which question each part answers

Every measure in the design doc, with the data it is computed from. If a measure
is not in this table it cannot be reported, so read it before the first session
rather than after the last one.

Run `scoring/check-session-complete.py` on a finished session and it checks the
same list against what actually arrived.

## The five things a session produces

| Source | Where it comes from | What it holds |
| --- | --- | --- |
| Interaction log | The study logger extension, in both conditions | Which file was on screen, which lines, for how long, and every edit as a character count |
| Claude Code transcript | `codoc-study/<workspace>/.claude-study/projects/`, collected at the end | Every prompt the participant wrote and every action the agent took, timestamped |
| Project snapshots | `session-log.sh`, every 20 seconds | The whole project, replayable commit by commit, plus codoc's own state files |
| Final projects | Collected at the end | What they finished with, for scoring and for a blind rater |
| Your notes and forms | The dashboard, during the session | Sign-off, who settled what, the answers to the questions |
| Questionnaires | The participant page | Background, both after-condition blocks, and which they would pick |

The clocks are all wall clock in milliseconds, so the three machine-written
sources merge on time without any correlation work.

## Why one logger runs in both conditions

The logger is a separate extension, installed in both conditions, writing one
schema. If codoc had logged navigation and the other condition had not, then
every navigation result would describe the tool rather than the person, and the
comparison the study is built on would not exist. Running the same code in both
places is the only version of this that survives review.

It records file paths, line numbers, durations and character counts. It never
records the text of a file, a description, or a prompt. Prompts live in the
transcript, which the participant is told about.

## Research question 1: who writes what

| Measure | Computed from |
| --- | --- |
| Where each change originated: written in the description and handed off, typed into chat, or edited straight into the code | codoc's own change ledger has actor and mode for the codoc condition. For both conditions, the transcript gives every agent action and the interaction log gives every human edit with whether the window was focused and whether that editor was the active one. Merge on time. |
| What kind of edits people make to the description, in six categories | The description at 20-second intervals from the snapshots, diffed. In one condition that is `tree.codoc`, in the other `CLAUDE.md`. |
| Who settled each decision the task left open | Your notes, the think-aloud, and the transcript. Hand-coded. |
| What the agent wrote back into the description | codoc's ledger for one condition; for the other, a `CLAUDE.md` edit inside an agent turn in the transcript. |
| Whether decisions settled in chat survive anywhere at the end | The transcript, against the final project. Hand-coded. |

## Research question 2: does the description stay true, and what does checking cost

| Measure | Computed from |
| --- | --- |
| Does the description match the code, per feature, before and after | The starting archive and the final project, rated blind. |
| Drift: features owning nothing, code nobody describes | Rated by hand from the final project, **in both conditions**. Do not use codoc's own drift numbers. Only one condition computes them, so using them would mean the tool is rating its own work. |
| Proposals raised, accepted, rejected | codoc's change ledger. Only the codoc condition has proposals, so this is reported on its own, not as a comparison. |
| How long people spent reviewing | Time on the description between a proposal appearing in the ledger and the verdict landing, plus dwell on the description from the interaction log. The resolution is one daemon pass (a few seconds), which is finer than the effect. Say so in the paper. |
| **Review coverage: what fraction of the changed lines did they actually look at** | The interaction log's view events give file, line range and duration. The snapshots give the changed lines at sign-off. A hunk counts as inspected if a view event covered its lines for at least two seconds before sign-off. |
| The sign-off, and what the confidence rested on | Your notes, verbatim. |
| Warranted trust: acting on the description without opening the code | The interaction log shows whether the code file was ever on screen before the action, and the transcript shows the action. Whether the claim was true is rated afterwards. |

## Research question 3: what they understand afterwards

| Measure | Computed from |
| --- | --- |
| **How well and how fast they can find an answer in this codebase** | The twelve questions, asked once before the task, **open book and timed**. The participant may read the description, read the code, run the project and ask the agent. Score and elapsed time both land in `answers/quiz-<project>-before`. Both are results: either way of working can reach every answer, and the question is what it costs. |
| **What they carried out of the task** | Six multiple-choice questions in `answers/reflect-<condition>`, **closed book**: no code, no description, no agent. They have right answers, held in `experimenter/after-questions.json` and never shipped to the browser, so the dashboard scores them and nothing is marked by hand. They are per project and about the change the participant just made, in the same four bands as the pre-task set. |
| Whether they were sure or reconstructing | The `recall` scale on the same page. A fluent reconstruction and a real memory read the same in prose, so it is asked directly. |
| Whether the decisions passed through them | The who-settled-what record in the dashboard, and the two after-task questions that turn on a decision the participant made rather than on the codebase alone. |

The after-task set was four boxes to type in until 2026-08. Freeform got short
answers to questions whose value is in the follow-up, at the end of two hours,
and nothing comparable between participants. The follow-up now happens out loud
in the closing interview, where it belongs.

Every one of the six turns on a consequence of the participant's own change
meeting a rule that was already in the codebase, so the two ways to get it right
are to have understood the codebase or to have made the decision and watched what
it did. A question answerable from the project briefing would measure reading.

**The quiz is no longer asked twice.** It used to be the same twelve before and
after, with the change between them as the measure. Both sittings asked about the
codebase, which the open-book sitting already reaches, and neither asked about
the thing the study is actually about: whether the person still owns the change
their agent helped them write. The set that replaced it asks exactly that, and can
be multiple choice because the CONSEQUENCES of the change are fixed by the
codebase even though the change itself differs from participant to participant.

The comparison is **within participant, between conditions**: each person does
one project each way, so their after-task score with codoc is compared against
their own after-task score without it. There is no comparison between the two
question sets, and none between participants.

## Did the work get done

| Measure | Computed from |
| --- | --- |
| The three things being scored, and regressions | `scoring/check-scribe.py` or `scoring/check-tally.py`, with the settings file you wrote for that participant. |
| Where the change landed, rated 0 to 3 | The final diff, rated by hand. |
| The combined score that joins working code with being able to explain it | The scorer for the code half, the questions and think-aloud for the human half. |

## How they worked

| Measure | Computed from |
| --- | --- |
| Time to first edit | The first edit event in the interaction log, minus the time the task started. |
| Switches between description, code, and agent | Focus events in the interaction log for the first two. Agent turns come from the transcript, because VS Code does not tell an extension when a terminal has focus. |
| How many files they opened before the right one | Focus events in order, against the file the change eventually landed in. |
| How long their instructions to the agent were | The transcript. |
| How they navigated, coded into seek, relate, and collect | The interaction log with the screen recording. Hand-coded. |

## Questionnaires and notes

Both are typed on the web now and land against the participant code. The
questionnaires come from the participant's page, and the sign-off, the
who-settled-what record and the question scores come from your dashboard.

The session checker can see both. Export the participant with
`scripts/export-session.mjs`, put the file beside the collected folder, and the
checker compares the two halves and reports what is missing from either.

Consent is the exception. It stays in the Google form, and no part of this
touches it.

## How the questionnaires are scored

Written down before the first session, because every rule below has a plausible
wrong version that produces a number nobody would question.

Scoring lives in `study-app/participant/instrument.js` and is imported, never
reimplemented. A second copy in an analysis script is how a reverse-keyed item
gets averaged the wrong way up: the number that comes out is in range, in the
right ballpark, and wrong.

| Block | Rule |
| --- | --- |
| **NASA-TLX** | `rtlx(answers)`. Six subscales, unweighted, each collected on the original 21-point 0–100 scale, averaged after performance is flipped. Reported on 0–100. Refuses to score a participant who left one of the six blank. |
| **UMUX-Lite** | `umuxLite(answers)`. Two items on seven points, scored by the published formula `(i1 + i2 − 2) × (100/12)`, reported raw on 0–100. **No SUS conversion.** The regression that produces a SUS-equivalent score has constants fitted to particular corpora, and a within-subject comparison gains nothing from it that the difference does not already give. Say so in the paper rather than leaving a reader to wonder. |
| **The four custom blocks** | Every item reported individually. `constructScore` is reported alongside as a summary, never instead of the items, and never as evidence that the items measure one thing. |
| **Which one, for which kind of work** | Seven activities, each answered as first / second / no preference. Reported per activity, never summed. Summing would treat a preference for reviewing somebody's change and a preference for an hour-long fix as the same quantity. |

### Where the seven activities come from

They are the kinds of work that empirical studies of developer time keep
reporting, rather than a list written for this study. The earlier list mixed job
size, ownership and time pressure in one item, so an answer could not be read as
being about any of them.

- Meyer, Fritz, Murphy and Zimmermann, "Software developers' perceptions of
  productivity", FSE 2014. 379 developers surveyed and 11 observed. The observed
  developers spent 32.3% of their time coding and 3.9% debugging, which is why
  the list is not weighted toward debugging.
- Meyer, Fritz, Murphy and Zimmermann, "The Work Life of Developers: Activities,
  Switches and Perceived Productivity", IEEE TSE, 2017.
- LaToza, Venolia and DeLine, "Maintaining mental models: a study of developer
  work habits", ICSE 2006. The finding this study is built on: the knowledge of
  why code is the way it is lives in memory, and is recovered by reading code and
  interrupting colleagues.
- Sillito, Murphy and De Volder, "Asking and Answering Questions during a
  Programming Change Task", IEEE TSE 34(4), 2008. The 44 questions a programmer
  asks while making a change.

**Three of the seven are cases where a written description plausibly does not
help, and they are there on purpose.** Debugging a fault you can already
reproduce is answered by running the code rather than by reading intent. New code
in a file that does not exist yet has nothing to describe. A fix due within the
hour is exactly where keeping a description current is pure overhead. A list the
tool could only win on would measure the list rather than the tool, so report all
seven whichever way they go.

Three things about TLX that have to appear in the paper's method section,
because the review literature consistently finds them missing: the scale was 21
points (0 to 100 in fives), no weighting step was used, and performance was
collected high-is-good and reversed before scoring.

**Reverse-keyed items** are `tlxSuccess`, `ctl4`, `ctl5`, `doc2`, `rev3`. Stored
as answered, flipped once in `keyed`.

## How the comparison is reported

The design is within-subject with two conditions, so every number is a **paired
difference**: one value per participant, codoc minus baseline.

- **The estimate is the headline, not the test.** Report the paired mean
  difference with a 95% bootstrap interval, and plot it. `figures/stats.js`
  already computes exactly this, with a fixed seed so the number in the caption
  matches the number in the picture. At the sample size this study can recruit, a
  p-value from a dozen pairs is unreliable. The interval communicates the same
  information and also shows how wide the uncertainty is.
- **Distributions, not means, for the agreement items.** `figures/likert.js`
  draws stacked counts per item. Four people at the middle and four people split
  between the extremes produce the same average but represent different findings.
- **A test where a test is wanted.** Wilcoxon signed-rank on the paired
  differences, reported alongside the interval rather than replacing it.
  Parametric tests on summed scales are defensible (Carifio and Perla, Norman,
  and Lee et al. for TLX are the usual citations), but at this sample size the
  choice of test family does not matter, and the non-parametric one is easier to
  defend. Individual Likert items are not analysed with a metric model at all.
  The ordinal-data literature agrees on at least this much, even where it
  disagrees about scales.
- **No reliability coefficient for a three-item block.** Cronbach's alpha rises
  with item count and breaks down at this sample size. Reporting it for a
  five-item block written for this study would make an ad-hoc block look like a
  validated instrument. Show the items and let the reader judge them.
- **Which comparisons were planned.** The four custom constructs and the two
  published instruments are six families of comparison. Name them here as
  planned, and report every one of them regardless of whether the result moved.
  Do not correct across them after the fact. With results this small, a
  correction makes no practical difference either way, and the real risk is
  silently dropping comparisons that did not move.

## What we cannot measure, and are not going to claim

- **Which terminal had focus.** VS Code does not expose which terminal has focus.
  Agent turns are timed from the transcript instead. The transcript is exact for
  when a prompt was sent and when the agent acted, but it does not show someone
  reading terminal output. Where a measure needs "was the participant attending
  to the agent", use the screen recording, not the log.
- **Reading without scrolling.** A file open in a pane counts as viewed whether
  or not the participant is actually looking at it. This overstates review
  coverage in both conditions equally, so the comparison between conditions is
  still valid even though the absolute number is not.
- **Anything after they close the window.** Nothing measures whether the
  description would still help a month later. Item 10 on the questionnaire asks
  people to predict it rather than demonstrating it. Long-term retention is a
  different study.
- **That any workload score is high or low.** TLX has no norms for this kind of
  work, so a score of 60 on its own is meaningless. TLX is also not a ratio
  scale, so 60 is not twice the load of 30. Only the difference between the two
  conditions is interpretable, and only for the task the block names. The same
  applies to UMUX-Lite reported raw.
