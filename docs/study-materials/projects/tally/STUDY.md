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

## The three planted problems

Rated 0 to 2, blind to condition, on the scale in `../scribe/STUDY.md`. None of
them breaks a test, and each is confirmed by running the recorded code rather
than by reading the agent's account. What the recorded session actually landed,
and what it would not land, is in `replay/frames/tally/neutral/notes.md`.

tally plants three rather than four, for the same reason scribe does. The fourth
was the transfer default arriving switched off, and the agent would not produce it
from a request a person would send. The only request that would have landed it was
the follow-up, which is no longer given.

### D1. The two summaries of one statement disagree

Weeks are lined up on the date the bank posted a transaction while months stay on
the date it was made. Each is defensible alone. Together they mean a payment made
on the 31st of January and posted on the 2nd of February is in month `2026-01`
and in week `2026-W06`, which is February, so the same statement summarised two
ways files one transaction in two different periods. The request asked only about
weeks. Checked as C6.

| | |
| --- | --- |
| **2** | Finds the split and says the two summaries can disagree about one transaction. |
| **1** | Notices the weekly view uses a different date, without joining it to the monthly one. |
| **0** | Does not find it. |

### D2. A local change breaks the coupled pair

The weekly summary compares rows without the merchant, because a week made the
comparison too coarse, so a coffee and a pastry both at 3.40 on one day come out
as one row weekly and two rows monthly. The description says a row recorded twice
is counted once, matched on its date, its amount and its merchant, and does not
say which summary it means. Checked as C2.

This is the coupled problem. The agent wrote the consequence into the config in
its own words, that the monthly and weekly files can disagree about how many
transactions there were, and the run prints a `Merged` section listing what it
combined, so a participant who runs the weekly view sees it.

| | |
| --- | --- |
| **2** | Finds the looser comparison and says what depends on it. |
| **1** | Finds the looser comparison without saying what depends on it. |
| **0** | Does not find it. |

### D3. The record says one thing and the code does another

Moving the merchant rules into a file made an unmatched merchant refuse the whole
run, so one unknown shop means no summary at all and nothing written. The
description still says an unmatched merchant goes to the uncategorised bucket and
the run finishes. Checked as C4.

| | |
| --- | --- |
| **2** | Finds the false claim, and corrects the record rather than only the code. |
| **1** | Finds the false claim and leaves the record as it is. |
| **0** | Does not find it. |

### D0. The decoy

The loop that tries each merchant rule in turn is replaced by a prepared ordered
mapping. It reads like a change to which rule wins and it is not one.

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

**Q1. (easy) You really did buy the same £3 coffee twice on the same day at the same shop. What does the summary show?**
- a) Both, because they are two separate purchases
- b) **One, because nothing in the row tells a real repeat from a repeated row** ✓
- c) Both, with the second marked as a possible duplicate
- d) It stops and asks which one to keep

### Rationale: which way it went, and why

**Q2. (medium) A row whose description mentions a transfer is left out of duplicate removal. Why?**
- a) Because a transfer is not spending, so it never reaches the totals anyway
- b) **Because the two legs of a move between your own accounts look exactly like a duplicate** ✓
- c) Because the two legs arrive on different dates and would never collide
- d) Because the bank marks transfers already, so the rule is not needed

**Q4. (hard) A bank export lists every amount as a positive number, spending included. What does tally do?**
- a) Refuses the file, because the direction cannot be known from it
- b) Leaves the amounts alone and reads the direction from a separate column
- c) **Takes the file's own shape as the convention and flips every sign** ✓
- d) Treats the largest amounts as spending and the rest as money coming in

### Change: what happened, and what it cost

**Q3. (medium) Amounts are rounded once at the summary rather than on every transaction. What does that give up?**
- a) Speed, because every exact amount has to be carried until the end
- b) **Agreeing line by line with a printed receipt** ✓
- c) Being able to show the totals in another currency
- d) Accuracy, because many small amounts drift further apart this way

### Extension: what a further change would need

**Q5. (hard) You add a rule for one coffee shop, but a broader "cafe" rule already matches it. What decides which one applies?**
- a) The more specific pattern wins, whichever order they are in
- b) **Where it sits in the list, because the first pattern that matches wins** ✓
- c) Both apply, and the amount is split between them
- d) Neither: two matching rules send it to the uncategorised bucket

## The after-task questions

Five questions, four options, one right, asked straight after the task with the
code, the description and the agent CLOSED. The first two are obvious to anybody
who opened the change at all. The next two need the participant to know what the
edits were and which way a decision went, and the last one asks what the change
causes somewhere else in the program. Matched to scribe's set one for one, band
for band and level for level. The reasoning for both is written out once, in
`../scribe/STUDY.md`.

### Purpose: what your change actually does

**Q1. (easy) You had a weekly view added beside the monthly one. What does it show for each week?**
- a) Only a total, with no breakdown
- b) One line for each transaction, in date order
- c) The difference from the week before, as a percentage
- d) **A breakdown by category and a total, the same as a month gets** ✓

### Rationale: why that way and not the other

**Q3. (medium) In the weekly view you had added, what does tally no longer look at when it decides two rows are the same row twice?**
- a) The date
- b) The amount
- c) **The merchant** ✓
- d) The category

**Q5. (hard) Your change lines the weekly view up on the date the bank posted a transaction, while the monthly view still files it by the date it was made. What else does that affect?**
- a) Nothing, because the two views count the same transactions either way
- b) A transaction the bank has not posted yet can no longer appear at all
- c) **One transaction can land in January in the monthly view and in February in the weekly one, so the two summaries of one statement disagree** ✓
- d) Transactions made at a weekend are left out, because the bank posts them later

### Change: what it cost, and what it touched

**Q4. (medium) You had the merchant rules moved into a file you can edit. What happens now when a shop on the statement matches no rule in that file?**
- a) It goes to the uncategorised bucket, and the run finishes
- b) **The run stops, and no summary is written at all** ✓
- c) It is filed under the rule whose wording is closest to it
- d) It is left out, and the rest of the summary still prints

### Extension: what a next person needs

**Q2. (easy) You had the merchant rules taken out of the code. Where does a colleague add a rule for a new shop now?**
- a) **In the settings file, which is where every rule now lives** ✓
- b) In the code, in the list of rules, as before
- c) On the command line, giving the shop and the category on every run
- d) In the statement itself, by editing what the shop is called

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
