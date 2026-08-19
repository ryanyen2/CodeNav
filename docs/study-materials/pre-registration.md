# Pre-registration

Written 2026-08-19, before the first participant of the redesigned study and
after one pilot. The pilot's data is not included in anything below and will not
be pooled with what comes next, because the task it used has been replaced.

Everything here is frozen. What is not written here is exploratory, and the paper
has to say so when it reports it. Read this with `analysis-plan.md`, which says
where each measure comes from, and with
`docs/plans/2026-08-19-001-task-redesign-v2-reviewing-an-agent-session.md`, which
is the design.

## What the study asks

The claim under test is that codoc is where a person decides and reviews a change
an agent made, and that the record they finish with stays true of the code. The
claim is about finding and about being made to decide. It is not that codoc helps
somebody read code, and it is not that codoc produces better code.

Three questions are confirmatory.

First, does codoc raise how much of an agent's change a reviewer finds? Second,
does codoc leave more of what was decided written down in a form that survives
the session? Record truth is measured too, as a pair of before and after values,
for the reason given under the predictions.

## Design

Twelve participants, each doing both conditions, with the order counterbalanced
and the two projects alternating so nobody meets the same task twice. Every
participant reviews the same recorded agent session for a given project, so the
change under review is identical across people and across conditions.

Recruitment stops at twelve. There is no optional stopping and no interim look at
the outcome measures.

A participant is excluded if the replay fails to complete, if the logger records
no events for a condition, or if they tell us during the session that they have
used codoc before. Exclusions are reported with their reason and the analysis is
reported both with and without them.

## The measures, and where each comes from

**Detection coverage.** Each project plants a small number of problems in the
recorded change, listed with their rubrics in that project's `STUDY.md`. Each is
rated 0, 1 or 2, blind to condition, where 0 is not found, 1 is found, and 2 is
found and correctly attributed to the commitment it contradicts. Coverage is the
sum over that project's problems, reported as a proportion of the maximum,
because the two projects do not plant the same number.

**A durable written trace.** For each planted problem, whether the record the
participant finished with says what was decided about it. In codoc that is an
accept or a reject in the change ledger, or an authored change to the feature's
description, in both cases after the handover stamp the player writes. In the
baseline it is a line in `CLAUDE.md`. Either side may have authored the words.
The evidence comes from `scoring/ledger-actions.py` and from the merged event
stream.

**Record truth.** Each project has a fixed list of claims that can be checked by
running the participant's final code, in `scoring/claims/<project>.json`. Each
claim is rated true, contradicted, or missing, blind to condition, against the
participant's final record. The codoc record is exported to Markdown first, so a
rater cannot tell the conditions apart by their formatting.

**False alarms.** The decoy plus any correct change the participant flagged as
wrong. A surface that makes everything look suspicious is not an improvement, and
a difference in detection cannot be reported without this beside it.

## What is predicted, and what counts as support

**Detection coverage is predicted to be higher with codoc.** The criterion is a
sign test on the paired difference. Support means at least 7 of the 12
participants have strictly higher coverage in their codoc condition than in their
own baseline condition. The paired mean difference is reported as the estimate,
with a 95% bootstrap confidence interval, and it is the estimate rather than the
test that the paper leads with.

**A durable written trace is predicted to be more common with codoc.** The
criterion is the same sign test, applied to the count of planted problems that
have a trace. Support means at least 7 of the 12 participants have a higher count
in codoc than in their own baseline. Chat that ends with the decision landing in
the record counts as a trace, because the claim is that decisions persist rather
than that typing happens in a particular pane.

**Record truth is predicted to favour the baseline at handover, and is not
interpretable as a final value.** Deriving both projects through both conditions
showed that the two do not start the review from the same place. Both baselines
end the recording with a true record, because the maintenance skill rewrites the
commitment to match what the agent did and argues for it. codoc ended the two
projects differently: on scribe its tree kept the sentences the change
contradicts and raised seven proposals, and on tally it amended to match and
raised two. The cause of that difference was not identified, and this study does
not claim one.

So record truth is measured twice, at handover and at the end, and reported as
the change between them, with the handover state given per project because it
differs per project. A raw final-value comparison favouring the baseline is
expected and is not evidence about either tool. The reasoning is in
`what-the-data-can-support.md`, and the short version is that a record which is
true because an agent quietly rewrote a person's stated intent to match its own
work is not the outcome this study wants to reward.


**False alarms are predicted not to differ.** A codoc advantage in detection
paired with a higher false alarm count is reported as a wash rather than as
support.

## What the study cannot support, whatever comes back

Codoc producing better code is out of reach, because both conditions were
calibrated to near ceiling and the tests pass at the end by design. Speed is out
of reach in either direction, because the baseline's maintenance skill spends
agent turns by our own design and the codoc condition has affordances the
baseline does not. Nothing can be attributed to one mechanism, because bindings,
verdicts, search and `/codoc:ask` all move together at twelve participants.
Nothing can be said about the week scale, because this is one session.

## What is exploratory

Time to the first correct detection, coverage at fifteen minutes, who settled
each problem, the strategy codes for what a participant read and in what order,
the closed-book quiz, the questionnaire, the agent turn overhead, and the offline
transfer probe. All of them are reported. None of them is a test of the claim,
and a result in any of them is a question for the next study rather than an
answer from this one.

## Analysis

The estimate is the headline. Every outcome is a paired difference of codoc minus
baseline, one value per participant, reported as a mean with a 95% bootstrap
confidence interval and plotted. A p-value from a dozen pairs invites a binary
reading the data cannot carry, while the interval says the same thing and shows
how little it pins down.

Where a test is wanted, Wilcoxon signed-rank with matched-pairs rank-biserial
effect sizes, reported with the interval rather than instead of it, and exact
McNemar for the per-problem binaries. Individual questionnaire items are not
given a metric model and their distributions are shown per item. No reliability
coefficient is reported for a block of three to five items written for this
study.

The qualitative half is reflexive thematic analysis for the interviews and
protocol analysis for the think-aloud, with two coders on a quarter of the
material, consensus, and one coder finishing.

## Two things the design does that a reader has to be told

The change under review is a constructed stimulus. An agent asked to add a
configuration layer is careful by default and lands none of the planted problems
on its own, so the recorded session was steered until it did, and every steer is
written down beside the frames. What is never authored is codoc's own response.
Every recorded file under `.codoc/` was written by a real daemon, and if the
daemon failed to surface a planted problem then participants see it fail and the
paper reports that.

The recorded agent discloses every planted problem somewhere in its own output.
Capable agents narrate, so a recording of a quieter one would be a strawman. The
study therefore asks whether a person ends up knowing what was decided, not
whether the information was available, and the strategy coding separates a
problem found by reading the account from one found by reviewing the change.

The replay is played back faster than the session ran. The factor is in the
manifest and is reported, and the lag between an edit and the tree reacting to it
survives playback in proportion rather than being removed.

## Deviations

Any departure from this document is listed here with its date and its reason,
before the affected analysis is run.

- (none yet)
