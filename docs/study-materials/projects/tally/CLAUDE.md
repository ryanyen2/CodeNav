# Codebase feature guide

## Reading bank statements

Every bank exports its transactions differently: different column names, and no agreement on whether money going out is negative. These features read whatever arrived and turn it into one common shape, so that every rule after this point can be written once instead of once per bank.

### Bank export row reader

Reads the CSV and maps its columns onto a common row.

Column names are matched against an ordered list of aliases, most specific first, so a file with both `transaction date` and `date` uses the one that means what it says. A row that cannot be read is skipped rather than guessed at — parsing here, judgement later.

Code: `tally/rows.py::COLUMNS`, `tally/rows.py::DATE_FORMATS`, `tally/rows.py::Row`, `tally/rows.py::Row.is_money_out`, `tally/rows.py::__module__`, `tally/rows.py::_pick`, `tally/rows.py::parse_amount`, `tally/rows.py::parse_date`, `tally/rows.py::read`

### Money handling policies

Two decisions about money that everything downstream depends on: which direction is negative, and when the rounding happens.

Code: `tally/money.py::__module__`

#### Summary total rounding

Rounds once, at the summary, not on every transaction.

Two hundred transactions rounded individually drift by a few pence; rounded once at the end they do not. The cost is that the totals no longer agree line by line with a printed receipt, which is the thing you would want if you were reconciling against one.

Code: `tally/money.py::PENNY`, `tally/money.py::round_total`

#### Transaction sign normalization

Makes spending negative and money coming in positive, whatever the bank did.

One bank writes a £12 coffee as `-12.00`; another writes `12.00` and puts the direction in a separate column. If almost every amount in the file is positive, that is taken as the second convention and every sign is flipped. It is a guess, made rather than asked about, so that every rule after this sees one convention.

Code: `tally/money.py::sign_convention`

## Applying transaction policies

With the rows in one shape, these are the rules that decide what actually counts as spending: which month it lands in, what is really a duplicate, what is a transfer rather than a purchase, and which category it belongs to.

### Transaction month assignment

Counts a transaction in the month it was made, not the month the bank posted it.

A card payment on the 31st that posts on the 2nd belongs to the month you remember spending it in. The alternative — posting dates — would make the summary agree with the bank statement instead, which is a different thing to want.

Code: `tally/months.py::__module__`, `tally/months.py::month_of`

### Transfer-aware duplicate filtering

Drops repeated rows so they do not inflate the totals.

Same made date, same amount, same description (ignoring case) counts as one. Nothing in a row says whether it was sent twice or bought twice, so buying the same coffee twice in one day is counted once — the cost of not double-counting everything else. Transfers are the exception: moving £500 from current to savings shows up as two rows that look exactly like a duplicate, and dropping one would leave a £500 payment that never happened. Transfers are also left out of spending, because the money is still yours.

Code: `tally/dedupe.py::TRANSFER_WORDS`, `tally/dedupe.py::__module__`, `tally/dedupe.py::drop_duplicates`, `tally/dedupe.py::is_transfer`, `tally/dedupe.py::key`

### Merchant category assignment

Puts each transaction in a category by matching its description against an ordered list of patterns.

The first pattern that matches wins, so specific ones sit before broad ones — a rule for one coffee shop has to come before the general `cafe` rule or it never fires. Anything unmatched goes to an uncategorised bucket, so the summary still adds up and the gap is visible instead of silent.

Code: `tally/categories.py::COMPILED`, `tally/categories.py::RULES`, `tally/categories.py::UNCATEGORISED`, `tally/categories.py::__module__`, `tally/categories.py::categorise`

### Refund netting policy

Takes money that came back off the category it was spent in.

A £40 refund on a £100 coat leaves £60 of clothing, not £100 of clothing and £40 of income. Refunds are recognised by their positive sign, so income has to be excluded by category before this runs, or a salary would look like a refund of something.

Code: `tally/months.py::is_refund`, `tally/months.py::net`

### Recurring payment detection

Finds the fixed commitments — rent, a subscription — by looking for the same description at the same amount in at least three different months.

Both the description and the amount have to match, or a weekly supermarket shop would look like a subscription. Three months rather than two, so a coincidence does not become a commitment.

Code: `tally/recurring.py::MONTHS`, `tally/recurring.py::__module__`, `tally/recurring.py::find`

## Producing a monthly spending summary

What comes out at the end: totals per category per month, and the ways to ask for them.

### Monthly spending summary

Adds the transactions up into monthly category totals, and reports what it did with the rest: how many rows it read, what it dropped as duplicates, what it treated as transfers, what it could not categorise, and which payments look recurring.

The policies run before the adding up, so the totals and the notes underneath them are telling the same story. Reordering that changes the numbers.

Code: `tally/summary.py::Month`, `tally/summary.py::Month.total`, `tally/summary.py::Summary`, `tally/summary.py::Summary.line`, `tally/summary.py::Summary.text`, `tally/summary.py::__module__`, `tally/summary.py::summarise`

### Command-line summary interface

`tally summarise statement.csv` prints the summary; pointed at a folder it reads every CSV in it. There is a check mode that reads and reports without writing anything, which is how you see what a rule change does before committing to it.

Code: `tally/cli.py::__module__`, `tally/cli.py::main`

## Checking statements

These features verify that statement reading, transaction policies, and monthly summaries continue to behave correctly across focused rule cases and complete sample statements.

### Rule contract test suite

Keeps the behavior of each transaction rule explicit, including the cases where one policy can change another policy’s result. The suite uses small common rows so changes to parsing, normalization, categorization, and summaries fail at the rule boundary instead of being hidden in a larger end-to-end example.

Code: `tests/test_rules.py::__module__`, `tests/test_rules.py::row`

#### Imported transaction contracts

Checks that bank-exported values become usable transactions without guessing when a row is unreadable, and that spending signs and dates have the expected meaning afterward. Made dates determine the spending month even when posting happens later, while a transaction date takes precedence when both dates are exported so month-end spending is not shifted.

Code: `tests/test_rules.py::test_a_transaction_belongs_to_the_month_it_was_made`, `tests/test_rules.py::test_a_transaction_date_beats_a_posting_date`, `tests/test_rules.py::test_an_export_with_spending_as_positive_is_flipped`, `tests/test_rules.py::test_an_ordinary_export_is_left_alone`, `tests/test_rules.py::test_an_unreadable_row_is_skipped_not_guessed_at`, `tests/test_rules.py::test_the_amount_formats_banks_use`, `tests/test_rules.py::test_the_date_formats_banks_use`

#### Transfer-safe duplicate contracts

Checks that duplicate removal drops only repeated transactions while preserving different rows and both legs of an own-account transfer. A transfer pair has the same shape as a duplicate, but removing one leg would make money appear to have been spent.

Code: `tests/test_rules.py::test_a_transfer_is_recognised_by_its_wording`, `tests/test_rules.py::test_a_transfer_pair_is_not_a_duplicate`, `tests/test_rules.py::test_the_same_transaction_twice_is_dropped_once`, `tests/test_rules.py::test_two_transactions_that_differ_are_both_kept`

#### Category and refund contracts

Checks that merchant rules use the first matching pattern and keep unmatched spending in an uncategorised bucket instead of stopping the summary. Refunds are then netted against the category’s spending, so category assignment must remain available before the refund calculation.

Code: `tests/test_rules.py::test_anything_unmatched_goes_to_a_bucket`, `tests/test_rules.py::test_refunds_net_against_the_category`, `tests/test_rules.py::test_the_first_matching_rule_wins`

#### Recurring payment contracts

Checks that a payment is called recurring only when the same merchant and amount appear across three months, while changing amounts or having only two months does not qualify. This keeps ordinary variable shopping from being mistaken for a fixed commitment.

Code: `tests/test_rules.py::test_a_payment_in_three_months_at_one_amount_is_recurring`, `tests/test_rules.py::test_the_same_merchant_at_different_amounts_is_not`, `tests/test_rules.py::test_two_months_is_not_enough`

#### Summary amount contracts

Checks that monetary totals are rounded once after the amounts have been added together. Rounding each row first could make several fractions total a penny more than the true sum.

Code: `tests/test_rules.py::test_rounding_happens_once_at_the_total`

### End-to-end statement summarization checks

Protects the complete statement-to-monthly-summary behavior across the sample banks, including empty input, differing columns, made-date boundaries, sign conversion, transfers, duplicates, refunds, recurring payments, uncategorised transactions, and stable output. The fixtures keep these cases together because each statement exercises a different part of the real summarization flow, so changing the pipeline without preserving its policy order or visible results will fail here.

Code: `tests/test_statements.py::FIXTURES`, `tests/test_statements.py::__module__`, `tests/test_statements.py::run`, `tests/test_statements.py::test_a_refund_nets_within_its_own_month_and_not_across_months`, `tests/test_statements.py::test_a_transaction_made_on_the_31st_is_january`, `tests/test_statements.py::test_an_empty_file_is_not_an_error`, `tests/test_statements.py::test_an_unknown_merchant_is_visible_rather_than_hidden`, `tests/test_statements.py::test_and_the_ones_made_in_february_are_february`, `tests/test_statements.py::test_both_legs_of_every_transfer_survive`, `tests/test_statements.py::test_different_column_names_still_read`, `tests/test_statements.py::test_months_come_out_in_order`, `tests/test_statements.py::test_no_statement_ends_up_empty`, `tests/test_statements.py::test_spending_exported_as_positive_is_flipped`, `tests/test_statements.py::test_summarising_twice_gives_the_same_thing`, `tests/test_statements.py::test_the_fixed_commitments_are_found`, `tests/test_statements.py::test_the_repeated_shop_is_counted_once`, `tests/test_statements.py::test_transfers_are_left_out_of_the_spending`

## Package identity metadata

Identifies the Tally package and records its current version so tools and users can recognize the installed release. The module also states that Tally turns bank exports into a monthly summary.

Code: `tally/__init__.py::__module__`
