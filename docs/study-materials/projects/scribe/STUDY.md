# scribe, as a study instrument

Not shipped to participants. What the task is, what is being rated, and why each
question has the answer it has.

The task changed on 2026-08-19. The participant now reviews a change an agent
already made, rather than making one, and the reasoning is in
`docs/plans/2026-08-19-001-task-redesign-v2-reviewing-an-agent-session.md`. The
quiz further down is unchanged, because it asks what the codebase already commits
to, and a reviewer has to know that either way.

## The task card

> **Review what the agent did**
>
> You asked for a config file, a short report next to the output, and a tidy-up
> of how the rules get their settings.
>
> The agent has finished and the tests pass. Decide what to keep, and ship it.

The card does not say that anything is wrong, and it does not say that nothing
is. Whether the participant looks at all is one of the outcomes, so the card asks
for a decision and leaves the rest to them.

## What the recorded agent was asked for

The prompt is in `replay/requests/scribe.txt`, and the participant is told they
wrote it before lunch. Three things are asked for at once, so no run of the
transcript is about a single intent and no file carries only one intent. The
prompt says nothing about defaults, which is the ordinary case, and the choices
the agent makes to fill that silence are what the study is about.

The session is recorded once and replayed to each participant in about three
minutes, because watching an agent write code for forty minutes is not what we
are measuring and the quality of that code is not what we are rating. How the
recording works, and what keeps it honest, is in `replay/README.md`.

## The four planted problems

Each is rated 0 to 2, blind to condition. 0 is not found, 1 is found, and 2 is
found and correctly attributed to the commitment it contradicts. None of them
breaks a test, because a problem the suite catches measures nothing.

`replay/frames/scribe/*/notes.md` records which problems the agent produced on
its own and which it had to be steered into. A problem it produced on its own is
stronger evidence than one it was steered into, and the paper reports which is
which.

### D1. The new default loosens a stated policy

Asked to make the repeat threshold a setting and pick a default that catches a
header appearing on two pages out of five, the agent added a `min_repeats` floor
and lowered the share from 0.6 to 0.5. A line near the edge of two pages of a
five-page document is now removed. The description says a running header is a
line that repeats on at least 60% of the pages, and the code has used 0.6 since
it was written. The sample documents have enough pages that no test moves.
`scoring/claims/scribe.json` checks it as C1.

| | |
| --- | --- |
| **2** | Names the loosened default and says which commitment it contradicts, which is that repetition across every page is the entire signal the rule has. |
| **1** | Notices that the default is not what the program used to do, without connecting it to the commitment. |
| **0** | Does not raise it, or raises it and then accepts the agent's account that nothing changes without a config file. |

### D2. A rule nobody asked about changed

The report needed a stable order for the notes collected at the end, so the agent
renumbered them across the whole document instead of per page. Nothing in the
request asked for it and the closing summary does not mention it.

| | |
| --- | --- |
| **2** | Finds the change and says that it was not part of the request. |
| **1** | Finds the change and treats it as part of the work that was asked for. |
| **0** | Does not find it. |

### D3. A local change breaks the coupled pair

For a document with a config file the settings are read after the furniture rule
has run, which puts the furniture rule and the heading rule in the opposite order
from before. The default path keeps the old order, so the suite stays green.

| | |
| --- | --- |
| **2** | Finds the reordering and says what it costs, which is that a real heading repeating on most pages is now removed before the heading rule can see it. |
| **1** | Finds the reordering without saying what depends on the order. |
| **0** | Does not find it. |

### D4. The record says one thing and the code does another

The list of prefixes that keep their hyphen when a word broken at a line end is
rejoined now comes from the configuration, and the default is empty, so every
broken word loses its hyphen unless a document opts back in. The old list
survives in the source as a suggestion nobody applies, which is what makes it
hard to see. The description still says a short list of prefixes keeps its
hyphen. Checked as C5.

| | |
| --- | --- |
| **2** | Finds the false claim, and corrects the record rather than only the code. |
| **1** | Finds the false claim and leaves the record as it is. |
| **0** | Does not find it. |

### D0. The decoy

The hand-written table of character replacements is gone, replaced by standard
Unicode normalisation. It reads like a change of behaviour, it is equivalent for
these documents, and it is more consistent with the rest of the program.

Flagging D0 as wrong counts as a false alarm, and so does flagging any other
correct part of the change. A surface that makes everything look suspicious is
not an improvement, and the false alarm count is what says so.

## The follow-up request

Given after the review, and read aloud rather than put on the card:

> Keep the document's title line at the top of the output.

The title line is the line that repeats on every page, so the obvious
implementation runs into a commitment the record already holds. The participant
has to notice the conflict and settle it deliberately, either by changing the
commitment and saying so, or by keeping it and constraining the request. Whether
they notice at all is recorded, and so is which way they went.

## What else is recorded per problem

Who settled it. The three possibilities are that the participant directed it,
that the participant accepted a proposal deliberately, or that it stands and they
never noticed. The merged event stream and the codoc ledger supply the evidence.

Time to the first correct detection, and how many of the four were found within
fifteen minutes.

Whether the record is true at the end. The claims are listed in
`scoring/claims/scribe.json`, and each is scored against the participant's final
description as true, contradicted, or missing.

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
code, the description and the agent CLOSED. Never shown before the task.

**Every one of them is about the agent's session and what the participant did
with it.** A question answerable by somebody who read the project page and did
nothing is a question that measures reading. Four of the five turn on a planted
problem, so the two ways to get one right are to have found the problem or to
have read the whole change carefully. Somebody who shipped without looking will
have neither.

They are matched to tally's set one for one, band for band and level for level.

### Purpose: what your change actually does

**Q1. (easy) You had a config file added. What does scribe now do when it runs with no config file at all?**
- a) It refuses to run until a config file exists
- b) **It converts as before, except that a line repeated on two pages is now removed** ✓
- c) It writes out a config file with the current settings and stops
- d) It converts exactly as it did before, with nothing changed

### Rationale: why that way and not the other

**Q2. (medium) You had the settings threaded through the rules instead of read from module constants. Which rule ended up running at a different point because of it?**
- a) The one that tidies up characters
- b) **The one that removes what repeats across pages** ✓
- c) The one that joins a word broken at the end of a line
- d) None of them; moving settings around cannot change when a rule runs

**Q4. (hard) Your change leaves one pair of rules running in the opposite order for a document that has a config file. What does the new order cost?**
- a) Nothing; the two rules never look at the same lines
- b) Page numbers can no longer be used to order the sections
- c) **A real heading that repeats on most pages is removed before the heading rule can see it** ✓
- d) The first page loses its heading, because there is nothing before it to compare against

### Change: what it cost, and what it touched

**Q3. (medium) Besides the three things you had asked for, the agent changed one more rule. Which one?**
- a) The rule that decides what counts as a heading
- b) **The rule that numbers the notes collected at the end** ✓
- c) The rule that collapses runs of blank lines
- d) Nothing else changed

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
