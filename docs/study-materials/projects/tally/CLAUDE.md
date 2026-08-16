# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

No `.venv` is checked in. Create one before using the console script:

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
```

```bash
.venv/bin/tally summarise fixtures/current.csv   # writes current.md beside the input
.venv/bin/tally summarise fixtures/current.csv - # writes to stdout instead
.venv/bin/tally check fixtures/                  # summarise every *.csv, write nothing
python3 -m tally.cli summarise fixtures/current.csv -   # same, without installing
```

Tests (stdlib-only package, so a system `pytest` works too):

```bash
python3 -m pytest tests/ -q
python3 -m pytest tests/test_rules.py -q                          # one file
python3 -m pytest tests/test_rules.py::test_the_first_matching_rule_wins -q   # one test
python3 -m pytest tests/ -q -k transfer                           # by name
```

There is no linter or formatter configured.

## Architecture

One pipeline, `summarise(raw: str) -> Summary` in `tally/summary.py`. Everything
else is either the parser or a single-policy module it calls.

**`rows.py` parses and never decides.** It matches the bank's header loosely
against `COLUMNS`, coerces dates and amounts, and emits `Row` objects. Every rule
downstream reads `Row`, never the bank's raw text. Unreadable rows are skipped
rather than guessed at; a file with no amount or no transaction-date column reads
as zero rows rather than raising. If you find yourself adding a judgement call
here, it belongs in a policy module instead.

**The policy modules each own one decision**, and each docstring names the
alternative that was rejected and why. Keep that convention when adding rules —
it is how the tradeoffs stay reviewable.

| module | policy |
| --- | --- |
| `money.py` | rounding (once, at the total) and sign convention |
| `dedupe.py` | what is a duplicate, what is an own-account transfer |
| `categories.py` | merchant description to category |
| `months.py` | which month a row belongs to, how refunds net |
| `recurring.py` | which payments are fixed commitments |

### The step order in `summarise()` is load-bearing

Four coupling constraints, all of them easy to break by reordering:

1. `money.sign_convention` runs first and **mutates rows in place** — it flips
   every sign when >80% of amounts are positive. Every rule below reads signs, so
   nothing may run before it.
2. `dedupe.drop_duplicates` runs before transfers are filtered out, and exempts
   transfers internally. Both legs of an own-account transfer are the same date,
   amount and description — the exact shape of a duplicate. Drop one and the
   money looks like it went somewhere it did not.
3. Transfers are removed from `spending` before recurring and categorisation, so
   moving savings around never reads as spending.
4. Categories are assigned before `months.net` sums them, because a refund nets
   against the category it came from.

### First-match-wins ordering

Two ordered lists where the specific entry must sit above the general one:

- `rows.COLUMNS` — `"transaction date"` before `"date"`, or a bank exporting both
  yields the posting date and shifts every month-end transaction.
- `categories.RULES` — `shell energy` before `shell`, or the electricity bill
  lands in fuel.

Adding an entry to either means deciding where in the order it goes, not
appending.

### Deliberate behaviours that look like bugs

- Refunds net **within a month only**. January keeps its spend and a February
  refund shows as February income. Covered by
  `test_a_refund_nets_within_its_own_month_and_not_across_months`.
- `sign_convention` guesses from the data rather than asking. This is a
  single-user tool; a wrong guess is immediately visible.
- Anything unmatched lands in `categories.UNCATEGORISED` and is counted in the
  summary line, rather than halting the run.
- `months.is_refund` is defined but currently unused by the pipeline.

## Fixtures

Each of the three files exercises something the others cannot, and the end-to-end
tests assert exact totals against them. Editing a fixture breaks those
assertions.

- `current.csv` — duplicates, a same-day transfer pair, a cross-month refund,
  recurring payments, one uncategorised merchant
- `other-bank.csv` — different column names, spending exported as positive
- `boundary.csv` — made in one month, posted in the next

Split of the suite: `tests/test_rules.py` tests each policy in isolation (plus a
coupled-pair test at the bottom), `tests/test_statements.py` runs the fixtures
end to end.
