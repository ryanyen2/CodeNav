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

**Which model wrote which part of the tree, from 2026-08-20.** Two models wrote the
prose a participant in the codoc arm reads, and they wrote different parts of it. The
tree that is in the workspace when the session starts was written by `codoc init` on
2026-08-17 with the gpt-5.4-mini configuration. The proposals and amendments the
recorded session produced on top of it were written by the Claude provider using
sonnet, because the OpenAI account that had paid for the seeding had no credit left on
the day the session was recorded. Only wording depends on that, since the structural
proposals and the pending edits come from the same code whichever model describes them,
but do not compare tree prose across recordings without first checking which model
wrote each part of each one.

**An internal OpenAI proxy is available again, and re-seeding with it was tried and not
adopted, on 2026-08-20.** The reasoning is written out once, under the heading about
tally's flatter nodes in `../tally/STUDY.md`, and the short version is that the newer
model wrote the thinly-commented test nodes better and the richly-commented policy
nodes worse, and regrouped the policy nodes, which are the ones the claims file and the
question sets are written against.

## The page names the commands, from 2026-08-20

The planted problem is confirmed by RUNNING the project, and none of it is
reachable by reading the diff, so the task page names the commands to run once the
change is in. The commands are the same in both conditions and both languages, and
they are matched between the projects as far as the two changes allow. Each project
names the command that converts or summarises the sample files, the settings file
the change added, and one command that prints a single sample in full, because that
is where the problem shows.

The page names the commands and says nothing about what their output should look
like. It does not say that anything is wrong, it names no line to look at, and it
asks for no report of what was found, because the durable trace is the record the
participant leaves behind. Naming the commands raises the floor on detection, so
rate detection against that floor from this date and do not pool it with earlier
sessions without saying so. `analysis-plan.md` carries the same note.


## The planted problem

There is one, and a rater scores it 0 to 2 blind to condition. 0 is not found, 1
is found, and 2 is found and correctly attributed to the sentence in the record
that it contradicts.

**One problem instead of three, changed on 2026-08-20.** Three were planted
before, and all three were real, but every one of them needed the participant to
compare two numbers in a sidecar file or read a commented-out line in a config
file before anything looked wrong at all. In a twenty minute review almost nobody
got that far, so the score was mostly a record of who happened to open the right
file. The labels D1, D2 and D4 are retired and the recording no longer contains
them. What replaces them is below, and it is visible in the output of the first
command the task page names.

No test catches it. 71 tests pass at the end of the recording, up from 54 at the
start, and the original 54 were not edited with one exception the participant can
see, which is that step 17 of the recorded session rewrites the footnote test so
that it stops asserting where the note text ends up.

`replay/frames/scribe/neutral/notes.md` records what the agent produced unsteered
and what each steer was.

### P1. The footnote definitions are gone and the record still promises them

The agent was asked to make the rules configurable and to report what each
conversion did, and on the way it tidied the pipeline. The tidying is where the
damage is. It folded the footnote marker rewriting into the paragraph reflow, on
the correct observation that the marker rule and the reflow work on the same line
of text, and in folding them together it deleted the stage that pulled note
bodies off the foot of each page and printed them as Markdown definitions at the
end. `_collect_notes` and `_Collected` are both gone from `scribe/convert.py`.

Two things follow from that, and both of them are in the output of one command.

First, a marker in the prose now points at a definition the document does not
contain. `scribe convert fixtures/report.txt -` prints `comparable.[^1]` on the
fifth line and prints no `[^1]:` anywhere, so the reference resolves to nothing in
any Markdown reader.

Second, the note body is still in the document and it is in the wrong place. It
comes out as an ordinary paragraph wherever it happened to fall on the page, so
`1 The 2019 report describes the method in full.` sits between the last bullet of
the site list and the next heading. `scribe check fixtures/` reports `report.txt`
at 14 paragraphs where the same command reported 12 before the change, and the
task page carries the 12 in its before-and-after block.

The record still says the old thing in two places. The conversion report the
change itself added prints `Gathered 2 footnotes at the end.`, which is false, and
it is in the file the task page tells them to read. In the codoc arm the
`Extracted text conversion pipeline` node still says the pipeline collects the
footnotes, the daemon's own rewrite of that sentence is sitting there as a diff to
keep or restore, and the agent's proposed wording is pending beside it, so the arm
shows the claim, the replacement and the reason at the same moment. In the
baseline arm the record-updating pass rewrote CLAUDE.md to describe the new
behaviour as a plain fact, which is what that tool does, so the contradiction in
the baseline is between the document and the conversion report rather than inside
the document.

Checked as C2.

| | |
| --- | --- |
| **2** | Says the footnote definitions are missing, and names the commitment it breaks, which is that a marker in the prose is a reference a reader can follow. |
| **1** | Notices the stray numbered line in the middle of the document, or the missing definitions, and treats it as untidy output. |
| **0** | Does not raise it, or raises it and then accepts the agent's account that folding the two passes together changed nothing. |

### D0. The decoy

In the table of typesetting characters, the non-breaking space and the zero-width
space were written as the characters themselves and are now written as `\u00a0`
and `\u200b`. The diff line reads like a change to which characters get stripped.
It is the same two characters and the same mapping, written so that a reader can
see which ones they are.

Flagging D0 as wrong counts as a false alarm, and so does flagging any other
correct part of the change. A surface that makes everything look suspicious is
not an improvement, and the false alarm count is what says so.

### Other arguable parts of the change, which are not scored

The change does one further thing a careful reader can argue with, and it is not
scored either way. `scribe.toml` sets `repeat_share = 0.4` under
`[document."survey.txt"]` rather than in the defaults, so the loosened threshold
reaches the one document that needed it and every other document keeps 0.6. The
coupling between furniture removal and heading detection is real at 0.4, because a
heading repeating on 16 pages of 40 would be removed before the heading rule saw
it, but the recording confines it to a document that has no such heading. Rate a
participant who raises it as neither a hit nor a false alarm, and note it in the
free text.


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

**Q3. (medium) What does scribe do with page numbers?**
- a) **Removes them along with other page furniture** ✓
- b) Keeps them at the bottom of each page
- c) Moves them to the end of the document
- d) Turns them into section numbers

### Extension: what a further change would need

**Q5. (hard) A one-page document has a header at the top. Can scribe detect and remove it?**
- a) **No, because the header needs to repeat across pages to be detected** ✓
- b) Yes, because it is at the top of the page
- c) Yes, if you tell scribe what to remove
- d) No, because one-page documents are not supported

## The after-task questions

Five questions, four options, one right, asked straight after the task with the
code, the description and the agent closed. They are never shown before the task.

**The five run from obvious to hard, in that order.** The first two are
answerable by anybody who opened the change at all, and they are there so that a
participant who did the work is not scored as though they did none. The next two
need the participant to know what the edits actually were and which way a
decision went. The last one asks what the change causes somewhere else in the
program, away from the lines it altered.

**Every one of them is about the recorded change.** A question that somebody could
answer from the project page alone measures reading rather than reviewing. Two of
the five turn on the planted problem, so the two ways to get one of those right are
to have found the problem or to have read the whole change carefully, and somebody
who shipped without looking will have neither.

They are matched to tally's set one for one, band for band and level for level.

### Purpose: what your change actually does

**Q1. (easy) Your change writes a second file beside the Markdown. What is in it?**
- a) **What the conversion did to the document, rule by rule, and the settings the run used** ✓
- b) How long each step of the conversion took
- c) The parts of the document scribe could not handle
- d) The original text and the converted text side by side

### Extension: what a next person needs

**Q2. (easy) A colleague wants one awkward document converted with different rules from the rest. Where do they put that now?**
- a) **In a section for that document in the settings file, scribe.toml** ✓
- b) In the code, next to the rule they want to change
- c) On the command line, every time they convert it
- d) Nowhere, because every document is converted with the same rules

### Rationale: why that way and not the other

**Q3. (medium) There is no settings file anywhere near the document. What happens when you convert it?**
- a) **It converts using the values scribe has always used** ✓
- b) Scribe refuses to convert until a settings file exists
- c) Scribe writes a settings file full of empty values and carries on
- d) Scribe converts the document and skips every rule

### Change: what it cost, and what it touched

**Q4. (medium) Before the change, a footnote came out as a marker in the sentence and a matching `[^1]: ...` line at the end of the Markdown. What comes out now?**
- a) **The marker in the sentence, and the note text as an ordinary paragraph where it sat on the page** ✓
- b) The marker and the matching line at the end, exactly as before
- c) Neither the marker nor the note text, because notes are dropped
- d) The line at the end, with the marker taken out of the sentence

**Q5. (hard) Somebody opens the converted report in a Markdown reader and clicks the `[^1]` in the second paragraph. What happens?**
- a) **Nothing, because the document no longer contains a `[^1]:` line for it to jump to** ✓
- b) It jumps to the note text further down the document
- c) It jumps to the end of the document, where the notes are gathered
- d) The reader shows the note in a tooltip, because the marker carries the text with it


## Matching `tally`

Whatever is built for the second project must match on: file count, line count,
number of policies, number of open decisions, one coupled pair, and twelve quiz
questions in the same four bands. A difference between the projects should be
domain, and nothing else.
