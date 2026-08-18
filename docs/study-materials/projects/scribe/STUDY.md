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

Five questions, four options, one right, asked before the task.

**They are answered open book, with a clock running.** The participant may read
the description, read the code, run the project, and ask the agent. The one thing
barred is pasting a question at the agent, which would measure the agent. This
changed in 2026-08: asking people to answer "from what you have just read"
measured how much of a two-minute briefing they retained, which is not what
either way of working is for. How quickly somebody can find five answers is
the comparison, so the elapsed time is stored with the answers.

**Every wrong option is something scribe could reasonably have done and did not.**
A quiz whose wrong answers are obviously wrong is answered by picking the
sensible one, and the first draft of the quiz scored twelve out of twelve with no
description at all.

**Every question carries an (easy), (medium) or (hard) tag.** With five
questions one band carries two and the levels cannot be evenly split. The tag is stripped before anybody sees it and never reaches the
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

**Q1. (easy) A three-page report repeats its section title at the top of every page. It is a real heading, not a running header. What does scribe do with it?**
- a) Keeps it as a heading, because it is numbered like the others
- b) **Drops it, because it repeats near the edge of most pages and that is all scribe can see** ✓
- c) Keeps the first one and drops the repeats
- d) Keeps it, and marks the repeats for review

### Rationale: which way it went, and why

**Q2. (medium) A word is split across a line break as `well-` then `being`. What comes out?**
- a) `wellbeing`, because a hyphen at a line end is the typesetter's, not the writer's
- b) **`well-being`, because a short list of prefixes is allowed to keep its hyphen** ✓
- c) `well- being`, because the break is preserved along with the hyphen
- d) `well-being`, because a dictionary is consulted for the compound

**Q4. (hard) Page furniture is removed before anything looks for headings. What does that ordering cost?**
- a) Nothing: the two rules never look at the same lines
- b) Page numbers can no longer be used to order the sections
- c) **A real heading that repeats on most pages is gone before the heading rule can see it** ✓
- d) A heading on the first page is missed, because there is nothing before it to compare against

### Change: what happened, and what it cost

**Q3. (medium) Footnote markers used to be found after any full stop, and the rule was tightened. What was going wrong?**
- a) A marker at the very end of a paragraph was being missed
- b) Two markers next to each other were being read as one
- c) **Every decimal number in the document was being read as a footnote reference** ✓
- d) A page number at the foot of a page was being taken for a marker

### Extension: what a further change would need

**Q5. (hard) You want the running header kept on a one-page letter but still dropped from a long report. What stands in the way?**
- a) Markdown has no way to mark a line as a page header
- b) **Repetition across pages is the only signal there is, and one page cannot show it** ✓
- c) The header is removed before anything could tell the two documents apart
- d) The page number would have to be kept along with it

## The after-task questions

Five questions, four options, one right, asked straight after the task with the
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

**Q1. (easy) Your change decides which passages become block quotes. What does scribe know about a line that a rule could be based on?**
- a) Its font and size, which the extracted text records
- b) **Its text, which page it came from, and where it sat on that page — nothing else** ✓
- c) Its colour and how far it was indented in the PDF
- d) Where it ends up in the finished Markdown

### Rationale: why that way and not the other

**Q2. (medium) A line that your change now puts inside a quote used to be joined into the paragraph around it. Which existing behaviour most likely treats it differently now?**
- a) The characters in it, which are tidied separately
- b) **Rejoining paragraphs, because a quote is a block and the prose around it stops flowing into it** ✓
- c) Which page it is recorded on
- d) Whether it counted towards the repeated-line threshold

**Q4. (hard) Suppose you had looked for quotes BEFORE the running header was removed. What would have started going wrong?**
- a) Nothing: the two are independent
- b) Quotes would lose their indentation
- c) **A quote crossing a page break would have the running header sitting inside it** ✓
- d) Headings would stop being recognised

### Change: what it cost, and what it touched

**Q3. (medium) The running header is removed before your change runs. For a quote that carries on across a page break, what does that ordering do?**
- a) It splits the quote, because the header lands between the two halves
- b) **It lets the halves join, because the header is gone before quotes are looked for** ✓
- c) It drops the quote, because removing the header takes the whole block
- d) Nothing: the two never see the same lines

### Extension: what a next person needs

**Q5. (hard) Someone picks this up tomorrow and adds another rule. What do they have to decide that they would not have to if the rules were independent?**
- a) Which file to put it in
- b) Whether to give its threshold a name
- c) **Where it goes in the fixed order the stages run in, because each stage sees what the ones before it left** ✓
- d) Whether to add a sample document for it

## Matching `tally`

Whatever is built for the second project must match on: file count, line count,
number of policies, number of open decisions, one coupled pair, and twelve quiz
questions in the same four bands. A difference between the projects should be
domain, and nothing else.
