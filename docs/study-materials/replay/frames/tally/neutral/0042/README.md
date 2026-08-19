# tally

A bank export, into a monthly summary.

Download your transactions and you get a CSV in the order things happened. That
answers "what happened on the 3rd". tally answers "what did I spend on food last
month".

```
.venv/bin/tally summarise fixtures/current.csv            write current.md beside it
.venv/bin/tally summarise fixtures/current.csv -          write to stdout
.venv/bin/tally summarise fixtures/current.csv --by-week  group by week: current.weekly.md
.venv/bin/tally check fixtures/                           summarise everything, write nothing
.venv/bin/python -m pytest tests/ -q                      run the tests
```

A run prints one line, e.g.
`current.csv: 37 rows, 3 months, 1 duplicates, 4 transfers, 1 uncategorised, 4 recurring`.

`--by-week` groups the same rows into ISO weeks (`2026-W03`, Monday to Sunday)
instead of months, and writes `current.weekly.md` so it does not land on top of
the monthly one. Nothing else changes: the same rules run in the same order, and
recurring payments are still counted in months, because a fixed commitment is a
monthly thing and no payment appears in three separate weeks.

## Money you moved rather than spent

A transfer into your own savings is not spending, but it is money that left the
account, and a summary that says nothing about it cannot answer "where did the
month go". Each period lists it under its total:

```
  total               1115.46
  transfers           -600.00   (not in the total)
```

The total is untouched — nothing there claims the money was spent. The figure is
the sum of the transfer rows in that period, so if your bank exports both legs of
a move, once out of the current account and once into the savings account, you
will see both. That is the other half of why it is kept out of the total.

`[transfers] show` in `tally/rules.toml` chooses: `"apart"` as above, `"never"`
to leave them out altogether, or `"spending"` to count them in the totals under
a category of their own.

## The rules are a file

Merchant patterns, what to do with transfers, and the numbers the rules use all
live in `tally/rules.toml`. Edit that; there is no need to touch code, and no
copy of those values in the code to keep in step.

```toml
[[categories.rules]]
match = "tesco|sainsbury|aldi|lidl|co-?op"
category = "groceries"
```

The rules are tried in the order the file has them and the first match wins,
which is why `shell energy` sits above `shell`: the other order puts the
electricity bill in the car. Patterns are regular expressions, matched
case-insensitively anywhere in the description.

The file is read once, when the command starts, and passed down to the rules as
an argument — no rule reads a global. A mistake in it stops the run and names the
rule rather than becoming a pattern that quietly matches nothing.

## Where things live

```
tally/rows.py         reads the CSV, whatever the bank called its columns
tally/rules.toml      the merchant rules, and the settings the rules read
tally/settings.py     reads rules.toml, and refuses it when it is wrong
tally/categories.py   merchant name to category
tally/dedupe.py       repeats, and spotting transfers between your own accounts
tally/periods.py      which month or week a payment belongs to, and refunds
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
