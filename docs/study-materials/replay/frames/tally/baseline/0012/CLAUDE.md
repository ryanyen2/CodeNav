# Codebase feature guide

## Reading bank statements

Every bank exports its transactions differently: different column names, and no agreement on whether money going out is negative. These features read whatever arrived and turn it into one common shape, so that every rule after this point can be written once instead of once per bank.

### Bank export row reader

Reads the CSV and maps its columns onto a common row.

Column names are matched against an ordered list of aliases, most specific first, so a file with both `transaction date` and `date` uses the one that means what it says. Every row carries both dates — the date the payment was made and the date the bank posted it, falling back to the made date when the export has only one — because which of them decides a summary is a setting, not a fixed choice. The line number is kept so an unknown merchant can be reported where it was found. A row that cannot be read is skipped rather than guessed at — parsing here, judgement later.

Code: `tally/rows.py::COLUMNS`, `tally/rows.py::DATE_FORMATS`, `tally/rows.py::Row`, `tally/rows.py::Row.is_money_out`, `tally/rows.py::__module__`, `tally/rows.py::_pick`, `tally/rows.py::parse_amount`, `tally/rows.py::parse_date`, `tally/rows.py::read`

### Rules as settings

Every value the rules used to hold as a module constant now comes from `tally/rules.toml`: the merchant patterns and their order, the transfer wording, how many months make a payment recurring, which date decides each summary, what makes two rows one transaction, what happens to transfers, and the share of positive rows that means an export has its signs the other way round.

The rules were changed to read settings so that a rule can be changed without editing code, and so the same statement can be run through two different rule sets. The file is the only source of these values: nothing supplies a default for a missing one, because two copies of a policy drift apart and the copy that loses is the one somebody edited. A file that is missing, unreadable, or says something that cannot be used stops the run and names the piece, rather than turning a bad pattern into a rule that quietly matches nothing. The settings are read once, at the edge — the CLI loads them and passes them down — and are frozen, so no rule can put the policy back somewhere other than the file. The choices a setting may take are listed in code, not in the file, because each is a behaviour some module has to implement.

Code: `tally/settings.py::DATES`, `tally/settings.py::DEFAULT_RULES`, `tally/settings.py::MATCH_ON`, `tally/settings.py::PERIOD_NAMES`, `tally/settings.py::Rules`, `tally/settings.py::RulesError`, `tally/settings.py::SHOW_TRANSFERS`, `tally/settings.py::Settings`, `tally/settings.py::UNMATCHED`, `tally/settings.py::__module__`, `tally/settings.py::_categories`, `tally/settings.py::_choice`, `tally/settings.py::_months`, `tally/settings.py::_per_summary`, `tally/settings.py::_share`, `tally/settings.py::_table`, `tally/settings.py::_text`, `tally/settings.py::_words`, `tally/settings.py::load`, `tally/rules.toml`

`rules.toml` ships inside the package: `pyproject.toml` declares it as package data, because without that an installed tally has no rules to read and stops on its first run.

Code: `pyproject.toml`

### Money handling policies

Two decisions about money that everything downstream depends on: which direction is negative, and when the rounding happens.

Code: `tally/money.py::__module__`

#### Summary total rounding

Rounds once, at the summary, not on every transaction.

Two hundred transactions rounded individually drift by a few pence; rounded once at the end they do not. The cost is that the totals no longer agree line by line with a printed receipt, which is the thing you would want if you were reconciling against one. The penny itself stays in code rather than in `rules.toml`, because it is what the currency is and not something to tune.

Code: `tally/money.py::PENNY`, `tally/money.py::round_total`

#### Transaction sign normalization

Makes spending negative and money coming in positive, whatever the bank did.

One bank writes a £12 coffee as `-12.00`; another writes `12.00` and puts the direction in a separate column. If the share of positive amounts is above the threshold in `rules.toml`, that is taken as the second convention and every sign is flipped. It is a guess, made rather than asked about, so that every rule after this sees one convention.

Code: `tally/money.py::sign_convention`

## Applying transaction policies

With the rows in one shape, these are the rules that decide what actually counts as spending: which period it lands in, what is really a duplicate, what is a transfer rather than a purchase, and which category it belongs to.

### Period assignment

Labels a transaction with the month or the week it belongs to, and which of its two dates decides that is a setting made separately for each summary.

A month is `2026-01`; a week is an ISO week, `2026-W03`, Monday to Sunday with week 01 holding the year's first Thursday. The ISO year is used rather than the calendar year, because the 1st of January can fall in week 52 of the year before and labelling it `2026-W53` would produce a week that sorts before every other week of the year and belongs to neither. The rejected alternative — weeks numbered from the 1st of January — keeps every week inside its own year at the cost of a short week each January.

A card payment made on the 31st can post on the 2nd. The monthly summary is filed by the made date, because it answers "what did I spend in January"; the weekly summary is filed by the posted date, because it is read next to the statement the bank sent. Neither is wrong, so `rules.toml` sets it per summary. The labelling is resolved before any row is read, so an unknown period or date name is an error even on an empty file, rather than a summary that quietly comes back with nothing in it.

This was `months.py`, and grew weeks when the summary did.

Code: `tally/periods.py::PERIODS`, `tally/periods.py::__module__`, `tally/periods.py::label_for`, `tally/periods.py::month_of`, `tally/periods.py::week_of`

### Transfer-aware duplicate filtering

Drops repeated rows so they do not inflate the totals, and reports the ones it merged.

Same date, same amount, and — under `"same wording"` — the same description counts as one. Under `"any wording"` the description is ignored, for banks that word the pending row and the settled row differently; that merges a genuine second purchase of the same amount on the same day, which is the price of it, and why every merge where the wording differed is listed at the end of the summary with both descriptions. Exact repeats are not listed, because there is nothing to see. The bank's own reference would settle it exactly, but half of the exports do not carry one and the ones that do reuse them across statements.

Both halves of the question are answered per summary: what makes two rows the same, and which of the two dates counts as "the same day" — so a weekly summary lined up on the posting date merges the rows the bank posted together. Transfers are exempt: moving £500 from current to savings shows up as two rows that look exactly like a duplicate, and dropping one would leave a £500 payment that never happened. Recognising a transfer is all that happens here; what is done with one is a question about the shape of the summary and is answered in `summary.py`. The wording is the only signal available — an account column would be better, but half of the exports have one account per file and no way to tell which.

Code: `tally/dedupe.py::Merge`, `tally/dedupe.py::Merge.reworded`, `tally/dedupe.py::__module__`, `tally/dedupe.py::drop_duplicates`, `tally/dedupe.py::is_transfer`, `tally/dedupe.py::key`

### Merchant category assignment

Puts each transaction in a category by matching its description against the ordered list of patterns in `rules.toml`.

The first pattern that matches wins, so specific ones sit before broad ones — `shell energy` is a utility and `shell` is fuel, and the other order would file the electricity bill under the car. Nothing but the matching itself lives in this module now; the patterns, their order, and the uncategorised name are settings.

What happens to a merchant nothing matches is `[categories] unmatched`. The default is `"stop"`, because a bucket is a place things go to be forgotten: money filed under "uncategorised" is money nobody looks at again, quietly. Stopping lists every unknown merchant at once with the line each was on, rather than handing them over one run at a time, which is the kind of job that gets abandoned halfway. `"bucket"` remains for anybody who would rather have the summary now and the rules later.

Code: `tally/categories.py::Unmatched`, `tally/categories.py::__module__`, `tally/categories.py::categorise`

### Refund netting policy

Takes money that came back off the category it was spent in.

A £40 refund on a £100 coat leaves £60 of clothing, not £100 of clothing and £40 of income. Refunds are recognised by their positive sign, so income has to be excluded by category before this runs, or a salary would look like a refund of something. Reporting them separately is defensible and is what a tax return wants, because there the gross figure matters.

Code: `tally/periods.py::is_refund`, `tally/periods.py::net`

### Recurring payment detection

Finds the fixed commitments — rent, a subscription — by looking for the same description at the same amount in enough different months.

Both the description and the amount have to match, or a weekly supermarket shop would look like a subscription. How many months is `[recurring] months`, shipped as three rather than two, so a coincidence does not become a commitment. Months are counted by the made date whatever the summary is grouped by: a fixed commitment is a monthly thing, and counting in weeks would find almost nothing.

Code: `tally/recurring.py::__module__`, `tally/recurring.py::find`

## Producing a spending summary

What comes out at the end: totals per category per period, and the ways to ask for them.

### Monthly or weekly spending summary

Adds the transactions up into per-period category totals, and reports what it did with the rest: how many rows it read, what it merged as duplicates, what it treated as transfers, what it could not categorise, which payments look recurring, and which merges are worth a second look.

The policies run before the adding up, so the totals and the notes underneath them are telling the same story. Reordering that changes the numbers: signs are normalised first because every rule below reads them, transfers are recognised before duplicates are dropped, and categories are assigned before refunds are netted.

`by` selects which summary this is — month or week — and it is more than a heading. It picks a whole set of answers out of `rules.toml`: how a period is labelled, which date decides it, and what makes two rows one transaction. The monthly and weekly summaries of one statement can therefore disagree about how many transactions there were, and are meant to.

Transfers are the one thing here that is not a rule applied in order, because what happens to them is a choice about the summary itself. Under `"apart"` — the shipped choice — they are listed beneath each period's total and left out of it, because both alternatives hide something: left out entirely, a standing order into savings never appears; counted as spending, it is money the person still has, and an export carrying both legs would add a move that never happened. The transfer line is printed whenever there were transfers even when they came to nothing, so "they cancelled out" and "there were none" do not look alike, and a period holding nothing but a transfer still gets a heading. Under `"spending"` transfers join the totals under their own name rather than through the merchant rules, which would file them as uncategorised. They count towards the recurring list unless the setting is `"never"`.

Code: `tally/summary.py::Period`, `tally/summary.py::Period.total`, `tally/summary.py::Summary`, `tally/summary.py::Summary.line`, `tally/summary.py::Summary.text`, `tally/summary.py::__module__`, `tally/summary.py::summarise`

### Command-line summary interface

`tally summarise statement.csv` prints the summary; pointed at a folder, `tally check` reads every CSV in it and writes nothing, which is how you see what a rule change does before committing to it. `--by-week` groups by week instead of month, and writes to `.weekly.md` rather than over the monthly file, because the two answer different questions and somebody who asked for both should end up with both.

The settings are loaded once here and passed down; nothing below reads a file, and a mistake in `rules.toml` stops the run with a message naming it. An argument starting with a dash that is not `-` is rejected as an unknown option rather than treated as a filename, so `--by-month` says there is no such option instead of "no such file". Under `check`, a statement that stops on an unknown merchant does not stop the others — seeing the whole folder in one go is the point — and the command exits non-zero if any did.

Code: `tally/cli.py::SUFFIX`, `tally/cli.py::__module__`, `tally/cli.py::main`

## Checking statements

These features verify that statement reading, transaction policies, settings loading, the command line, and the summaries themselves continue to behave correctly across focused rule cases and complete sample statements.

### Rule contract test suite

Keeps the behavior of each transaction rule explicit, including the cases where one policy can change another policy's result. The suite uses small common rows and the shipped `rules.toml`, so changes to parsing, normalization, period labelling, categorization, and summaries fail at the rule boundary instead of being hidden in a larger end-to-end example.

Code: `tests/test_rules.py::RULES`, `tests/test_rules.py::__module__`, `tests/test_rules.py::row`

#### Imported transaction contracts

Checks that bank-exported values become usable transactions without guessing when a row is unreadable, and that spending signs and dates have the expected meaning afterward. A transaction date takes precedence when both dates are exported, and which of the two decides a period is checked as a setting rather than as a fixed rule.

Code: `tests/test_rules.py::test_a_transaction_date_beats_a_posting_date`, `tests/test_rules.py::test_an_export_with_spending_as_positive_is_flipped`, `tests/test_rules.py::test_an_ordinary_export_is_left_alone`, `tests/test_rules.py::test_an_unreadable_row_is_skipped_not_guessed_at`, `tests/test_rules.py::test_the_amount_formats_banks_use`, `tests/test_rules.py::test_the_date_formats_banks_use`, `tests/test_rules.py::test_which_date_decides_the_period_is_a_setting`

#### Period labelling contracts

Checks that weeks are ISO weeks, that a week at a year boundary takes its ISO year so the labels still sort in the order the weeks happened, and that an unknown period or date name is refused by name. One test holds the period list and the settings' list of period names together, so adding a period without telling the settings about it fails here.

Code: `tests/test_rules.py::test_a_week_belongs_to_its_iso_year_not_its_calendar_year`, `tests/test_rules.py::test_a_week_is_an_iso_week`, `tests/test_rules.py::test_an_unknown_date_is_refused_by_name`, `tests/test_rules.py::test_an_unknown_period_is_refused_by_name`, `tests/test_rules.py::test_the_periods_on_offer_are_the_ones_the_settings_check_for`, `tests/test_rules.py::test_weeks_sort_in_the_order_they_happened`

#### Transfer-safe duplicate contracts

Checks that duplicate removal drops only repeated transactions while preserving different rows and both legs of an own-account transfer, and that the same shop written two ways is one thing or two depending on the matching setting. The date compared is the one that summary is lined up on. A transfer pair has the same shape as a duplicate, but removing one leg would make money appear to have been spent.

Code: `tests/test_rules.py::test_a_transfer_is_recognised_by_its_wording`, `tests/test_rules.py::test_a_transfer_pair_is_not_a_duplicate`, `tests/test_rules.py::test_the_day_compared_is_the_one_that_summary_is_lined_up_on`, `tests/test_rules.py::test_the_same_shop_written_two_ways_is_two_things_or_one_by_setting`, `tests/test_rules.py::test_the_same_transaction_twice_is_dropped_once`, `tests/test_rules.py::test_two_transactions_that_differ_are_both_kept`

#### Category and refund contracts

Checks that merchant rules use the first matching pattern and that unmatched spending can land in an uncategorised bucket instead of stopping the summary. Refunds are then netted against the category's spending, so category assignment must remain available before the refund calculation.

Code: `tests/test_rules.py::test_anything_unmatched_goes_to_a_bucket`, `tests/test_rules.py::test_refunds_net_against_the_category`, `tests/test_rules.py::test_the_first_matching_rule_wins`

#### Recurring payment contracts

Checks that a payment is called recurring only when the same merchant and amount appear across three months, while changing amounts or having only two months does not qualify. This keeps ordinary variable shopping from being mistaken for a fixed commitment.

Code: `tests/test_rules.py::test_a_payment_in_three_months_at_one_amount_is_recurring`, `tests/test_rules.py::test_the_same_merchant_at_different_amounts_is_not`, `tests/test_rules.py::test_two_months_is_not_enough`

#### Summary amount contracts

Checks that monetary totals are rounded once after the amounts have been added together. Rounding each row first could make several fractions total a penny more than the true sum.

Code: `tests/test_rules.py::test_rounding_happens_once_at_the_total`

### Settings loading test suite

Protects the promise that `rules.toml` is the only place the policy lives: that the shipped file loads, that a different file genuinely changes the categories, the transfer wording, the dates, the matching, the transfer handling, the recurring window, and the flip threshold, and that the loaded settings cannot be written back into. The rest of the suite is about refusal — a pattern that does not compile, a rule missing its category, a file with no rules, a missing section, a per-summary table missing or inventing a summary, a value outside the fixed set of choices, a nonsense recurring window or share, a missing file, and a file that is not TOML each stop the run with a message naming what went wrong.

Code: `tests/test_settings.py::MINIMAL`, `tests/test_settings.py::__module__`, `tests/test_settings.py::rules`, `tests/test_settings.py::row`, `tests/test_settings.py::test_a_date_that_does_not_exist_is_an_error`, `tests/test_settings.py::test_a_different_file_gives_different_categories`, `tests/test_settings.py::test_a_file_that_is_not_toml_says_so`, `tests/test_settings.py::test_a_file_with_no_rules_in_it_is_an_error`, `tests/test_settings.py::test_a_missing_file_names_the_path`, `tests/test_settings.py::test_a_missing_section_says_which_one`, `tests/test_settings.py::test_a_nonsense_recurring_window_is_an_error`, `tests/test_settings.py::test_a_pattern_that_does_not_compile_stops_the_run`, `tests/test_settings.py::test_a_rule_missing_its_category_says_which_rule`, `tests/test_settings.py::test_a_share_outside_nought_to_one_is_an_error`, `tests/test_settings.py::test_a_summary_left_out_of_a_per_summary_table_is_an_error`, `tests/test_settings.py::test_a_summary_that_does_not_exist_is_an_error`, `tests/test_settings.py::test_a_way_of_handling_an_unknown_merchant_that_does_not_exist`, `tests/test_settings.py::test_a_way_of_handling_transfers_that_does_not_exist_is_an_error`, `tests/test_settings.py::test_a_way_of_matching_that_does_not_exist_is_an_error`, `tests/test_settings.py::test_how_many_months_make_a_payment_recurring_comes_from_the_file`, `tests/test_settings.py::test_the_name_for_unmatched_comes_from_the_file`, `tests/test_settings.py::test_the_order_in_the_file_is_the_order_they_are_tried`, `tests/test_settings.py::test_the_settings_cannot_be_changed_once_read`, `tests/test_settings.py::test_the_shipped_rules_load`, `tests/test_settings.py::test_the_transfer_wording_comes_from_the_file`, `tests/test_settings.py::test_what_happens_to_transfers_comes_from_the_file`, `tests/test_settings.py::test_what_makes_two_rows_one_transaction_comes_from_the_file`, `tests/test_settings.py::test_what_transfers_are_called_comes_from_the_file`, `tests/test_settings.py::test_when_to_flip_the_signs_comes_from_the_file`, `tests/test_settings.py::test_which_date_decides_each_summary_comes_from_the_file`

### Command-line test suite

Covers what the command does to the filesystem and to the exit code: a summary is written beside its statement, `--by-week` writes beside the monthly file rather than over it, `-` prints instead of writing, `check` writes nothing and carries on past a statement that stopped, and mistakes — a missing file, a flag with no command, an option that does not exist, a merchant with no rule — are reported rather than traced.

Code: `tests/test_cli.py::FIXTURES`, `tests/test_cli.py::__module__`, `tests/test_cli.py::statement`, `tests/test_cli.py::unknown_merchant`, `tests/test_cli.py::test_a_dash_still_prints_instead_of_writing`, `tests/test_cli.py::test_a_merchant_with_no_rule_stops_the_run`, `tests/test_cli.py::test_a_missing_file_is_reported_not_traced`, `tests/test_cli.py::test_a_summary_is_written_beside_the_statement`, `tests/test_cli.py::test_an_option_that_does_not_exist_says_so`, `tests/test_cli.py::test_by_week_writes_beside_the_monthly_one_rather_than_over_it`, `tests/test_cli.py::test_check_carries_on_past_a_statement_that_stopped`, `tests/test_cli.py::test_check_writes_nothing`, `tests/test_cli.py::test_the_flag_on_its_own_is_not_a_command`

### End-to-end statement summarization checks

Protects the complete statement-to-summary behavior across the sample banks, including empty input, differing columns, period boundaries, sign conversion, transfers, duplicates, refunds, recurring payments, unknown merchants, and stable output. The fixtures are run through the shipped rules with `unmatched` relaxed to a bucket, so an unknown merchant does not stop the very tests that are about something else, and the stopping behaviour gets its own tests. The fixtures keep these cases together because each statement exercises a different part of the real summarization flow, so changing the pipeline without preserving its policy order or visible results will fail here.

Code: `tests/test_statements.py::FIXTURES`, `tests/test_statements.py::RAW`, `tests/test_statements.py::REWORDED`, `tests/test_statements.py::RULES`, `tests/test_statements.py::SHIPPED`, `tests/test_statements.py::__module__`, `tests/test_statements.py::run`

#### Monthly statement contracts

Checks reading, month boundaries, sign flipping, duplicates, refunds netting within their own month, and the fixed commitments being found, plus the invariants that no statement comes out empty, periods come out in order, and summarising twice gives the same thing.

Code: `tests/test_statements.py::test_a_refund_nets_within_its_own_month_and_not_across_months`, `tests/test_statements.py::test_a_transaction_made_on_the_31st_is_january`, `tests/test_statements.py::test_an_empty_file_is_not_an_error`, `tests/test_statements.py::test_and_the_ones_made_in_february_are_february`, `tests/test_statements.py::test_different_column_names_still_read`, `tests/test_statements.py::test_no_statement_ends_up_empty`, `tests/test_statements.py::test_periods_come_out_in_order`, `tests/test_statements.py::test_spending_exported_as_positive_is_flipped`, `tests/test_statements.py::test_summarising_twice_gives_the_same_thing`, `tests/test_statements.py::test_the_fixed_commitments_are_found`, `tests/test_statements.py::test_the_repeated_shop_is_counted_once`

#### Transfer handling contracts

Checks all three answers to what happens to a transfer — shown apart and left out of the spending, counted as spending, or left out altogether — along with both legs of every transfer surviving deduplication, a standing order into savings counting as a fixed commitment, transfers that cancel out still being shown, and a transfer never being reported as an unknown merchant.

Code: `tests/test_statements.py::test_a_transfer_is_never_an_unknown_merchant`, `tests/test_statements.py::test_both_legs_of_every_transfer_survive`, `tests/test_statements.py::test_the_standing_order_into_savings_is_a_fixed_commitment`, `tests/test_statements.py::test_transfers_are_shown_but_left_out_of_the_spending`, `tests/test_statements.py::test_transfers_can_be_counted_as_spending_instead`, `tests/test_statements.py::test_transfers_can_be_left_out_altogether`, `tests/test_statements.py::test_transfers_that_cancel_out_are_still_shown`

#### Weekly summary contracts

Checks that grouping by week moves the money around without changing it, that a week with nothing but a transfer in it still appears, and that the fixed commitments stay monthly under a weekly summary. Because the weekly summary matches on any wording, it also checks that a shop written two ways is one shop in the week but two in the month, and that every penny the weekly summary drops is one it listed under "Merged".

Code: `tests/test_statements.py::test_a_week_with_nothing_in_it_but_a_transfer_still_appears`, `tests/test_statements.py::test_a_weekly_shop_written_two_ways_is_one_shop_in_the_week`, `tests/test_statements.py::test_and_two_shops_in_the_month_because_nothing_says_they_are_one`, `tests/test_statements.py::test_every_penny_the_weekly_summary_drops_is_one_it_listed`, `tests/test_statements.py::test_grouping_by_week_moves_the_money_around_but_does_not_change_it`, `tests/test_statements.py::test_the_fixed_commitments_are_still_monthly_under_a_weekly_summary`, `tests/test_statements.py::test_the_same_statement_by_week`

#### Unknown merchant and merge reporting contracts

Checks that a merchant nothing matches stops the run and that every unknown merchant is listed at once rather than one per run, that the bucket is still available for anybody who wants it, and that a merge is reported with both descriptions while an exact repeat is not worth listing.

Code: `tests/test_statements.py::test_a_bucket_is_still_there_for_anybody_who_wants_it`, `tests/test_statements.py::test_a_merchant_nothing_matches_stops_the_run`, `tests/test_statements.py::test_an_exact_repeat_is_not_worth_listing`, `tests/test_statements.py::test_every_unknown_merchant_is_listed_at_once`, `tests/test_statements.py::test_what_was_merged_is_listed_with_both_descriptions`

## Package identity metadata

Identifies the Tally package and records its current version so tools and users can recognize the installed release. Its docstring still describes Tally as turning a bank export into a monthly summary, which now understates it — the same statement can be summarised by week.

Code: `tally/__init__.py::__module__`
