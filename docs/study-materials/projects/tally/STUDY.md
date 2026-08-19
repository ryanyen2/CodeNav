# tally, as a study instrument

Not shipped to participants. Matched to scribe one for one. The reasoning for
both is written out once, in `../scribe/STUDY.md`, and only what differs is
repeated here.

## The task card

> **Review what the agent did**
>
> You asked for the merchant rules to move into a file you can edit, a weekly
> view beside the monthly one, and a tidy-up of how the rules get their settings.
>
> The agent has finished and the tests pass. Decide what to keep, and ship it.

## What the recorded agent was asked for

The prompt is in `replay/requests/tally.txt`, and the participant is told they
wrote it before lunch.

## The four planted problems

Rated 0 to 2, blind to condition, on the scale in `../scribe/STUDY.md`. None of
them breaks a test.

### D1. The new default loosens a stated policy

Leaving out money moved between your own accounts becomes a setting, and the
setting arrives switched off, so the totals now include it. The description says
that money moved between your own accounts is left out.

| | |
| --- | --- |
| **2** | Names the default and says which commitment it contradicts. |
| **1** | Notices the totals moved, without connecting it to the commitment. |
| **0** | Does not raise it, or accepts the agent's account that nothing changed. |

### D2. A rule nobody asked about changed

Weeks are lined up on the date the bank posted a transaction, so which month a
transaction counts in moved from the date it was made to the date it posted. The
request did not ask for it and the summary does not mention it.

| | |
| --- | --- |
| **2** | Finds the change and says that it was not part of the request. |
| **1** | Finds the change and treats it as part of the work that was asked for. |
| **0** | Does not find it. |

### D3. A local change breaks the coupled pair

The weekly path compares rows without the merchant, because a week made the
comparison too coarse. Leaving out money moved between your own accounts and
removing repeated rows are the same two rows seen twice, so the two rules now
disagree about them.

| | |
| --- | --- |
| **2** | Finds the looser comparison and says what depends on it. |
| **1** | Finds the looser comparison without saying what depends on it. |
| **0** | Does not find it. |

### D4. The record says one thing and the code does another

Moving the merchant rules into a file made an unmatched merchant an error, so
the run now stops instead of putting the row in the uncategorised bucket. The
description still says an unmatched merchant goes to uncategorised and the run
finishes. Checked as C4.

| | |
| --- | --- |
| **2** | Finds the false claim, and corrects the record rather than only the code. |
| **1** | Finds the false claim and leaves the record as it is. |
| **0** | Does not find it. |

### D0. The decoy

The loop that tries each merchant rule in turn is replaced by a prepared ordered
mapping. It reads like a change to which rule wins and it is not one.

## The follow-up request

Given after the review, and read aloud:

> Include the money I move into savings in the totals. I want to see everything
> that left the account.

Money moved between your own accounts is two rows that look like one row
recorded twice, so counting it and removing repeats cannot both be done the naive
way. The obvious implementation runs into a commitment the record already holds.

## What else is recorded per problem

The same as scribe. Who settled it, the time to the first correct detection,
coverage at fifteen minutes, and whether the record is true at the end against
`scoring/claims/tally.json`.

## The quiz

Five questions, four options, one right, asked before the task. Matched to scribe band for band and level for
level, which `extract-questions.mjs` refuses to let drift.

Answered open book with a clock running, and the difficulty tags work the same
way as scribe's. The reasoning for both is written out once, in
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
code, the description and the agent CLOSED. Matched to scribe's set one for one,
band for band and level for level. The reasoning for both is written out once, in
`../scribe/STUDY.md`.

### Purpose: what your change actually does

**Q1. (easy) You had the merchant rules moved into a file you can edit. What does tally now do with money moved between your own accounts?**
- a) Leaves it out of the totals, as it did before
- b) **Counts it in the totals, because the new setting arrives switched off** ✓
- c) Lists it separately at the bottom of the summary
- d) Refuses to run until you say which you want

### Rationale: why that way and not the other

**Q2. (medium) You had a weekly view added beside the monthly one. What does the weekly view no longer look at when it decides two rows are the same row twice?**
- a) The date
- b) The amount
- c) **The merchant** ✓
- d) The category

**Q4. (hard) Your change leaves two rules disagreeing about the same pair of rows. Which two?**
- a) Recurring payments and refunds
- b) **Leaving out money moved between your own accounts, and removing a row recorded twice** ✓
- c) Categorising and rounding
- d) Month attribution and the sign convention

### Change: what it cost, and what it touched

**Q3. (medium) Besides the three things you had asked for, the agent changed one more rule. Which one?**
- a) The rule that decides what counts as recurring
- b) **The rule that decides which month a transaction belongs to** ✓
- c) The rule that nets refunds against a category
- d) Nothing else changed

### Extension: what a next person needs

**Q5. (hard) Someone picks this up tomorrow and wants the weekly view to agree with the monthly one again. What do they have to settle first?**
- a) Which file the weekly code lives in
- b) Whether weeks start on Monday or Sunday
- c) **Which date a transaction belongs to, because the two views answer that differently now** ✓
- d) Whether to add a sample file for it

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
