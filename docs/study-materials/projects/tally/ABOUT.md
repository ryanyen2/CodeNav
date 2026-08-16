# About tally

This is what the participant reads. Two minutes, no assumed knowledge, one worked
example rather than a description.

---

## The problem

Download your transactions from a bank and you get a CSV: one row per payment,
hundreds of rows, in the order they happened.

```
2026-01-03,TESCO STORES 3241,-52.40
2026-01-04,Transfer to savings,-300.00
2026-01-06,PRET A MANGER,-4.85
2026-01-08,SHELL 4417,-61.20
```

It answers "what happened on the 3rd". It does not answer "what did I spend on
food last month", which is the question people actually have.

**tally answers that.** Same data, after:

```
## 2026-01

  housing             -950.00
  groceries           -104.80
  utilities            -88.00
  fuel                 -61.20
  eating out            -4.85

  total              -1208.85
```

## Six things it does

You do not need to remember these. They are here so nothing in the code is a
surprise.

**Reads any bank's file.** Every bank names its columns differently and writes
dates differently. tally matches loosely against the names banks actually use.

**Puts each payment in a category.** By matching the merchant name against a list
of patterns: anything with "tesco" is groceries, anything with "shell" is fuel.

**Groups by month.** So you can compare one month against another.

**Drops repeats.** Banks sometimes export the same payment twice. The second one
is dropped.

**Leaves out transfers.** Moving £300 from your current account to your savings
is not spending — the money is still yours.

**Finds what recurs.** A payment that appears every month at the same amount is a
fixed commitment. Those are listed separately.

## What it does not do

It does not connect to a bank. Something else downloads the file. It does not
tell you whether you can afford anything, and it has no opinion about your
spending.

## The one idea worth holding

**Every one of those is a judgement call, and it could have gone the other way.**

Take the month a payment belongs to. You pay for something on the 31st of
January; the bank processes it on the 2nd of February. Which month was it?
tally says January, because that is the day you remember. Your bank's own
statement says February. Both are right, for different questions.

Or transfers. Leaving them out is right if you are asking what you spent. If you
are asking where your money went, you might want them in.

tally made a choice about each. The code shows you what it chose. It does not
tell you why, or what it gave up.

## Running it

From inside the project folder:

| Command | What it does |
| --- | --- |
| `.venv/bin/tally summarise fixtures/current.csv` | Summarise one file, write the `.md` beside it |
| `.venv/bin/tally summarise fixtures/current.csv -` | Summarise one file, print it instead |
| `.venv/bin/tally check fixtures/` | Summarise everything, write nothing |
| `.venv/bin/python -m pytest tests/ -q` | Run the tests |

A run prints one line:

```
current.csv: 37 rows, 3 months, 1 duplicates, 4 transfers, 1 uncategorised, 3 recurring
```

## The files

Nine, and small. You will probably touch two or three.

```
tally/rows.py         reads the CSV, whatever the bank called its columns
tally/categories.py   merchant name to category
tally/dedupe.py       repeats, and transfers between your own accounts
tally/months.py       which month, and what a refund does
tally/recurring.py    payments that come round every month
tally/money.py        rounding, and which way round the signs are
tally/summary.py      runs the rules, in order
```

There are three sample files in `fixtures/`: a current account over three months,
an export from a different bank with different column names, and a short file of
payments made at the end of one month and processed at the start of the next.
They are different on purpose.

## What we are asking

You will get a short task card. Work however you normally would and use the
coding agent as much or as little as you like.

The card is short on purpose. Anything it does not say is yours to decide, and we
will ask you about those decisions afterwards, so make them on purpose.

We will also ask you to explain the code: what it does, why it is built that way,
and what you would change to extend it.
