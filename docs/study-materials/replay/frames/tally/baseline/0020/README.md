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
`current.csv: 37 rows, 3 months, 1 duplicates, 4 transfers, 1 uncategorised, 3 recurring`.

`--by-week` groups the same rows into ISO weeks (`2026-W03`, Monday to Sunday)
instead of months, and writes `current.weekly.md` so it does not land on top of
the monthly one. Nothing else changes: the same rules run in the same order, and
recurring payments are still counted in months, because a fixed commitment is a
monthly thing and no payment appears in three separate weeks.

## The rules are a file

Merchant patterns, transfer wording, and the two numbers the rules use live in
`tally/rules.toml`. Edit that; there is no need to touch code, and no copy of
those values in the code to keep in step.

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
tally/dedupe.py       repeats, and transfers between your own accounts
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
