# Codebase feature guide

## Preparing extracted text

Text pulled out of a PDF arrives as one long string with a form feed between pages, and the page furniture comes with it: the running header repeated at the top of every page, the page number at the foot. These features turn that string into pages and lines, then take the furniture back out, so the rules that follow are reading the document and not the paper it was printed on.

### Extracted text page model

Splits the raw string into pages at each form feed, and each page into lines.

A line knows three things: its text, which page it came from, and where it sits on that page. That is the whole model, and it is deliberate — everything downstream is a guess made from those three facts. A rule that would need to know a font, a colour, or where the words sat on the physical page cannot be written here without changing what scribe is.

Code: `scribe/lines.py::Document`, `scribe/lines.py::Document.lines`, `scribe/lines.py::Line`, `scribe/lines.py::Line.from_bottom`, `scribe/lines.py::Line.from_top`, `scribe/lines.py::Line.is_blank`, `scribe/lines.py::PAGE_BREAK`, `scribe/lines.py::Page`, `scribe/lines.py::__module__`, `scribe/lines.py::read`

### Page furniture removal

Drops the running header, the footer, and the page number.

A line counts as furniture when it sits near the top or bottom of a page and the same text repeats on at least 60% of pages — that share and the width of the edge are the two values `scribe.toml` can move, and the CLI moves them by rebinding `REPEAT_SHARE` and `EDGE` before converting. Repetition is the only signal available, which has two consequences worth knowing: a one-page letter can never show it, so its header stays; and a real heading that happens to repeat is thrown away with the furniture. This runs before headings are looked for, so nothing later can rescue it.

Code: `scribe/furniture.py::EDGE`, `scribe/furniture.py::MIN_PAGE`, `scribe/furniture.py::PAGE_NUMBER`, `scribe/furniture.py::REPEAT_SHARE`, `scribe/furniture.py::__module__`, `scribe/furniture.py::_near_edge`, `scribe/furniture.py::_normalise`, `scribe/furniture.py::find_repeated`, `scribe/furniture.py::is_page_number`, `scribe/furniture.py::strip`

## Turning extracted text into clean Markdown

Once the furniture is gone, what is left is prose that has been through a typesetter: paragraphs broken into lines, words split at the margin, footnotes stranded at the bottom of the page, and quotation marks that are not the ones on your keyboard. These features put it back together as Markdown.

### Extracted text conversion pipeline

Runs the stages in one fixed order: strip the furniture, find the blocks, collect the footnotes, rejoin the paragraphs, then tidy the characters.

The order is the design. Furniture goes first so a running header is never mistaken for a heading; characters are tidied last so every rule before it sees the text exactly as the PDF gave it. Move a stage and rules start seeing text another stage has already altered.

Code: `scribe/convert.py::Converted`, `scribe/convert.py::Converted.summary`, `scribe/convert.py::_Collected`, `scribe/convert.py::__module__`, `scribe/convert.py::_collect_notes`, `scribe/convert.py::_join`, `scribe/convert.py::convert`

### Text block recognition rules

Decides which lines are headings and which are bullets.

A heading is a numbered line — `3.`, `3.1`, `3.1.4 Findings` — of no more than twelve words. The catch is that a numbered list item looks exactly the same, so the line underneath settles it: a heading has a blank line under it, a wrapped list item runs straight on. That is why `3. We asked each participant to describe what they had understood.` is not a heading — too long, and nothing but prose beneath it.

Code: `scribe/blocks.py::BULLET`, `scribe/blocks.py::MAX_HEADING_WORDS`, `scribe/blocks.py::NUMBERED`, `scribe/blocks.py::__module__`, `scribe/blocks.py::bullet`, `scribe/blocks.py::collapse_blanks`, `scribe/blocks.py::heading_level`

### Footnote recognition and references

Finds footnotes and turns them into Markdown references.

A note is a numbered line near the foot of a page; the same shape in the middle of a page is a list item, so position is what tells them apart. The marker in the prose is digits welded to the end of a word — `comparable.1`. The character before the digits is restricted on purpose: allowing any full stop once made every decimal in the document — `0.8`, `3.14` — into a footnote reference.

Code: `scribe/notes.py::MARKER`, `scribe/notes.py::NOTE_LINE`, `scribe/notes.py::__module__`, `scribe/notes.py::looks_like_note`, `scribe/notes.py::mark`, `scribe/notes.py::split_note`

### Extracted text paragraph reflow

Puts paragraphs back together and repairs words the typesetter broke.

Ordinary line breaks are joined; a blank line or a short line ending in a full stop ends the paragraph. A word split at the margin is rejoined and the hyphen dropped, so `photogram-` + `metry` becomes `photogrammetry`. Some hyphens belong to the word, though, so a short list of prefixes keeps theirs: `well-` + `being` stays `well-being`. The text alone cannot tell a real compound from a line break, which is why that list exists.

Code: `scribe/paragraphs.py::HYPHEN_END`, `scribe/paragraphs.py::KEEP_HYPHEN`, `scribe/paragraphs.py::SENTENCE_END`, `scribe/paragraphs.py::__module__`, `scribe/paragraphs.py::dehyphenate`, `scribe/paragraphs.py::is_break`, `scribe/paragraphs.py::reflow`

### Typeset character normalization

Replaces the typesetter's characters with plain ones: ligatures, curly quotes, en and em dashes, non-breaking spaces.

So `ﬁ` becomes `fi` and a curly `’` becomes `'`. This loses typographic fidelity on purpose — the output is meant to be grepped, diffed, and pasted, and none of those work well against characters you cannot type.

Code: `scribe/text.py::SUBSTITUTIONS`, `scribe/text.py::__module__`, `scribe/text.py::normalise`

## Configuring the rules

The tuning numbers used to be module constants, so bending one for a single awkward document meant editing the source. These features gather them into one object that a file can supply, while leaving the library usable with no file at all: absent a config, every document converts exactly as it did before.

### Rule settings values

One frozen dataclass holding the values the rules use — `repeat_share`, `edge`, `keep_hyphen` — with the built-in defaults as its field defaults, so the no-config path is the old behaviour by construction rather than by convention. `merged` drops `None` overrides, which lets a partly-filled config section be layered over the defaults without spelling out the values it does not care about.

Code: `scribe/settings.py::DEFAULTS`, `scribe/settings.py::Settings`, `scribe/settings.py::Settings.merged`, `scribe/settings.py::__module__`

### Configuration file discovery and parsing

Finds the nearest `scribe.toml` at or above a starting directory and reads it into defaults plus per-document overrides, keyed by file name. A `[document."survey.txt"]` section is merged over `[defaults]`, which is merged over the built-ins.

Unknown keys and out-of-range values are rejected rather than ignored: a misspelled setting that silently did nothing would look exactly like a rule that does not work, and the file exists precisely for people who are already puzzled by the output. The repo's own `scribe.toml` is the worked example — the survey's appendix carries a header over only two of its five pages, under the default 60% repeat share.

Code: `scribe/config.py::CONFIG_NAME`, `scribe/config.py::Config`, `scribe/config.py::Config.for_document`, `scribe/config.py::ConfigError`, `scribe/config.py::KNOWN`, `scribe/config.py::_checked`, `scribe/config.py::__module__`, `scribe/config.py::find`, `scribe/config.py::load`, `scribe.toml`

### Conversion report

A short Markdown note written beside the output saying what the rules did: how much furniture went, how many headings were promoted, how many bullets and footnotes there are, and which settings produced that. It is written to a file rather than printed because the question it answers — "where did that line go?" — is usually asked a week later, by somebody looking at the Markdown and not at a terminal.

Code: `scribe/report.py::__module__`, `scribe/report.py::render`, `scribe/report.py::write_report`

## Running conversions

The ways to actually run it: convert one file, print the result instead of writing it, or check a whole folder without touching anything.

### Command-line conversion interface

`convert report.txt` writes `report.md` beside it, plus a conversion report; `convert report.txt -` prints the Markdown instead and writes nothing.

`check fixtures/` converts every file in a folder and writes nothing, printing a line per document. That is the fast way to see what a rule change does to the whole corpus before committing to it.

Settings are loaded once at startup from the nearest `scribe.toml` to the working directory, and a bad config file fails the run before any document is touched. `_apply` then rebinds the module constants the rules read — the rules themselves still read module globals, so the settings object is plumbed no further than this one function.

Three limits of the current wiring, all live:

- Only `conf.defaults` is applied. Per-document settings are computed in `_convert_one` but reach the report only, so a `[document."..."]` override is described in the report without having changed the conversion.
- `keep_hyphen` is carried by `Settings` and accepted in the config file, but nothing applies it; `paragraphs.KEEP_HYPHEN` is still the only list in play.
- The report is always written as `report.md` next to the output, so converting a second document into the same folder overwrites the first one's report — and converting a file actually named `report.txt` overwrites its own Markdown.

Code: `scribe/cli.py::__module__`, `scribe/cli.py::_apply`, `scribe/cli.py::_convert_one`, `scribe/cli.py::main`

## Checking conversion

The tests are where the rules are pinned down, because most of them are judgement calls that look arbitrary until you see what they were protecting against.

### End-to-end conversion tests

Runs three sample documents end to end.

Each covers something the others cannot: `report.txt` has furniture, footnotes and numbered headings; `memo.txt` has none of them, which is what makes keeping a running header a live option rather than a hypothetical; `handbook.txt` has deep numbering and a numbered list that must not turn into headings. The fixtures are the specification.

There is a fourth fixture, `fixtures/survey.txt`, whose appendix carries its own header across two of its five pages — the case the per-document `repeat_share` in `scribe.toml` exists for. No test reads it yet, and neither the config loading, the settings object, nor the conversion report has tests.

Code: `tests/test_documents.py::FIXTURES`, `tests/test_documents.py::__module__`, `tests/test_documents.py::md`, `tests/test_documents.py::test_a_numbered_list_does_not_become_headings`, `tests/test_documents.py::test_a_paragraph_broken_across_a_page_is_rejoined`, `tests/test_documents.py::test_a_two_page_memo_keeps_its_first_line`, `tests/test_documents.py::test_a_word_split_across_a_line_break_is_whole_again`, `tests/test_documents.py::test_a_word_split_across_a_page_is_handled_the_same_way`, `tests/test_documents.py::test_bullets_are_a_tight_list`, `tests/test_documents.py::test_converting_twice_gives_the_same_thing`, `tests/test_documents.py::test_decimals_survive`, `tests/test_documents.py::test_deep_numbering_becomes_deep_headings`, `tests/test_documents.py::test_footnotes_are_collected_at_the_end`, `tests/test_documents.py::test_headings_carry_their_depth`, `tests/test_documents.py::test_ligatures_are_normalised`, `tests/test_documents.py::test_no_document_ends_up_empty`, `tests/test_documents.py::test_no_form_feed_survives`, `tests/test_documents.py::test_no_three_blank_lines_in_a_row`, `tests/test_documents.py::test_the_bullet_character_is_recognised`, `tests/test_documents.py::test_the_memo_has_no_headings`, `tests/test_documents.py::test_the_page_numbers_are_gone`, `tests/test_documents.py::test_the_running_header_is_gone_from_the_report`

### Rule policy regression tests

One test per rule, plus a section for the places two rules meet — which is where changing one of them shows up as a break in another.

These record the boundaries: that furniture removal happens before headings, that a decimal is not a footnote marker, which hyphens survive a line break. A rule changed without updating them tends to bring back the layout artefact it was written to remove.

Code: `tests/test_rules.py::__module__`, `tests/test_rules.py::page`, `tests/test_rules.py::test_a_bare_number_at_the_foot_is_a_page_number`, `tests/test_rules.py::test_a_blank_line_always_breaks`, `tests/test_rules.py::test_a_dash_before_a_number_is_not_a_broken_word`, `tests/test_rules.py::test_a_dash_with_no_space_is_prose`, `tests/test_rules.py::test_a_decimal_is_not_a_footnote_marker`, `tests/test_rules.py::test_a_line_on_every_page_near_the_top_is_furniture`, `tests/test_rules.py::test_a_long_numbered_line_is_a_list_item_not_a_heading`, `tests/test_rules.py::test_a_marker_welded_to_a_word_becomes_a_reference`, `tests/test_rules.py::test_a_number_in_the_middle_of_a_page_is_not`, `tests/test_rules.py::test_a_numbered_line_at_the_foot_is_a_note`, `tests/test_rules.py::test_a_real_compound_keeps_its_hyphen`, `tests/test_rules.py::test_a_running_header_with_a_changing_number_still_counts`, `tests/test_rules.py::test_a_short_line_ending_a_sentence_breaks`, `tests/test_rules.py::test_a_single_newline_continues_the_paragraph`, `tests/test_rules.py::test_a_two_page_document_has_no_furniture`, `tests/test_rules.py::test_a_typeset_hyphen_is_dropped`, `tests/test_rules.py::test_a_year_is_not_a_footnote_marker`, `tests/test_rules.py::test_an_unnumbered_line_is_not_a_heading`, `tests/test_rules.py::test_furniture_runs_before_headings_and_that_is_load_bearing`, `tests/test_rules.py::test_leading_and_trailing_blanks_go`, `tests/test_rules.py::test_ligatures_and_quotes_are_normalised`, `tests/test_rules.py::test_numbering_gives_the_depth`, `tests/test_rules.py::test_runs_of_blank_lines_become_one`, `tests/test_rules.py::test_the_same_line_in_the_middle_of_a_page_is_not`, `tests/test_rules.py::test_the_three_bullet_marks`

## Package identity metadata

Names the package and its version: scribe turns text pulled out of a PDF into clean Markdown.

Code: `scribe/__init__.py::__module__`
