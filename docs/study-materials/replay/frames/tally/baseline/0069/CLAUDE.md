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

One bank writes a £12 coffee as `-12.00`; another writes `12.00` and puts the direction in a separate column. If more than the share of rows named in rules.toml is positive (`[money] flip_when_positive_share_above`, 0.8 as shipped), that is taken as the second convention and every sign is flipped. It is a guess, made rather than asked about, so that every rule after this sees one convention. The share is a setting rather than a literal because it is a judgement about how much ordinary money-in a statement carries — a salary and the odd refund — and that differs between people; the penny in `round_total` stays in code, because it is what the currency is rather than something to tune.

Code: `tally/money.py::sign_convention`

## The rules as a file

The judgements the policies make used to be constants in the modules that made them, so changing a merchant pattern meant editing code and there was no way to run one statement through two rule sets. They are now data.

### Rules file and settings

Holds every value the rules need — merchant patterns and their order, the wording that marks a transfer and what to do with one, which date decides a period, what makes two rows one transaction, how many months make a payment recurring, when to flip signs — in `tally/rules.toml`, read once into a frozen `Settings` and handed to each rule as an argument.

Reading happens at the edge: the CLI loads it and passes it down, and nothing below opens a file or reaches for a global, which is what lets a test or an alternative rule set be swapped in by passing a different `Settings`. The file is the only place these values live; no module keeps a default to fall back on, because two copies of one policy drift apart and the copy that loses is always the one somebody edited. `Settings` is frozen for the same reason — a rule that could rewrite the settings it was handed would put the policy back outside the file. `settings.py` imports nothing else from tally, which keeps the dependency one-way: everything may read the settings, so the settings may read nothing. Because the file is the rules and not an extra, it is declared as package data in `pyproject.toml` — without that an installed tally has no rules to read and stops on the first run.

Code: `tally/settings.py::__module__`, `tally/settings.py::Settings`, `tally/settings.py::load`, `tally/settings.py::DEFAULT_RULES`, `tally/settings.py::Rules`, `tally/rules.toml`, `pyproject.toml`

#### Rules file validation

Refuses a rules file that is missing, unreadable, or says something that cannot be used, naming the file and the rule at fault.

Raised rather than repaired: a pattern that does not compile would otherwise become a rule that matches nothing, and a category quietly gone missing from a summary is worse than a run that stopped and said so. The behaviours a setting may name are lists in code (`SHOW_TRANSFERS`, `PERIOD_NAMES`, `DATES`, `MATCH_ON`, `UNMATCHED`) rather than anything the file can invent, and a misspelling is checked against them instead of falling through to whatever the code happens to do last. The per-summary tables have to name both the monthly and the weekly answer, since a defaulted one would answer a different question from the one somebody wrote and the two files would disagree without saying why.

Code: `tally/settings.py::RulesError`, `tally/settings.py::_categories`, `tally/settings.py::_choice`, `tally/settings.py::_per_summary`, `tally/settings.py::_words`, `tally/settings.py::_months`, `tally/settings.py::_share`, `tally/settings.py::_text`, `tally/settings.py::_table`, `tally/settings.py::SHOW_TRANSFERS`, `tally/settings.py::PERIOD_NAMES`, `tally/settings.py::DATES`, `tally/settings.py::MATCH_ON`, `tally/settings.py::UNMATCHED`

## Applying transaction policies

With the rows in one shape, these are the rules that decide what actually counts as spending: which period it lands in, what is really a duplicate, what is a transfer rather than a purchase, and which category it belongs to. Each takes the settings it needs as an argument and holds no policy of its own.

### Transaction period assignment

Counts a transaction in a month or an ISO week, under whichever of its two dates rules.toml gives that summary.

A card payment on the 31st that posts on the 2nd belongs to the month you remember spending it in, so the monthly summary is filed by the date the payment was made. The weekly summary is filed by the posting date instead, because it is the one read with the bank's own statement next to it and shifting dates in your head to reconcile is the thing it exists to avoid. Neither date is wrong — they answer different questions — which is why the choice is per summary in rules.toml rather than settled here. Weeks are ISO weeks labelled by their ISO year, so that the 1st of January falling in week 52 of the year before still sorts with the week it belongs to; the alternative, numbering from the 1st of January, keeps every week inside its own year at the cost of a short week each January. The period name is resolved before any row is read, so an unknown period or date is an error even on an empty file rather than a summary that comes back empty and looks like a quiet month.

Code: `tally/periods.py::__module__`, `tally/periods.py::month_of`, `tally/periods.py::week_of`, `tally/periods.py::PERIODS`, `tally/periods.py::label_for`

### Transfer-aware duplicate filtering

Drops repeated rows so they do not inflate the totals, and recognises the rows that only look repeated.

Same day and same amount always; whether the description has to match as well is per summary in rules.toml. Under `"same wording"` — the monthly answer — two rows for the same amount on the same day are taken to be two things, because nothing in a row says whether it was sent twice or bought twice and that is the safer read. Under `"any wording"` — the weekly one — the description is ignored, for banks that word the pending row and the settled row differently; that merges a genuine second identical purchase, which is the price of it and the reason each merge is recorded rather than only counted. The day compared is the one that summary is lined up on, so a weekly summary on posting dates merges what the bank posted together. The bank's own reference would settle it exactly, but half the exports carry none and the rest reuse them across statements.

Transfers are the exception, and that exemption is the whole reason this is not three lines: moving £500 from current to savings shows up as two rows that look exactly like a duplicate, and dropping one would leave a £500 payment that never happened. Recognising a transfer is all that happens here — what is then done with it is a question about the shape of the summary, so it is answered in `summary.py` from rules.toml. Wording is the only signal available; an account column would be better, but half the exports have one account per file and no way to tell which.

Code: `tally/dedupe.py::__module__`, `tally/dedupe.py::Merge`, `tally/dedupe.py::Merge.reworded`, `tally/dedupe.py::drop_duplicates`, `tally/dedupe.py::is_transfer`, `tally/dedupe.py::key`

### Merchant category assignment

Puts each transaction in a category by matching its description against the ordered list of patterns rules.toml carries.

The first pattern that matches wins, so specific ones sit before broad ones — `shell energy` has to come before `shell` or the electricity bill lands in fuel. That is why the settings keep the rules in the order the file wrote them and never sort them. Anything unmatched comes back under whatever rules.toml calls uncategorised; whether that is the end of it or the run stops is decided once, in `summary.py`, when the whole statement has been read. Requiring exactly one match and refusing when two apply is stricter and is what an accounting system should do, but here it would stop a month's summary over one ambiguous coffee shop.

Code: `tally/categories.py::__module__`, `tally/categories.py::categorise`

#### Unknown merchants stop the run

Ends the run and lists every merchant no rule matched, with how many rows each had and the line the first was on, rather than filing them together and carrying on.

A bucket is where things go to be forgotten: the summary is read for its categories, and money under "uncategorised" is money nobody looks at again — quietly, which is the part that matters. The cost is real, so `[categories] unmatched = "bucket"` keeps the old behaviour for anybody who would rather have the summary now and the rules later. The whole list is raised at once because adding rules one run at a time is the kind of job that gets abandoned halfway, with the rest of the statement still filed under a name nobody reads. Transfers never reach this check: they are named rather than run through the merchant rules, since "Transfer to savings" matches no pattern and stopping for it would make the setting unusable for anybody who moves money.

Code: `tally/categories.py::Unmatched`, `tally/categories.py::Unmatched.__init__`, `tally/summary.py::summarise`

### Refund netting policy

Takes money that came back off the category it was spent in.

A £40 refund on a £100 coat leaves £60 of clothing, not £100 of clothing and £40 of income. Reporting them separately is defensible and is what a tax return wants, since there the gross figure matters. Refunds are recognised by their positive sign, so income has to be excluded by category before this runs, or a salary would look like a refund of something — `is_refund` states that rule and is currently used by nothing in the pipeline, which sums the amounts a category holds and lets the signs do the netting.

Code: `tally/periods.py::is_refund`, `tally/periods.py::net`

### Recurring payment detection

Finds the fixed commitments — rent, a subscription — by looking for the same description at the same amount in at least as many different months as rules.toml asks for (three as shipped).

Both the description and the amount have to match, or a weekly supermarket shop would look like a subscription. Three months rather than two, so a coincidence does not become a commitment. Months are counted by the date the payment was made whatever the summary is grouped by, because a fixed commitment is a monthly thing and a weekly window would find almost nothing.

Code: `tally/recurring.py::__module__`, `tally/recurring.py::find`

## Producing a spending summary

What comes out at the end: totals per category per period, monthly or weekly, and the ways to ask for them.

### Spending summary

Adds the transactions up into category totals per period, and reports what it did with the rest: how many rows it read, what it dropped as duplicates, what it treated as transfers, what it could not categorise, which payments look recurring, and which differently-worded rows it merged anyway.

The policies run in an order that is load-bearing, and it is the thing most likely to surprise somebody changing this: signs are normalised first because every rule below reads them, duplicates are dropped with transfers exempted before transfers are separated out, and categories are assigned before the amounts are netted, because a refund nets against the category it came from. Reordering that changes the numbers.

`by` is more than which heading a row lands under: it selects which of rules.toml's per-summary answers apply — how a period is labelled, which date decides it, and what makes two rows one transaction — so the monthly and weekly summaries of one statement can disagree about how many transactions there were, and are meant to. The printed form rounds only where it prints, keeps `Merged` at the bottom so the merges that might be wrong are visible rather than silently gone, and repeats the transfer note under every period on purpose, since one period gets read or copied out on its own far more often than the whole file does.

Code: `tally/summary.py::__module__`, `tally/summary.py::Period`, `tally/summary.py::Period.by_category`, `tally/summary.py::Period.transactions`, `tally/summary.py::Period.total`, `tally/summary.py::Summary`, `tally/summary.py::Summary.by`, `tally/summary.py::Summary.line`, `tally/summary.py::Summary.text`, `tally/summary.py::summarise`

#### Transfers in the summary

Answers what becomes of money moved between the person's own accounts: listed beside each period's total and left out of it (`"apart"`), left out altogether (`"never"`), or counted as spending under a category of its own (`"spending"`).

The choice is made here rather than in `dedupe.py` because it is a question about the shape of the summary, and it shows up in three places: what the totals are made of, what the recurring list sees — a standing order into savings is a fixed commitment, unless transfers were asked not to be shown at all — and what each period reports underneath its total. `"apart"` is the shipped answer because the other two both hide something: left out, a standing order into savings never appears and a person looking for where the month went cannot find it; counted as spending, money they still have reads as a month of spending, and an export carrying both legs of one move adds a move that never happened. The row count is kept alongside the amount, and a period holding nothing but a transfer still gets a heading, so that "they cancelled out" and "there were none" do not look alike.

Code: `tally/summary.py::Period.moved`, `tally/summary.py::Period.transfers`, `tally/summary.py::Summary.transfer_label`, `tally/summary.py::summarise`

#### Merged rows report

Lists the rows that were written differently and counted as one transaction anyway, with both descriptions.

Exact repeats are not listed — there is nothing to see. These are the ones that might be wrong, since under `"any wording"` two different things bought for the same amount on the same day merge; a person shown which rows were merged can judge that at a glance, where a count alone would only say something happened.

Code: `tally/summary.py::Summary.merged`, `tally/dedupe.py::Merge`, `tally/dedupe.py::Merge.reworded`

### Command-line summary interface

`tally summarise statement.csv` writes the summary beside the input (`-` prints it instead); pointed at a folder, `check` reads every CSV in it and writes nothing, which is how you see what a rule change does before committing to it. `--by-week` asks for the weekly summary.

The rules file is loaded once here and passed down, so a mistake in it stops the run with a message naming it before any statement is read. A weekly summary is written to its own suffix rather than over the monthly one, because the two answer different questions and somebody who ran both should end up with both. An argument starting with a dash that is not `-` is rejected as an unknown option rather than taken for a file name, so `--by-month` — which somebody will type, having seen `--by-week` — says there is no such option instead of "no such file". A statement with an unknown merchant exits 2 and writes nothing, but under `check` the folder is still walked to the end and the stopped ones counted, because seeing the whole folder in one go is the point of it.

Code: `tally/cli.py::__module__`, `tally/cli.py::main`, `tally/cli.py::SUFFIX`

## Checking statements

These features verify that statement reading, the rules file, transaction policies, the summaries and the command continue to behave correctly across focused rule cases, complete sample statements, and runs of the command itself.

### Rule contract test suite

Keeps the behavior of each transaction rule explicit, including the cases where one policy can change another policy’s result. The suite uses small common rows so changes to parsing, normalization, categorization, and summaries fail at the rule boundary instead of being hidden in a larger end-to-end example. Every test is handed the shipped settings, read once at the top, rather than reaching for a constant inside a module — which is the point of the settings being an argument.

Code: `tests/test_rules.py::__module__`, `tests/test_rules.py::RULES`, `tests/test_rules.py::row`

#### Imported transaction contracts

Checks that bank-exported values become usable transactions without guessing when a row is unreadable, and that spending signs have the expected meaning afterward. A transaction date takes precedence when both dates are exported, so month-end spending is not shifted into the month the bank posted it.

Code: `tests/test_rules.py::test_a_transaction_date_beats_a_posting_date`, `tests/test_rules.py::test_an_export_with_spending_as_positive_is_flipped`, `tests/test_rules.py::test_an_ordinary_export_is_left_alone`, `tests/test_rules.py::test_an_unreadable_row_is_skipped_not_guessed_at`, `tests/test_rules.py::test_the_amount_formats_banks_use`, `tests/test_rules.py::test_the_date_formats_banks_use`

#### Period labelling contracts

Checks that which date decides a period is a setting and that both answers are honoured, and pins the ISO week rules that make weekly labels sortable as text — the ISO year, so a January date in the previous year's last week does not sort above every real week of its own year. An unknown period or date name is refused by name rather than quietly producing nothing, and the periods on offer are asserted to be the ones the settings validate against, since that list lives in two modules.

Code: `tests/test_rules.py::test_which_date_decides_the_period_is_a_setting`, `tests/test_rules.py::test_a_week_is_an_iso_week`, `tests/test_rules.py::test_a_week_belongs_to_its_iso_year_not_its_calendar_year`, `tests/test_rules.py::test_weeks_sort_in_the_order_they_happened`, `tests/test_rules.py::test_an_unknown_period_is_refused_by_name`, `tests/test_rules.py::test_an_unknown_date_is_refused_by_name`, `tests/test_rules.py::test_the_periods_on_offer_are_the_ones_the_settings_check_for`

#### Transfer-safe duplicate contracts

Checks that duplicate removal drops only repeated transactions while preserving different rows and both legs of an own-account transfer. A transfer pair has the same shape as a duplicate, but removing one leg would make money appear to have been spent. Also pins the two settings that decide the question: whether the same shop written two ways is one transaction, and which of a row's two dates counts as the same day.

Code: `tests/test_rules.py::test_a_transfer_is_recognised_by_its_wording`, `tests/test_rules.py::test_a_transfer_pair_is_not_a_duplicate`, `tests/test_rules.py::test_the_day_compared_is_the_one_that_summary_is_lined_up_on`, `tests/test_rules.py::test_the_same_shop_written_two_ways_is_two_things_or_one_by_setting`, `tests/test_rules.py::test_the_same_transaction_twice_is_dropped_once`, `tests/test_rules.py::test_two_transactions_that_differ_are_both_kept`

#### Category and refund contracts

Checks that merchant rules use the first matching pattern and that an unmatched merchant comes back under the uncategorised name rather than raising here — whether that stops the run is the summary's decision, not this function's. Refunds are then netted against the category’s spending, so category assignment must remain available before the refund calculation.

Code: `tests/test_rules.py::test_anything_unmatched_goes_to_a_bucket`, `tests/test_rules.py::test_refunds_net_against_the_category`, `tests/test_rules.py::test_the_first_matching_rule_wins`

#### Recurring payment contracts

Checks that a payment is called recurring only when the same merchant and amount appear across three months, while changing amounts or having only two months does not qualify. This keeps ordinary variable shopping from being mistaken for a fixed commitment.

Code: `tests/test_rules.py::test_a_payment_in_three_months_at_one_amount_is_recurring`, `tests/test_rules.py::test_the_same_merchant_at_different_amounts_is_not`, `tests/test_rules.py::test_two_months_is_not_enough`

#### Summary amount contracts

Checks that monetary totals are rounded once after the amounts have been added together. Rounding each row first could make several fractions total a penny more than the true sum.

Code: `tests/test_rules.py::test_rounding_happens_once_at_the_total`

### End-to-end statement summarization checks

Protects the complete statement-to-summary behavior across the sample banks, including empty input, differing columns, made-date boundaries, sign conversion, transfers, duplicates, refunds, recurring payments, and stable output. The fixtures keep these cases together because each statement exercises a different part of the real summarization flow, so changing the pipeline without preserving its policy order or visible results will fail here. The shipped rules are read once and re-used with `unmatched = "bucket"` for most tests, because `current.csv` carries a merchant no rule matches on purpose and stopping for it is its own group of tests.

Code: `tests/test_statements.py::__module__`, `tests/test_statements.py::FIXTURES`, `tests/test_statements.py::SHIPPED`, `tests/test_statements.py::RULES`, `tests/test_statements.py::RAW`, `tests/test_statements.py::run`, `tests/test_statements.py::test_a_refund_nets_within_its_own_month_and_not_across_months`, `tests/test_statements.py::test_a_transaction_made_on_the_31st_is_january`, `tests/test_statements.py::test_an_empty_file_is_not_an_error`, `tests/test_statements.py::test_and_the_ones_made_in_february_are_february`, `tests/test_statements.py::test_different_column_names_still_read`, `tests/test_statements.py::test_no_statement_ends_up_empty`, `tests/test_statements.py::test_periods_come_out_in_order`, `tests/test_statements.py::test_spending_exported_as_positive_is_flipped`, `tests/test_statements.py::test_summarising_twice_gives_the_same_thing`, `tests/test_statements.py::test_the_fixed_commitments_are_found`, `tests/test_statements.py::test_the_repeated_shop_is_counted_once`

#### Transfer visibility checks

Pins what a whole statement does with money moved between the person's own accounts under each of the three settings: shown beside the total and out of it, counted as spending, or left out altogether. Both legs of every transfer survive the duplicate rule, a standing order into savings reaches the recurring list, a period whose only event was a transfer still gets a heading, and transfers that cancel to nothing still print a line — the case that would otherwise look exactly like a statement with no transfers in it.

Code: `tests/test_statements.py::test_both_legs_of_every_transfer_survive`, `tests/test_statements.py::test_transfers_are_shown_but_left_out_of_the_spending`, `tests/test_statements.py::test_the_standing_order_into_savings_is_a_fixed_commitment`, `tests/test_statements.py::test_transfers_can_be_counted_as_spending_instead`, `tests/test_statements.py::test_transfers_can_be_left_out_altogether`, `tests/test_statements.py::test_a_week_with_nothing_in_it_but_a_transfer_still_appears`, `tests/test_statements.py::test_transfers_that_cancel_out_are_still_shown`

#### Weekly summary checks

Checks the weekly summary against the monthly one on the same statements: the money is cut differently but adds up to the same figure once both are made to merge duplicates alike, a payment made on the 31st and posted on the 2nd lands in the week the bank cleared it while the month leaves it in January, and the recurring list stays monthly whatever the grouping. Where the two summaries disagree on purpose — the weekly merges more rows — the pennies dropped are asserted to be exactly the ones it listed as merged, with both descriptions, while an exact repeat is merged without being listed.

Code: `tests/test_statements.py::REWORDED`, `tests/test_statements.py::test_the_same_statement_by_week`, `tests/test_statements.py::test_grouping_by_week_moves_the_money_around_but_does_not_change_it`, `tests/test_statements.py::test_a_week_is_where_the_bank_posted_it`, `tests/test_statements.py::test_the_fixed_commitments_are_still_monthly_under_a_weekly_summary`, `tests/test_statements.py::test_a_weekly_shop_written_two_ways_is_one_shop_in_the_week`, `tests/test_statements.py::test_and_two_shops_in_the_month_because_nothing_says_they_are_one`, `tests/test_statements.py::test_every_penny_the_weekly_summary_drops_is_one_it_listed`, `tests/test_statements.py::test_what_was_merged_is_listed_with_both_descriptions`, `tests/test_statements.py::test_an_exact_repeat_is_not_worth_listing`

#### Unknown merchant checks

Checks that the shipped rules stop on a merchant no rule matches, name it with the line it was on, and list every unknown merchant at once with how often each appeared rather than the first one each run. The bucket is still asserted to work for anybody who wants it, and a transfer is never treated as an unknown merchant — stopping for wording that matches no pattern by design would make the setting unusable.

Code: `tests/test_statements.py::test_a_merchant_nothing_matches_stops_the_run`, `tests/test_statements.py::test_every_unknown_merchant_is_listed_at_once`, `tests/test_statements.py::test_a_bucket_is_still_there_for_anybody_who_wants_it`, `tests/test_statements.py::test_a_transfer_is_never_an_unknown_merchant`

### Rules file contract tests

Checks that every value the rules use comes from the file and that every mistake in it stops the run saying which rule is at fault — the risk the move to rules.toml introduced, since a typo can now break a run that used to be unbreakable. A minimal rules file is the starting point for each case, so a test bends one thing and nothing else, and the shipped file is loaded separately to prove it is valid and that the order written in it is the order the rules are tried in. The settings are also asserted to be unchangeable once read.

Code: `tests/test_settings.py::__module__`, `tests/test_settings.py::MINIMAL`, `tests/test_settings.py::rules`, `tests/test_settings.py::row`, `tests/test_settings.py::test_the_shipped_rules_load`, `tests/test_settings.py::test_the_order_in_the_file_is_the_order_they_are_tried`, `tests/test_settings.py::test_a_different_file_gives_different_categories`, `tests/test_settings.py::test_the_name_for_unmatched_comes_from_the_file`, `tests/test_settings.py::test_the_transfer_wording_comes_from_the_file`, `tests/test_settings.py::test_which_date_decides_each_summary_comes_from_the_file`, `tests/test_settings.py::test_what_makes_two_rows_one_transaction_comes_from_the_file`, `tests/test_settings.py::test_what_happens_to_transfers_comes_from_the_file`, `tests/test_settings.py::test_what_transfers_are_called_comes_from_the_file`, `tests/test_settings.py::test_how_many_months_make_a_payment_recurring_comes_from_the_file`, `tests/test_settings.py::test_when_to_flip_the_signs_comes_from_the_file`, `tests/test_settings.py::test_the_settings_cannot_be_changed_once_read`, `tests/test_settings.py::test_a_summary_left_out_of_a_per_summary_table_is_an_error`, `tests/test_settings.py::test_a_summary_that_does_not_exist_is_an_error`, `tests/test_settings.py::test_a_date_that_does_not_exist_is_an_error`, `tests/test_settings.py::test_a_way_of_matching_that_does_not_exist_is_an_error`, `tests/test_settings.py::test_a_way_of_handling_an_unknown_merchant_that_does_not_exist`, `tests/test_settings.py::test_a_way_of_handling_transfers_that_does_not_exist_is_an_error`, `tests/test_settings.py::test_a_pattern_that_does_not_compile_stops_the_run`, `tests/test_settings.py::test_a_rule_missing_its_category_says_which_rule`, `tests/test_settings.py::test_a_file_with_no_rules_in_it_is_an_error`, `tests/test_settings.py::test_a_missing_section_says_which_one`, `tests/test_settings.py::test_a_nonsense_recurring_window_is_an_error`, `tests/test_settings.py::test_a_share_outside_nought_to_one_is_an_error`, `tests/test_settings.py::test_a_missing_file_names_the_path`, `tests/test_settings.py::test_a_file_that_is_not_toml_says_so`

### Command-line behaviour tests

Checks the command mostly for which file gets written: a summary lands beside its statement, a weekly run writes its own file instead of overwriting the monthly one, `-` prints instead of writing, and `check` writes nothing at all. The failure paths are pinned too — a statement with an unknown merchant exits 2 and leaves no half-written summary, `check` reports it and carries on through the folder, a missing file is reported rather than traced, and an option that does not exist says so instead of being read as a file name. `boundary.csv` is the fixture for the passing cases because every merchant in it has a rule.

Code: `tests/test_cli.py::__module__`, `tests/test_cli.py::FIXTURES`, `tests/test_cli.py::statement`, `tests/test_cli.py::unknown_merchant`, `tests/test_cli.py::test_a_summary_is_written_beside_the_statement`, `tests/test_cli.py::test_by_week_writes_beside_the_monthly_one_rather_than_over_it`, `tests/test_cli.py::test_a_dash_still_prints_instead_of_writing`, `tests/test_cli.py::test_check_writes_nothing`, `tests/test_cli.py::test_a_merchant_with_no_rule_stops_the_run`, `tests/test_cli.py::test_check_carries_on_past_a_statement_that_stopped`, `tests/test_cli.py::test_a_missing_file_is_reported_not_traced`, `tests/test_cli.py::test_the_flag_on_its_own_is_not_a_command`, `tests/test_cli.py::test_an_option_that_does_not_exist_says_so`

## Package identity metadata

Identifies the Tally package and records its current version so tools and users can recognize the installed release. The module also states that Tally turns bank exports into a monthly summary.

Code: `tally/__init__.py::__module__`
