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

Each is rated **0 to 2 for consistency with what scribe already does**, blind to
condition. The rating is about consistency, not correctness. None of these has a
single right answer. There are only answers that fit the codebase and answers
that contradict it.

### 1. What marks a quote

| | |
| --- | --- |
| **2, consistent** | Indentation, with a threshold, and the threshold named as a constant beside `EDGE` and `MAX_HEADING_WORDS`. Every other rule in scribe recognises structure from the text's own shape and puts its threshold in a named constant. |
| **1, defensible** | A leading character such as `>`, which the extracted text will not contain but which is cheap to check for. |
| **0, contradicts** | Anything requiring information scribe does not have: font, colour, position on the page. `lines.py` deliberately keeps only text, page and index, and a rule needing more would have to change what the program is. |

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
| **0** | No. The quote joins the paragraph, which is the one thing no other block does. |

### 4. A quote that runs across a page break

**The coupled decision.** `furniture.strip` runs before anything else and removes
the running header, which sits between the two halves of the quote. A change that
does not account for the ordering will silently drop or split the quote.

| | |
| --- | --- |
| **2** | The two halves join, because furniture is already gone by the time quotes are found. Said out loud, in the description or in a comment: the order is what makes it work. |
| **1** | The halves join, apparently by luck, with nothing showing the author knew why. |
| **0** | The halves are kept apart, or a new rule is added ahead of `furniture.strip` to handle it, which reintroduces the problem the ordering solved. |

**Also recorded per decision:** who settled it. The three possibilities are: they
decided, the agent proposed and they accepted, or the agent did it and they never
noticed.

## The quiz

Twelve questions, four options, one right. Asked before the task and again after,
so the change is the measure.

**They are answered open book, with a clock running.** The participant may read
the description, read the code, run the project, and ask the agent. The one thing
barred is pasting a question at the agent, which would measure the agent. This
changed in 2026-08: asking people to answer "from what you have just read"
measured how much of a two-minute briefing they retained, which is not what
either way of working is for. How quickly somebody can find twelve answers is
the comparison, so the elapsed time is stored with the answers.

**Every wrong option is something scribe could reasonably have done and did not.**
A quiz whose wrong answers are obviously wrong is answered by picking the
sensible one, and the first draft of the quiz scored twelve out of twelve with no
description at all.

**Every question carries an (easy), (medium) or (hard) tag**, four of each, one
per band. The tag is stripped before anybody sees it and never reaches the
browser copy. It is there so the extractor can refuse a set that has drifted to
one end: a quiz where everything is hard measures who already knew the domain,
and the earlier set was all hard. `extract-questions.mjs` also refuses if scribe
and tally stop matching band for band and level for level, since each participant
does one project each way and a difficulty gap between the projects would land
entirely on whichever condition drew the harder one.

Check it with:

```
python3 ../../scoring/check-description-answers.py --blind scribe
python3 ../../scoring/check-description-answers.py <a scribe workspace> scribe
```

Near three out of twelve is chance. Measured 2026-08-17 with gpt-5.6-luna, on
the questions as they now stand:

| Run | Correct |
| --- | --- |
| Blind, no description at all | 9/12 |
| From `CLAUDE.md`, written by `/init` | 9/12 (11/12 in zh-Hans) |
| From the codoc tree, written by `codoc init` | 9/12 (7, 8, 9 in zh-Hans) |

**Read the spread, not the numbers.** Three runs of the SAME artefact — the
zh-Hans codoc tree — scored 7, 8 and 9. The check asks a model whether an answer
is present in a text, and that judgement moves by two or three questions from run
to run. So no single-run comparison here supports a claim: not one arm against the
other, and not one language against another. What the runs do support is the
weaker and still useful statement that every version of every description lands in
the same band, and that translation does not obviously cost information — tally
scored 11/12 from its tree in English and 11/12 again after `codoc translate`.

Use this as a floor on guessability and a smoke test for a description that lost
its content, which is what it was built for. It is not an outcome measure, and a
run of it is not evidence that one arm is ahead.

Both descriptions are generated by their own tool: `CLAUDE.md` by Claude Code's
`/init`, and the tree by `codoc init`. Neither is hand-written. Writing one by
hand and generating the other would make the comparison meaningless.

**Nine blind is still too high, and it is the open problem in this instrument.**
A frontier model with no description gets as many as one reading either
description, so on this evidence the questions do not separate the arms. Two
things make that less damaging than it reads. A frontier model is a harsh proxy
for somebody with ten minutes and a codebase they met today, so this is a ceiling
on guessability rather than a prediction. And the sitting is now open book and
timed, so the measure is no longer only the score: it is also how long the answers
took, which a blind run cannot speak to at all. Say both of these in the
pre-registration rather than discovering them in the results.

### Purpose: what it is for, and where it stops

Asked as behaviour rather than as description. "What is scribe for" is answerable
from the name; "what happens when you hand it a PDF" is answerable only if you
know where the program's edges are.

**Q1. (easy) Jane wants scribe to keep the tables out of a report. Can it?**
- a) Yes, it rebuilds them from where the columns sat
- b) Yes, but only for tables with a header row
- c) **No: the table is gone before scribe is handed anything** ✓
- d) No, but it marks the place where a table was

**Q2. (medium) Raj makes heading detection stricter, so fewer lines become headings. Which other part of the output changes?**
- a) Footnotes, because a note number looks like a heading number
- b) Character normalising, because heading text is normalised separately
- c) **The paragraphs, because a line that is no longer a heading joins the prose around it** ✓
- d) Nothing else: headings are decided line by line and touch nothing else

**Q3. (hard) Raj moves the character normalising so it runs first instead of last. What breaks?**
- a) Nothing: normalising early or late comes to the same thing
- b) The footnote markers are normalised away before they can be found
- c) **Rules that match on the characters as they came out of the PDF stop matching** ✓
- d) The output keeps its ligatures, because normalising happens before the text exists

### Rationale: which way it went, and why

These ask what scribe actually does in a named case. Both answers are defensible
and another converter would go the other way, so the only way to know is to have
learned what this one decided. A question whose right answer is simply the more
sensible one is answerable without reading anything, which is what the first
draft of this quiz was.

**Q4. (easy) A word is split across a line break as `photogram-` then `metric`. What comes out?**
- a) `photogram-metric`, keeping the hyphen
- b) `photogram metric`, as two words
- c) **`photogrammetric`, joined with the hyphen dropped** ✓
- d) It is left as it was, because the word is not in the exception list

**Q5. (medium) A word is split as `well-` then `being`. What comes out?**
- a) `wellbeing`, because the hyphen was the typesetter's
- b) **`well-being`, because that hyphen belongs to the word** ✓
- c) `well- being`, leaving the break visible
- d) `well-being`, but only if the word appears unbroken elsewhere in the document

**Q6. (hard) A line reads `3. We asked each participant to describe what they had understood.` What does scribe make of it?**
- a) A second-level heading, from the numbering
- b) A heading, but only if a blank line follows
- c) **Not a heading: it is too long and it ends in a full stop** ✓
- d) A numbered list item, rendered as a bullet

### Change: what happened, and what it cost

**Q7. (easy) A three-page report has the same line at the top of two of its three pages. What happens to that line?**
- a) It is kept, because two pages is not a pattern
- b) **It is dropped: two pages out of three is over the threshold** ✓
- c) It is kept on the first page and dropped on the second
- d) It is dropped only if a page number appears with it

**Q8. (medium) Footnote markers used to be found after any full stop, and that rule was changed. What was going wrong?**
- a) A marker at the very end of a paragraph was missed
- b) Two markers next to each other were read as one
- c) **Every decimal number in the document was read as a footnote reference** ✓
- d) A page number at the foot of a page was taken for a marker

**Q9. (hard) Page furniture is removed before headings are looked for. What does that ordering cost?**
- a) Nothing: the two rules do not interact
- b) Page numbers can no longer be used to order the sections
- c) **A real heading that happens to repeat is gone before the heading rule can see it** ✓
- d) The line count per page is wrong by the time headings are found

### Extension: what a further change would need

**Q10. (easy) Jane wants scribe to recognise a new kind of block. Where does that go?**
- a) Into `lines.py`, with the rest of the parsing
- b) **Into a policy module of its own, and into the order in `convert.py`** ✓
- c) Into `text.py`, with the other rewriting
- d) Anywhere: the rules do not depend on each other

**Q11. (medium) Two guards stop furniture removal firing on a short document. One is a minimum number of pages. What is the other?**
- a) A minimum number of words in the repeated line
- b) **A minimum number of lines on a page, so that being near the edge means something** ✓
- c) A maximum number of pages, above which it is assumed to be a book
- d) There is only one guard

**Q12. (hard) Jane wants the running header kept on a one-page letter but still dropped from a long report. What stands in the way?**
- a) By the time anything could tell the two apart, the header has already been removed
- b) **Nothing scribe can see tells them apart: it is handed text and nothing else** ✓
- c) Markdown has no way to mark a line as a page header
- d) The page numbers would have to be kept along with it

## The after-task questions

Six questions, four options, one right, asked straight after the task with the
code, the description and the agent CLOSED. Never shown to a participant before
they have done the task.

They used to be four boxes to type in. Freeform got short answers to questions
whose value is in the follow-up, at the end of two hours, and nothing that could
be scored the same way twice. These have right answers.

**Every one of them is about the change they just made, and none can be answered
from the briefing.** That is the whole design: a question answerable by somebody
who read the project page and did nothing is a question that measures reading.
Each turns on a consequence of block quotes meeting a rule that was already
there, so the two ways to get it right are to have understood the codebase or to
have made the decision yourself and watched what it did. Somebody who let the
agent write it and did not look will not have either.

They are matched to tally's set one for one, band for band.

### Purpose: what your change actually does

**Q1. (easy) Your change turns some passages into Markdown block quotes. What decides which passages?**
- a) The words in them, matched against a list
- b) **Something about their shape on the page, which is all `lines.py` keeps** ✓
- c) Their font, which the extracted text records
- d) Their position in the document, counted from the top

**Q2. (medium) A line that is now inside a quote. Which existing behaviour is most likely to treat it differently than before?**
- a) Whether its ligatures were normalised
- b) Which page it is recorded on
- c) **Whether it was joined into the paragraph around it** ✓
- d) Whether it counted toward the furniture threshold

### Rationale: why that way and not the other

**Q3. (medium) You chose how a quote is recognised. What makes a rule based on font inconsistent with the rest of scribe, whatever its merits?**
- a) Markdown cannot express a font
- b) It would be slower than the other rules
- c) **`lines.py` keeps text, page and index and nothing else, so no rule downstream has a font to look at** ✓
- d) The other rules are all in one file and it would have to be too

**Q4. (hard) `furniture.strip` runs before anything looks for quotes. For a quote that runs across a page break, what does that ordering do?**
- a) It splits the quote, because the running header lands between the halves
- b) **It joins them, because the running header is gone before quotes are looked for** ✓
- c) It drops the quote, because furniture removal takes the whole block
- d) Nothing: the two rules never see the same lines

### Change: what it cost, and what it touched

**Q5. (hard) Suppose you had put quote detection BEFORE `furniture.strip` instead. What would have started going wrong?**
- a) Nothing: the two are independent
- b) Quotes would lose their indentation
- c) **A quote crossing a page break would have the running header inside it** ✓
- d) Headings would stop being recognised

### Extension: what a next person needs

**Q6. (medium) Somebody picks this up tomorrow and wants a rule that runs before yours. What do they have to decide that they would not have to in a codebase of independent rules?**
- a) Which file to put it in
- b) Whether to give its threshold a named constant
- c) **Where in `convert.py`'s fixed order it goes, because the order is load-bearing** ✓
- d) Whether to write a fixture for it

## Matching `tally`

Whatever is built for the second project must match on: file count, line count,
number of policies, number of open decisions, one coupled pair, and twelve quiz
questions in the same four bands. A difference between the projects should be
domain, and nothing else.
