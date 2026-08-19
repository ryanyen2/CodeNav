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

