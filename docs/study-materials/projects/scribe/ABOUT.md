# About scribe

The long version of what a participant reads about scribe, kept here beside the
code. No assumed knowledge, and one worked example rather than a description.

The page they actually read is shorter, runs to five minutes, and is built from
`study-app/participant/prose.js`. A change to their wording goes there. Keep this
file saying the same things about the program, because it is what anybody working
on the instrument reads.

---

## The problem

Copy text out of a PDF and paste it somewhere. It comes out broken:

```
The survey covered four hundred kilometres of shoreline between March
and September. Rates of retreat were higher than the 2019 baseline at
every site except Ardmore, where a new revetment has held the line.
```

Those line breaks are not in the writing. They are where the line ran out on the
page. Paste that into an email and it stays broken.

A PDF stores text with the page baked in: where each line sat, where each word
was split. Take the text back out and you get the page, not the writing.

**scribe puts the writing back.** Same passage, after:

```
The survey covered four hundred kilometres of shoreline between March and
September. Rates of retreat were higher than the 2019 baseline at every site
except Ardmore, where a new revetment has held the line.
```

One paragraph, as it was written.

## Six things it fixes

You do not need to remember these. They are here so nothing in the code is a
surprise.

**Broken words.** A long word at the end of a line gets split with a hyphen:
`photogram-` then `metric`. scribe joins it back into `photogrammetric`.

**Broken paragraphs.** As above. Lines inside a paragraph are joined; the gap
between paragraphs is kept.

**Repeated headers.** A report often has the same line at the top of every page,
like `Coastal Erosion Survey 2026, Marine Institute`. Useful on paper, noise in
the text. scribe drops it, and drops page numbers the same way.

**Headings.** `3.1 Sites` becomes a real heading, so the result has structure.

**Footnotes.** In a PDF the little number is stuck to a word and the note itself
is at the bottom of that page. scribe gathers the notes at the end and links
them.

**Typesetting characters.** Printers use `ﬁ` as one character and curly quotes
for straight ones. scribe replaces them, so the result can be searched normally.

## What it does not do

It does not read PDF files. Something else does that first and hands scribe plain
text. It does not recover tables, images or columns. All of those are lost before scribe
sees the text.

## Each rule is a tradeoff

Each of the six rules above chose one reasonable option over another reasonable
option.

For example, scribe drops repeated headers. For a hundred-page report, the
repeated title is noise. For a one-page letter, the line at the top is the
letterhead, and dropping it loses information. scribe drops it anyway.

Another example: `photogram-metric` split across two lines should join into
`photogrammetric`. But `well-being` split across two lines should keep its
hyphen, because the hyphen is part of the word. Nothing in the text says which
case applies. scribe guesses, and the guess has a rule you can read.

The code shows you what scribe chose in each case. It does not always say why, or
what the alternative would have cost.

## Running it

From inside the project folder:

| Command | What it does |
| --- | --- |
| `.venv/bin/scribe convert fixtures/report.txt` | Convert one file, write the `.md` beside it |
| `.venv/bin/scribe convert fixtures/report.txt -` | Convert one file, print it instead |
| `.venv/bin/scribe check fixtures/` | Convert everything, write nothing |
| `.venv/bin/python -m pytest tests/ -q` | Run the tests |

A run prints one line:

```
report.txt: 3 pages, 8 headings, 8 paragraphs, 6 bullets, 2 notes, 6 lines of furniture
```

"Furniture" is the project's word for repeated headers and page numbers, meaning
the parts of the page that belong to the paper rather than to the writing.

## The files

Nine, and small. You will probably touch two or three.

```
scribe/lines.py       splits the input into pages and lines
scribe/furniture.py   repeated headers, page numbers
scribe/paragraphs.py  joining broken words and broken lines
scribe/blocks.py      headings, bullets, blank space
scribe/notes.py       footnotes
scribe/text.py        typesetting characters
scribe/convert.py     runs the rules, in order
```

There are three sample documents in `fixtures/`: a survey report, a short memo,
and a field handbook. They are different on purpose. The memo has no repeated
header, so a rule that helps the report can hurt the memo.

## What we are asking

You will ask the coding agent for a change, and then decide what to keep. Work
however you normally would and use the agent as much or as little as you like.

The request is short on purpose. Anything it does not say is yours to decide, and
we will ask you about those decisions afterwards, so make them on purpose.

We will also ask you to explain the code: what it does, why it is built that way,
and what you would change to extend it.
