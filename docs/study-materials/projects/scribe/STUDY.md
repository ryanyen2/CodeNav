# scribe, as a study instrument

Not shipped to participants. What the task is, what is being rated, and why each
question has the answer it has.

The task changed on 2026-08-19. The participant asks the agent for a change and
then reviews what it did, rather than making the change themselves, and the
reasoning is in
`docs/plans/2026-08-19-001-task-redesign-v2-reviewing-an-agent-session.md`. Two
things went with that change and are recorded where they used to be described: the
follow-up request, and the round of questions that used to be asked before the
task. Both sections say what happened to them rather than having been deleted.

## The task, as the participant meets it

There is no task card. It was replaced on 2026-08-19, because a card that said
"you asked for" assumed a story nobody had told the participant, and meeting it
cold the reasonable next move was to ask the researcher.

The task page now reads as one occasion, in this order. First, one case where
scribe behaves unhelpfully, which is a report carrying a running header over its
main pages and a different one over its appendix, where the first is removed and
the second stays in the middle of the writing. Second, what they are therefore
asking for, as three plain lines. Third, the request itself in a copy block, which
they paste into the agent. Fourth, what to do while it works, and what is left to
them.

The two halves of the task are on the page as well. The first is working out what
the agent changed and how the project works now, and the second is deciding what to
keep and leaving the project in a state they would be happy to ship.

Nothing on the page says that anything is wrong, and nothing says that everything
is fine. Whether the participant looks at all is one of the outcomes, so the page
asks for a decision and leaves the rest to them. Sending the request themselves is
what makes the change theirs to decide about.

## What the recorded agent was asked for

The prompt is in `replay/requests/scribe.txt`, and it is word for word the request
the participant is given to paste, so the change they watch arrive is the change
they asked for. Three things are asked for at once, so no run of the transcript is
about a single intent and no file carries only one intent. The prompt says nothing
about defaults, which is the ordinary case, and the choices the agent makes to fill
that silence are what the study is about.

The session is recorded once and replayed to each participant in about three
minutes, because watching an agent write code for forty minutes is not what we
are measuring and the quality of that code is not what we are rating. How the
recording works, and what keeps it honest, is in `replay/README.md`.

## The page names the commands, from 2026-08-20

Every problem below is confirmed by RUNNING the project, and none of them is reachable
by reading. The task page now names the commands to run once the change is in — the
same in both conditions and both languages, and matched between the projects as far as
the two changes allow: each names the command that runs the fixtures and the settings
file the change added, and tally names two more because its change added a second way
to view the same numbers and scribe's did not —
because a participant who did not think to run anything scored zero for a reason that
has nothing to do with the way of working they were given.

The page still says nothing about anything being wrong, names no line to look at, and
asks for no report of what was found: the durable trace stays in the record they leave
behind. Rate detection against this higher floor from this date, and do not pool it
with earlier sessions without saying so (`analysis-plan.md` carries the same note).

## The three planted problems

Each is rated 0 to 2, blind to condition. 0 is not found, 1 is found, and 2 is
found and correctly attributed to the commitment it contradicts. None of them
breaks a test, because a problem the suite catches measures nothing. All 98 tests
pass at the end of the recording, up from 54 at the start, and the original 54
were not edited.

**Three rather than four, and the reason is in the recording.** The fourth was
going to be D3, the coupled one, where a change looks local and is not. The agent
built per-document settings correctly in its first pass, so the request that was
meant to produce it was never needed. A problem the agent will not produce from a
request a person would actually send is not nudged into existence with a request
nobody would send. Some of the coupled class survives inside D1, as a consequence
of it rather than as a problem of its own, and D1 says so. The labels below keep
the gap at D3 so that they match `scoring/claims/scribe.json` and the recording
notes.

`replay/frames/scribe/neutral/notes.md` records what the agent produced unsteered
and what each steer was. The first request produced none of the three: it kept
every original test passing untouched and checked its own converted Markdown byte
identical against the original. That is why the stimulus is constructed, and the
paper says it is.

### D1. The new default loosens a stated policy

Asked to make the repeat threshold a setting and pick a default that catches a
header appearing on two pages out of five, the agent found that the threshold was
two things and only one of them was a setting. It exposed the hardcoded floor as
`min_repeats` and moved the share from 0.6 to **0.4**, having laid the thresholds
out in a table and rejected 0.5 because 0.5 catches a five-page document only
through truncation and a six-page one would still slip through.

The description says a running header is a line that repeats on at least 60% of
the pages, and the code used 0.6 from the day it was written. The sample documents
have enough pages that no test moves. Checked as C1.

The coupling lives here. At 0.4 a real heading repeating on 16 pages of 40 is
removed before the heading rule can see it, where at 0.6 it survived, and
`furniture.py` warns that a heading eaten this way is gone before anything can
rescue it. The agent wrote that cost down in its own reply.

| | |
| --- | --- |
| **2** | Names the loosened default and says which commitment it contradicts, which is that repetition across most of the pages is the entire signal the rule has. |
| **1** | Notices that the default is not what the program used to do, without connecting it to the commitment. |
| **0** | Does not raise it, or raises it and then accepts the agent's account that nothing changes without a config file. |

### D2. The report promises a cross-reference that does not work

The report lists the notes in document order, numbers them by position, and
prints beside each the marker the note actually carries. On a document that
numbers its notes per page that produces two lines both marked `[^1]`, so the
cross-reference the request asked for cannot be followed. Checked as C2.

The agent half saw it. The report now says in its own text that the marker is
"the source's own numbering" and "is not always the same", so a participant has
two places to catch this, the report itself and the record's promise. Reading the
report is a route worth coding separately.

| | |
| --- | --- |
| **2** | Finds that the cross-reference does not work and says what the record promises. |
| **1** | Finds the repeated marker and treats it as cosmetic. |
| **0** | Does not find it. |

### D4. The record says one thing and the code does another

The list of prefixes that keep their hyphen when a word broken at a line end is
rejoined now comes from the configuration, and the default is empty, so every
broken word loses its hyphen unless a document opts back in. `well-` and `being`
come back as `wellbeing`. The old twelve prefixes survive in `scribe.toml` on a
commented line framed as somewhere to start, which is what makes this hard to see:
the words are still there. The description still says a short list of prefixes
keeps its hyphen. Checked as C5.

| | |
| --- | --- |
| **2** | Finds the false claim, and corrects the record rather than only the code. |
| **1** | Finds the false claim and leaves the record as it is. |
| **0** | Does not find it. |

### D0. The decoy

In the table of typesetting characters, the non-breaking space and the zero-width
space were written as the characters themselves and are now written as `\u00a0`
and `\u200b`. The diff line reads like a change to which characters get stripped.
It is the same two characters and the same mapping, written so that a reader can
see which ones they are.

Flagging D0 as wrong counts as a false alarm, and so does flagging any other
correct part of the change. A surface that makes everything look suspicious is
not an improvement, and the false alarm count is what says so.

## The follow-up request, which is no longer given

**Dropped on 2026-08-19. It is written down here because it was part of the
instrument, not because anybody reads it out.** It used to be given after the
review:

> Keep the document's title line at the top of the output.

The title line is the line that repeats on every page, so the obvious
implementation runs into a commitment the record already holds. Whether the
participant noticed the conflict, and which way they settled it, was recorded.

It was dropped because there is one request per condition now and the task runs
twenty minutes. A second request read out mid-task splits those twenty minutes
into reviewing somebody else's change and then making one of your own, which are
different activities, and the study is about the first. Nothing measures that
conflict any more. What survives of the idea that a change can look local and not
be is inside the planted set, where D1 carries the coupling and says so.

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

**No longer asked in a session. Dropped on 2026-08-19.** These five questions were
asked before the task, open book and timed, and the round has been removed from
the participant's page. Nothing writes `answers/quiz-scribe-before` any more, and
the measure it fed, how well and how fast somebody can find an answer in this
codebase, is gone with no replacement. `analysis-plan.md` records that.

It was dropped because reviewing a change to the codebase means working the
codebase out, so the first half of the task is the same activity this round
measured, and the whole task budget is twenty minutes.

The questions are kept, and this heading has to stay exactly as it is, because two
programs read the section. `scoring/check-description-answers.py` uses them as a
smoke test on the descriptions, which is a floor on guessability and a check that a
description has not lost its content. `study-app/scripts/extract-questions.mjs`
still extracts them. What follows describes the round as it was run, and is kept as
the record of an instrument rather than as instructions for a session.

They were five questions, four options, one right.

**They were answered open book, with a clock running.** The participant could read
the description, read the code, run the project, and ask the agent. The one thing
barred was pasting a question at the agent, which would measure the agent. That
changed in 2026-08: asking people to answer "from what you have just read"
measured how much of a two-minute briefing they retained, which is not what either
way of working is for. How quickly somebody could find five answers was the
comparison, so the elapsed time was stored with the answers.

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

**Nine blind was the open problem in this instrument, and it is now moot.** A
frontier model with no description gets as many as one reading either description,
so on this evidence the questions did not separate the arms. A frontier model is a
harsh proxy for somebody with ten minutes and a codebase they met today, so the
number is a ceiling on guessability rather than a prediction, which is exactly what
the smoke test still uses it for. The answer to it used to be that the sitting was
timed as well as scored, and a blind run cannot speak to elapsed time at all. With
the round dropped there is no sitting and no elapsed time, so nothing rests on the
score either way.

### Purpose: what it is for, and where it stops

**Q1. (easy) A page header appears on every page of a three-page document. What does scribe do with it?**
- a) **Removes it, because it repeats on most pages** ✓
- b) Keeps it, because it is real text
- c) Keeps the first one and removes the rest
- d) Turns it into a heading

### Rationale: which way it went, and why

**Q2. (easy) The word "well-being" is split across two lines as "well-" then "being". What does scribe produce?**
- a) wellbeing, with the hyphen removed
- b) **well-being, with the hyphen kept** ✓
- c) well- being, with the line break still there
- d) well being, with both the hyphen and line break removed

**Q4. (medium) A heading like "3.1 Sites" appears on most pages of a document. What happens to it?**
- a) **It is removed as page furniture, because it repeats on most pages** ✓
- b) It is kept as a heading, because it is numbered
- c) The first one is kept and the repeats are removed
- d) It is kept, because furniture removal happens after heading detection

### Change: what happened, and what it cost

**Q3. (easy) What does scribe do with page numbers?**
- a) **Removes them along with other page furniture** ✓
- b) Keeps them at the bottom of each page
- c) Moves them to the end of the document
- d) Turns them into section numbers

### Extension: what a further change would need

**Q5. (easy) A one-page document has a header at the top. Can scribe detect and remove it?**
- a) **No, because the header needs to repeat across pages to be detected** ✓
- b) Yes, because it is at the top of the page
- c) Yes, if you tell scribe what to remove
- d) No, because one-page documents are not supported

## The after-task questions

Five questions, four options, one right, asked straight after the task with the
code, the description and the agent CLOSED. Never shown before the task.

**The five run from obvious to hard, in that order.** The first two are
answerable by anybody who opened the change at all, and they are there so that a
participant who did the work is not scored as though they did none. The next two
need the participant to know what the edits actually were and which way a
decision went. The last one asks what the change causes somewhere else in the
program, away from the lines it altered, and it is the only hard one in the set.

**Every one of them is about the recorded change.** A question answerable by
somebody who read the project page and did nothing is a question that measures
reading. Three of the five turn on a planted problem, so the two ways to get one
of those right are to have found the problem or to have read the whole change
carefully. Somebody who shipped without looking will have neither.

They are matched to tally's set one for one, band for band and level for level.

### Purpose: what your change actually does

**Q1. (easy) Your change added a report beside the Markdown output. What does the report show?**
- a) **What scribe changed: lines removed, words rejoined, and notes moved** ✓
- b) How long each step of the conversion took
- c) The parts of the document scribe could not handle
- d) A comparison of the original and converted text side by side

### Extension: what a next person needs

**Q2. (easy) Where are the conversion settings stored now?**
- a) **In a settings file that scribe looks for near the document** ✓
- b) In the code, where they were before
- c) On the command line, given on every run
- d) In an environment variable

### Rationale: why that way and not the other

**Q3. (easy) A document has no settings file. What happens when you convert it?**
- a) **It works the same as before, using the default rules** ✓
- b) Scribe refuses to convert it
- c) It skips all the rules and just joins lines
- d) It creates a settings file with empty values

### Change: what it cost, and what it touched

**Q4. (easy) What did the change do to the furniture threshold — the share of pages a header has to appear on before it is removed?**
- a) **Moved it into the settings file so you can change it per document** ✓
- b) Kept it the same but made it stricter
- c) Removed it, so all repeated lines are removed
- d) Did not change it at all

**Q5. (medium) If you lower the furniture threshold so that fewer repeats are needed, what else could that affect?**
- a) **A real heading that repeats across pages could be removed as furniture** ✓
- b) Nothing, because furniture and headings are completely separate
- c) Page numbers would stop being removed
- d) The document would get longer

## Matching `tally`

Whatever is built for the second project must match on: file count, line count,
number of policies, number of open decisions, one coupled pair, and twelve quiz
questions in the same four bands. A difference between the projects should be
domain, and nothing else.
