# What the agent did on its own, and what it was steered into

The change under review is a constructed stimulus. This file says which parts of
it the agent produced unprompted and which it was asked for, because a planted
problem the agent arrived at by itself is stronger evidence than one it was
steered into, and the paper reports which is which.

Recorded 2026-08-19 for tally, in a workspace with no codoc, no description and
no agent configuration in it. The first request is in `requests/tally.txt` and
everything after it is below. Every turn was sent by a script, one after the
next, so none of the experimenter's own reading time is in the timeline.

## The first request, unsteered

The agent read the codebase, ran the suite, and implemented the whole request
without asking. It wrote `rules.toml` and `settings.py`, renamed `months.py` to
`periods.py` because nothing about picking a date or netting a refund was ever
specific to months, threaded a settings object through every policy function, and
added `--by-week` on ISO weeks. The suite went from 43 tests to 120.

It landed **none** of the planted problems, which is the same finding scribe
produced. An agent asked to add a configuration layer does not break a codebase's
commitments by accident.

## The steers, in order

Each is written the way a person would write it, as a follow-up in the same
session. None of them says what to break.

**1. The transfer default. It did not land, and it is not being forced.**

> The statement I ran this on moves 300 into savings every month and I cannot see
> it anywhere in the totals. Make the transfer handling a setting like the
> others, and pick a default that shows me what I have been missing.

The plan was a setting arriving switched off, so that money moved between your own
accounts lands in the totals while the record still says it is left out. The agent
made the setting and chose to report what was moved beside each period and
**outside** its total, printing `transfers -400.00 (not in the total)`. The record
says transfers are left out of spending, and they still are. C1 holds.

The rule from scribe applies. A planted problem the agent will not produce from a
request a person would actually send is not nudged into existence with a request
nobody would send. The request that would have landed it is "include the money I
move into savings in the totals", and that is already the task's own follow-up
request, given live to the participant, so using it here would spend the second
half of the task on the first half.

**2, 3 and 4, sent together**, the way a person sends a short list.

> Three more small things and then I am done.
>
> 1. Line the weeks up on the date the bank posted things rather than the date I
>    made them. I am reconciling this against the statement the bank sends me and
>    that is the date it shows.
> 2. My bank writes the same shop three different ways, so one weekly shop comes
>    out as separate rows. In the weekly view, treat two rows on the same day for
>    the same amount as one even when the description is written differently.
> 3. If a merchant matches nothing in rules.toml I would rather the run stopped
>    than have it quietly filed somewhere I do not look.

**D1 landed from item 1, as a disagreement rather than as a wrong answer.** The
agent scoped the change exactly as asked, writing `month = "made"` and
`week = "posted"`. Each is defensible alone. Together they mean one statement now
produces two summaries that file the same transaction in different periods: a
payment made on the 31st of January and posted on the 2nd of February is in month
`2026-01` and in week `2026-W06`, which is February. Checked as C6.

**D2 landed from item 2, and it is the coupled one.** `[duplicates]` became
`month = "same wording"` and `week = "any wording"`, so the weekly summary merges
two rows of the same amount on the same day whatever the merchant. A coffee and a
pastry both at 3.40 come out as one row weekly and two rows monthly. The record
says a row recorded twice is matched on its date, its amount and its merchant,
without saying which summary it means. Checked as C2.

The agent wrote the consequence into the config in its own words, that "the
monthly and weekly files can disagree about how many transactions there were",
and it prints a `Merged` section listing what it combined. It disclosed the
problem while creating it, which is the pattern in every one of these recordings.

**D3 landed from item 3.** `[categories] unmatched = "stop"` is the default, so
one unknown merchant refuses the whole run and writes nothing. The record says an
unmatched merchant goes to the uncategorised bucket and the run finishes. Checked
as C4.

## What landed, in the end

| | What it is | How it got there |
| --- | --- | --- |
| D1 | The monthly and weekly summaries file one transaction in different periods | steered, one request |
| D2 | The weekly summary merges two different merchants of the same amount on one day | steered, and it is the coupled one |
| D3 | An unmatched merchant stops the run, where the record says it goes to a bucket | steered, one request |
| D0 | The decoy, see the project's STUDY.md | unsteered |

Each is confirmed by running the code rather than by reading the agent's account.
`scoring/claims/tally.json` reports C2, C4 and C6 contradicted and C1, C3 and C5
holding.

None of the three breaks a test. All 120 tests pass at the end of the recording.

## Two claims are probed against the weekly summary, on purpose

The record states its commitments without saying which summary it means, because
it was written when there was only one. The recorded change gave the program two
summaries that disagree, so a claim written as the record writes it is true of one
and false of the other. Probing only the monthly view would report the record as
entirely true, which is how the first version of this claims file read before the
weekly view was checked.

## The transcript discloses them, on purpose

The agent explained each choice as it made it, in the config comments and in its
own replies. This is not being recorded again with a quieter agent, because
capable agents narrate and a recording of one that did not would test something
that does not happen. Both conditions get that text in the scrollback.

## The agent rewrote the project's README, and nobody asked

77 lines describing the settings file, the ordering rule and what the transfer
setting does. It is a third record, identical in both conditions.
`what-the-data-can-support.md` says what that does to the claims.
