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
fixtures/             four sample statements, each exercising something different
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

## One shop written three different ways

Banks are not consistent about merchant names, so the same purchase can arrive
two or three times under two or three spellings. `[duplicates] match` says what
makes two rows the same row:

```
match = ["date", "amount"]                     one shop, three spellings, counted once
match = ["date", "amount", "description"]      only exact repeats are dropped
```

Fields are `date` (the transaction date), `posted`, `amount` and `description`.

Leaving `description` out has a cost that is worth understanding before you rely
on it. Two genuinely separate purchases of the same amount on the same day — two
coffees at 3.40, two 10.00 top ups — are indistinguishable in the fields being
compared, so one of them is dropped and that money quietly goes missing.
`fixtures/one-shop-three-ways.csv` has both cases in it, and the tests assert
both outcomes. The count of dropped rows is on the line every run prints, which
is the only way to notice.

Transfers are exempt from all of this whatever is listed, because both legs of a
transfer are meant to survive.

## Money moved to your own accounts

A transfer to savings is not spending — the money is still yours — but it is not
nothing either, and dropping it silently leaves a summary that cannot be checked
against a statement. `[transfers] handling` decides:

```
handling = "show"    report it beside each period, outside the total  (default)
handling = "hide"    leave it out of the summary entirely
```

Shown, it appears under the total:

```
  total               1115.46
  moved               -600.00   (2 transfers, not in the total)
```

The count is there because transfers are exempt from the duplicate rule, so a
month holding two moves shows twice the amount of a month holding one. Without
the count that reads as an error in the total rather than as two transfers,
which is a question only you can settle by looking at your statement.

Whichever way it is set, `total + moved` accounts for every row that survived
deduping, period by period. There is a test for that.

There is deliberately no mode that counts transfers as spending. On an export
covering both accounts that double counts every move, once leaving one account
and once arriving in the other. If money going to an account you do not track
really is spending to you, take the word out of `[transfers] words` and let it
be categorised like any other payment.

## A merchant that matches nothing

`[categories] unmatched` decides:

```
unmatched = "stop"      refuse, and name every merchant that did not match  (default)
unmatched = "bucket"    file it under "uncategorised" and carry on
```

Stopping names all of them at once, so a month of new shops is one run and one
edit rather than one run per shop. Nothing is written when it stops.

It costs something, and the cost is the point: you cannot have the summary at
all until every merchant is accounted for, including the one-off from a shop you
will never visit again. `check` still tries every statement in the folder and
reports all of them before exiting non-zero.

## By month or by week

`--by-week` is a different cut of the same statement, not an extra section. The
same rows, the same rules, the same categories; only the grouping changes.
Weeks are ISO weeks, so they run Monday to Sunday and are labelled `2026-W05`.

The two cuts are lined up on different dates, set in `[periods]`:

```
month = "made"      the date you made the payment    -- what you remember doing
week  = "posted"    the date it reached the account  -- what the bank shows you
```

They answer different questions, which is why they can differ. A card payment
made on the 31st that posts on the 2nd is January spending to you and February
to the bank. The monthly view is the one you read to see where the money went;
the weekly view is the one held up next to a statement. Set them the same if you
only ever ask one of those questions. An export with no posting date column
falls back to the transaction date, so `"posted"` is safe on a bank that does
not provide one.

Two more things follow, and both are deliberate:

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
