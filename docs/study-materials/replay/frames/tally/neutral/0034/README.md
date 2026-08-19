# tally

A bank export, into a monthly summary.

Download your transactions and you get a CSV in the order things happened. That
answers "what happened on the 3rd". tally answers "what did I spend on food last
month".

```
.venv/bin/tally summarise fixtures/current.csv     write current.md beside it
.venv/bin/tally summarise fixtures/current.csv -   write to stdout
.venv/bin/tally check fixtures/                    summarise everything, write nothing
.venv/bin/python -m pytest tests/ -q               run the tests
```

Either command takes `--by-week` to group by week rather than by month, and
`--rules PATH` to use a different rules file.

A run prints one line, e.g.
`current.csv: 37 rows, 3 months, 1 duplicates, 4 transfers, 1 uncategorised, 3 recurring`.

## Where things live

```
tally/rows.py         reads the CSV, whatever the bank called its columns
tally/rules.toml      the merchant list and the settings, edited without touching code
tally/rules.py        reads that file, and refuses a broken one
tally/categories.py   merchant name to category
tally/dedupe.py       repeats, and transfers between your own accounts
tally/periods.py      which month or week a payment belongs to, and refunds
tally/recurring.py    payments that come round every month
tally/money.py        rounding, and which way round the signs are
tally/summary.py      the order the rules run in
fixtures/             three sample statements, each exercising something different
```

## Changing what it decides

`tally/rules.toml` holds the merchant patterns, the wording that means a
transfer and what to do with it, how many months make a payment recurring, and
the share of positive rows that means an export has its signs the other way
round. Adding a shop is an edit to a list.

The merchant patterns are tried in the order they appear in the file and the
first match wins, which is why `shell energy` sits above `shell`: the other
order puts the electricity bill in the car. They are written as separate
`[[rule]]` entries so that ordering is a property of the file, rather than
something that survives only as long as nobody alphabetises it.

A rules file that cannot be read stops the run rather than falling back to
anything built in. Summarising against rules other than the ones you are looking
at produces a wrong answer that looks entirely fine.

## By month or by week

`--by-week` is a different cut of the same statement, not an extra section. The
same rows, the same rules, the same categories; only the grouping changes.
Weeks are ISO weeks, so they run Monday to Sunday and are labelled `2026-W05`.

Two things follow from that, and both are deliberate:

Refunds net **within** a period, so a purchase and its refund landing in
different weeks show up separately — more often than they would by month, since
weeks are shorter. Carrying a refund back to the period the purchase was in
would mean a summary that changes after it has been read.

Recurring payments stay **monthly** whichever way the spending is grouped,
because that is what the word means. A fixed commitment does not become a
different thing because you asked to see seven days at a time.

## The one thing to know

`summary.py` runs the rules in an order that is not arbitrary. Signs are
normalised first, because everything below reads them. Transfers are found before
duplicates are dropped, because a transfer between your own accounts is two rows
that look exactly like a duplicate. Categories are assigned before refunds are
netted, because a refund nets against the category it came from.

`summary.py` is also the only place that reads the rules. Each policy is handed
what it needs as an argument rather than reaching for a constant of its own, so
what a run depends on is one object passed in at the top and visible at every
call site.
