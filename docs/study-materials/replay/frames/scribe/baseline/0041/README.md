# scribe

Text pulled out of a PDF, into clean Markdown.

`pdftotext` gives you a wall of text: lines broken where the column ran out,
words broken where the hyphenation dictionary allowed, a header repeated on every
page, and footnotes stranded at the foot of the page they came from. scribe puts
it back together.

```
.venv/bin/scribe convert fixtures/report.txt     write report.md beside it
.venv/bin/scribe convert fixtures/report.txt -   write to stdout
.venv/bin/scribe check fixtures/                 convert everything, write nothing
.venv/bin/python -m pytest tests/ -q             run the tests

  --config FILE   use this config instead of the scribe.toml beside the document
  --no-report     write only the Markdown, not the conversion report
```

A run prints one line, e.g.
`report.txt: 3 pages, 8 headings, 12 paragraphs, 6 bullets, 2 notes, 6 lines of furniture`.

`convert` writes two files: the Markdown, and a short report beside it. The
report is the receipt — what was thrown away, what was moved, and which settings
were in force — because the lossy steps leave no trace in the Markdown itself. It
is named after its source, so `report.txt` gives `report.md` and
`report.report.md`; a fixed name would collide with the Markdown of any document
actually called `report.txt`, which the fixtures include.

## Where things live

```
scribe/lines.py       extracted text into pages and lines
scribe/furniture.py   the running header, the page number
scribe/paragraphs.py  joining broken words, and broken lines
scribe/blocks.py      headings, bullets, blank space
scribe/notes.py       footnotes
scribe/text.py        ligatures and smart quotes
scribe/settings.py    every number the rules used to hard-code, and the config file
scribe/convert.py     the order the rules run in
scribe/report.py      the note written beside the Markdown
fixtures/             three sample documents, each exercising something different
fixtures/scribe.toml  what a config file looks like
```

## Changing the rules for one document

Rules take their numbers from a `Settings`, and never from a module constant. The
defaults are in `scribe/settings.py`, which is also the list of everything that
can be changed. A `scribe.toml` beside a document overrides them:

```toml
[furniture]
repeat_share = 0.5            # applies to every document in the directory

[documents."memo.txt".paragraphs]
keep_all_hyphens = true       # applies to memo.txt alone
```

`keep_hyphen` starts empty, so a word broken across a line loses its hyphen
unless a document names the prefix: "well-" / "being" comes back as "wellbeing"
until something says otherwise. That is deliberate — a list nobody chose is
wrong for any corpus whose compounds are not on it, and silently so.
`SUGGESTED_KEEP_HYPHEN` in `scribe/settings.py` is the list to start from, and
`keep_all_hyphens = true` is the blunt alternative.

A running header has to appear on `repeat_share` of the pages, or on
`min_repeats` of them, whichever is the larger number. Half rather than more,
because a header is often missing from the title page and from any page a
full-width table took over: at 0.6 a five page report needed its header on three
pages and kept it on all five when it was on two. The floor is what stops the
share falling to one on a short document, where a threshold of one would make
every line near an edge furniture.

Sections are named after the rule modules. Only the document's own directory is
searched, because a config file three directories away changing the output of a
conversion is hard to notice and harder to explain. An unknown section or key is
an error rather than a shrug: a misspelled key that quietly does nothing looks
like a broken rule, which is a much longer afternoon.

## The one thing to know

`convert.py` runs the rules in an order that is not arbitrary. Furniture is
stripped before anything looks for headings, because a running header is usually
the section title and would otherwise be promoted on every page. Footnotes are
collected before paragraphs are reflowed. Characters are normalised last, so
every rule above sees the text as it came out of the PDF.
