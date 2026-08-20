# Codebase feature guide

## Reading bank statements

Every bank exports its transactions differently: different column names, and no agreement on whether money going out is negative. These features read whatever arrived and turn it into one common shape, so that every rule after this point can be written once instead of once per bank.

### Bank export row reader

Reads the CSV and maps its columns onto a common row.

Column names are matched against an ordered list of aliases, most specific first, so a file with both `transaction date` and `date` uses the one that means what it says. A row that cannot be read is skipped rather than guessed at — parsing here, judgement later.

Code: `tally/rows.py::COLUMNS`, `tally/rows.py::DATE_FORMATS`, `tally/rows.py::Row`, `tally/rows.py::Row.is_money_out`, `tally/rows.py::__module__`, `tally/rows.py::_pick`, `tally/rows.py::parse_amount`, `tally/rows.py::parse_date`, `tally/rows.py::read`

### Rule settings

All the policy that used to be module constants — merchant patterns, transfer wording, what makes a duplicate, which date decides a period, how many months make a payment recurring, the sign-flip threshold — now lives as data in `tally/rules.toml`, read once by `tally/settings.py` and handed down as a `Settings` object. No rule module reaches for a global or reads a file itself.

This was decided so a rule could be changed by editing data instead of code, and so the same statement could be run through two different rule sets (the test suite does this) without touching a module. Nothing here supplies a default for a missing value: a rules file with a piece missing stops the run and names the piece, rather than silently falling back to a copy nobody remembers is there. A bad value — an unknown choice, a pattern that will not compile, a share outside 0 and 1 — is also refused at load time rather than discovered mid-run.

Code: `tally/settings.py::__module__`, `tally/settings.py::DEFAULT_RULES`, `tally/settings.py::Rules`, `tally/settings.py::SHOW_TRANSFERS`, `tally/settings.py::PERIOD_NAMES`, `tally/settings.py::DATES`, `tally/settings.py::MATCH_ON`, `tally/settings.py::UNMATCHED`, `tally/settings.py::RulesError`, `tally/settings.py::Settings`, `tally/settings.py::load`, `tally/settings.py::_table`, `tally/settings.py::_text`, `tally/settings.py::_categories`, `tally/settings.py::_words`, `tally/settings.py::_choice`, `tally/settings.py::_per_summary`, `tally/settings.py::_months`, `tally/settings.py::_share`, `tally/rules.toml`

### Money handling policies

Two decisions about money that everything downstream depends on: which direction is negative, and when the rounding happens.

Code: `tally/money.py::__module__`

#### Summary total rounding

Rounds once, at the summary, not on every transaction.

Two hundred transactions rounded individually drift by a few pence; rounded once at the end they do not. The cost is that the totals no longer agree line by line with a printed receipt, which is the thing you would want if you were reconciling against one. The penny itself (`PENNY`) is not a rules.toml setting — it is what the currency is, not something to tune.

Code: `tally/money.py::PENNY`, `tally/money.py::round_total`

#### Transaction sign normalization

Makes spending negative and money coming in positive, whatever the bank did.

One bank writes a £12 coffee as `-12.00`; another writes `12.00` and puts the direction in a separate column. If more than `[money] flip_when_positive_share_above` (in `rules.toml`, 0.8 by default) of the amounts in the file are positive, that is taken as the second convention and every sign is flipped. It is a guess, made rather than asked about, so that every rule after this sees one convention; the threshold is above 0.5 because an ordinary statement carries a salary and the odd refund without needing to be flipped.

Code: `tally/money.py::sign_convention`

## Applying transaction policies

With the rows in one shape, these are the rules that decide what actually counts as spending: which period it lands in, what is really a duplicate, what is a transfer rather than a purchase, and which category it belongs to. Each rule takes a `Settings` object as an argument rather than reading rules.toml itself.

### Period labelling and refund netting

Labels each transaction with the month or week it falls in, and nets refunds against the category they came from.

Both a monthly and a weekly summary can now be produced from one statement (`tally summarise --by-week`), so this module — `tally/periods.py`, renamed from `months.py` — grew a `week_of` alongside `month_of`. A week is labelled by ISO year and week number rather than the calendar year, because the 1st of January can fall in week 52 of the previous ISO year, and using the calendar year would label it `2026-W53`, a week that sorts before every other week of 2026 and belongs to neither. Which of the two dates a row carries — made or posted — decides a given summary's periods is a per-summary choice in `rules.toml`, not a constant: the monthly summary answers "what did I spend in January" (made date, what a person remembers), the weekly summary is meant to be read against the statement the bank sends (posted date, what the bank shows). `label_for` resolves the requested period name and date once, before any row is read, so an unknown name is an error even on an empty file.

Refunds are still recognised by sign alone and netted against the category they were spent in, unchanged from before except that `is_refund` and `net` now live alongside the period rules rather than in `months.py`.

Code: `tally/periods.py::month_of`, `tally/periods.py::week_of`, `tally/periods.py::PERIODS`, `tally/periods.py::label_for`, `tally/periods.py::is_refund`, `tally/periods.py::net`, `tally/periods.py::__module__`

### Transfer-aware duplicate filtering

Drops repeated rows so they do not inflate the totals, and reports the merges that might be wrong.

What counts as "the same row" is now a per-summary setting from `rules.toml`: `[duplicates] month`/`week` chooses "same wording" (date, amount, and description all have to match — the safe default) or "any wording" (date and amount only, for a bank that words a pending row differently from the settled one). The date compared is whichever date that summary is lined up on (`[periods]`), so a weekly summary lined up on the posted date merges what the bank posted together rather than what was made together. "Any wording" can merge a genuine second purchase of the same amount on the same day; every such merge is kept as a `Merge` (kept row, dropped row, day) and the ones where the wording actually differed (`Merge.reworded`) are listed in the summary under "Merged" so the ones that might be wrong are visible rather than silently gone. Transfers remain exempt from deduplication for the same reason as before — moving £500 between accounts looks exactly like a duplicate, and dropping one leg would leave a payment that never happened.

Code: `tally/dedupe.py::__module__`, `tally/dedupe.py::Merge`, `tally/dedupe.py::Merge.reworded`, `tally/dedupe.py::key`, `tally/dedupe.py::is_transfer`, `tally/dedupe.py::drop_duplicates`

### Merchant category assignment

Puts each transaction in a category by matching its description against an ordered list of patterns, now read from `[[categories.rules]]` in `rules.toml` instead of a module constant.

The first pattern that matches wins, so specific ones sit before broad ones in the file — a rule for one merchant has to come before a general one or it never fires. What happens to a merchant nothing matches is itself a setting, `[categories] unmatched`: `"bucket"` files it under the configured `uncategorised` name and the summary carries on (the old, only, behaviour); `"stop"` — the shipped default — collects every unmatched merchant across the whole statement and raises `Unmatched` naming each one and the line it first appeared on, rather than letting a bucket quietly become a place spending goes to be forgotten. `Unmatched` is raised once summary.py has read the whole statement, not from `categorise` itself, since only then is the complete list of unknown merchants known.

Code: `tally/categories.py::__module__`, `tally/categories.py::Unmatched`, `tally/categories.py::categorise`

### Recurring payment detection

Finds the fixed commitments — rent, a subscription — by looking for the same description at the same amount in at least `[recurring] months` different months (three by default, from `rules.toml` rather than a constant).

Both the description and the amount have to match, or a weekly supermarket shop would look like a subscription. Recurring is always counted in months regardless of whether the summary itself is grouped by month or by week, because a fixed commitment is a monthly thing and a weekly window would find almost nothing.

Code: `tally/recurring.py::__module__`, `tally/recurring.py::find`

## Producing a spending summary

What comes out at the end: totals per category per period (month or week), and the ways to ask for them.

### Monthly and weekly spending summary

Adds the transactions up into category totals per period, and reports what it did with the rest: how many rows it read, what it dropped as duplicates, what it treated as transfers, what it could not categorise, and which payments look recurring.

`summarise(raw, settings, by="month")` can now group by month or by week — `Period` (renamed from `Month`) is generic over either — because the same statement is read for two different questions: "what did I spend in January" versus "what does this look like next to the bank's own statement". The policies still run in the order they always have (signs normalised, then transfers found, then duplicates dropped, then categories assigned, then refunds netted), because each later step depends on an earlier one, and grouping by week does not change that order.

What happens to transfers is itself a setting, `[transfers] show`, and it now branches three ways instead of always being excluded from the total: `"apart"` (the shipped default) reports them beside each period's total, separately, so the money is visible but no total claims it as spending; `"never"` drops them from the summary entirely, as if they had not happened; `"spending"` counts them in the totals, filed under the configured `transfer_category` name (not run through the merchant rules, since "Transfer to savings" would otherwise land in uncategorised). A period with nothing in it but a transfer still gets a heading under `"apart"`, so the money is not hidden in exactly the period where hiding it would be likeliest. `Unmatched` is raised here, from the gathered list of every row categories.categorise could not place, rather than from categories.py directly.

Code: `tally/summary.py::__module__`, `tally/summary.py::Period`, `tally/summary.py::Period.total`, `tally/summary.py::Summary`, `tally/summary.py::Summary.line`, `tally/summary.py::Summary.text`, `tally/summary.py::summarise`

### Command-line summary interface

`tally summarise statement.csv` writes the summary beside it; `tally summarise statement.csv -` prints it instead; `tally check fixtures/` reads every CSV in a folder and writes nothing, which is how you see what a rule change does before committing to it. `--by-week` groups by week rather than month, on either command, and writes to a `.weekly.md` file rather than `.md` so a weekly and a monthly summary can both exist beside a statement without one overwriting the other.

Settings are loaded once from `tally/rules.toml` at the start of `main` and passed down; a broken rules file is reported and stops the run before any statement is read. A merchant `Unmatched` under `summarise` stops that one file and is reported; under `check` it is reported but the folder keeps going, since the point of `check` is to see everything in one pass, including which statements have unknown merchants in them — the exit code reflects whether anything stopped. Anything starting with a dash that is not recognised as an option or as the literal `-` (meaning stdout) is reported as an unknown option rather than treated as a filename that happens not to exist.

Code: `tally/cli.py::__module__`, `tally/cli.py::SUFFIX`, `tally/cli.py::main`

## Checking statements

These features verify that statement reading, transaction policies, settings loading, monthly and weekly summaries, and the CLI continue to behave correctly across focused rule cases and complete sample statements.

### Rule contract test suite

Keeps the behavior of each transaction rule explicit, including the cases where one policy can change another policy's result, and where a rule now depends on a per-summary setting rather than a constant. The suite uses small common rows so changes to parsing, normalization, categorization, periods, and summaries fail at the rule boundary instead of being hidden in a larger end-to-end example.

Code: `tests/test_rules.py::__module__`, `tests/test_rules.py::row`

#### Imported transaction contracts

Checks that bank-exported values become usable transactions without guessing when a row is unreadable, and that spending signs and dates have the expected meaning afterward. Made dates determine the spending period even when posting happens later, while a transaction date takes precedence when both dates are exported so month-end spending is not shifted.

Code: `tests/test_rules.py::test_a_transaction_date_beats_a_posting_date`, `tests/test_rules.py::test_an_export_with_spending_as_positive_is_flipped`, `tests/test_rules.py::test_an_ordinary_export_is_left_alone`, `tests/test_rules.py::test_an_unreadable_row_is_skipped_not_guessed_at`, `tests/test_rules.py::test_the_amount_formats_banks_use`, `tests/test_rules.py::test_the_date_formats_banks_use`

#### Period and refund contracts

Checks that a period label comes from whichever date `rules.toml` names for that summary, that weeks follow ISO year-and-week numbering rather than the calendar year (including the year-boundary case), that weeks sort in the order they happened, and that an unknown period or date name is refused rather than silently ignored. Refunds are then netted against the category's spending, so category assignment must remain available before the refund calculation.

Code: `tests/test_rules.py::test_which_date_decides_the_period_is_a_setting`, `tests/test_rules.py::test_refunds_net_against_the_category`, `tests/test_rules.py::test_a_week_is_an_iso_week`, `tests/test_rules.py::test_a_week_belongs_to_its_iso_year_not_its_calendar_year`, `tests/test_rules.py::test_weeks_sort_in_the_order_they_happened`, `tests/test_rules.py::test_an_unknown_period_is_refused_by_name`, `tests/test_rules.py::test_an_unknown_date_is_refused_by_name`, `tests/test_rules.py::test_the_periods_on_offer_are_the_ones_the_settings_check_for`

#### Transfer-safe duplicate contracts

Checks that duplicate removal drops only repeated transactions while preserving different rows and both legs of an own-account transfer, and that "same wording" versus "any wording" (and which date a summary is lined up on) come from settings rather than being fixed. A transfer pair has the same shape as a duplicate, but removing one leg would make money appear to have been spent.

Code: `tests/test_rules.py::test_the_same_transaction_twice_is_dropped_once`, `tests/test_rules.py::test_two_transactions_that_differ_are_both_kept`, `tests/test_rules.py::test_the_same_shop_written_two_ways_is_two_things_or_one_by_setting`, `tests/test_rules.py::test_the_day_compared_is_the_one_that_summary_is_lined_up_on`, `tests/test_rules.py::test_a_transfer_is_recognised_by_its_wording`, `tests/test_rules.py::test_a_transfer_pair_is_not_a_duplicate`

#### Category contracts

Checks that merchant rules use the first matching pattern and keep unmatched spending in an uncategorised bucket rather than stopping the summary, when `unmatched = "bucket"`.

Code: `tests/test_rules.py::test_the_first_matching_rule_wins`, `tests/test_rules.py::test_anything_unmatched_goes_to_a_bucket`

#### Recurring payment contracts

Checks that a payment is called recurring only when the same merchant and amount appear across the configured number of months, while changing amounts or having one month short of that does not qualify. This keeps ordinary variable shopping from being mistaken for a fixed commitment.

Code: `tests/test_rules.py::test_a_payment_in_three_months_at_one_amount_is_recurring`, `tests/test_rules.py::test_the_same_merchant_at_different_amounts_is_not`, `tests/test_rules.py::test_two_months_is_not_enough`

#### Summary amount contracts

Checks that monetary totals are rounded once after the amounts have been added together. Rounding each row first could make several fractions total a penny more than the true sum.

Code: `tests/test_rules.py::test_rounding_happens_once_at_the_total`

### Settings loading contracts

Checks that `settings.load` reads the shipped `rules.toml` into a usable `Settings`, that every per-file value it exposes (category patterns and order, the uncategorised and unmatched names, per-summary period dates and duplicate matching, transfer wording and handling and category, the recurring window, the sign-flip share) really comes from the file rather than a hidden default, and that a missing file, unreadable TOML, missing section, missing per-summary entry, or out-of-range value is refused with a message naming the file and what is wrong — never silently patched over or allowed to fall through. Also checks that a loaded `Settings` is frozen and cannot be written back into.

Code: `tests/test_settings.py::__module__`, `tests/test_settings.py::row`

### End-to-end statement summarization checks

Protects the complete statement-to-summary behavior across the sample banks, including empty input, differing columns, made-date boundaries, sign conversion, transfers under all three `show` settings, duplicates and merged rows, refunds, recurring payments, uncategorised transactions under both `unmatched` settings, grouping by month versus by week, and stable output. The fixtures keep these cases together because each statement exercises a different part of the real summarization flow, so changing the pipeline without preserving its policy order or visible results will fail here.

Code: `tests/test_statements.py::__module__`, `tests/test_statements.py::FIXTURES`, `tests/test_statements.py::run`, `tests/test_statements.py::test_the_repeated_shop_is_counted_once`, `tests/test_statements.py::test_both_legs_of_every_transfer_survive`, `tests/test_statements.py::test_transfers_are_shown_but_left_out_of_the_spending`, `tests/test_statements.py::test_the_standing_order_into_savings_is_a_fixed_commitment`, `tests/test_statements.py::test_transfers_can_be_counted_as_spending_instead`, `tests/test_statements.py::test_transfers_can_be_left_out_altogether`, `tests/test_statements.py::test_a_refund_nets_within_its_own_month_and_not_across_months`, `tests/test_statements.py::test_the_fixed_commitments_are_found`, `tests/test_statements.py::test_different_column_names_still_read`, `tests/test_statements.py::test_spending_exported_as_positive_is_flipped`, `tests/test_statements.py::test_a_transaction_made_on_the_31st_is_january`, `tests/test_statements.py::test_and_the_ones_made_in_february_are_february`, `tests/test_statements.py::test_the_same_statement_by_week`, `tests/test_statements.py::test_grouping_by_week_moves_the_money_around_but_does_not_change_it`, `tests/test_statements.py::test_a_week_with_nothing_in_it_but_a_transfer_still_appears`, `tests/test_statements.py::test_transfers_that_cancel_out_are_still_shown`, `tests/test_statements.py::test_the_fixed_commitments_are_still_monthly_under_a_weekly_summary`, `tests/test_statements.py::test_a_weekly_shop_written_two_ways_is_one_shop_in_the_week`, `tests/test_statements.py::test_and_two_shops_in_the_month_because_nothing_says_they_are_one`, `tests/test_statements.py::test_every_penny_the_weekly_summary_drops_is_one_it_listed`, `tests/test_statements.py::test_what_was_merged_is_listed_with_both_descriptions`, `tests/test_statements.py::test_an_exact_repeat_is_not_worth_listing`, `tests/test_statements.py::test_a_merchant_nothing_matches_stops_the_run`, `tests/test_statements.py::test_every_unknown_merchant_is_listed_at_once`, `tests/test_statements.py::test_a_bucket_is_still_there_for_anybody_who_wants_it`, `tests/test_statements.py::test_a_transfer_is_never_an_unknown_merchant`, `tests/test_statements.py::test_no_statement_ends_up_empty`, `tests/test_statements.py::test_periods_come_out_in_order`, `tests/test_statements.py::test_summarising_twice_gives_the_same_thing`, `tests/test_statements.py::test_an_empty_file_is_not_an_error`

### Command-line interface contracts

Checks the CLI end to end against real fixture files: a summary is written beside the statement, `--by-week` writes to a `.weekly.md` file beside the monthly one rather than over it, `-` prints to stdout instead, `check` writes nothing, an unmatched merchant stops `summarise` but not `check` (which carries on to the rest of the folder and reports how many stopped), a missing file is reported rather than raising, and an unrecognised option is reported by name rather than treated as a filename.

Code: `tests/test_cli.py::__module__`, `tests/test_cli.py::FIXTURES`

## Package identity metadata

Identifies the Tally package and records its current version so tools and users can recognize the installed release. The module also states that Tally turns bank exports into a monthly summary.

Code: `tally/__init__.py::__module__`
