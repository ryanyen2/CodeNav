# tally, as a study instrument

Not shipped to participants. Matched to scribe one for one. The reasoning for
both is written out once, in `../scribe/STUDY.md`, and only what differs is
repeated here.

## The task, as the participant meets it

There is no task card. It was replaced on 2026-08-19, for the reason written out
in `../scribe/STUDY.md`.

The task page reads as one occasion, in this order. First, one case where tally
behaves unhelpfully, which is a supermarket missing from the merchant patterns
being counted as uncategorised, so that no month in the file gets a groceries
figure at all. Second, what they are therefore asking for, as three plain lines.
Third, the request itself in a copy block, which they paste into the agent.
Fourth, what to do while it works, and what is left to them.

## What the recorded agent was asked for

The prompt is in `replay/requests/tally.txt`, and it is word for word the request
the participant is given to paste.

## The page names the commands, from 2026-08-20

The planted problem is confirmed by RUNNING the project, and none of it is
reachable by reading the diff, so the task page names the commands to run once the
change is in. The commands are the same in both conditions and both languages, and
they are matched to scribe's as far as the two changes allow. The page names the
command that checks every sample statement, the settings file the change added, and
the command that prints one statement's summary in full, because that is where the
problem shows.

The page names the commands and says nothing about what their output should look
like. It does not say that anything is wrong, it names no line to look at, and it
asks for no report of what was found. Naming the commands raises the floor on
detection, so rate detection against that floor from this date and do not pool it
with earlier sessions without saying so. `analysis-plan.md` carries the same note.

**Which model wrote which part of the tree, from 2026-08-20.** Two models wrote the
prose a participant in the codoc arm reads, and they wrote different parts of it. The
tree that is in the workspace when the session starts was written by `codoc init` on
2026-08-17 with the gpt-5.4-mini configuration. The proposals and amendments the
recorded session produced on top of it were written by the Claude provider using
sonnet, because the OpenAI account that had paid for the seeding had no credit left on
the day the session was recorded. Only wording depends on that, since the structural
proposals and the pending edits come from the same code whichever model describes them,
but do not compare tree prose across recordings without first checking which model
wrote each part of each one.

**An internal OpenAI proxy is available again, and re-seeding with it was tried and not
adopted, on 2026-08-20.** The reasoning is written out once, under the heading about
tally's flatter nodes in `../tally/STUDY.md`, and the short version is that the newer
model wrote the thinly-commented test nodes better and the richly-commented policy
nodes worse, and regrouped the policy nodes, which are the ones the claims file and the
question sets are written against.

## The planted problem

There is one, rated 0 to 2 blind to condition on the scale in
`../scribe/STUDY.md`. No test catches it, and it is confirmed by running the
recorded code rather than by reading the agent's account of what it did.

**One problem instead of three, changed on 2026-08-20**, for the reason written
out in `../scribe/STUDY.md`. The labels D1, D2 and D3 are retired. Two of the three
things they described are still in the recorded code, because the session still
does that work, and they are listed at the bottom of this section as unscored
background so that a rater does not count them either way.

120 tests pass at the end of the recording, up from 43 at the start.
`replay/frames/tally/neutral/notes.md` records what the agent produced unsteered
and what each steer was.

### P1. The summary stops adding up, and the record still says it does

The agent was asked to move the merchant rules into a settings file and to add a
weekly view, and it did both. On the way it also changed how a period is printed.
The uncategorised bucket is no longer one of the rows under a month heading. The
money is still counted in the month's total and the number of rows that landed in
the bucket is still in the one-line summary at the end, but the row itself is not
printed, so the figures a reader can see do not add up to the total printed
underneath them.

`tally/summary.py` holds the change, in the loop that prints a period's
categories, and the agent wrote its reasoning into a comment beside it, which is
that the bucket is not a category anybody chose and so belongs in the count line
rather than among the reader's own categories. Read on its own the argument is
reasonable, which is what makes the problem worth planting.

The consequence is arithmetic a participant can do in their head.
`tally summarise fixtures/current.csv -` prints eight rows for January that come
to 1139.46 and a total of 1115.46 underneath them, so 24.00 of spending is in the
total and in no visible row. The same command run before the change printed
`uncategorised -24.00` as the sixth row and the figures agreed. The count line at
the end still says `1 uncategorised`, so the count survived and the money went
quiet.

The record still says the old thing. The `Merchant category assignment` node says
that anything unmatched goes to an uncategorised bucket so the summary still adds
up and the gap is visible instead of silent, and the sentence is now false in both
of its halves. In the codoc arm the agent's proposed rewording of the
`Monthly spending summary` node is pending at the review stop, and it states the
new behaviour plainly, so the arm shows a node claiming the summary adds up and a
proposal explaining why the bucket is not among the rows, at the same moment and
one screen apart. In the baseline arm the record-updating pass brought CLAUDE.md
in line with the code, so the baseline document describes the new behaviour and no
longer contains the contradiction.

Checked as C4.

| | |
| --- | --- |
| **2** | Says the printed rows no longer add up to the printed total, and names the commitment it breaks, which is that unmatched money stays visible in the summary. |
| **1** | Notices that the uncategorised row is gone, or that a figure is missing, and treats it as a formatting choice. |
| **0** | Does not raise it, or raises it and then accepts the comment's account that the count line covers it. |

### D0. The decoy

The loop that tries each merchant rule in turn is replaced by a prepared ordered
mapping. It reads like a change to which rule wins and it is not one. Flagging it
counts as a false alarm, and so does flagging any other correct part of the change.

### Other arguable parts of the change, which are not scored

The session still adds a weekly view and still moves the rules into a file, so two
further things in the recorded code are open to argument. Neither is scored. Rate a
participant who raises either as neither a hit nor a false alarm, and write it in
the free text, because a participant who finds one of these has read the change
carefully whatever the detection score says.

First, `week = "posted"` in `rules.toml` is read, validated against the list of
allowed values, and then never used. `summary.py` passes the month's date setting
to the labelling function whichever summary is being built, so a payment made on
the 31st of January and posted on the 2nd of February is labelled `2026-W05`, which
is the week it was made in. The settings file explains the choice at length in a
comment and the choice has no effect, which is checked as C6.

Second, the weekly view compares rows for duplicates without the merchant, so a
coffee and a pastry both at 3.40 on one day come out as one row weekly and two rows
monthly. The description says a row recorded twice is counted once, matched on its
date, its amount and its merchant, and does not say which summary it means. Checked
as C2.

**codoc cannot state a value that lives in a config file.** `_INCLUDED_PATTERNS`
in `codoc/pipelines/indexing/cocoindex_app.py` is `.py` and `.ts` only, so
`rules.toml` is never chunked and no Loop A pass can read `month = "made"` out of
it. Anything the study needs the record to state has to live in Python, which is
one reason the planted problem above is a change to a printing loop rather than a
change to a setting.


## The follow-up request, which is no longer given

**Dropped on 2026-08-19, with scribe's, and for the same reason: one request per
condition, and twenty minutes for the task.** It is written down here because it
was part of the instrument. It used to be read aloud after the review:

> Include the money I move into savings in the totals. I want to see everything
> that left the account.

Money moved between your own accounts is two rows that look like one row recorded
twice, so counting it and removing repeats cannot both be done the naive way. The
obvious implementation ran into a commitment the record already holds, and whether
the participant noticed was recorded. Nothing measures that now. The coupled
problem in the planted set is D2, and it is what is left of the idea.

## What else is recorded per problem

The same as scribe. Who settled it, the time to the first correct detection,
coverage at fifteen minutes, and whether the record is true at the end against
`scoring/claims/tally.json`.

## The quiz

**No longer asked in a session. Dropped on 2026-08-19, with scribe's, and for the
same reason.** The round is off the participant's page, nothing writes
`answers/quiz-tally-before` any more, and the measure it fed is gone with no
replacement. `analysis-plan.md` records that, and `../scribe/STUDY.md` gives the
reasoning.

The questions are kept and this heading has to stay exactly as it is, because
`scoring/check-description-answers.py` uses them as a smoke test on the
descriptions and `study-app/scripts/extract-questions.mjs` reads the section. What
follows describes the round as it was run.

They were five questions, four options, one right. They are matched to scribe band
for band and level for level, which `extract-questions.mjs` refuses to let drift.

They were answered open book with a clock running, and the difficulty tags work the
same way as scribe's. The reasoning for both is written out once, in
`../scribe/STUDY.md`, rather than kept in two copies that can disagree.

**Every wrong option is something tally could reasonably have done and did not,
and the correct answer is usually the less obvious of the two.** A question whose
correct answer is simply the more sensible one can be answered without reading
anything. Check with:

```
python3 ../../scoring/check-description-answers.py --blind tally
python3 ../../scoring/check-description-answers.py <a tally workspace> tally
```

Measured 2026-08-17 with gpt-5.6-luna:

| Run | Correct | Grounded in the text |
| --- | --- | --- |
| Blind, no description at all | 10/12 | — |
| From the codoc tree, written by `codoc init` | 11/12 | 11/12 |

Ten blind is a point worse than scribe's nine, which is the same open problem and
slightly larger. tally is the project to rewrite first if the pilots show the
questions are not separating the arms.

### Purpose: what it is for, and where it stops

**Q1. (easy) You bought the same £3 coffee twice on the same day at the same shop. What does the summary show?**
- a) **One, because the two rows look identical and one is treated as a duplicate** ✓
- b) Both, because they are two separate purchases
- c) Both, with the second one flagged as a possible duplicate
- d) Neither, because duplicates are removed entirely

### Rationale: which way it went, and why

**Q2. (easy) You move £300 from your current account to your savings account. How does tally treat this?**
- a) **Leaves it out, because it is a transfer, not spending** ✓
- b) Counts it as spending in a transfers category
- c) Counts it as spending, because it left the current account
- d) Asks you whether to include it

**Q4. (medium) A shop name does not match anything on the list. What happens to that payment?**
- a) **It goes under uncategorised** ✓
- b) It is left out of the summary
- c) Tally stops and reports an error
- d) It is put in the category closest to its name

### Change: what happened, and what it cost

**Q3. (medium) You make a payment on the last day of January. The bank processes it on the first day of February. Which month does the summary put it in?**
- a) **January, because tally uses the date you made the payment** ✓
- b) February, because tally uses the date the bank processed it
- c) Both months, split equally
- d) Whichever month the bank says

### Extension: what a further change would need

**Q5. (hard) The list of shop names is written into the code. What is the problem with that?**
- a) **You have to change the code to add a new shop** ✓
- b) The list cannot be shared with other people
- c) The list is too slow to search
- d) The list cannot handle shops with similar names

## The after-task questions

Five questions, four options, one right, asked straight after the task with the
code, the description and the agent closed. The first two are answerable by
anybody who opened the change at all. The next two need the participant to know
what the edits were and which way a decision went, and the last one asks what the
change causes somewhere else in the program. Matched to scribe's set one for one,
band for band and level for level, and the reasoning for both is written out once
in `../scribe/STUDY.md`.

### Purpose: what your change actually does

**Q1. (easy) Your change added a weekly view. What does one week show?**
- a) **A breakdown by category and a total, the same as a month gets** ✓
- b) Only a total, with no breakdown
- c) One line for each transaction
- d) The difference from the week before

### Extension: what a next person needs

**Q2. (easy) Where does a colleague add a rule for a new shop now?**
- a) **In the settings file, which is where the merchant rules now live** ✓
- b) In the code, in the same list as before
- c) On the command line, on every run
- d) In the bank export file itself

### Rationale: why that way and not the other

**Q3. (medium) A shop on the statement matches no rule in the settings file. What does the summary do with that payment, as the project is set up now?**
- a) **Counts it in the month's total and in the count line at the end, without printing it in any category row** ✓
- b) Prints it as an uncategorised row alongside the other categories
- c) Leaves it out of the summary and out of the total
- d) Stops the run and lists the shop it could not match

### Change: what it cost, and what it touched

**Q4. (medium) You already have a monthly summary written out for a statement, and you run the same command again with `--by-week`. What happens to the monthly file?**
- a) **It stays where it is, and the weekly summary is written beside it under a different name** ✓
- b) It is overwritten, because both summaries are written to the same file
- c) It is deleted, because a weekly summary replaces a monthly one
- d) Nothing is written at all, because `--by-week` only prints to the screen

**Q5. (hard) You add up the category rows printed under a month heading, and you compare your figure with the total printed underneath them. What do you find?**
- a) **The two disagree, by exactly the amount that went to shops matching no rule** ✓
- b) They agree, because every row under the heading is counted in the total
- c) They disagree, because transfers are in the total but have no row of their own
- d) They disagree by a penny or two, because each row is rounded before the total is added up


## Some of tally's tree nodes read flatter than the rest, and are being left alone

Nine nodes in tally's feature tree read as though a language model wrote them, which
one did, and so did every other node in the tree. The nine are "Checking statements",
"Rule contract test suite" with its five sub-nodes, "End-to-end statement summarization
checks" and "Package identity metadata". They sit next to nodes like "Transfer-aware
duplicate filtering" that read in a person's voice, and the difference between the two
groups is not which model wrote them, because one pass wrote all of them. The policy
modules carry docstrings and comments that say why a rule went the way it did, so the
seeding pass had something to report and reported it. The test files carry almost no
prose, so the same pass had the code alone and wrote from the code, which is what flat
prose about tests looks like. scribe's tree does not have as sharp a split, so the two
projects are not quite the same instrument on the one surface the codoc condition is
about.

**What an earlier version of this section claimed, and why it was wrong.** It said the
tree could not be re-seeded because the claims file, the after-task questions and both
sets of frames are keyed to feature ids. They are not. `scoring/claims/tally.json`
matches a claim to the record by its wording and by the keywords in its
`find_in_record` list, and neither STUDY.md nor either question set names an id
anywhere. The ids that really are written down are three, which are two in
`replay/script/tally/session.json` and one in `replay/test_agent.py`. So a re-seed costs
those three lines, a re-derive of the codoc arm, and a `check`, and it does not cost the
scoring material at all.

**Re-seeding was tried on 2026-08-20 with a newer model, and not adopted.** tally was
seeded again in a scratch copy with gpt-5.6-luna through the internal OpenAI proxy, with
the shared writing guide now included in codoc's prompts. The test nodes came out
better, and they name their thresholds, for example that three months is the threshold
for calling a payment recurring and that the amount has to stay the same. The policy
nodes came out worse. Where the current tree says that an unreadable row is skipped
rather than guessed at, the new one says the resulting amounts and dates must be valid
before a row enters the policy pipeline, which is longer, more abstract and no more
informative.

The re-seeded tree also grouped the policies differently. Refund netting was folded into
the month node, and the two money decisions that currently sit together under "Money
handling policies" were split and sent to different parents. Both groupings are
defensible, and neither is the grouping the claims file and the two question sets were
written against, so adopting the new tree means re-checking every claim and every
question against a tree that changed shape for no gain on the nodes those items are
about. That is a bad trade for a fix to nine nodes that say nothing untrue and that the
planted problem does not touch, so the current tree stays.

**The option that is still open, and what it would cost.** An experimenter could rewrite
those nine descriptions by hand. Editing a description keeps every feature id, so the
cost is a re-derive of the codoc arm and a `check`, and nothing in the scoring material
moves. What it costs instead is the claim that the codoc arm shows what codoc produced,
because part of what a participant read would be the experimenter's own writing. Doing
it would have to be declared here, in `pre-registration.md`, and in scribe's tree as
well, or the two projects stop matching in the other direction. It has not been done.

Until one of those is chosen, treat the difference in tree prose as a known limitation,
and do not read a difference between the two projects on the description items as being
about the tool.

## Where it does not match scribe, and by how much

| | scribe | tally |
| --- | --- | --- |
| Files | 9 | 9 |
| Lines | 633 | 478 |
| Lines of code, excluding blanks and prose | 277 | 223 |
| Functions | 28 | 21 |
| Policies | 9 | 9 |
| Tests | 54 | 43 |
| Sample inputs | 3 | 3 |

tally is about a fifth smaller. The policy count, file count, and task shape all
match, and these are the things the design depends on. The size difference is
recorded here rather than hidden, and is small enough that counterbalancing
project order should absorb it. If a pilot shows otherwise, tally is the one to
expand.
