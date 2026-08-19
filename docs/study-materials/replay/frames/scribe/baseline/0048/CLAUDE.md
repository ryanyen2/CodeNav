# Codebase feature guide

## Preparing extracted text

Text pulled out of a PDF arrives as one long string with a form feed between pages, and the page furniture comes with it: the running header repeated at the top of every page, the page number at the foot. These features turn that string into pages and lines, then take the furniture back out, so the rules that follow are reading the document and not the paper it was printed on.

### Extracted text page model

Splits the raw string into pages at each form feed, and each page into lines.

A line knows three things: its text, which page it came from, and where it sits on that page. That is the whole model, and it is deliberate — everything downstream is a guess made from those three facts. A rule that would need to know a font, a colour, or where the words sat on the physical page cannot be written here without changing what scribe is.

Code: `scribe/lines.py::Document`, `scribe/lines.py::Document.lines`, `scribe/lines.py::Line`, `scribe/lines.py::Line.from_bottom`, `scribe/lines.py::Line.from_top`, `scribe/lines.py::Line.is_blank`, `scribe/lines.py::PAGE_BREAK`, `scribe/lines.py::Page`, `scribe/lines.py::__module__`, `scribe/lines.py::read`

### Page furniture removal

Drops the running header, the footer, and the page number.

A line counts as furniture when it sits near the top or bottom of a page and the same text repeats over enough of the document. "Enough" is the larger of a share of the pages and a floor of two: the share is what makes repetition mean anything on a long document, the floor is what carries a short one where a share rounds down to nothing. The share is 0.4, lowered from 0.6 because a running header that starts after a title page or stops before the appendices appears on two pages of five, and 0.6 put the threshold at three and left it sitting in the prose; the cost is on long documents, where a line near an edge on 16 pages of 40 is now furniture where it used to take 24. Repetition is still the only signal available, which has two consequences worth knowing: a document of under three pages is assumed to have none, so a one-page letter keeps its header; and a real heading that happens to repeat is thrown away with the furniture. This runs before headings are looked for, so nothing later can rescue it — which is why `partition` hands back the lines it took out rather than dropping them on the floor, for the conversion report to list. Every number here is now a setting rather than a constant, so a corpus that wants more evidence before a line is dropped can ask for it.

Code: `scribe/furniture.py::PAGE_NUMBER`, `scribe/furniture.py::SETTINGS`, `scribe/furniture.py::Stripped`, `scribe/furniture.py::__module__`, `scribe/furniture.py::_near_edge`, `scribe/furniture.py::_normalise`, `scribe/furniture.py::find_repeated`, `scribe/furniture.py::is_page_number`, `scribe/furniture.py::partition`, `scribe/furniture.py::strip`, `scribe/config.py::FurnitureSettings`

## Turning extracted text into clean Markdown

Once the furniture is gone, what is left is prose that has been through a typesetter: paragraphs broken into lines, words split at the margin, footnotes stranded at the bottom of the page, and quotation marks that are not the ones on your keyboard. These features put it back together as Markdown.

### Extracted text conversion pipeline

Runs the stages in one fixed order: strip the furniture, find the blocks, collect the footnotes, rejoin the paragraphs, then tidy the characters.

The order is the design. Furniture goes first so a running header is never mistaken for a heading; characters are tidied last so every rule before it sees the text exactly as the PDF gave it. Move a stage and rules start seeing text another stage has already altered. Each stage is handed the slice of one `Settings` it needs instead of reading a constant of its own, which is what makes a per-document config possible: the same code converts two documents two ways in one run. `Converted` also carries what the run did rather than only how much of it — the lines removed as furniture, the words rejoined across a line break, the notes moved to the end — each recorded in the loop that performs it, so the conversion report cannot drift out of step with the Markdown it describes.

Code: `scribe/convert.py::Converted`, `scribe/convert.py::Converted.summary`, `scribe/convert.py::_Collected`, `scribe/convert.py::__module__`, `scribe/convert.py::_collect_notes`, `scribe/convert.py::_join`, `scribe/convert.py::convert`

### Text block recognition rules

Decides which lines are headings and which are bullets.

A heading is a numbered line — `3.`, `3.1`, `3.1.4 Findings` — of no more than twelve words. The catch is that a numbered list item looks exactly the same, so the line underneath settles it: a heading has a blank line under it, a wrapped list item runs straight on. That is why `3. We asked each participant to describe what they had understood.` is not a heading — too long, and nothing but prose beneath it. Twelve is `[blocks] max_heading_words`, a setting rather than a constant, because the length that separates the two is a fact about a corpus: raise it and long list items are promoted, lower it and real headings with long titles are missed.

Code: `scribe/blocks.py::BULLET`, `scribe/blocks.py::NUMBERED`, `scribe/blocks.py::SETTINGS`, `scribe/blocks.py::__module__`, `scribe/blocks.py::bullet`, `scribe/blocks.py::collapse_blanks`, `scribe/blocks.py::heading_level`, `scribe/config.py::BlockSettings`

### Footnote recognition and references

Finds footnotes and turns them into Markdown references.

A note is a numbered line near the foot of a page; the same shape in the middle of a page is a list item, so position is what tells them apart, and how near the foot counts is `[notes] foot_zone`. The marker in the prose is digits welded to the end of a word — `comparable.1`. The character before the digits is restricted on purpose: allowing any full stop once made every decimal in the document — `0.8`, `3.14` — into a footnote reference. Collecting them at the end is what Markdown footnotes are, but it is the rejected alternative that is now reachable: `[notes] collect = false` leaves every note where the page had them, marker and all, which is what you want when they are asides meant to be read in place.

Code: `scribe/notes.py::MARKER`, `scribe/notes.py::NOTE_LINE`, `scribe/notes.py::SETTINGS`, `scribe/notes.py::__module__`, `scribe/notes.py::looks_like_note`, `scribe/notes.py::mark`, `scribe/notes.py::split_note`, `scribe/config.py::NoteSettings`

### Extracted text paragraph reflow

Puts paragraphs back together and repairs words the typesetter broke.

Ordinary line breaks are joined; a blank line, or a line ending in a full stop that is shorter than `[paragraphs] short_line`, ends the paragraph. A word split at the margin is rejoined and the hyphen dropped, so `photogram-` + `metry` becomes `photogrammetry`. Some hyphens belong to the word, and the text alone cannot tell a real compound from a line break, so the prefixes that keep theirs are listed rather than guessed — but that list now starts empty and is named per corpus in `[paragraphs] keep_hyphen`, so `well-` + `being` comes back as `wellbeing` until a config file asks for `well`. The twelve prefixes that used to ship with the program were a guess at a corpus nobody had, and which compounds a document contains is a fact about that document; `scribe.toml` carries the old list as somewhere to start, and `keep_all_hyphens` keeps the lot for technical writing full of real compounds. Every word rejoined this way is recorded and handed back, because a dropped hyphen cannot afterwards be told from one that was never there — the conversion report lists them, and that is where a corpus finds out which prefixes it needs.

Code: `scribe/paragraphs.py::HYPHEN_END`, `scribe/paragraphs.py::Reflowed`, `scribe/paragraphs.py::SENTENCE_END`, `scribe/paragraphs.py::SETTINGS`, `scribe/paragraphs.py::__module__`, `scribe/paragraphs.py::dehyphenate`, `scribe/paragraphs.py::is_break`, `scribe/paragraphs.py::reflow`, `scribe/paragraphs.py::reflow_recording`, `scribe/config.py::ParagraphSettings`

### Typeset character normalization

Replaces the typesetter's characters with plain ones: ligatures, curly quotes, en and em dashes, non-breaking spaces.

So `ﬁ` becomes `fi` and a curly `’` becomes `'`. This loses typographic fidelity on purpose — the output is meant to be grepped, diffed, and pasted, and none of those work well against characters you cannot type. A corpus being archived for fidelity wants the opposite, and rather than deleting the table it sets `[text] normalise = false`; `[text.extra]` is folded in after the built-in table, so a corpus can add a character it does not know about or override one it does.

Code: `scribe/text.py::SETTINGS`, `scribe/text.py::SUBSTITUTIONS`, `scribe/text.py::__module__`, `scribe/text.py::normalise`, `scribe/config.py::TextSettings`

## Running conversions

The ways to actually run it: convert one file, print the result instead of writing it, or check a whole folder without touching anything. What the rules are tuned by on a given run, and the note left behind saying what that run did, live here too.

### Command-line conversion interface

`convert report.txt` writes `report.md` beside it, and `report.report.md` next to that; `convert report.txt -` prints the Markdown instead and writes no report, because the point of stdout is to pipe the document somewhere. The one-line summary follows the same reasoning: it goes to stderr when the Markdown is going to stdout, since `convert x.txt - > x.md` used to staple a line of statistics onto the end of the document.

`check fixtures/` converts every file in a folder and writes nothing, printing a line per document plus the config it found. That is the fast way to see what a rule change does to the whole corpus before committing to it, and naming the config is part of that: a run that silently used the wrong one would be hard to notice.

`--config PATH` overrides the search for a `scribe.toml`, and `--no-report` suppresses the note for one run. Both are pulled out of the arguments wherever they appear, so the positional arguments stay where they were. A config that will not parse stops the run with the offending key on stderr rather than converting with defaults nobody asked for.

Code: `scribe/cli.py::__module__`, `scribe/cli.py::_convert_one`, `scribe/cli.py::_load_config`, `scribe/cli.py::main`

### Conversion settings and the config file

Every rule in this program makes a judgement that is right for some documents and wrong for others. The values behind those judgements used to be module constants, which meant a corpus with one awkward document had to be converted by editing the source; they are plain data here instead — one frozen dataclass per rule module, gathered into a single `Settings` the pipeline hands out. The defaults are the values the constants held, with two deliberate exceptions argued in the furniture and reflow sections above (`repeat_share`, `keep_hyphen`), so a run with no config file behaves as the program always did apart from those two. The file is `scribe.toml`, listing every setting commented out at its default, found by searching upwards from the document so that one config at the root of a corpus covers everything filed beneath it. Top-level tables are the defaults for every document; a `[document."NAME"]` table, where NAME may be a glob and the last match wins, overrides them for one document, which is what lets a single run convert two documents two ways. Types are checked rather than coerced and an unknown key is an error naming it, because a misspelled setting that silently does nothing is the failure that wastes an afternoon; values that would empty a document rather than tidy it, such as a repeat threshold below two, are refused by the settings class itself so the invariant holds however it was built. What is deliberately not settable is the regular expressions that recognise a heading, a bullet, a note or a page number: the code reads their capture groups by number, so a mistyped pattern would fail somewhere far from the typo, and changing those stays a code change.

Code: `scribe/config.py::BlockSettings`, `scribe/config.py::CONFIG_NAME`, `scribe/config.py::Config`, `scribe/config.py::Config.for_document`, `scribe/config.py::ConfigError`, `scribe/config.py::DEFAULTS`, `scribe/config.py::FurnitureSettings`, `scribe/config.py::FurnitureSettings.__post_init__`, `scribe/config.py::FurnitureSettings.min_page_lines`, `scribe/config.py::NoteSettings`, `scribe/config.py::ParagraphSettings`, `scribe/config.py::ReportSettings`, `scribe/config.py::Settings`, `scribe/config.py::TextSettings`, `scribe/config.py::_SECTIONS`, `scribe/config.py::__module__`, `scribe/config.py::_apply`, `scribe/config.py::_coerce`, `scribe/config.py::_sections`, `scribe/config.py::discover`, `scribe/config.py::find`, `scribe/config.py::load`, `scribe/config.py::parse`

### Conversion report

Converting is lossy in ways the Markdown cannot show: lines are removed, hyphens are dropped, footnotes are moved, and the output of a rule that got it wrong looks exactly like the output of a rule that got it right. So writing a document also writes a short note beside it listing the lossy decisions — the lines taken as furniture, the words rejoined across a line break, the footnotes moved to the end and the settings that were in force — to be checked against the source without reading the two documents side by side. It is named `<stem>.report.md` because `report.txt` already converts to `report.md` and a report called `report.md` would overwrite it, and the whole stem is kept so `survey.2026.md` does not report to the same place as `survey.md`. It carries no timestamp on purpose, so a change in the report is a change in the conversion rather than in the clock, and long lists are tallied and cut off at twelve, since the report is read beside the document rather than instead of it.

Code: `scribe/report.py::LIMIT`, `scribe/report.py::__module__`, `scribe/report.py::_changed`, `scribe/report.py::_excerpt`, `scribe/report.py::_tally`, `scribe/report.py::name_for`, `scribe/report.py::render`, `scribe/config.py::ReportSettings`

## Checking conversion

The tests are where the rules are pinned down, because most of them are judgement calls that look arbitrary until you see what they were protecting against.

### End-to-end conversion tests

Runs three sample documents end to end.

Each covers something the others cannot: `report.txt` has furniture, footnotes and numbered headings; `memo.txt` has none of them, which is what makes keeping a running header a live option rather than a hypothetical; `handbook.txt` has deep numbering and a numbered list that must not turn into headings. The fixtures are the specification.

Code: `tests/test_documents.py::FIXTURES`, `tests/test_documents.py::__module__`, `tests/test_documents.py::md`, `tests/test_documents.py::test_a_numbered_list_does_not_become_headings`, `tests/test_documents.py::test_a_paragraph_broken_across_a_page_is_rejoined`, `tests/test_documents.py::test_a_two_page_memo_keeps_its_first_line`, `tests/test_documents.py::test_a_word_split_across_a_line_break_is_whole_again`, `tests/test_documents.py::test_a_word_split_across_a_page_is_handled_the_same_way`, `tests/test_documents.py::test_bullets_are_a_tight_list`, `tests/test_documents.py::test_converting_twice_gives_the_same_thing`, `tests/test_documents.py::test_decimals_survive`, `tests/test_documents.py::test_deep_numbering_becomes_deep_headings`, `tests/test_documents.py::test_footnotes_are_collected_at_the_end`, `tests/test_documents.py::test_headings_carry_their_depth`, `tests/test_documents.py::test_ligatures_are_normalised`, `tests/test_documents.py::test_no_document_ends_up_empty`, `tests/test_documents.py::test_no_form_feed_survives`, `tests/test_documents.py::test_no_three_blank_lines_in_a_row`, `tests/test_documents.py::test_the_bullet_character_is_recognised`, `tests/test_documents.py::test_the_memo_has_no_headings`, `tests/test_documents.py::test_the_page_numbers_are_gone`, `tests/test_documents.py::test_the_running_header_is_gone_from_the_report`

### Rule policy regression tests

One test per rule, plus a section for the places two rules meet — which is where changing one of them shows up as a break in another.

These record the boundaries: that furniture removal happens before headings, that a decimal is not a footnote marker, which hyphens survive a line break. A rule changed without updating them tends to bring back the layout artefact it was written to remove. They pin the behaviour of the rules on their defaults, which is why `test_a_real_compound_loses_its_hyphen_unless_asked_for` reads the way it does: the shipped list of prefixes went away, and the test that used to assert `well-being` now records the new default and points at the config test that restores it.

Code: `tests/test_rules.py::__module__`, `tests/test_rules.py::page`, `tests/test_rules.py::test_a_bare_number_at_the_foot_is_a_page_number`, `tests/test_rules.py::test_a_blank_line_always_breaks`, `tests/test_rules.py::test_a_dash_before_a_number_is_not_a_broken_word`, `tests/test_rules.py::test_a_dash_with_no_space_is_prose`, `tests/test_rules.py::test_a_decimal_is_not_a_footnote_marker`, `tests/test_rules.py::test_a_line_on_every_page_near_the_top_is_furniture`, `tests/test_rules.py::test_a_long_numbered_line_is_a_list_item_not_a_heading`, `tests/test_rules.py::test_a_marker_welded_to_a_word_becomes_a_reference`, `tests/test_rules.py::test_a_number_in_the_middle_of_a_page_is_not`, `tests/test_rules.py::test_a_numbered_line_at_the_foot_is_a_note`, `tests/test_rules.py::test_a_real_compound_loses_its_hyphen_unless_asked_for`, `tests/test_rules.py::test_a_running_header_with_a_changing_number_still_counts`, `tests/test_rules.py::test_a_short_line_ending_a_sentence_breaks`, `tests/test_rules.py::test_a_single_newline_continues_the_paragraph`, `tests/test_rules.py::test_a_two_page_document_has_no_furniture`, `tests/test_rules.py::test_a_typeset_hyphen_is_dropped`, `tests/test_rules.py::test_a_year_is_not_a_footnote_marker`, `tests/test_rules.py::test_an_unnumbered_line_is_not_a_heading`, `tests/test_rules.py::test_furniture_runs_before_headings_and_that_is_load_bearing`, `tests/test_rules.py::test_leading_and_trailing_blanks_go`, `tests/test_rules.py::test_ligatures_and_quotes_are_normalised`, `tests/test_rules.py::test_numbering_gives_the_depth`, `tests/test_rules.py::test_runs_of_blank_lines_become_one`, `tests/test_rules.py::test_the_same_line_in_the_middle_of_a_page_is_not`, `tests/test_rules.py::test_the_three_bullet_marks`

### Settings and config file tests

Pins the defaults, then checks that a config file says what it looks like it says and that its values actually reach the rules.

The first job is the one worth keeping: `test_the_defaults_are_the_values_the_constants_had` writes the numbers out rather than deriving them, so moving one is a change to this test and has to be argued for in the diff — and it records why the two that moved did (`repeat_share` from 0.6 to 0.4, `keep_hyphen` from twelve prefixes to none). The furniture threshold cases are the argument for 0.4 specifically: two pages in five, and in six and seven, are furniture, while two in forty stays a coincidence, which is what ruled out 0.5. The rest cover the failure modes of a hand-written file — an unknown key, a section nobody exercises, a number where a flag belongs — because a setting that quietly does nothing is worse than one that fails.

Code: `tests/test_config.py::BODY`, `tests/test_config.py::__module__`, `tests/test_config.py::pages`, `tests/test_config.py::surviving`, `tests/test_config.py::tag`, `tests/test_config.py::test_a_corpus_can_add_a_substitution_of_its_own`, `tests/test_config.py::test_a_document_can_have_its_own_prefixes`, `tests/test_config.py::test_a_document_pattern_can_be_a_glob`, `tests/test_config.py::test_a_document_section_changes_only_that_document`, `tests/test_config.py::test_a_header_on_two_pages_in_five_is_furniture`, `tests/test_config.py::test_a_header_on_two_pages_in_six_and_seven_is_furniture_too`, `tests/test_config.py::test_a_line_on_two_pages_of_forty_is_still_a_coincidence`, `tests/test_config.py::test_a_longer_heading_is_allowed_when_the_setting_says_so`, `tests/test_config.py::test_a_number_is_not_accepted_where_a_flag_belongs`, `tests/test_config.py::test_a_prefix_is_opted_into_rather_than_shipped`, `tests/test_config.py::test_a_refused_value_names_the_file_and_the_section`, `tests/test_config.py::test_a_share_outside_nought_to_one_is_refused`, `tests/test_config.py::test_a_threshold_below_two_is_refused`, `tests/test_config.py::test_a_top_level_section_changes_every_document`, `tests/test_config.py::test_a_typo_in_a_document_nobody_converts_is_still_an_error`, `tests/test_config.py::test_a_wider_edge_catches_a_header_that_sits_further_in`, `tests/test_config.py::test_an_unknown_section_is_an_error`, `tests/test_config.py::test_an_unknown_setting_is_an_error_naming_it`, `tests/test_config.py::test_broken_toml_names_the_file`, `tests/test_config.py::test_furniture_detection_can_be_asked_to_wait_for_more_pages`, `tests/test_config.py::test_keeping_every_hyphen_is_one_setting`, `tests/test_config.py::test_no_config_anywhere_is_not_an_error`, `tests/test_config.py::test_normalisation_can_be_turned_off_for_an_archive`, `tests/test_config.py::test_notes_left_in_place_when_they_are_not_collected`, `tests/test_config.py::test_passing_the_defaults_is_the_same_as_passing_nothing`, `tests/test_config.py::test_the_config_is_found_from_a_subdirectory`, `tests/test_config.py::test_the_defaults_are_the_values_the_constants_had`, `tests/test_config.py::test_the_floor_carries_documents_too_short_for_the_share_to_mean_anything`, `tests/test_config.py::test_the_floor_is_a_setting_now`, `tests/test_config.py::test_the_footnote_zone_can_be_narrowed`, `tests/test_config.py::test_the_last_matching_section_wins`, `tests/test_config.py::test_the_prefixes_are_matched_without_case`, `tests/test_config.py::test_the_settings_remember_where_they_came_from`, `tests/test_config.py::test_the_wrong_type_is_an_error`, `tests/test_config.py::test_two_documents_in_one_run_can_be_converted_two_ways`

### Conversion report tests

Checks that the report names the lossy decisions, and that writing it cannot destroy the document it reports on.

The naming test is the one that would cost somebody their output rather than merely annoy them: `report.txt` converts to `report.md`, so the report cannot also be `report.md`. The rest are about being readable and being trustworthy — notes listed in the document's own order rather than sorted, a long note cut short, a document of twenty notes still producing a short report, the same conversion reporting the same thing twice. The command-line half checks the pieces that only exist once files are involved: the report appears beside the Markdown, `check` still writes nothing, stdout stays pure Markdown, and a broken config stops the run before anything is written.

Code: `tests/test_report.py::FIXTURES`, `tests/test_report.py::TOPICS`, `tests/test_report.py::__module__`, `tests/test_report.py::converted`, `tests/test_report.py::corpus`, `tests/test_report.py::notes_out_of_order`, `tests/test_report.py::page_ending_in`, `tests/test_report.py::rendered`, `tests/test_report.py::test_a_broken_config_stops_the_run`, `tests/test_report.py::test_a_document_of_many_notes_is_still_a_short_report`, `tests/test_report.py::test_a_document_with_no_furniture_has_no_furniture_section`, `tests/test_report.py::test_a_long_note_is_cut_short`, `tests/test_report.py::test_a_named_config_is_used`, `tests/test_report.py::test_a_note_is_quoted_by_its_opening_words`, `tests/test_report.py::test_check_writes_nothing`, `tests/test_report.py::test_converting_a_file_writes_the_report_beside_it`, `tests/test_report.py::test_it_counts_a_header_that_repeated`, `tests/test_report.py::test_it_lists_the_footnotes_it_moved`, `tests/test_report.py::test_it_lists_the_words_it_rejoined`, `tests/test_report.py::test_it_names_the_lines_it_removed`, `tests/test_report.py::test_it_says_when_there_was_no_config_at_all`, `tests/test_report.py::test_it_says_which_settings_were_in_force`, `tests/test_report.py::test_nothing_but_the_markdown_goes_to_stdout`, `tests/test_report.py::test_the_config_can_turn_the_report_off`, `tests/test_report.py::test_the_markdown_is_the_conversion_not_the_report`, `tests/test_report.py::test_the_notes_are_listed_in_the_order_the_document_has_them`, `tests/test_report.py::test_the_numbering_is_the_position_not_the_marker`, `tests/test_report.py::test_the_report_can_be_turned_off`, `tests/test_report.py::test_the_report_does_not_overwrite_the_document_it_reports_on`, `tests/test_report.py::test_the_report_is_short`, `tests/test_report.py::test_the_same_conversion_reports_the_same_thing`, `tests/test_report.py::test_two_documents_do_not_share_a_report`, `tests/test_report.py::test_writing_to_stdout_writes_no_report`

## Package identity metadata

Names the package and its version: scribe turns text pulled out of a PDF into clean Markdown. The version is not decoration — the conversion report stamps each note with it, so a report says which scribe produced the document beside it.

Code: `scribe/__init__.py::__module__`, `scribe/__init__.py::__version__`
