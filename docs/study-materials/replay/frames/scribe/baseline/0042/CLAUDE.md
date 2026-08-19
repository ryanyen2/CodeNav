# Codebase feature guide

## Preparing extracted text

Text pulled out of a PDF arrives as one long string with a form feed between pages, and the page furniture comes with it: the running header repeated at the top of every page, the page number at the foot. These features turn that string into pages and lines, then take the furniture back out, so the rules that follow are reading the document and not the paper it was printed on.

### Extracted text page model

Splits the raw string into pages at each form feed, and each page into lines.

A line knows three things: its text, which page it came from, and where it sits on that page. That is the whole model, and it is deliberate — everything downstream is a guess made from those three facts. A rule that would need to know a font, a colour, or where the words sat on the physical page cannot be written here without changing what scribe is.

Code: `scribe/lines.py::Document`, `scribe/lines.py::Document.lines`, `scribe/lines.py::Line`, `scribe/lines.py::Line.from_bottom`, `scribe/lines.py::Line.from_top`, `scribe/lines.py::Line.is_blank`, `scribe/lines.py::PAGE_BREAK`, `scribe/lines.py::Page`, `scribe/lines.py::__module__`, `scribe/lines.py::read`

### Page furniture removal

Drops the running header, the footer, and the page number.

A line counts as furniture when it sits near the top or bottom of a page and the same text repeats on at least half the pages, and on no fewer than two of them whichever way the share falls. Half rather than more, because a running header is often missing from the title page and from any page a full-width table took over: at the old 0.6 a five page report needed its header on three pages and kept it on all five when it was on two. The floor of two is what stops the share collapsing to one on a short document, where a threshold of one would make every line near an edge furniture.

Repetition is the only signal available, which has two consequences worth knowing: a one-page letter can never show it, so its header stays; and a real heading that happens to repeat is thrown away with the furniture. This runs before headings are looked for, so nothing later can rescue it.

None of these numbers live here any more — how near an edge counts as near, how long a page has to be for "near an edge" to mean anything, the share and the floor are all fields on `Settings`, passed in by the caller.

Code: `scribe/furniture.py::PAGE_NUMBER`, `scribe/furniture.py::__module__`, `scribe/furniture.py::_near_edge`, `scribe/furniture.py::_normalise`, `scribe/furniture.py::find_repeated`, `scribe/furniture.py::is_page_number`, `scribe/furniture.py::strip`

## Turning extracted text into clean Markdown

Once the furniture is gone, what is left is prose that has been through a typesetter: paragraphs broken into lines, words split at the margin, footnotes stranded at the bottom of the page, and quotation marks that are not the ones on your keyboard. These features put it back together as Markdown.

### Extracted text conversion pipeline

Runs the stages in one fixed order: strip the furniture, find the blocks, collect the footnotes, rejoin the paragraphs, then tidy the characters.

The order is the design. Furniture goes first so a running header is never mistaken for a heading; characters are tidied last so every rule before it sees the text exactly as the PDF gave it. Move a stage and rules start seeing text another stage has already altered.

`convert` takes a `Settings` and threads it through every stage; nothing below reads a module constant. That is what makes converting the same text twice with different settings give two different answers and leave no state behind.

What comes back carries more than the Markdown now, because the report needs it: the settings that were in force, the furniture lines that were dropped and how often each one appeared, and the footnotes in the order they were written out. Furniture is counted with a `Counter` difference rather than by subtracting line totals, so the dropped lines can be named rather than only totalled.

Code: `scribe/convert.py::Converted`, `scribe/convert.py::Converted.summary`, `scribe/convert.py::_Collected`, `scribe/convert.py::__module__`, `scribe/convert.py::_collect_notes`, `scribe/convert.py::_join`, `scribe/convert.py::convert`

### Text block recognition rules

Decides which lines are headings and which are bullets.

A heading is a numbered line — `3.`, `3.1`, `3.1.4 Findings` — of no more than twelve words, which is `Settings.max_heading_words` and the one thing a handbook full of long numbered lists is most likely to want to change. The catch is that a numbered list item looks exactly the same, so the line underneath settles it: a heading has a blank line under it, a wrapped list item runs straight on. That is why `3. We asked each participant to describe what they had understood.` is not a heading — too long, and nothing but prose beneath it.

Code: `scribe/blocks.py::BULLET`, `scribe/blocks.py::NUMBERED`, `scribe/blocks.py::__module__`, `scribe/blocks.py::bullet`, `scribe/blocks.py::collapse_blanks`, `scribe/blocks.py::heading_level`

### Footnote recognition and references

Finds footnotes and turns them into Markdown references.

A note is a numbered line within `Settings.note_depth` lines of the foot of a page — six by default; the same shape in the middle of a page is a list item, so position is what tells them apart. The marker in the prose is digits welded to the end of a word — `comparable.1`. The character before the digits is restricted on purpose: allowing any full stop once made every decimal in the document — `0.8`, `3.14` — into a footnote reference.

Code: `scribe/notes.py::MARKER`, `scribe/notes.py::NOTE_LINE`, `scribe/notes.py::__module__`, `scribe/notes.py::looks_like_note`, `scribe/notes.py::mark`, `scribe/notes.py::split_note`

### Extracted text paragraph reflow

Puts paragraphs back together and repairs words the typesetter broke.

Ordinary line breaks are joined; a blank line, or a line shorter than `Settings.short_line` that ends in a full stop, ends the paragraph. A word split at the margin is rejoined and the hyphen dropped, so `photogram-` + `metry` becomes `photogrammetry`. Some hyphens belong to the word, though, and `Settings.keep_hyphen` is the list of prefixes that keep theirs.

That list is now empty by default, which is a deliberate change: out of the box `well-` + `being` comes back as `wellbeing`. The text alone cannot tell a real compound from a line break, so the twelve prefixes that used to be hard-coded here were a guess made on every corpus's behalf — wrong for any corpus whose compounds are not those, and silently so. They are still written down as `settings.SUGGESTED_KEEP_HYPHEN` for a document to copy into its config file, and `keep_all_hyphens` is the blunt alternative for technical writing where dropping a hyphen does more damage than keeping a typeset one. The cost of the new default is that a document which says nothing loses hyphens it may have wanted.

Code: `scribe/paragraphs.py::HYPHEN_END`, `scribe/paragraphs.py::SENTENCE_END`, `scribe/paragraphs.py::__module__`, `scribe/paragraphs.py::dehyphenate`, `scribe/paragraphs.py::is_break`, `scribe/paragraphs.py::reflow`

### Typeset character normalization

Replaces the typesetter's characters with plain ones: ligatures, curly quotes, en and em dashes, non-breaking spaces.

So `ﬁ` becomes `fi` and a curly `’` becomes `'`. This loses typographic fidelity on purpose — the output is meant to be grepped, diffed, and pasted, and none of those work well against characters you cannot type. A corpus being archived for fidelity wants the opposite, and now says so with `normalise = false` under `[text]` in its config file rather than by deleting the substitution table.

Code: `scribe/text.py::SUBSTITUTIONS`, `scribe/text.py::__module__`, `scribe/text.py::normalise`

## Telling the rules what to do

Every rule above is a guess, and the numbers behind the guesses used to be module constants. That was fine for one document and wrong for two: a handbook whose numbered lists run long needs a different heading cut-off from a report whose headings are terse, and there was nowhere to say so short of editing the source. These features are where the numbers live now, and how a document asks for different ones.

### Rule settings and the config file

Holds every number the rules used to hard-code, as one frozen `Settings` a caller passes in, and reads it from a `scribe.toml` beside the document.

The defaults are the values the rules used to hard-code, but for the two changes recorded above: `repeat_share` moved from 0.6 to 0.5, and `keep_hyphen` starts empty rather than listing twelve prefixes. Everything else is unchanged, which is the promise that lets a caller pass nothing — as most of the test suite still does — and get the old behaviour. A document block replaces a setting rather than adding to it, so a prefix list named in one block is the whole list for that document.

A section per rule module sets the default for every document in the directory, and an optional `[documents."name.txt"]` block overrides those for one document. Only the document's own directory is searched: walking up the tree would let a file three directories away change the output of a conversion, which is hard to notice and harder to explain. An unknown section or key is a `ConfigError` rather than a shrug, and a bad `[documents."..."]` block is caught at load time even if nobody converts that document today — a misspelled key that quietly does nothing looks like a broken rule, which is a much longer afternoon. `min_repeats = 1` is refused outright, because a line on a single page being a running header means every line near an edge is furniture, which means an empty conversion.

`fixtures/scribe.toml` is the worked example: it gives `memo.txt` `keep_all_hyphens` so its one broken word comes out as `part-signed`, and deliberately leaves `report.txt` alone because that document is justified and full of hyphens the typesetter put in.

Code: `scribe/settings.py::CONFIG_NAME`, `scribe/settings.py::Config`, `scribe/settings.py::Config.for_document`, `scribe/settings.py::Config.names`, `scribe/settings.py::ConfigError`, `scribe/settings.py::DEFAULTS`, `scribe/settings.py::SUGGESTED_KEEP_HYPHEN`, `scribe/settings.py::Settings`, `scribe/settings.py::Settings.min_page_lines`, `scribe/settings.py::_DOCUMENTS`, `scribe/settings.py::_FIELDS`, `scribe/settings.py::_SECTIONS`, `scribe/settings.py::__module__`, `scribe/settings.py::_apply`, `scribe/settings.py::_read_value`, `scribe/settings.py::find`, `scribe/settings.py::load`

## Running conversions

The ways to actually run it: convert one file, print the result instead of writing it, or check a whole folder without touching anything — and a receipt beside the Markdown saying what the conversion did to get there.

### Command-line conversion interface

`convert report.txt` writes `report.md` beside it and a `report.report.md` beside that; `convert report.txt -` prints the Markdown instead and writes nothing at all.

The report file is named after its source rather than being a fixed `report.md`, because a fixed name would clobber the Markdown of any document actually called `report.txt` — and the fixtures include one. `--no-report` writes only the Markdown. Writing to stdout suppresses the report file without being asked, since a command whose whole point is to leave nothing behind should not leave a file behind; the one-line summary goes to stderr in that mode so stdout stays pipeable.

`check fixtures/` converts every file in a folder and writes nothing, printing a line per document. That is the fast way to see what a rule change does to the whole corpus before committing to it. It looks the config up once per document rather than once per run, so a document's settings always come from beside the document — with the current glob that resolves to the same file every time, but it means nothing here has to change the day `check` learns to walk into subdirectories.

`--config FILE` overrides the lookup for either command. The config is resolved before the conversion runs, so a `ConfigError` prints and exits 2 with no file written.

Code: `scribe/cli.py::__module__`, `scribe/cli.py::_config_for`, `scribe/cli.py::_convert_one`, `scribe/cli.py::_report_path`, `scribe/cli.py::_take_flag`, `scribe/cli.py::_take_option`, `scribe/cli.py::main`

### Conversion report

Writes a short Markdown note beside the output saying what the conversion threw away, what it moved, and which settings were in force.

It exists because the lossy steps are invisible in the result: a running header that was removed leaves no trace, and the only way to see it went is to be told. Footnotes carry two numbers — the position at the foot of the finished Markdown and the marker the document itself used — because a report numbering its notes from one on every page yields several called `[^1]`, so the ordinal finds it and the marker confirms it. Furniture is capped at ten distinct lines with a count of the rest, and the notes are deliberately not capped: a capped list of notes cannot be read against the document, and reading it against the document is the whole job.

Settings at their defaults are one line rather than a table, so the settings a document actually changed are what the eye lands on, and flags are spelled the way the config file spells them so a line can be copied back into `scribe.toml`. There is no timestamp on purpose: the report is a function of the input and the settings alone, so a report checked into git only changes when the conversion does.

Code: `scribe/report.py::MOST_FURNITURE`, `scribe/report.py::NOTE_EXCERPT`, `scribe/report.py::__module__`, `scribe/report.py::_changed`, `scribe/report.py::_excerpt`, `scribe/report.py::_furniture`, `scribe/report.py::_notes`, `scribe/report.py::_show`, `scribe/report.py::render`

## Checking conversion

The tests are where the rules are pinned down, because most of them are judgement calls that look arbitrary until you see what they were protecting against.

### End-to-end conversion tests

Runs three sample documents end to end.

Each covers something the others cannot: `report.txt` has furniture, footnotes and numbered headings; `memo.txt` has none of them, which is what makes keeping a running header a live option rather than a hypothetical; `handbook.txt` has deep numbering and a numbered list that must not turn into headings. The fixtures are the specification.

A short section runs each document a second time, the way `fixtures/scribe.toml` asks for, against the same document converted with the built-in defaults. That pairing is what states the cost of the empty hyphen list in public — the memo says `partsigned` with the defaults and `part-signed` with its config block — and what pins a per-document override to the one document it names, since the report and the handbook have to come out byte for byte the same either way.

Code: `tests/test_documents.py::FIXTURES`, `tests/test_documents.py::__module__`, `tests/test_documents.py::configured`, `tests/test_documents.py::md`, `tests/test_documents.py::test_a_numbered_list_does_not_become_headings`, `tests/test_documents.py::test_a_paragraph_broken_across_a_page_is_rejoined`, `tests/test_documents.py::test_a_two_page_memo_keeps_its_first_line`, `tests/test_documents.py::test_a_word_split_across_a_line_break_is_whole_again`, `tests/test_documents.py::test_a_word_split_across_a_page_is_handled_the_same_way`, `tests/test_documents.py::test_and_its_config_block_fixes_it`, `tests/test_documents.py::test_bullets_are_a_tight_list`, `tests/test_documents.py::test_converting_twice_gives_the_same_thing`, `tests/test_documents.py::test_decimals_survive`, `tests/test_documents.py::test_deep_numbering_becomes_deep_headings`, `tests/test_documents.py::test_footnotes_are_collected_at_the_end`, `tests/test_documents.py::test_headings_carry_their_depth`, `tests/test_documents.py::test_ligatures_are_normalised`, `tests/test_documents.py::test_no_document_ends_up_empty`, `tests/test_documents.py::test_no_form_feed_survives`, `tests/test_documents.py::test_no_three_blank_lines_in_a_row`, `tests/test_documents.py::test_the_bullet_character_is_recognised`, `tests/test_documents.py::test_the_config_leaves_the_other_documents_alone`, `tests/test_documents.py::test_the_memo_has_no_headings`, `tests/test_documents.py::test_the_memo_needs_a_hyphen_the_defaults_do_not_keep`, `tests/test_documents.py::test_the_page_numbers_are_gone`, `tests/test_documents.py::test_the_running_header_is_gone_from_the_report`

### Rule policy regression tests

One test per rule, plus a section for the places two rules meet — which is where changing one of them shows up as a break in another.

These record the boundaries: that furniture removal happens before headings, that a decimal is not a footnote marker, which hyphens survive a line break. A rule changed without updating them tends to bring back the layout artefact it was written to remove.

The hyphen pair is worth reading together. One test shows `well-being` surviving when a document asks for it by naming the prefix; the one beside it shows the same input coming back as `wellbeing` when nothing asks. The price of the empty default is stated in the suite rather than left for someone to discover in an output file.

Code: `tests/test_rules.py::__module__`, `tests/test_rules.py::page`, `tests/test_rules.py::test_a_bare_number_at_the_foot_is_a_page_number`, `tests/test_rules.py::test_a_blank_line_always_breaks`, `tests/test_rules.py::test_a_dash_before_a_number_is_not_a_broken_word`, `tests/test_rules.py::test_a_dash_with_no_space_is_prose`, `tests/test_rules.py::test_a_decimal_is_not_a_footnote_marker`, `tests/test_rules.py::test_a_line_on_every_page_near_the_top_is_furniture`, `tests/test_rules.py::test_a_long_numbered_line_is_a_list_item_not_a_heading`, `tests/test_rules.py::test_a_marker_welded_to_a_word_becomes_a_reference`, `tests/test_rules.py::test_a_number_in_the_middle_of_a_page_is_not`, `tests/test_rules.py::test_a_numbered_line_at_the_foot_is_a_note`, `tests/test_rules.py::test_a_real_compound_keeps_its_hyphen_when_a_document_asks`, `tests/test_rules.py::test_a_running_header_with_a_changing_number_still_counts`, `tests/test_rules.py::test_a_short_line_ending_a_sentence_breaks`, `tests/test_rules.py::test_a_single_newline_continues_the_paragraph`, `tests/test_rules.py::test_a_two_page_document_has_no_furniture`, `tests/test_rules.py::test_a_typeset_hyphen_is_dropped`, `tests/test_rules.py::test_a_year_is_not_a_footnote_marker`, `tests/test_rules.py::test_an_unnumbered_line_is_not_a_heading`, `tests/test_rules.py::test_and_by_default_it_does_not`, `tests/test_rules.py::test_furniture_runs_before_headings_and_that_is_load_bearing`, `tests/test_rules.py::test_leading_and_trailing_blanks_go`, `tests/test_rules.py::test_ligatures_and_quotes_are_normalised`, `tests/test_rules.py::test_numbering_gives_the_depth`, `tests/test_rules.py::test_runs_of_blank_lines_become_one`, `tests/test_rules.py::test_the_same_line_in_the_middle_of_a_page_is_not`, `tests/test_rules.py::test_the_three_bullet_marks`

### Settings and config file tests

Pins two promises: that the defaults are what the rules used to hard-code, and that a config file saying something wrong says so loudly.

The first is what lets every other test in the suite go on calling the rules with no settings at all, so it is checked field by field — including the two defaults that deliberately did move, each with the case that moved it. The second is a table of malformed values, an unknown key that has to name the key it was probably meant to be, and a typo in a document block nobody converted, because the error should arrive on the run that introduced it. The last test converts a whole document twice with different settings and once more with none, to prove nothing was left behind in a module.

Code: `tests/test_settings.py::__module__`, `tests/test_settings.py::test_a_config_further_up_the_tree_is_not_found`, `tests/test_settings.py::test_a_document_block_leaves_the_other_settings_alone`, `tests/test_settings.py::test_a_document_block_overrides_that_document_only`, `tests/test_settings.py::test_a_file_that_is_not_toml_is_an_error`, `tests/test_settings.py::test_a_floor_of_one_is_refused`, `tests/test_settings.py::test_a_header_on_two_of_five_pages_is_caught`, `tests/test_settings.py::test_a_section_sets_the_default_for_every_document`, `tests/test_settings.py::test_a_short_page_still_has_no_middle`, `tests/test_settings.py::test_a_typo_in_a_document_nobody_converted_is_still_an_error`, `tests/test_settings.py::test_a_value_of_the_wrong_shape_is_an_error`, `tests/test_settings.py::test_an_unknown_key_is_an_error_and_names_the_alternatives`, `tests/test_settings.py::test_an_unknown_section_is_an_error`, `tests/test_settings.py::test_keeping_every_hyphen_is_a_single_flag`, `tests/test_settings.py::test_no_config_file_means_the_defaults`, `tests/test_settings.py::test_normalising_characters_can_be_turned_off`, `tests/test_settings.py::test_settings_change_the_document_and_leave_nothing_behind`, `tests/test_settings.py::test_the_config_is_found_beside_the_document`, `tests/test_settings.py::test_the_defaults_are_the_values_the_rules_used_to_hard_code`, `tests/test_settings.py::test_the_floor_holds_when_the_share_falls_below_it`, `tests/test_settings.py::test_the_floor_is_a_setting_of_its_own`, `tests/test_settings.py::test_the_heading_word_limit_is_what_separates_a_heading_from_a_list_item`, `tests/test_settings.py::test_the_hyphen_list_is_what_dehyphenate_consults`, `tests/test_settings.py::test_the_hyphen_list_starts_empty`, `tests/test_settings.py::test_the_note_depth_is_what_makes_a_line_a_footnote`, `tests/test_settings.py::test_the_page_threshold_is_what_makes_furniture_possible`, `tests/test_settings.py::test_the_repeat_threshold_is_the_one_default_that_moved`, `tests/test_settings.py::test_the_short_line_length_is_what_ends_a_paragraph`, `tests/test_settings.py::test_the_suggested_list_is_a_config_file_away`, `tests/test_settings.py::write`

### Conversion report tests

Covers what the report says, and which files the command actually leaves on disk.

Mostly it checks that the removals are named, since that is the only place a stripped running header survives. The file-name tests are less obvious than they look: `report.txt` converts to `report.md`, so a report file with a fixed name would overwrite the Markdown of the very document it describes. The rest hold the boundaries the command promises — stdout leaves no files, `check` still writes nothing, a broken config exits before writing, and two documents in one directory really do get different settings.

Code: `tests/test_report.py::FIXTURES`, `tests/test_report.py::__module__`, `tests/test_report.py::copy_fixture`, `tests/test_report.py::rendered`, `tests/test_report.py::test_a_broken_config_stops_the_run_and_writes_nothing`, `tests/test_report.py::test_a_changed_setting_is_named_with_the_default_beside_it`, `tests/test_report.py::test_a_document_with_nothing_removed_says_nothing_about_removals`, `tests/test_report.py::test_a_flag_is_spelled_the_way_the_config_file_spells_it`, `tests/test_report.py::test_a_long_note_is_cut_short`, `tests/test_report.py::test_check_and_convert_agree_on_a_documents_settings`, `tests/test_report.py::test_check_still_writes_nothing`, `tests/test_report.py::test_convert_writes_the_markdown_and_a_report_beside_it`, `tests/test_report.py::test_each_note_carries_the_marker_to_search_for`, `tests/test_report.py::test_it_carries_the_same_counts_as_the_command_prints`, `tests/test_report.py::test_it_names_the_document_and_what_was_written`, `tests/test_report.py::test_it_names_the_furniture_that_was_removed`, `tests/test_report.py::test_it_says_so_when_there_was_no_config_file`, `tests/test_report.py::test_it_says_the_footnotes_moved`, `tests/test_report.py::test_it_says_which_config_file_the_settings_came_from`, `tests/test_report.py::test_no_report_writes_only_the_markdown`, `tests/test_report.py::test_rendering_twice_gives_the_same_thing`, `tests/test_report.py::test_settings_at_their_defaults_are_one_line_not_a_table`, `tests/test_report.py::test_the_notes_are_listed_in_the_order_the_document_ends_with`, `tests/test_report.py::test_the_report_does_not_overwrite_the_markdown_it_describes`, `tests/test_report.py::test_two_documents_in_one_directory_get_different_settings`, `tests/test_report.py::test_two_notes_with_the_same_marker_are_told_apart_by_position`, `tests/test_report.py::test_writing_to_stdout_leaves_no_files_at_all`

## Package identity metadata

Names the package and its version: scribe turns text pulled out of a PDF into clean Markdown.

Code: `scribe/__init__.py::__module__`
