# Codebase feature guide

## Reading bank statements

Every bank exports its transactions differently: different column names, and no agreement on whether money going out is negative. These features read whatever arrived and turn it into one common shape, so that every rule after this point can be written once instead of once per bank.

### Bank export row reader

Reads the CSV and maps its columns onto a common row.

Column names are matched against an ordered list of aliases, most specific first, so a file with both `transaction date` and `date` uses the one that means what it says. A row that cannot be read is skipped rather than guessed at — parsing here, judgement later. Each row carries both dates the bank gave — the date the payment was made and the date it posted — and the line it came from, so a rule downstream can pick the date it needs and a message can say where a transaction was.

Code: `tally/rows.py::COLUMNS`, `tally/rows.py::DATE_FORMATS`, `tally/rows.py::Row`, `tally/rows.py::Row.is_money_out`, `tally/rows.py::__module__`, `tally/rows.py::_pick`, `tally/rows.py::parse_amount`, `tally/rows.py::parse_date`, `tally/rows.py::read`

### Money handling policies

Two decisions about money that everything downstream depends on: which direction is negative, and when the rounding happens. The share of positive rows that decides the direction is a setting; the penny is not, because it is what the currency is rather than something to tune.

Code: `tally/money.py::__module__`

#### Summary total rounding

Rounds once, at the summary, not on every transaction.

Two hundred transactions rounded individually drift by a few pence; rounded once at the end they do not. The cost is that the totals no longer agree line by line with a printed receipt, which is the thing you would want if you were reconciling against one.

Code: `tally/money.py::PENNY`, `tally/money.py::round_total`

#### Transaction sign normalization

Makes spending negative and money coming in positive, whatever the bank did.

One bank writes a £12 coffee as `-12.00`; another writes `12.00` and puts the direction in a separate column. If the share of positive amounts in the file is above `[money] flip_when_positive_share_above` in rules.toml — 0.8 as shipped, because an ordinary statement carries a salary and the odd refund — that is taken as the second convention and every sign is flipped. It is a guess, made rather than asked about, so that every rule after this sees one convention.

Code: `tally/money.py::sign_convention`

## Where the rules live

The values the rules read — merchant patterns, transfer wording, how many months make a commitment, and the rest — are in `tally/rules.toml` rather than in the modules that use them. They are read once and handed to each rule as an argument, so the same statement can be run through a different rule set without touching code, and no rule reaches for a global.

### The rules file and its loader

Reads rules.toml into a frozen `Settings` and refuses anything it cannot use.

rules.toml is the only source of these values: nothing supplies a default for a missing one, so a file with a piece missing stops the run and names the piece rather than falling back on a copy in the code. A pattern that does not compile, a setting spelled wrong, a `[periods]` table that says nothing about weeks — each stops the run with a message naming the file and the rule, rather than becoming a rule that matches nothing or a branch that happens to run last. The choices themselves (`SHOW_TRANSFERS`, `PERIOD_NAMES`, `DATES`, `MATCH_ON`, `UNMATCHED`) are in code because each names a behaviour some module implements; the file picks between them and cannot invent a new one. `Settings` is frozen, and the per-summary tables are read-only copies, so a rule cannot write policy back into the settings it was handed. This module imports nothing else from tally, which is what keeps the imports one-way.

Merchant rules keep the order the file has them and are never sorted, because the first match wins and that order is the policy. rules.toml ships inside the installed package, declared as package data in `pyproject.toml`, so an installed tally has rules to read.

Code: `tally/settings.py::DATES`, `tally/settings.py::DEFAULT_RULES`, `tally/settings.py::MATCH_ON`, `tally/settings.py::PERIOD_NAMES`, `tally/settings.py::Rules`, `tally/settings.py::RulesError`, `tally/settings.py::SHOW_TRANSFERS`, `tally/settings.py::Settings`, `tally/settings.py::UNMATCHED`, `tally/settings.py::__module__`, `tally/settings.py::_categories`, `tally/settings.py::_choice`, `tally/settings.py::_months`, `tally/settings.py::_per_summary`, `tally/settings.py::_share`, `tally/settings.py::_table`, `tally/settings.py::_text`, `tally/settings.py::_words`, `tally/settings.py::load`

## Applying transaction policies

With the rows in one shape, these are the rules that decide what actually counts as spending: which period it lands in, what is really a duplicate, what is a transfer rather than a purchase, and which category it belongs to.

### Transaction period assignment

Labels a transaction with the month or the ISO week it belongs to, from whichever of its two dates that summary is lined up on.

Which date decides is `[periods]` in rules.toml, set separately for each summary, and the shipped file answers differently for the two. The monthly summary uses the date the payment was made: a card payment on the 31st that posts on the 2nd is January spending, which is what the person remembers doing. The weekly summary uses the posting date, because it is read next to the statement the bank sends and lines up on the date the bank used. Weeks are ISO weeks — Monday to Sunday, week 01 holding the first Thursday — labelled with the ISO year rather than the date's own year, so the 1st of January can be `2026-W53` and every label sorts in the order it happened. An unknown period or date name is refused, and the lookup happens before any row is read, so a bad name is an error even on an empty file rather than a summary that quietly comes back with no periods in it.

Code: `tally/periods.py::PERIODS`, `tally/periods.py::__module__`, `tally/periods.py::label_for`, `tally/periods.py::month_of`, `tally/periods.py::week_of`

### Transfer-aware duplicate filtering

Drops repeated rows so they do not inflate the totals, and reports the ones it merged.

The date and the amount always have to match. Whether the description does is `[duplicates]` in rules.toml, per summary: under `same wording` the descriptions have to match too, because two rows for the same amount on the same day are more often two things than one thing written twice; under `any wording` the description is ignored, for a bank that words the pending row and the settled row differently. The date compared is the one `[periods]` gives that summary, so a weekly summary lined up on the posting date merges the rows the bank posted together. As shipped the monthly summary matches on the wording and the weekly one does not, which means the two files can disagree about how many transactions there were. Every merge where the wording differed is kept and printed under `## Merged` with both descriptions, since those are the ones a person might want to look at; exact repeats are not listed.

Transfers are exempt from all of it, and that exemption is the whole reason the function is not three lines. A transfer between two of your own accounts exports as two rows on the same day for the same amount with the same wording — exactly the shape of a duplicate — and dropping one leg would leave a lone entry that looks like a real payment. Recognising a transfer is done by wording, from `[transfers] words`; what is then done with one is a question about the shape of the summary and is answered in `summary.py`.

Code: `tally/dedupe.py::Merge`, `tally/dedupe.py::Merge.reworded`, `tally/dedupe.py::__module__`, `tally/dedupe.py::drop_duplicates`, `tally/dedupe.py::is_transfer`, `tally/dedupe.py::key`

### Merchant category assignment

Puts each transaction in a category by matching its description against the patterns in rules.toml, in the order the file has them.

The first pattern that matches wins, so specific ones sit before broad ones — `shell energy` is above `shell` or the electricity bill ends up in the car. What happens to a merchant nothing matches is `[categories] unmatched`. Under `bucket`, which is what ships, it is filed under the name `[categories] uncategorised` gives and the run goes on, with the count line at the end saying how many rows landed there. Under `stop` the run raises instead, listing every unmatched merchant at once with how many rows each had and the line the first was on, and writes no summary. The list is gathered whole rather than reported one merchant per run, and the decision is taken in `summary.py` once the entire statement has been read.

Code: `tally/categories.py::Unmatched`, `tally/categories.py::__module__`, `tally/categories.py::categorise`

### Refund netting policy

Takes money that came back off the category it was spent in.

A £40 refund on a £100 coat leaves £60 of clothing, not £100 of clothing and £40 of income. Refunds are recognised by their positive sign, so income has to be excluded by category before this runs, or a salary would look like a refund of something.

Code: `tally/periods.py::is_refund`, `tally/periods.py::net`

### Recurring payment detection

Finds the fixed commitments — rent, a subscription, a standing order into savings — by looking for the same description at the same amount in at least `[recurring] months` different months.

Both the description and the amount have to match, or a weekly supermarket shop would look like a subscription. Three months rather than two, as shipped, so a coincidence does not become a commitment. Months are counted by the date the payment was made whatever the summary is grouped by, because that is when a commitment falls; counting in weeks would find almost nothing.

Code: `tally/recurring.py::__module__`, `tally/recurring.py::find`

## Producing a spending summary

What comes out at the end: totals per category per month or per week, and the ways to ask for them.

### Period spending summary

Adds the transactions up into per-period category totals, and reports what it did with the rest: how many rows it read, what it merged as duplicates, what it treated as transfers, what it could not categorise, which payments look recurring, and which reworded rows were counted as one.

The policies run before the adding up, so the totals and the notes underneath them are telling the same story. Reordering that changes the numbers. Every rule is handed the settings it needs, read once by whoever called this.

`by` — `month` or `week` — is more than which heading a row lands under: rules.toml answers three questions per summary, and this is which set of answers to use. How a period is labelled, which of the two dates decides it, and what makes two rows one transaction. The monthly and weekly summaries of one statement can therefore disagree about how many transactions there were.

Transfers are the one thing here that is not simply a rule applied in order, because what happens to them is a choice about the summary itself, taken from `[transfers] show`. Under `apart`, which ships, the money moved is listed under each period beneath its total and marked as not being in it, and the line is printed whenever there were transfers even when they cancelled out, so "they netted to nothing" and "there were none" do not look alike. Under `spending` the transfers join the totals under the transfer category name, named rather than put through the merchant rules. Under `never` they are left out altogether and out of the recurring list as well. A period whose only event was a transfer still gets a heading. The uncategorised bucket is reported in the count line rather than standing in the category list, since it is not a category anybody chose.

Code: `tally/summary.py::Period`, `tally/summary.py::Period.total`, `tally/summary.py::Summary`, `tally/summary.py::Summary.line`, `tally/summary.py::Summary.text`, `tally/summary.py::__module__`, `tally/summary.py::summarise`

### Command-line summary interface

`tally summarise statement.csv` writes the summary beside the input, or prints it when the second argument is `-`; pointed at a folder, `check` reads every CSV in it and writes nothing, which is how you see what a rule change does before committing to it. `--by-week` groups by week instead of month, and the weekly summary is written to its own `.weekly.md` file rather than over the monthly one, so somebody who ran both ends up with both.

The rules are read once here and passed down; nothing below reads a file, and a mistake in rules.toml stops the run with a message naming it. Anything starting with a dash that is not `-` is reported as an unknown option rather than taken for a file name. A statement whose merchants stop the run is reported and, under `check`, does not stop the others — the point of `check` is to see the whole folder in one go — and the count line at the end says how many stopped, with the exit code saying no.

Code: `tally/cli.py::SUFFIX`, `tally/cli.py::__module__`, `tally/cli.py::main`

## Checking statements

These features verify that statement reading, transaction policies, the rules file, the summaries and the command continue to behave correctly across focused rule cases, complete sample statements, and the files the command writes.

### Rule contract test suite

Keeps the behavior of each transaction rule explicit, including the cases where one policy can change another policy’s result. The suite uses small common rows so changes to parsing, normalization, categorization, and summaries fail at the rule boundary instead of being hidden in a larger end-to-end example. The rules the tool ships with are loaded once and handed to every test, rather than reached for inside a module, which is the point of the settings being an argument.

Code: `tests/test_rules.py::RULES`, `tests/test_rules.py::__module__`, `tests/test_rules.py::row`

#### Imported transaction contracts

Checks that bank-exported values become usable transactions without guessing when a row is unreadable, and that spending signs have the expected meaning afterward. A transaction date takes precedence when both dates are exported, so month-end spending is not shifted.

Code: `tests/test_rules.py::test_a_transaction_date_beats_a_posting_date`, `tests/test_rules.py::test_an_export_with_spending_as_positive_is_flipped`, `tests/test_rules.py::test_an_ordinary_export_is_left_alone`, `tests/test_rules.py::test_an_unreadable_row_is_skipped_not_guessed_at`, `tests/test_rules.py::test_the_amount_formats_banks_use`, `tests/test_rules.py::test_the_date_formats_banks_use`

#### Period labelling contracts

Checks that which date decides a period is a setting, with one row made on the 31st and posted on the 2nd landing in a different month and week depending on the answer. Weeks are pinned to ISO weeks and to the ISO year, including a New Year's Day that falls in week 53 of the year before, so that labels sorted as text come out in the order they happened. An unknown period or date name is refused by name, and the periods on offer are checked against the names the settings validate rules.toml against, since the two lists live in different modules and have to agree.

Code: `tests/test_rules.py::test_a_week_belongs_to_its_iso_year_not_its_calendar_year`, `tests/test_rules.py::test_a_week_is_an_iso_week`, `tests/test_rules.py::test_an_unknown_date_is_refused_by_name`, `tests/test_rules.py::test_an_unknown_period_is_refused_by_name`, `tests/test_rules.py::test_the_periods_on_offer_are_the_ones_the_settings_check_for`, `tests/test_rules.py::test_weeks_sort_in_the_order_they_happened`, `tests/test_rules.py::test_which_date_decides_the_period_is_a_setting`

#### Transfer-safe duplicate contracts

Checks that duplicate removal drops only repeated transactions while preserving different rows and both legs of an own-account transfer, under both summaries. A transfer pair has the same shape as a duplicate, but removing one leg would make money appear to have been spent. The same shop written two ways is two things under `same wording` and one under `any wording`, with the merge recorded and both descriptions kept, and the day compared is the one that summary is lined up on, so rows made a day apart but posted together are one transaction to the weekly summary.

Code: `tests/test_rules.py::test_a_transfer_is_recognised_by_its_wording`, `tests/test_rules.py::test_a_transfer_pair_is_not_a_duplicate`, `tests/test_rules.py::test_the_day_compared_is_the_one_that_summary_is_lined_up_on`, `tests/test_rules.py::test_the_same_shop_written_two_ways_is_two_things_or_one_by_setting`, `tests/test_rules.py::test_the_same_transaction_twice_is_dropped_once`, `tests/test_rules.py::test_two_transactions_that_differ_are_both_kept`

#### Category and refund contracts

Checks that merchant rules use the first matching pattern and that unmatched spending gets the uncategorised name instead of stopping the summary. Refunds are then netted against the category’s spending, so category assignment must remain available before the refund calculation.

Code: `tests/test_rules.py::test_anything_unmatched_goes_to_a_bucket`, `tests/test_rules.py::test_refunds_net_against_the_category`, `tests/test_rules.py::test_the_first_matching_rule_wins`

#### Recurring payment contracts

Checks that a payment is called recurring only when the same merchant and amount appear across three months, while changing amounts or having only two months does not qualify. This keeps ordinary variable shopping from being mistaken for a fixed commitment.

Code: `tests/test_rules.py::test_a_payment_in_three_months_at_one_amount_is_recurring`, `tests/test_rules.py::test_the_same_merchant_at_different_amounts_is_not`, `tests/test_rules.py::test_two_months_is_not_enough`

#### Summary amount contracts

Checks that monetary totals are rounded once after the amounts have been added together. Rounding each row first could make several fractions total a penny more than the true sum.

Code: `tests/test_rules.py::test_rounding_happens_once_at_the_total`

### Rules file test suite

Covers the file the rules now come from, and what happens when it is wrong. One half checks that each value really is read from the file and not from the code: a different file gives different categories, different transfer wording, a different unmatched name, a different recurring window, a different sign-flip threshold. The other half checks that every mistake stops the run and says which rule — a pattern that does not compile, a rule with no category, a file with no rules at all, a missing section, a setting outside the choices that exist, a per-summary table that leaves a summary out or names one that does not exist, a missing or unparseable file — because a broken pattern would otherwise become a rule that matches nothing and a category would quietly disappear from a summary. The shipped rules.toml is loaded and checked here too, including that its merchant order survives loading, so a typo committed to it fails in the tests rather than on the next run.

Code: `tests/test_settings.py::MINIMAL`, `tests/test_settings.py::__module__`, `tests/test_settings.py::row`, `tests/test_settings.py::rules`, `tests/test_settings.py::test_a_date_that_does_not_exist_is_an_error`, `tests/test_settings.py::test_a_different_file_gives_different_categories`, `tests/test_settings.py::test_a_file_that_is_not_toml_says_so`, `tests/test_settings.py::test_a_file_with_no_rules_in_it_is_an_error`, `tests/test_settings.py::test_a_missing_file_names_the_path`, `tests/test_settings.py::test_a_missing_section_says_which_one`, `tests/test_settings.py::test_a_nonsense_recurring_window_is_an_error`, `tests/test_settings.py::test_a_pattern_that_does_not_compile_stops_the_run`, `tests/test_settings.py::test_a_rule_missing_its_category_says_which_rule`, `tests/test_settings.py::test_a_share_outside_nought_to_one_is_an_error`, `tests/test_settings.py::test_a_summary_left_out_of_a_per_summary_table_is_an_error`, `tests/test_settings.py::test_a_summary_that_does_not_exist_is_an_error`, `tests/test_settings.py::test_a_way_of_handling_an_unknown_merchant_that_does_not_exist`, `tests/test_settings.py::test_a_way_of_handling_transfers_that_does_not_exist_is_an_error`, `tests/test_settings.py::test_a_way_of_matching_that_does_not_exist_is_an_error`, `tests/test_settings.py::test_how_many_months_make_a_payment_recurring_comes_from_the_file`, `tests/test_settings.py::test_the_name_for_unmatched_comes_from_the_file`, `tests/test_settings.py::test_the_order_in_the_file_is_the_order_they_are_tried`, `tests/test_settings.py::test_the_settings_cannot_be_changed_once_read`, `tests/test_settings.py::test_the_shipped_rules_load`, `tests/test_settings.py::test_the_transfer_wording_comes_from_the_file`, `tests/test_settings.py::test_what_happens_to_transfers_comes_from_the_file`, `tests/test_settings.py::test_what_makes_two_rows_one_transaction_comes_from_the_file`, `tests/test_settings.py::test_what_transfers_are_called_comes_from_the_file`, `tests/test_settings.py::test_when_to_flip_the_signs_comes_from_the_file`, `tests/test_settings.py::test_which_date_decides_each_summary_comes_from_the_file`

### End-to-end statement summarization checks

Protects the complete statement-to-summary behavior across the sample banks. The fixtures are read once and run against the shipped rules, with copies that vary one setting where a test is about that setting — the `bucket` and `stop` answers for an unmatched merchant, and each of the three answers for transfers.

Code: `tests/test_statements.py::FIXTURES`, `tests/test_statements.py::RAW`, `tests/test_statements.py::RULES`, `tests/test_statements.py::SHIPPED`, `tests/test_statements.py::STOPS`, `tests/test_statements.py::__module__`, `tests/test_statements.py::run`

#### Current account contracts

Checks the repeated shop counted once, both legs of every transfer surviving, and the refund netting inside its own month rather than across months. The 300 a month into savings is visible under each period and left out of its total, is found as a fixed commitment, and moves into the totals under the transfer category when transfers are counted as spending or vanishes from the summary and the recurring list when they are not shown at all.

Code: `tests/test_statements.py::test_a_refund_nets_within_its_own_month_and_not_across_months`, `tests/test_statements.py::test_both_legs_of_every_transfer_survive`, `tests/test_statements.py::test_the_fixed_commitments_are_found`, `tests/test_statements.py::test_the_repeated_shop_is_counted_once`, `tests/test_statements.py::test_the_standing_order_into_savings_is_a_fixed_commitment`, `tests/test_statements.py::test_transfers_are_shown_but_left_out_of_the_spending`, `tests/test_statements.py::test_transfers_can_be_counted_as_spending_instead`, `tests/test_statements.py::test_transfers_can_be_left_out_altogether`

#### Other bank and month boundary contracts

Checks that a different set of column names still reads, that an export with spending as positive comes out as money out, and that a transaction made on the 31st and posted in the next month is counted in the month it was made.

Code: `tests/test_statements.py::test_a_transaction_made_on_the_31st_is_january`, `tests/test_statements.py::test_and_the_ones_made_in_february_are_february`, `tests/test_statements.py::test_different_column_names_still_read`, `tests/test_statements.py::test_spending_exported_as_positive_is_flipped`

#### Weekly summary contracts

Checks the same statements grouped by week: the labels and the count line say weeks, and with both summaries made to merge duplicates alike the whole statement still adds up to the same figure, so the grouping is neither dropping rows nor counting them twice. A week whose only event was a transfer still appears, transfers that cancel out are still printed rather than left silent, and the recurring list is the same as the monthly one because commitments are counted in months.

Code: `tests/test_statements.py::test_a_week_with_nothing_in_it_but_a_transfer_still_appears`, `tests/test_statements.py::test_grouping_by_week_moves_the_money_around_but_does_not_change_it`, `tests/test_statements.py::test_the_fixed_commitments_are_still_monthly_under_a_weekly_summary`, `tests/test_statements.py::test_the_same_statement_by_week`, `tests/test_statements.py::test_transfers_that_cancel_out_are_still_shown`

#### Reworded row contracts

Checks a hand-written statement where one shop is written two ways for the same amount on the same day: one shop in the week, two in the month, and the difference between the two totals is exactly the rows the weekly summary listed under `## Merged` and nothing else. The merge is printed with both descriptions and the date it was matched on; an exact repeat is counted and not listed, because there is nothing to look at.

Code: `tests/test_statements.py::REWORDED`, `tests/test_statements.py::test_a_weekly_shop_written_two_ways_is_one_shop_in_the_week`, `tests/test_statements.py::test_an_exact_repeat_is_not_worth_listing`, `tests/test_statements.py::test_and_two_shops_in_the_month_because_nothing_says_they_are_one`, `tests/test_statements.py::test_every_penny_the_weekly_summary_drops_is_one_it_listed`, `tests/test_statements.py::test_what_was_merged_is_listed_with_both_descriptions`

#### Unmatched merchant contracts

Checks both answers for a merchant no rule matches: filed under one name and counted in the count line, or stopping the run with every unknown merchant listed at once — each with how many rows it had and where the first was — rather than the next one each run. A transfer is never an unknown merchant, since its wording matches no merchant rule and stopping for one would make the setting unusable for anybody who moves money.

Code: `tests/test_statements.py::test_a_merchant_nothing_matches_can_stop_the_run`, `tests/test_statements.py::test_a_transfer_is_never_an_unknown_merchant`, `tests/test_statements.py::test_an_unmatched_merchant_is_filed_under_one_name`, `tests/test_statements.py::test_every_unknown_merchant_is_listed_at_once`

#### Whole-statement contracts

Checks what has to hold for every fixture: no statement ends up empty, periods come out in order under both groupings — weeks being the interesting half, since they are sorted as text and only hold because the number is padded and the year is the ISO year — summarising twice gives the same thing, and an empty file is not an error.

Code: `tests/test_statements.py::test_an_empty_file_is_not_an_error`, `tests/test_statements.py::test_no_statement_ends_up_empty`, `tests/test_statements.py::test_periods_come_out_in_order`, `tests/test_statements.py::test_summarising_twice_gives_the_same_thing`

### Command test suite

Covers what the command writes and what it says, which is mostly about which file gets written: a summary beside the statement, and a weekly summary beside the monthly one rather than over it, so somebody who runs both ends up with both. `-` still prints instead of writing, `check` writes nothing, a missing file is reported rather than traced, and an option that does not exist says so instead of coming back as a missing file. With the rules asked to stop on an unmatched merchant, `summarise` writes nothing and reports the merchant, while `check` reports it and carries on through the rest of the folder.

Code: `tests/test_cli.py::FIXTURES`, `tests/test_cli.py::__module__`, `tests/test_cli.py::statement`, `tests/test_cli.py::stopping`, `tests/test_cli.py::test_a_dash_still_prints_instead_of_writing`, `tests/test_cli.py::test_a_merchant_with_no_rule_is_filed_and_the_summary_is_written`, `tests/test_cli.py::test_a_merchant_with_no_rule_stops_the_run`, `tests/test_cli.py::test_a_missing_file_is_reported_not_traced`, `tests/test_cli.py::test_a_summary_is_written_beside_the_statement`, `tests/test_cli.py::test_an_option_that_does_not_exist_says_so`, `tests/test_cli.py::test_by_week_writes_beside_the_monthly_one_rather_than_over_it`, `tests/test_cli.py::test_check_carries_on_past_a_statement_that_stopped`, `tests/test_cli.py::test_check_writes_nothing`, `tests/test_cli.py::test_the_flag_on_its_own_is_not_a_command`, `tests/test_cli.py::unknown_merchant`

## Package identity metadata

Identifies the Tally package and records its current version so tools and users can recognize the installed release. The module also states that Tally turns bank exports into a monthly summary.

Code: `tally/__init__.py::__module__`
