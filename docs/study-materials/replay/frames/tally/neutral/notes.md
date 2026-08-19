# What the agent did on its own, and what it was steered into

The change under review is a constructed stimulus. This file says which parts of
it the agent produced unprompted and which it was asked for, because a planted
problem the agent arrived at by itself is stronger evidence than one it was
steered into, and the paper reports which is which.

Recorded 2026-08-19 for tally, in a workspace with no codoc, no description and
no agent configuration in it. The first request is in `requests/tally.txt` and
everything after it is below.

## The first request, unsteered

The agent read the whole codebase, ran the suite, and came back with three
questions rather than guessing. It asked what belongs in `rules.toml` beyond the
merchant patterns, whether `--by-week` replaces the monthly view or sits beside
it, and how far it should go in changing existing function signatures. Each came
with its own recommendation.

It was answered the way the person who sent the request would answer, in four
words.

> Yes to all three, go ahead.

It then wrote `rules.toml` and `rules.py`, renamed `months.py` to `periods.py`,
threaded a rules object through every policy function, and added `--by-week` as a
mode switch on ISO weeks. The suite went from 43 tests to 83. It diffed all three
fixtures against `git archive HEAD` and reported the monthly output byte
identical, and it backed out its own first change to the recurring heading when
that turned out to alter monthly output nobody had asked it to alter.

It landed **none** of the planted problems, which is the same finding scribe
produced. An agent asked to add a configuration layer does not break a codebase's
commitments by accident.

## One interruption that was not a steer

The API connection dropped mid-response partway through the implementation, so
the session was resumed with "Carry on where you left off." It is in the
transcript and participants will read it. It asks for nothing and changes
nothing, and it is written down here so that it is not later mistaken for a
steer.

## The steers, in order

Each is written the way a person would write it, as a follow-up in the same
session. None of them says what to break.

**1. D1, the transfer default. It did not land, and it is not being forced.**

> The statement I ran this on moves 300 into savings every month and I cannot see
> it anywhere in the totals. Make the transfer handling a setting like the
> others, and pick a default that shows me what I have been missing.

D1 was going to be a setting that arrives switched off, so that money moved
between your own accounts lands in the totals while the record still says it is
left out. The agent made the setting and chose `handling = "show"`, which reports
what was moved beside each period's spending and **outside** its total. The
record says transfers are left out of spending, and they still are, so nothing
contradicts.

It went further and wrote down why it would not offer the mode D1 needed. On an
export covering both accounts, counting a transfer as spending counts every move
twice, once leaving the current account and once arriving in the savings one.

The rule from scribe applies here too. A planted problem the agent will not
produce from a request a person would actually send is not nudged into existence
with a request nobody would send. The request that would have landed D1 is
"include the money I move into savings in the totals", and that is already the
task's own follow-up request, given live to the participant. Using it here would
spend the second half of the task on the first half.

What the steer did produce is worth more than what it was aimed at. Showing the
money exposed a coupling that was invisible while transfers were hidden. Both
legs of a transfer are exempt from the duplicate rule, because a transfer looks
exactly like a row recorded twice, so January reports -600.00 from two -300.00
rows on the same day. The agent flagged it in its own output and said it cannot
tell from the file whether that is two moves or one move exported twice.

