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
| Claude Code transcript | `~/.claude/projects/`, collected at the end | Every prompt the participant wrote and every action the agent took, timestamped |
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
| Drift: features owning nothing, code nobody describes | Rated by hand from the final project, **in both conditions**. Do not use codoc's own drift numbers for this. Only one condition computes them, so using them would be the tool marking its own work. |
| Proposals raised, accepted, rejected | codoc's change ledger. This one is codoc-only by nature and is reported as such, not as a comparison. |
| How long people spent reviewing | Time on the description between a proposal appearing in the ledger and the verdict landing, plus dwell on the description from the interaction log. Resolution is one daemon pass, a few seconds, which is finer than the effect but say so. |
| **Review coverage: what fraction of the changed lines did they actually look at** | The interaction log's view events give file, line range and duration. The snapshots give the changed lines at sign-off. A hunk counts as inspected if a view event covered its lines for at least two seconds before sign-off. |
| The sign-off, and what the confidence rested on | Your notes, verbatim. |
| Warranted trust: acting on the description without opening the code | The interaction log shows whether the code file was ever on screen before the action, and the transcript shows the action. Whether the claim was true is rated afterwards. |

## Research question 3: what they understand afterwards

| Measure | Computed from |
| --- | --- |
| The ten questions, closed book then open book | Your notes and the scoring tables in the question sheets. |
| Whether they can explain the rule the task turned on | Question 7, against what their code actually does. |
| Whether the decisions passed through them | Question 8 and question 10, plus who settled what. |

## Did the work get done

| Measure | Computed from |
| --- | --- |
| The three things being scored, and regressions | `scoring/check-hearth.py` or `scoring/check-ember.py`, with the settings file you wrote for that participant. |
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

That means the session checker CAN see them. Export the participant with
`scripts/export-session.mjs`, put the file beside the collected folder, and the
checker compares the two halves and reports what is missing from either.

Consent is the exception. It stays in the Google form, and no part of this
touches it.

## What we cannot measure, and are not going to claim

- **Which terminal had focus.** VS Code does not expose it. Agent turns are timed
  from the transcript instead, which is exact for when a prompt was sent and when
  the agent acted, but does not show someone reading terminal output. Where a
  measure needs "was the participant attending to the agent", the screen
  recording is the source, not the log.
- **Reading without scrolling.** A file open in a pane counts as viewed whether
  or not their eyes were on it. Overstates review coverage in both conditions
  equally, so the comparison stands and the absolute number does not.
- **Anything after they close the window.** Nothing measures whether the
  description would still help a month later, and item 10 on the questionnaire
  asks people to predict it rather than showing it. That is the deployment study,
  not this one.
