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
```

A run prints one line, e.g.
`report.txt: 3 pages, 8 headings, 8 paragraphs, 6 bullets, 2 notes, 6 lines of furniture`.

## Where things live

```
scribe/lines.py       extracted text into pages and lines
scribe/furniture.py   the running header, the page number
scribe/paragraphs.py  joining broken words, and broken lines
scribe/blocks.py      headings, bullets, blank space
scribe/notes.py       footnotes
scribe/text.py        ligatures and smart quotes
scribe/convert.py     the order the rules run in
fixtures/             three sample documents, each exercising something different
```

## The one thing to know

`convert.py` runs the rules in an order that is not arbitrary. Furniture is
stripped before anything looks for headings, because a running header is usually
the section title and would otherwise be promoted on every page. Footnotes are
collected before paragraphs are reflowed. Characters are normalised last, so
every rule above sees the text as it came out of the PDF.
