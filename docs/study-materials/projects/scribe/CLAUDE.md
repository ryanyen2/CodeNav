# scribe

Text pulled out of a PDF, into clean Markdown.

Something else reads the PDF and hands scribe plain text with a form feed between
pages. scribe puts the writing back together: joins words the typesetter broke,
joins lines the column broke, drops the parts of the page that belong to the
paper rather than the writing, and gathers the footnotes at the end.

The output is meant to be grepped, diffed and pasted into other things. That is
why characters like `ﬁ` and curly quotes are replaced, and why fidelity to the
original page is not a goal.

Not in scope: reading PDF files, recovering tables, columns or images. That
information is gone before scribe sees it.

## Reading the input

**Pages and lines.** The form feed between pages is the only structure the input
gives for free, so it is turned into real objects once rather than split on
repeatedly. Every rule downstream needs to know which page a line came from and
where it sat, because a line at the top of every page means something a line in
the middle does not.

A blank page is kept rather than dropped, so the numbering keeps matching the PDF
somebody would open beside the output.

## The nine rules

Every one of these is a judgement call with a defensible alternative. What was
chosen is below; what it costs is below it.

**Joining broken words.** A hyphen at the end of a line is dropped and the word
joined, because in a justified column most of them were put there by the
typesetter. The exception is a short list of prefixes — `co`, `non`, `pre`, `re`,
`self`, `well` — where the hyphen usually is part of the word: `well-being` split
across two lines is not `wellbeing`. Nothing in the text says which is which, so
the list is the whole of the judgement. Keeping every hyphen would be right for a
corpus of technical writing full of real compounds.

**Joining broken lines.** A single newline continues the paragraph; a blank line
breaks it. A short line that ends a sentence also breaks, without which the last
line of every paragraph glues onto the first line of the next — the most visible
failure there is. Treating every newline as a break is what poetry or an address
block wants and would ruin a report.

**Headings.** Found by leading numbering: `3.1.4 Findings`. Depth comes from the
numbering, so Markdown is offset by one because `#` belongs to the document
title. Guessing from length instead — a short line with no full stop — was tried
first and promoted every caption, every list item and every name in a list of
names. The cost of numbering is that a document which does not number its
headings gets none.

A numbered line is not a heading if it is longer than twelve words, if it ends in
punctuation, or if something is on the very next line. That last check is what
separates a heading from the first line of a wrapped numbered list item, which
otherwise looks identical.

**Page furniture.** A line near the top or bottom of most pages is a running
header or footer and is dropped, as are bare page numbers. Digits are folded
before comparing, so `Chapter 3 — page 7` and `page 8` count as the same header.

Two guards. A document under three pages has none removed, because there is no
pattern to establish and a short letter whose first line echoes its last would
lose both. And a page has to be long enough to have a margin: on a four-line page
every line is near an edge, so body text differing only by a number was being read
as a header.

Keeping the header is the right choice for a one-off document, where that line is
the letterhead.

**Bullets.** Recognised from `-`, `*` or `•`, and only when a space follows, so
`-3 degrees` stays prose.

**Blank space.** Runs of blank lines become one. A PDF is full of vertical space
that was typesetting rather than meaning. The cost is that deliberate spacing, in
a poem or on a title page, is flattened.

**Footnotes.** A numbered line near the foot of a page is the note; digits welded
to a word in the prose are the marker. The notes are collected at the end and
linked, because that is what Markdown footnotes are. Leaving them inline keeps
the page's original look and is what you want if the notes are asides meant to be
read in place.

Position is what separates a note from a numbered list item — the same shape in
the middle of a page is a list, and treating those as notes would move half a
list to the end of the document.

**Typesetting characters.** Ligatures and smart punctuation are replaced with
plain equivalents, because the output is meant to be searched. A corpus being
archived for fidelity would want the opposite.

## The order the rules run in

Not arbitrary, and the thing most likely to surprise somebody changing this.

**Furniture is stripped before headings are found.** A running header is often
the section title, so it looks exactly like a heading; taking it out first means
the heading rule never sees it. The cost is that a genuine heading which happens
to repeat is gone before anything can rescue it, which is why the repeat
threshold is as high as it is.

**Footnotes are collected before paragraphs are reflowed**, or a note at the foot
of a page would be glued to the last sentence above it.

**Characters are normalised last**, so every rule above sees the text as it came
out of the PDF rather than a partly rewritten version of it.

## Changes worth knowing about

**Footnote markers used to fire after any full stop.** Every decimal in a
document became a footnote reference: `0.8 metres` came out as `0.[^8] metres`.
The character before is now checked and digits are excluded. Every test passed
before this, because none of them had a number in them.

**Heading detection did not look at the following line.** The first line of a
wrapped numbered list item — `1. Entering water above the knee, whether or not
you are wearing a` — was short enough to be a heading, and the rest of the
sentence became a paragraph beneath it.

## The sample documents

Three, in `fixtures/`, different on purpose.

`report.txt` has a running header, page numbers, numbered headings, hyphenation
and footnotes. `memo.txt` has none of those and is only two pages, so it is the
document that makes "keep the running header" a real alternative — a rule that
helps the report can hurt the memo. `handbook.txt` has deep numbering, a numbered
list that must not become headings, and a paragraph running across a page break.
