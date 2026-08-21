# Codebase feature guide

## Preparing extracted text

Text pulled out of a PDF arrives as one long string with a form feed between pages, and the page furniture comes with it: the running header repeated at the top of every page, the page number at the foot. These features turn that string into pages and lines, then take the furniture back out, so the rules that follow are reading the document and not the paper it was printed on.

### Extracted text page model

Splits the raw string into pages at each form feed, and each page into lines.

A line knows three things: its text, which page it came from, and where it sits on that page. That is the whole model, and it is deliberate — everything downstream is a guess made from those three facts. A rule that would need to know a font, a colour, or where the words sat on the physical page cannot be written here without changing what scribe is.

Code: `scribe/lines.py::Document`, `scribe/lines.py::Document.lines`, `scribe/lines.py::Line`, `scribe/lines.py::Line.from_bottom`, `scribe/lines.py::Line.from_top`, `scribe/lines.py::Line.is_blank`, `scribe/lines.py::PAGE_BREAK`, `scribe/lines.py::Page`, `scribe/lines.py::__module__`, `scribe/lines.py::read`

### Page furniture removal

Drops the running header, the footer, and the page number.

A line counts as furniture when it sits near the top or bottom of a page and the same text repeats on at least a set share of the pages. Both numbers — how near an edge counts as near, and what share counts as repeating — come from the settings the rules are handed, so one awkward document can be converted differently without editing this file; the defaults are the 2 lines and 0.6 share that were module constants here before. The minimum page length below which "near an edge" means nothing is derived from the edge, at twice it plus two. Repetition is the only signal available, which has two consequences worth knowing: a one-page letter can never show it, so its header stays; and a real heading that happens to repeat is thrown away with the furniture. This runs before headings are looked for, so nothing later can rescue it.

Code: `scribe/furniture.py::PAGE_NUMBER`, `scribe/furniture.py::__module__`, `scribe/furniture.py::_min_page`, `scribe/furniture.py::_near_edge`, `scribe/furniture.py::_normalise`, `scribe/furniture.py::find_repeated`, `scribe/furniture.py::is_page_number`, `scribe/furniture.py::strip`

## Turning extracted text into clean Markdown

Once the furniture is gone, what is left is prose that has been through a typesetter: paragraphs broken into lines, words split at the margin, footnote markers welded to words, and quotation marks that are not the ones on your keyboard. These features put it back together as Markdown.

### Extracted text conversion pipeline

Runs the stages in one fixed order: strip the furniture, flatten the pages into one list of lines, collapse the runs of blanks, walk the lines once deciding headings and bullets and reflowing everything between them, then tidy the characters.

The order is the design. Furniture goes first so a running header is never mistaken for a heading; characters are tidied last so every rule before it sees the text exactly as the PDF gave it. Move a stage and rules start seeing text another stage has already altered. Footnote markers are rewritten inside that single pass over the prose, as each paragraph comes back from the reflow, because the marker rule and the reflow work on the same line of text and walking the document a second time for them bought nothing. The note count on the result is the number of markers found in the prose; the note lines themselves stay where they stood on the page, so a note at the foot of page two comes out as a paragraph between the paragraphs that surrounded it.

Flattening keeps each page boundary as a blank line, which is what lets the reflow decide for itself whether a sentence runs on across the break. `convert` takes the settings for the document as its second argument and hands them to each rule that reads one, defaulting to the built-in values, so the same document can be converted twice with different settings and neither run affects the other.

Code: `scribe/convert.py::Converted`, `scribe/convert.py::Converted.summary`, `scribe/convert.py::__module__`, `scribe/convert.py::_flatten`, `scribe/convert.py::_join`, `scribe/convert.py::convert`

### Text block recognition rules

Decides which lines are headings and which are bullets.

A heading is a numbered line — `3.`, `3.1`, `3.1.4 Findings` — of no more than twelve words. The catch is that a numbered list item looks exactly the same, so the line underneath settles it: a heading has a blank line under it, a wrapped list item runs straight on. That is why `3. We asked each participant to describe what they had understood.` is not a heading — too long, and nothing but prose beneath it.

Code: `scribe/blocks.py::BULLET`, `scribe/blocks.py::MAX_HEADING_WORDS`, `scribe/blocks.py::NUMBERED`, `scribe/blocks.py::__module__`, `scribe/blocks.py::bullet`, `scribe/blocks.py::collapse_blanks`, `scribe/blocks.py::heading_level`

### Footnote recognition and references

Turns the footnote markers in the prose into Markdown references, and recognises a note line at the foot of a page.

The marker in the prose is digits welded to the end of a word — `comparable.1` — and `mark` rewrites it as `comparable.[^1]` in place, in the sentence it belongs to. The character before the digits is restricted on purpose: allowing any full stop once made every decimal in the document — `0.8`, `3.14` — into a footnote reference.

The note-line rule is the other half of the module: a numbered line near the foot of a page is a note, and the same shape in the middle of a page is a list item, so position is what tells them apart. `looks_like_note` and `split_note` hold that rule and its splitting; the pipeline does not call them, so what they recognise is pinned by the rule tests rather than by any document's output.

Code: `scribe/notes.py::MARKER`, `scribe/notes.py::NOTE_LINE`, `scribe/notes.py::__module__`, `scribe/notes.py::looks_like_note`, `scribe/notes.py::mark`, `scribe/notes.py::split_note`

### Extracted text paragraph reflow

Puts paragraphs back together and repairs words the typesetter broke.

Ordinary line breaks are joined; a blank line or a short line ending in a full stop ends the paragraph. A word split at the margin is rejoined and the hyphen dropped, so `photogram-` + `metry` becomes `photogrammetry`. Some hyphens belong to the word, though, so a short list of prefixes keeps theirs: `well-` + `being` stays `well-being`. The text alone cannot tell a real compound from a line break, which is why that list exists. The list comes from the settings the reflow is handed, and the default is the list this module held before the settings existed; hand it an empty list and every hyphen at a line break is dropped.

Code: `scribe/paragraphs.py::HYPHEN_END`, `scribe/paragraphs.py::SENTENCE_END`, `scribe/paragraphs.py::__module__`, `scribe/paragraphs.py::dehyphenate`, `scribe/paragraphs.py::is_break`, `scribe/paragraphs.py::reflow`

### Typeset character normalization

Replaces the typesetter's characters with plain ones: ligatures, curly quotes, en and em dashes, non-breaking spaces; the zero-width space is removed outright.

So `ﬁ` becomes `fi` and a curly `’` becomes `'`. This loses typographic fidelity on purpose — the output is meant to be grepped, diffed, and pasted, and none of those work well against characters you cannot type. The two invisible characters are written in the substitution table as `\u00a0` and `\u200b` escapes rather than as themselves, so a reader can tell which characters they are; as literals they were invisible in the source and in every diff of it.

Code: `scribe/text.py::SUBSTITUTIONS`, `scribe/text.py::__module__`, `scribe/text.py::normalise`

## Running conversions

The ways to actually run it: convert one file, print the result instead of writing it, or check a whole folder without touching anything. A conversion also picks up the settings for the document it is converting, and leaves a note beside the output saying what it did.

### Command-line conversion interface

`convert report.txt` writes `report.md` beside it, and `report.conversion.md` next to that; `convert report.txt -` prints the Markdown to stdout and writes neither file.

`check fixtures/` converts every `.txt` in a folder and writes nothing, printing a line per document. That is the fast way to see what a rule change does to the whole corpus before committing to it.

Both commands load the nearest `scribe.toml` before doing anything, and each document is converted with the settings its own name asks for. A config file that says something scribe cannot act on stops the run: the message goes to stderr prefixed `scribe.toml:` and the exit status is 2, so a typo in a setting name is a failure rather than a silently ignored line.

Code: `scribe/cli.py::__module__`, `scribe/cli.py::_convert_one`, `scribe/cli.py::main`

### Per-document conversion settings

The numbers and lists the rules use, gathered in one frozen dataclass, so converting one awkward document differently does not mean editing the source.

Every default is the value the rule was already using as a module constant, and `convert` with no settings is the behaviour that existed before the settings did — that equivalence is what keeps the library usable on its own and is pinned by a test. `merged` returns a copy with some values replaced and ignores the ones that are absent, which is how a `[document."name"]` section is layered over `[defaults]` and `[defaults]` over the built-ins.

Code: `scribe/settings.py::KEEP_HYPHEN`, `scribe/settings.py::Settings`, `scribe/settings.py::Settings.merged`, `scribe/settings.py::DEFAULTS`, `scribe/settings.py::__module__`

### Reading scribe.toml

Finds the nearest `scribe.toml` at or above a starting folder, reads it, and turns it into the settings for each document.

The file is optional and there is no error in its absence. A `[document."name"]` section applies to the document whose file name matches; anything not named there falls back to `[defaults]`, and anything not in `[defaults]` falls back to the built-in value. Three things are refused rather than accepted quietly, each raising `ConfigError` naming the section it came from: a setting scribe does not have, a `repeat_share` that is not above 0 and at most 1, and an `edge` below 1. Refusing an unknown key is the reason a misspelled setting cannot look like it took effect.

The repo's own `scribe.toml` carries one section: `survey.txt` converts with `repeat_share = 0.4`, because that document's appendix carries its own header over two of its five pages, which is under the default share. Every other document uses the built-in values.

Code: `scribe/config.py::CONFIG_NAME`, `scribe/config.py::Config`, `scribe/config.py::Config.for_document`, `scribe/config.py::ConfigError`, `scribe/config.py::KNOWN`, `scribe/config.py::__module__`, `scribe/config.py::_checked`, `scribe/config.py::find`, `scribe/config.py::load`

### Conversion report beside the output

A short Markdown note written next to the converted file: pages in and paragraphs out, what each rule did, and the settings the run used.

It is written to disk rather than printed so it is still there when somebody opens the Markdown a week later and wonders where a line went. The counts come straight off the `Converted` result, and the furniture line spells the share out as a percentage so the note explains its own numbers.

Code: `scribe/report.py::__module__`, `scribe/report.py::render`, `scribe/report.py::write_report`

## Checking conversion

The tests are where the rules are pinned down, because most of them are judgement calls that look arbitrary until you see what they were protecting against.

### End-to-end conversion tests

Runs three of the four sample documents end to end.

Each covers something the others cannot: `report.txt` has furniture, footnote markers and numbered headings; `memo.txt` has none of them, which is what makes keeping a running header a live option rather than a hypothetical; `handbook.txt` has deep numbering and a numbered list that must not turn into headings. The fixtures are the specification. `survey.txt` is the fourth in the folder — the one whose appendix header repeats on too few pages for the default share — and it is reached through `check` and through the settings tests rather than from here.

The footnote test asserts that the reference sits in the sentence it belongs to, `comparable.[^1]`, and says nothing about where the note text ends up.

Code: `tests/test_documents.py::FIXTURES`, `tests/test_documents.py::__module__`, `tests/test_documents.py::md`, `tests/test_documents.py::test_a_numbered_list_does_not_become_headings`, `tests/test_documents.py::test_a_paragraph_broken_across_a_page_is_rejoined`, `tests/test_documents.py::test_a_two_page_memo_keeps_its_first_line`, `tests/test_documents.py::test_a_word_split_across_a_line_break_is_whole_again`, `tests/test_documents.py::test_a_word_split_across_a_page_is_handled_the_same_way`, `tests/test_documents.py::test_bullets_are_a_tight_list`, `tests/test_documents.py::test_converting_twice_gives_the_same_thing`, `tests/test_documents.py::test_decimals_survive`, `tests/test_documents.py::test_deep_numbering_becomes_deep_headings`, `tests/test_documents.py::test_footnote_markers_are_rewritten_where_they_stand`, `tests/test_documents.py::test_headings_carry_their_depth`, `tests/test_documents.py::test_ligatures_are_normalised`, `tests/test_documents.py::test_no_document_ends_up_empty`, `tests/test_documents.py::test_no_form_feed_survives`, `tests/test_documents.py::test_no_three_blank_lines_in_a_row`, `tests/test_documents.py::test_the_bullet_character_is_recognised`, `tests/test_documents.py::test_the_memo_has_no_headings`, `tests/test_documents.py::test_the_page_numbers_are_gone`, `tests/test_documents.py::test_the_running_header_is_gone_from_the_report`

### Rule policy regression tests

One test per rule, plus a section for the places two rules meet — which is where changing one of them shows up as a break in another.

These record the boundaries: that furniture removal happens before headings, that a decimal is not a footnote marker, which hyphens survive a line break. A rule changed without updating them tends to bring back the layout artefact it was written to remove.

Code: `tests/test_rules.py::__module__`, `tests/test_rules.py::page`, `tests/test_rules.py::test_a_bare_number_at_the_foot_is_a_page_number`, `tests/test_rules.py::test_a_blank_line_always_breaks`, `tests/test_rules.py::test_a_dash_before_a_number_is_not_a_broken_word`, `tests/test_rules.py::test_a_dash_with_no_space_is_prose`, `tests/test_rules.py::test_a_decimal_is_not_a_footnote_marker`, `tests/test_rules.py::test_a_line_on_every_page_near_the_top_is_furniture`, `tests/test_rules.py::test_a_long_numbered_line_is_a_list_item_not_a_heading`, `tests/test_rules.py::test_a_marker_welded_to_a_word_becomes_a_reference`, `tests/test_rules.py::test_a_number_in_the_middle_of_a_page_is_not`, `tests/test_rules.py::test_a_numbered_line_at_the_foot_is_a_note`, `tests/test_rules.py::test_a_real_compound_keeps_its_hyphen`, `tests/test_rules.py::test_a_running_header_with_a_changing_number_still_counts`, `tests/test_rules.py::test_a_short_line_ending_a_sentence_breaks`, `tests/test_rules.py::test_a_single_newline_continues_the_paragraph`, `tests/test_rules.py::test_a_two_page_document_has_no_furniture`, `tests/test_rules.py::test_a_typeset_hyphen_is_dropped`, `tests/test_rules.py::test_a_year_is_not_a_footnote_marker`, `tests/test_rules.py::test_an_unnumbered_line_is_not_a_heading`, `tests/test_rules.py::test_furniture_runs_before_headings_and_that_is_load_bearing`, `tests/test_rules.py::test_leading_and_trailing_blanks_go`, `tests/test_rules.py::test_ligatures_and_quotes_are_normalised`, `tests/test_rules.py::test_numbering_gives_the_depth`, `tests/test_rules.py::test_runs_of_blank_lines_become_one`, `tests/test_rules.py::test_the_same_line_in_the_middle_of_a_page_is_not`, `tests/test_rules.py::test_the_three_bullet_marks`

### Settings and config file tests

Covers the settings defaults, the reading of `scribe.toml`, the rules actually reading what they are handed, and the report.

The defaults are asserted to be the old constants, and converting with `DEFAULTS` is asserted to match converting with nothing, so the settings cannot quietly change any document's output. The rest is mostly what happens when the file says something scribe cannot act on — an unknown setting, a share out of range, an edge below 1 — and that the message names the document section it came from. `five_pages` builds a five-page document with a header on two of them, which is the case that survives the default share and is removed at 0.4: the survey's situation, in miniature.

Code: `tests/test_config.py::FIXTURES`, `tests/test_config.py::__module__`, `tests/test_config.py::five_pages`, `tests/test_config.py::test_a_config_file_is_found_from_a_folder_below_it`, `tests/test_config.py::test_a_document_section_wins_over_the_defaults`, `tests/test_config.py::test_a_header_on_two_pages_of_five_survives_the_default`, `tests/test_config.py::test_a_lower_share_removes_a_header_that_repeats_less_often`, `tests/test_config.py::test_a_setting_scribe_does_not_have_is_refused`, `tests/test_config.py::test_a_share_outside_the_range_is_refused`, `tests/test_config.py::test_an_edge_below_one_is_refused`, `tests/test_config.py::test_an_empty_prefix_list_drops_every_hyphen`, `tests/test_config.py::test_converting_with_the_defaults_matches_converting_with_nothing`, `tests/test_config.py::test_defaults_apply_to_every_document`, `tests/test_config.py::test_merging_ignores_values_that_are_not_there`, `tests/test_config.py::test_merging_nothing_changes_nothing`, `tests/test_config.py::test_no_config_file_anywhere_is_not_an_error`, `tests/test_config.py::test_the_defaults_are_the_old_constants`, `tests/test_config.py::test_the_document_it_names_is_in_the_message`, `tests/test_config.py::test_the_prefixes_keep_their_hyphen_by_default`, `tests/test_config.py::test_the_report_names_the_document_and_the_settings_used`, `tests/test_config.py::write`

## Package identity metadata

Names the package and its version: scribe turns text pulled out of a PDF into clean Markdown.

Code: `scribe/__init__.py::__module__`
