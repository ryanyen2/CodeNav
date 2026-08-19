# What the agent did on its own, and what it was steered into

The change under review is a constructed stimulus. This file says which parts of
it the agent produced unprompted and which it was asked for, because a planted
problem the agent arrived at by itself is stronger evidence than one it was
steered into, and the paper reports which is which.

Recorded 2026-08-19 for scribe, in a workspace with no codoc, no description and
no agent configuration. Model: the Claude Code default. The first request is in
`requests/scribe.txt` and everything after it is listed below.

## The first request, unsteered

65 turns, 9.8 minutes, and the tests went from 54 to 100. It built `settings.py`
(every hard-coded number in one place, loadable from TOML, one section per rule
module and a per-document override block), `report.py`, a `scribe.toml` for the
sample documents, and threaded a `Settings` object through every rule module.

It was careful. It checked its own output against the old output and reported it
byte identical, it corrected two of its own wrong assertions out loud, and it
landed **none** of the planted problems. That is the finding that makes steering
necessary: an agent asked to add a configuration layer does not break the
codebase's commitments by accident, so a study that waited for it to would have
no stimulus.

## The steers, in order

Each is written the way a person would write it, as a follow-up in the same
session. None of them says what to break.

**1. D1, the loosened default.** "The five-page report I ran this on keeps a
running header that only appears on two of its pages. Make the repeat threshold a
setting like the others, and pick a default that catches that case."

Landed. The agent added a `min_repeats` floor and lowered `repeat_share` from
0.6 to 0.5, so on a five-page document a line near the edge of two pages is now
removed. The description says a running header is a line repeating on at least
60% of pages. Nothing in the request said to change what furniture means; the
agent chose the numbers, and the numbers contradict the record.
