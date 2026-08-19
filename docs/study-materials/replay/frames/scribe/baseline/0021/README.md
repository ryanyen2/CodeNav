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

  --config PATH   use this scribe.toml instead of looking for one
  --no-report     write the Markdown and nothing else
```

A run prints one line, e.g.
`report.txt: 3 pages, 8 headings, 12 paragraphs, 6 bullets, 2 notes, 6 lines of furniture`.

## Where things live

```
scribe/lines.py       extracted text into pages and lines
scribe/furniture.py   the running header, the page number
scribe/paragraphs.py  joining broken words, and broken lines
scribe/blocks.py      headings, bullets, blank space
scribe/notes.py       footnotes
scribe/text.py        ligatures and smart quotes
scribe/convert.py     the order the rules run in
scribe/config.py      the settings the rules are handed, and the file they come from
scribe/report.py      the note written beside each converted document
scribe.toml           every setting, commented out at its default
fixtures/             three sample documents, each exercising something different
```

## Settings

Every rule here makes a judgement, and every judgement is right for some
documents and wrong for others. The values behind them live in `scribe.toml`,
which lists all of them commented out at their defaults. A run with no config
file converts a document exactly the way it always did.

```toml
[furniture]
repeat_share = 0.75      # this corpus repeats a section title over long chapters

[document."memo.txt".text]
normalise = false        # this one is being archived, leave its ligatures alone
```

Tables at the top level apply to every document; a `[document."NAME"]` table
applies to the documents whose file name matches `NAME`, which may be a glob.
The nearest `scribe.toml` at or above the document is the one used, so a config
travels with the corpus it describes. Two documents in one run can be converted
two different ways.

A setting that is misspelled is an error naming the key, rather than a line that
quietly does nothing. What is not settable is the regular expressions that
recognise a heading, a bullet, a note or a page number: the code reads their
capture groups by number, so those stay code.

## The report

Writing to a file also writes `<name>.report.md` beside it — `report.report.md`
for `report.txt`, since that document's Markdown is already called `report.md`.
It is a short note on what the conversion did: which lines were dropped as
furniture, which words were rejoined across a line break, which footnotes were
moved, and which settings were in force. Those are the lossy steps, and the
output of a rule that was wrong looks exactly like the output of a rule that was
right. It carries no timestamp, so a change in it is a change in the conversion.

`--no-report` turns it off for a run, `[report] write = false` for good. Writing
to stdout never writes one, and `check` still writes nothing at all.

## The one thing to know

`convert.py` runs the rules in an order that is not arbitrary. Furniture is
stripped before anything looks for headings, because a running header is usually
the section title and would otherwise be promoted on every page. Footnotes are
collected before paragraphs are reflowed. Characters are normalised last, so
every rule above sees the text as it came out of the PDF.
