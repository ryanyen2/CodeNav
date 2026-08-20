# tally

A bank export, into a monthly summary.

Download your transactions and you get a CSV in the order things happened. That
answers "what happened on the 3rd". tally answers "what did I spend on food last
month".

```
.venv/bin/tally summarise fixtures/boundary.csv            write boundary.md beside it
.venv/bin/tally summarise fixtures/boundary.csv -          write to stdout
.venv/bin/tally summarise fixtures/boundary.csv --by-week  by week: boundary.weekly.md
.venv/bin/tally check fixtures/                            summarise everything, write nothing
.venv/bin/python -m pytest tests/ -q                       run the tests
```

`check fixtures/` stops on `current.csv`, which carries a merchant no rule
matches. That is the tool working — see below.

A run prints one line, e.g.
`boundary.csv: 7 rows, 3 months, 0 duplicates, 0 transfers, 0 uncategorised, 0 recurring`.

## The two summaries answer different questions

`--by-week` is not the monthly summary cut smaller. It is the one you read with
the bank's own statement next to you, and rules.toml sets it up that way:

|                        | monthly            | weekly              |
| ---------------------- | ------------------ | ------------------- |
| a payment belongs to   | the day you made it | the day it posted  |
| two rows are one when  | the wording matches | the amount and day match |

A card payment made on the 31st can post on the 2nd. The monthly summary calls
that January, because that is when you spent it. The weekly summary calls it the
week the bank cleared it, because that is the line you are checking it against.
The two files will disagree about a week at every month end, on purpose.

The weekly summary also treats two rows on the same posted day for the same
amount as one transaction whatever they are called, for banks that word the
pending row and the settled row differently. That will sometimes be wrong — two
identical fares in a day are two fares — so everything it merged is listed at the
bottom of the summary with both descriptions:

```
## Merged

  2026-02-02      -52.40  TESCO STORES 3241 / TESCO-EXPRESS 3241
```

Both halves are `[periods]` and `[duplicates]` in `tally/rules.toml`, set for
each summary separately. Recurring payments are counted in months by the date
they were made whatever those say, because that is about when a commitment
falls, not about which summary is open.

## A merchant with no rule stops the run

```
$ tally summarise statement.csv
statement.csv: no rule matches:
  QUEENS ARMS  (3 rows, first at line 12)
  MOONLIGHT RECORDS  (line 15)
add a rule for each in tally/rules.toml, or set [categories] unmatched = "bucket"
to file them under one name instead.
```

Every unknown merchant is listed at once, with the line each was on, because
adding rules one run at a time is a job that gets abandoned halfway. Nothing is
written and the exit code is 2; `check` reports the statement that stopped and
carries on through the rest of the folder.

The cost is that a statement full of new merchants gives no summary at all until
each has a rule. `[categories] unmatched = "bucket"` puts them under one name and
writes the summary anyway, which is what this used to do.

## Money you moved rather than spent

A transfer into your own savings is not spending, but it is money that left the
account, and a summary that says nothing about it cannot answer "where did the
month go". Each period lists it under its total:

```
  total               1115.46
  transfers           -600.00   (not in the total)
```

The total is untouched — nothing there claims the money was spent. The figure is
the sum of the transfer rows in that period, which is the amount moved when your
bank writes one row a move. If it writes both legs, once out of the current
account and once into the savings account, the figure is twice that or zero
depending on the signs it used, which is the other half of why it is kept out of
the total. The line appears whenever there were transfers at all, even when they
came to nothing, so that "they cancelled out" does not look like "there were
none".

`[transfers] show` in `tally/rules.toml` chooses: `"apart"` as above, `"never"`
to leave them out altogether, or `"spending"` to count them in the totals under
a category of their own.

## The rules are a file

Merchant patterns, what to do with transfers, which date decides a period, what
makes two rows one transaction, and the numbers the rules use all live in
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
tally/dedupe.py       repeats, and spotting transfers between your own accounts
tally/periods.py      which month or week a payment belongs to, and refunds
tally/recurring.py    payments that come round every month
tally/money.py        rounding, and which way round the signs are
tally/summary.py      the order the rules run in
fixtures/             three sample statements, each exercising something different
                      (current.csv has a merchant with no rule, so it stops)
```

## The one thing to know

`summary.py` runs the rules in an order that is not arbitrary. Signs are
normalised first, because everything below reads them. Transfers are found before
duplicates are dropped, because a transfer between your own accounts is two rows
that look exactly like a duplicate. Categories are assigned before refunds are
netted, because a refund nets against the category it came from.
