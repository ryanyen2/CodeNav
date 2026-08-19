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

A run prints one line, e.g.
`current.csv: 37 rows, 3 months, 1 duplicates, 4 transfers, 1 uncategorised, 3 recurring`.

## Where things live

```
tally/rows.py         reads the CSV, whatever the bank called its columns
tally/categories.py   merchant name to category
tally/dedupe.py       repeats, and transfers between your own accounts
tally/months.py       which month a payment belongs to, and refunds
tally/recurring.py    payments that come round every month
tally/money.py        rounding, and which way round the signs are
tally/summary.py      the order the rules run in
fixtures/             three sample statements, each exercising something different
```

## The one thing to know

`summary.py` runs the rules in an order that is not arbitrary. Signs are
normalised first, because everything below reads them. Transfers are found before
duplicates are dropped, because a transfer between your own accounts is two rows
that look exactly like a duplicate. Categories are assigned before refunds are
netted, because a refund nets against the category it came from.
