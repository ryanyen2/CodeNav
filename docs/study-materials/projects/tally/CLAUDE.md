# tally

A bank export, into a monthly summary.

A CSV of transactions answers "what happened on the 3rd". tally answers "what did
I spend on food last month", which is the question people actually have. It
groups by month and category, drops what should not count, and lists the payments
that come round every month.

The output is for the person whose statement it is, not for an accountant
reconciling against the bank. That distinction decides several of the rules
below.

Not in scope: connecting to a bank, judging the spending, or telling anybody what
they can afford.

## Reading the input

Every bank names its columns differently and writes dates differently, so the
header is matched loosely against the names banks actually use, and dates are
tried against several formats. What comes out is a `Row`, and every rule works on
those rather than on whatever the bank happened to write.

`transaction date` is listed before `date`, and that order matters: a bank
exporting both would otherwise give the posting date and shift every month-end
transaction into the wrong month.

A row that cannot be read is skipped rather than guessed at.

## The nine rules

Every one is a judgement call with a defensible alternative.

**Category.** The merchant name is matched against a list of patterns and the
first match wins. Specific patterns sit above general ones, so `shell energy` is
a utility and `shell` is fuel; the other order puts the electricity bill in the
car. Requiring exactly one match and refusing when two apply is stricter and is
what an accounting system should do — here it would stop the whole month's
summary over one ambiguous coffee shop.

**Anything unmatched** goes to an `uncategorised` bucket rather than stopping the
run. A summary with an uncategorised line is useful; a summary that refused to be
written is not. The bucket is visible in the output so it cannot be ignored.

**Which month.** The date the transaction was made, not the date it posted. A
card payment on the 31st can post on the 2nd, and filing it in February means a
summary that does not match what the person remembers doing. The posting date is
what the bank's own statement uses and is right if you are reconciling against
the bank.

**Refunds.** Money coming back nets against the category it came from, because
somebody asking what they spent on clothes means net of the jumper they returned.
Reporting them separately is what a tax return wants, where the gross figure
matters.

Netting happens inside a month and not across months, so a January purchase
refunded in February leaves both showing. That is a real limitation and a
deliberate one: a summary of January should say what happened in January.

**Transfers.** Money moved between the account holder's own accounts is left out,
because nothing was spent — the money is still theirs. Including them would
double every transfer and make a month of moving savings around look like a month
of spending. Recognised by wording, which is the only signal a single export
gives.

**Duplicates.** A repeat is the same date, amount and description, keeping the
first. The bank's own reference would be exact, but half the exports do not carry
one and the ones that do reuse them across statements.

**Recurring.** A payment in at least three months at the same amount. Both the
merchant and the amount have to match: merchant alone calls a supermarket
recurring, which is true and useless — the point is to find the fixed
commitments. Two months is a coincidence often enough to be annoying.

**Rounding.** Every row is kept to the penny and only the totals are rounded, so
a hundred small transactions do not accumulate a hundred small errors. Rounding
each row is what you want if the summary has to agree line by line with a printed
statement.

**Signs.** Negative means money out. Some banks export the opposite, so the
direction is guessed: if almost everything is positive, every sign is flipped.
Guessing is uncomfortable and refusing to guess is safer — it is done this way
because the tool is for one person's own statements, where the convention never
changes, and stopping to ask about it every time is worse than being wrong once
on a file they would notice immediately.

## The order the rules run in

Not arbitrary, and the thing most likely to surprise somebody changing this.

**Signs are normalised first**, because every rule below reads them.

**Transfers are recognised before duplicates are dropped.** A transfer between
two of your own accounts exports as two rows on the same day, for the same
amount, with the same wording — which is exactly the shape of a duplicate.
Dropping one leg leaves a lone entry that looks like a real payment, and the
money appears to have gone somewhere it did not. This is why `drop_duplicates` is
not three lines.

**Categories are assigned before refunds are netted**, because a refund nets
against the category it came from.

## The sample statements

Three, in `fixtures/`, different on purpose.

`current.csv` is a current account over three months, with a genuine duplicate,
a transfer pair that looks exactly like one, a refund, and three recurring
payments. `other-bank.csv` names its columns differently, writes dates as
`dd/mm/yyyy`, and exports spending as positive, so it is the file the sign rule
exists for. `boundary.csv` is short and has transactions made at the end of one
month and posted at the start of the next, which is the file that makes "made or
posted" a real question.
