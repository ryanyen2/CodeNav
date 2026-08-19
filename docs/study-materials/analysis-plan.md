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
| **The agent's half** | `logger/transcript.js` over the collected transcript | Every file the agent read and wrote, and every command it ran. An editor can only see files that are OPEN: on the first pilot the agent touched 12 files per condition and the logger saw 1 and 2. Without this, "who writes what" counts almost none of what the agent did. |
| **The codoc ledger** | `scoring/ledger-actions.py` over the collected `.codoc/` | The codoc arm's own description edits and verdicts. The tree is edited in a custom editor, so no text document changes and the interaction log records nothing — the baseline's `CLAUDE.md` edits WERE recorded, so before this the comparison counted one arm and not the other, against codoc. Seeding (bootstrap, translate) is excluded; on the pilot that was 57 of 68 events. |
| Claude Code transcript | `codoc-study/<workspace>/.claude-study/projects/`, collected at the end | Every prompt the participant wrote and every action the agent took, timestamped |
| Project snapshots | The logger extension, every 20 seconds | The whole project, replayable commit by commit on `refs/study/<code>-<workspace>`, plus the description and codoc's own state files each time they change. Taken automatically: this used to be a script somebody started by hand, and on the first pilot nobody did, in either condition |
| **The recorded session** | `replay/frames/<project>/<condition>/` | The change every participant reviews, played back in about three minutes instead of run live. The manifest carries the speed it was compressed by, and `notes.md` says which planted problems the agent produced on its own. Participants share it, so detection is comparable across them. |
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

## A note on the numbering

RQ1 and RQ2 below are **RQ1 — understanding** (can codoc help somebody build a
theory of the program) and **RQ2 — authored modification** (do the consequential
decisions pass through the person). The section headings kept the wording of an
earlier three-question design, so read the heading as a topic and the RQ tag as
the claim.

**RQ1 is now reached only through the change under review.** The one measure that
asked about the program on its own was the round of five questions before the
task, and it was dropped on 2026-08-19 for the reason written under "What they
understand afterwards". What is left under RQ1 is what somebody carried out of
reviewing a change and what they found in it.

**Open point, and it is the study lead's to settle.** The position stated for RQ1
in `what-the-data-can-support.md` is that the claim it can support is about
finding rather than about content, and that position rested on the open-book
round's score together with how long the answers took. With the round gone,
nothing measures how fast somebody finds an answer in the codebase, so RQ1 has to
be restated in terms of what a person carried out of reviewing a change. The
restatement has not been made, and that subsection carries a note saying so.

Nothing confirmatory hangs on it. `pre-registration.md` predicts two things,
detection coverage and a durable written trace, and both are measured. The word
"finding" means different things in the two documents: in the pre-registration it
is finding a problem in the change, which is detection coverage, and in
`what-the-data-can-support.md` it was finding an answer in the codebase, which is
the measure that is gone.

What is confirmatory and what is exploratory is settled in
`pre-registration.md`, and it does not follow the RQ tags. Detection coverage and
a durable written trace are the two confirmatory predictions. Record truth is
measured and reported, and it is pre-registered as predicted NOT to differ.
Everything else in this file is exploratory and has to be labelled that way when
it is reported.

## Who writes what  (serves RQ2)

| Measure | Computed from |
| --- | --- |
| Where each change originated: written in the description and handed off, typed into chat, or edited straight into the code | codoc's own change ledger has actor and mode for the codoc condition. For both conditions, the transcript gives every agent action and the interaction log gives every human edit with whether the window was focused and whether that editor was the active one. Merge on time. |
| What kind of edits people make to the description, in six categories | The description at 20-second intervals from the snapshots, diffed. In one condition that is `tree.codoc`, in the other `CLAUDE.md`. |
| Who settled each decision the task left open | Your notes, the think-aloud, and the transcript. Hand-coded. |
| What the agent wrote back into the description | codoc's ledger for one condition; for the other, a `CLAUDE.md` edit inside an agent turn in the transcript. |
| Whether decisions settled in chat survive anywhere at the end | The transcript, against the final project. Hand-coded. |

## Does the description stay true, and what does checking cost  (serves RQ2)

| Measure | Computed from |
| --- | --- |
| Does the description match the code, per feature, before and after | The starting archive and the final project, rated blind. |
| Drift: features owning nothing, code nobody describes | Rated by hand from the final project, **in both conditions**. Do not use codoc's own drift numbers. Only one condition computes them, so using them would mean the tool is rating its own work. |
| Proposals raised, accepted, rejected | codoc's change ledger. Only the codoc condition has proposals, so this is reported on its own, not as a comparison. |
| Walkthroughs asked for (`/codoc:ask`) | The interaction log's `ASK` action (a count of stops, never the question). Only the codoc condition can draw one, so like proposals it is reported within the arm, never as a between-condition number — the shared-only filter drops it automatically. The `PROMPT` that triggered the ask is shared and is counted on both sides. |
| How long people spent reviewing | Time on the description between a proposal appearing in the ledger and the verdict landing, plus dwell on the description from the interaction log. The resolution is one daemon pass (a few seconds), which is finer than the effect. Say so in the paper. |
| **Review coverage: what fraction of the changed lines did they actually look at** | The interaction log's view events give file, line range and duration. The snapshots give the changed lines at sign-off. A hunk counts as inspected if a view event covered its lines for at least two seconds before sign-off. |
| The sign-off, and what the confidence rested on | Your notes, verbatim. |
| Warranted trust: acting on the description without opening the code | The interaction log shows whether the code file was ever on screen before the action, and the transcript shows the action. Whether the claim was true is rated afterwards. |

## What they understand afterwards  (serves RQ1)

| Measure | Computed from |
| --- | --- |
| **What they carried out of the task** | Five multiple-choice questions in `answers/reflect-<condition>`, **closed book**: no code, no description, no agent. They have right answers, held in `experimenter/after-questions.json` and never shipped to the browser, so the dashboard scores them and nothing is marked by hand. They are per project and about the change the participant just made, and they run two easy, two medium and one hard, in that order. |
| Whether they were sure or reconstructing | The `recall` scale on the same page. A fluent reconstruction and a real memory read the same in prose, so it is asked directly. |
| Whether the decisions passed through them | The who-settled-what record in the dashboard, and the two after-task questions that turn on a decision the participant made rather than on the codebase alone. |

**One measure was dropped on 2026-08-19, and nothing replaces it.** How well and
how fast somebody can find an answer in this codebase was measured by five
questions asked before the task, open book and timed, with the score and the
elapsed time both landing in `answers/quiz-<project>-before`. That round is gone
from the session, so nothing writes that document any more and the measure cannot
be computed.

It was dropped because the task changed. Reviewing a change to the codebase means
working the codebase out, so the first half of the task is the same activity the
open-book round measured, and running both put the same activity on the clock
twice inside a session whose whole task budget is now twenty minutes. Nothing has
been put in its place, and a session collected before that date holds the round
while a later one does not, so the two cannot be pooled on it.

The after-task set was four boxes to type in until 2026-08. Freeform got short
answers to questions whose value is in the follow-up, at the end of two hours,
and nothing comparable between participants. The follow-up now happens out loud
in the closing interview, where it belongs.

Three of the five turn on a planted problem in the agent's session. The two ways
to get one of those right are to have found the problem or to have read the whole
change carefully. A question answerable from the project briefing would measure
reading, and somebody who shipped without looking will have neither route.

**There is one question round, and it is after the task.** It used to be the same
twelve questions before and after, with the change between them as the measure.
Both sittings asked about the codebase, and neither asked about the thing the
study is actually about, which is whether the person still owns the change their
agent helped them write. The set that replaced it asks exactly that, and it can be
multiple choice because the CONSEQUENCES of the change are fixed by the codebase
even though the change itself differs from participant to participant. The
open-book sitting survived that revision and was dropped by the next one, for the
reason above.

The comparison is **within participant, between conditions**: each person does
one project each way, so their after-task score with codoc is compared against
their own after-task score without it. There is no comparison between
participants.

## What they found in the agent's change  (serves RQ1 and RQ2)

Every participant reviews the same recorded change, which carries a decoy and as
many planted problems as the recorded session actually landed. That is three for
scribe rather than the four the design assumed, because one of them needed a
request nobody would send. The problems and their rubrics are in each project's
`STUDY.md`, and each is checked by a probe in `scoring/claims/<project>.json` that
runs the participant's final code and looks for one signal. Coverage is reported
as a proportion of that project's maximum, so the two projects can be pooled.

| Measure | Computed from |
| --- | --- |
| **Detection coverage** | Each planted problem rated 0 to 2 in the dashboard during the session, and again afterwards by a rater who does not know the condition. 0 not found, 1 found, 2 found and correctly attributed to the commitment it contradicts. The two ratings are compared rather than merged. |
| Time to the first correct detection, and coverage at fifteen minutes | The interaction log and the transcript, against the moment the participant took over. That moment is `at_ms` in `.claude-study/handover.json`, which `agent.py` writes in both conditions. `.codoc/replay.stamp` carries the same instant but only where there is a `.codoc` directory to write it into, so it is the codoc arm's copy and not the shared one. Fifteen minutes of a twenty-minute task is close to the end of it, so the number is nearly the final coverage. |
| **False alarms** | The count and the notes in the dashboard. The decoy, plus any correct part of the change the participant called wrong. A blank is a gap the dashboard names before the call ends, because none and not-asked are different answers. |
| Who settled each problem | Directed by the participant, accepted deliberately, or standing and never noticed. The merged stream and the codoc ledger. |
| **Which route they took, per problem** | Found by reading the agent's own account, or found by reviewing the change. The recorded agent mentions all three planted problems somewhere in 54 blocks and 14,235 characters of its own prose, and both conditions get that text in the scrollback. The two are different abilities and only the second is what codoc is for, so they are coded separately rather than added together. |
| Whether they found it and shipped it anyway | A distinct outcome from never finding it, which is why who-settled-it sits beside detection rather than folded into it. |
| **A durable written trace, per problem** | Whether the record they finished with says what was decided about it. In codoc that is an accept or a reject in the change ledger, or an authored change to the feature's description, in both cases after the handover stamp. In the baseline it is a line in `CLAUDE.md`. Either side may have authored the words, because the claim is that decisions persist rather than that typing happens in a particular pane. From `scoring/ledger-actions.py` and the merged stream. |

## Is the record true at the end  (serves RQ2, and it is the headline)

| Measure | Computed from |
| --- | --- |
| **Each claim true, contradicted, or missing** | `scoring/score-record-truth.py`. The probe measures what the participant's final code does, and a rater reads the final description against it, blind. The codoc description is exported to Markdown first, so a rater cannot tell the conditions apart. |
| **Does the record still work as the agent's memory** | `scoring/transfer-probe.py`, run after all the sessions. The participant's final description goes into a clean copy of the project as `CLAUDE.md`, an agent is given a further task, and the same claim probes say how many commitments survived. It concedes a world where no human reads the description and measures the difference anyway. |

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
| **Which part of the session an action belongs to** | The interaction log is one stream, so it is cut on wall clock. The task runs from `task-<condition>.startedAt` to its `finishedAt`, both stamped by the participant's page, on the same machine as the log, so they merge on time like every other source here. They are the only stamps of their kind left: the question round after the task stores answers and no clock, and the round that had one ran before the task and is gone. Everything before `startedAt` is the participant reading the two pages, and it is reported as such rather than folded into the task. |
| Where the task's own two halves divide | Nowhere in the data. The participant is asked to work out what the agent changed and then to decide what to keep, and nothing stamps the moment they move from one to the other, so an action inside the task cannot be assigned to one half. `startedAt` is also stamped when the task page opens, which is before the agent runs, so the first three minutes of the task are the participant watching it work. Any split finer than the task itself has to come from the think-aloud, coded by hand. |

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
- **How fast somebody can find an answer in this codebase.** The open-book round
  that measured it was dropped on 2026-08-19 with the task redesign, and nothing
  replaces it. The reasoning is under "What they understand afterwards" above.
- **Anything after they close the window.** Nothing measures whether the
  description would still help a month later. Item 10 on the questionnaire asks
  people to predict it rather than demonstrating it. Long-term retention is a
  different study.
- **That any workload score is high or low.** TLX has no norms for this kind of
  work, so a score of 60 on its own is meaningless. TLX is also not a ratio
  scale, so 60 is not twice the load of 30. Only the difference between the two
  conditions is interpretable, and only for the task the block names. The same
  applies to UMUX-Lite reported raw.
