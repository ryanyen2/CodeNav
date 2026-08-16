# scribe, as a study instrument

Not shipped to participants. This is what the task is, what is being rated, and
why each question has the answer it has.

## The task card

> **Support block quotes.**
>
> Some of the sample documents quote another document. Those passages should come
> out as Markdown block quotes.
>
> Decide anything this card does not specify, and be ready to explain your
> decisions.

`report.txt` has one that runs across a page break, and `memo.txt` has one that
does not, so both cases are reachable from the fixtures rather than hypothetical.

The card no longer says the quotes arrive indented. It used to, which answered
the first decision before the participant reached it.

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

Twelve questions, four options, one right. Asked before the task and again after,
so the change is the measure.

**Every wrong option is something scribe could defensibly have done and did not.**
That is the whole design. A quiz whose distractors are implausible is answered by
picking the sensible one, and the first draft of this scored twelve out of twelve
with no description at all — which would have measured nothing, because both
conditions would have got them. Check it with:

```
python3 ../../scoring/check-description-answers.py --blind scribe
```

Near three out of twelve is chance. Where it stands today, measured with
gpt-5.6-luna:

| Run | Correct | Grounded in the text |
| --- | --- | --- |
| Blind, no description at all | 9/12 | — |
| From the generated codoc tree | 11/12 | 7/12 |
| From the hand-written `CLAUDE.md` | 12/12 | 12/12 |

Nine blind is still too high, and the three it misses are the three whose right
answer is the LESS obvious of two defensible options. That is the recipe the rest
of the quiz needs rewriting to. A frontier model is a harsh proxy for somebody
with two minutes, so this is a ceiling on guessability rather than a prediction
of what a participant scores — but it is the only floor available before running
anyone.

### Purpose — what this program is for

**Q1. What is scribe for?**
- a) Pulling the text layer out of a PDF file
- b) **Turning text already extracted from a PDF into readable Markdown** ✓
- c) Converting a PDF to HTML and then to Markdown
- d) Tidying up Markdown that somebody wrote by hand

**Q2. What does scribe expect to be handed?**
- a) A PDF file
- b) One text file per page
- c) **One text file, with a form feed between pages** ✓
- d) Text with each page's number on a line of its own

**Q3. Which of these is out of scope for scribe?**
- a) Dropping a header repeated on every page
- b) Joining a word split across a line break
- c) **Working out where a table's columns were** ✓
- d) Gathering footnotes at the end of the document

**Q4. Who is the output written for?**
- a) An archive that has to preserve the original exactly
- b) **Somebody who will search it, diff it and paste it elsewhere** ✓
- c) A typesetter laying the document out again
- d) A screen reader

### Rationale — which way it went, and why

These ask what scribe actually does in a named case. Both answers are defensible
and another converter would go the other way, so the only way to know is to have
learned what this one decided. A question whose right answer is simply the more
sensible one is answerable without reading anything, which is what the first
draft of this quiz was.

**Q5. A word is split across a line break as `well-` then `being`. What comes out?**
- a) `wellbeing`, because the hyphen was the typesetter's
- b) **`well-being`, because the hyphen is part of the word** ✓
- c) `well- being`, leaving the break visible
- d) `well-being` only if the word appears elsewhere in the document unbroken

**Q6. A word is split as `photogram-` then `metric`. What comes out?**
- a) `photogram-metric`, keeping the hyphen
- b) `photogram metric`, as two words
- c) **`photogrammetric`, joined with the hyphen dropped** ✓
- d) It is left as it was, because the word is not in the exception list

**Q7. A three-page report has the same line at the top of two of its pages. What happens to it?**
- a) It is kept, because two pages is not a pattern
- b) **It is dropped, because two of three is over the threshold** ✓
- c) It is kept on the first page and dropped on the second
- d) It is dropped only if a page number appears with it

**Q8. A line reads `3. We asked each participant to describe what they had understood.` What does scribe make of it?**
- a) A second-level heading, from the numbering
- b) A heading, but only if a blank line follows
- c) **Not a heading: it is too long and it ends in a full stop** ✓
- d) A numbered list item, rendered as a bullet

### Change — what happened, and what it cost

**Q9. Footnote markers used to be found after any full stop. What went wrong?**
- a) A marker at the very end of a paragraph was missed
- b) Two markers next to each other were read as one
- c) **Every decimal number in the document became a footnote reference** ✓
- d) A page number at the foot of a page was taken for a marker

**Q10. Page furniture is removed before headings are found. What does that cost?**
- a) Nothing: the two rules do not interact
- b) Page numbers can no longer be used to order the sections
- c) **A real heading that happens to repeat is gone before anything can rescue it** ✓
- d) The line count per page is wrong by the time headings are found

**Q11. A document of four lines has a repeated first line. Is it removed?**
- a) Yes, repetition is repetition
- b) **No: the page is too short for anything to count as being in the margin** ✓
- c) Yes, but only if it is also on the last page
- d) No, because a four-line document has only one page

### Extension — what a further change would need

**Q12. To keep the running header on a one-off document while still dropping it from a report, what has to be settled first?**
- a) Whether the header becomes a heading or ordinary text
- b) How to keep the page numbers while dropping the header
- c) **What tells the two kinds of document apart, given that scribe sees only text** ✓
- d) Where the setting that turns it off should live

## Matching `tally`

Whatever is built for the second project must match on: file count, line count,
number of policies, number of open decisions, one coupled pair, and twelve quiz
questions in the same four bands. A difference between the projects should be
domain, and nothing else.
