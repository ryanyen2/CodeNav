# scribe, as a study instrument

Not shipped to participants. This is what the task is, what is being rated, and
why each question has the answer it has.

## The task card

> **Support block quotes.**
>
> A quote pulled out of a PDF arrives as an indented run of lines. It should come
> out as a Markdown block quote.
>
> Decide anything this card does not specify, and be ready to explain your
> decisions.

The agent implements this in about a minute. Everything below is what the card
leaves open, and the open decisions are the measurement.

## The four open decisions

Each is rated **0–2 for consistency with what scribe already believes**, blind to
condition. Consistency, not correctness: there is no single right answer, only
answers that fit this codebase and answers that contradict it.

### 1. What marks a quote

| | |
| --- | --- |
| **2 — consistent** | Indentation, with a threshold, and the threshold named as a constant beside `EDGE` and `MAX_HEADING_WORDS`. Every other rule in scribe recognises structure from the text's own shape and puts its threshold in a named constant. |
| **1 — defensible** | A leading character such as `>`, which the extracted text will not contain but which is cheap to check for. |
| **0 — contradicts** | Anything requiring information scribe does not have: font, colour, position on the page. `lines.py` deliberately keeps only text, page and index, and a rule needing more would have to change what the program is. |

### 2. Does de-hyphenation apply inside a quote

| | |
| --- | --- |
| **2** | Yes. A quote is prose, it was typeset in the same column, and its words were broken by the same hyphenation. `paragraphs.py` exists to undo typesetting, and a quote was typeset. |
| **1** | Yes but with the prefix list disabled, on the argument that quoted material should be altered as little as possible. |
| **0** | No, on the argument that a quote is verbatim. Verbatim would also mean keeping the line breaks, which nobody proposes, so this is inconsistent with itself. |

### 3. Does a quote end the paragraph before it

| | |
| --- | --- |
| **2** | Yes. Headings and bullets both flush the run before them in `convert.py`; a quote is a block and behaves like the other blocks. |
| **1** | Yes, and the paragraph after it too, which is stricter than the others but not in conflict with them. |
| **0** | No — the quote joins the paragraph. That is the one thing no other block does. |

### 4. A quote that runs across a page break

**This is the coupled one.** `furniture.strip` runs before anything else and
removes the running header, which sits between the two halves of the quote. A
change that does not account for it will silently drop or split the quote.

| | |
| --- | --- |
| **2** | The two halves join, because furniture is already gone by the time quotes are found. Said out loud, in the description or in a comment: the order is what makes it work. |
| **1** | The halves join, apparently by luck, with nothing showing the author knew why. |
| **0** | The halves are kept apart, or a new rule is added ahead of `furniture.strip` to handle it, which reintroduces the problem the ordering solved. |

**Also recorded per decision:** who settled it — they decided, the agent proposed
and they accepted, or the agent did it and they never noticed.

## The quiz

Twelve questions, four options, one right. Closed book, asked before the task and
again after, so the change is the measure. Wrong options are written to be
plausible to somebody who read the code and never learned why it is that way.

### Purpose — what this program is for

**Q1. What is scribe for?**
- a) Reading PDF files
- b) **Turning text already extracted from a PDF into readable Markdown** ✓
- c) Converting Markdown into PDF
- d) Checking that a PDF's text layer is complete

**Q2. What does scribe assume about its input?**
- a) It is a PDF file
- b) It is Markdown with some errors in it
- c) **It is plain text with a form feed between pages** ✓
- d) It is HTML from a PDF viewer

**Q3. Which of these is out of scope for scribe?**
- a) Removing a header repeated on every page
- b) Joining a word split across a line break
- c) Collecting footnotes at the end
- d) **Recovering a table's column boundaries** ✓

**Q4. Who is the output for?**
- a) A printer
- b) **Somebody who will grep, diff and paste it into other things** ✓
- c) An archive that must preserve the original exactly
- d) A screen reader

### Rationale — why it is the way it is

**Q5. Why are headings found by their numbering rather than by being short?**
- a) Numbering is faster to match
- b) **Short-line matching promoted captions, list items and names** ✓
- c) Markdown requires numbered headings
- d) Because the fixtures all use numbering

**Q6. Why does a hyphen at a line end usually disappear?**
- a) Markdown does not allow hyphens inside words
- b) **In a justified column most of them were put there by the typesetter** ✓
- c) It is faster than checking a dictionary
- d) Because the alternative loses the word entirely

**Q7. Why does a document under three pages have no furniture removed?**
- a) Short documents never have headers
- b) It would be too slow on long documents otherwise
- c) **Under three pages there is no pattern, so a coincidence would be treated as one** ✓
- d) The page numbers are unreliable

**Q8. Why are ligatures and curly quotes replaced?**
- a) They are not valid Markdown
- b) They render badly in a browser
- c) **The output is meant to be grepped, diffed and pasted** ✓
- d) They take more bytes

### Change — why something was done

**Q9. Footnote markers used to be found after any full stop. Why did that change?**
- a) It missed markers at the end of a paragraph
- b) **Every decimal number became a footnote reference** ✓
- c) Markdown changed its footnote syntax
- d) It was too slow on long documents

**Q10. Why does heading detection look at the following line?**
- a) To get the heading's depth right
- b) To find the section's first paragraph
- c) **The first line of a wrapped list item looks exactly like a heading** ✓
- d) To decide how much space to leave

**Q11. Why does furniture removal run before heading detection, and not after?**
- a) It is faster that way
- b) **A running header is often the section title, so it would be promoted on every page** ✓
- c) Heading detection needs the page numbers gone first
- d) The two do not interact; the order is arbitrary

### Extension — what a further change would need

**Q12. To keep the running header on a one-off document while still removing it from a report, what has to be decided first?**
- a) Which Markdown syntax a header should use
- b) Whether to read the PDF metadata
- c) **What distinguishes the two kinds of document, since scribe sees only text** ✓
- d) Whether to make it a command line flag

## Matching `tally`

Whatever is built for the second project must match on: file count, line count,
number of policies, number of open decisions, one coupled pair, and twelve quiz
questions in the same four bands. A difference between the projects should be
domain, and nothing else.
