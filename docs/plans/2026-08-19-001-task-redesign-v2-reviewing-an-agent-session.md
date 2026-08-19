# Task redesign v2, reviewing an agent session

Status: agreed in outline on 2026-08-19, now being built on branch
`study/task-redesign-v2-review-session`. Supersedes the task half of
`2026-08-16-001-task-redesign.md`. The two projects, their policy tables, both
trees, both CLAUDE.md files and the whole instrument survive. The task, the
hazards and the measures are replaced.

## Why the claim changed

The study has been measuring whether a person can understand a codebase. A
reviewer can refuse the claim easily, because models keep getting better at
writing code, so fewer people read code, so an aid for reading code is worth
less over time.

The claim we want to defend is different, and the reviewer's own premise is what
makes it true. Once working code stops being the hard part, the hard part is
saying what you want and checking that you got it. Every request is
underspecified, so the agent fills the gaps with choices, and those choices
constrain every later change. If nobody can see or revise the choices, you are
not directing the system, you are accepting whatever has accumulated.

So codoc is the surface where a person decides and reviews, rather than an aid
for reading code. The better the models get, the more necessary a non-code
surface becomes, because the code is the artifact nobody opens.

Against a hand-written CLAUDE.md the difference is not the idea of keeping a
document. The difference is whether anything keeps the document true. Prose that
nobody checks drifts away from the code, and a log of attempts records history
rather than current state. What we are testing is faithfulness, meaning that the
record keeps saying what the system now does, and that any claim in it can be
visited, revised, and checked. Visiting a claim means jumping to the code the
claim is about. Revising a claim means changing the words and having the code
follow. Checking a claim means seeing who wrote it, what request it answered,
and what text it replaced.

We concede one case in the paper rather than arguing it. If someone never
revisits a system, never debugs it, and never carries a requirement that
conflicts with an old decision, then no document helps them. Our claim is not
that everyone must understand their code. Our claim is that anyone who has to
change a system twice needs a trustworthy record of what the system is supposed
to do, and unmaintained prose is not one.

## Why the current task cannot show it

The current task asks the participant to add one rule and settle four questions
the card leaves open. The agent implements the rule in about a minute, in one or
two files, and it narrates itself while working.

For a change that small, the agent's own transcript is already an adequate
shared record. The person watches Claude Code's output, sees the two files it
touched, and knows what happened, so codoc has nothing to be better at. The
first pilot showed exactly that shape in both conditions. The person wrote one
or two prompts, the agent did everything, and the person opened almost nothing.

The 2026-08-16 redesign was right that the difficulty belongs at the human layer
and wrong about which phase it belongs in. It moved the difficulty from finding
code to settling decisions, but it kept the person in front of the change.
Bindings, amend diffs, proposals with verdicts, comments that become directives,
per-span authorship, the provenance chain and the timeline are all built for the
person who arrives after a lot of code has moved. The task has to put the
participant there.

## The scenario

The participant is told the following.

> You wrote a request before lunch. Your agent worked for forty minutes and
> finished. The tests pass. Decide what to keep, and ship it.

The participant did not author the change. The participant is its first reader,
and has to end the session in a state they would put their name on, meaning the
code does what they meant and the record says what the code does.

One scenario now covers both research questions. For understanding, the question
is no longer what the codebase is, it is what the agent decided on the person's
behalf and what else the agent touched without being asked. For authored
modification, the question is whether the person takes control back, by
rejecting a part, commenting on a claim, correcting the record, and redirecting
the agent.

## What the recorded session has to contain

A review task is only hard when the change is more than a transcript and a
file-ordered diff can carry. Four properties do that, so the recorded session is
designed to have all four rather than designed to be large.

| Property | What it defeats | How the session gets it |
| --- | --- | --- |
| Volume | Holding the change in your head | About 500 changed lines over about 12 files, on a 600-line program |
| Spread across intents | `git diff`, which is ordered by file | One intent touches every module, and one module carries three intents |
| Interleaving | The transcript, which is ordered by time | Three intents pursued in braided order, so no run of the transcript is one intent |
| Self-report | The summary, which the reviewed party wrote | The agent's closing message and its CLAUDE.md edit are the agent's own account |

The fourth property is the important one, and it is not a trick played on the
baseline. In the baseline condition the record after the session is the agent's
description of its own work. In the codoc condition the bindings are derived from
the code by Loop A. A defect where the description and the code disagree is the
case that separates the two conditions, and it is the mechanism we are testing.

## The codebase question, answered

The obvious move is to find a larger and more realistic repository, and it is the
wrong move. In a review task the participant's reading is scoped by the record
rather than by the codebase, so the participant never needs to understand code
they were not pointed at. Size then buys no difficulty and only risks the floor
effect that stopped the first pilot, where 2,000 lines across 15 files could not
be read cold. What buys difficulty is the shape of the diff, and a small program
can produce a hard diff if the session restructures it while extending it.

So we keep `scribe` and `tally` unchanged, and the recorded session grows them.
The session adds a configuration layer and a second output path, both of which
are natural for both programs and both of which require every policy module to be
touched. Afterwards each project is about 1,000 lines, and the diff under review
is about 500 lines across about 12 files. Nothing new is built, because the
growth is the change itself.

Keeping the projects also keeps the instrument. The nine policy tables, the
coupled pairs, both seeded trees, both CLAUDE.md files, the content parity gate
between them, the zh-Hans translations, the four workspace tarballs, the scoring
scripts and the bundle build all carry over.

We rejected three alternatives. Reusing `hearth` and `ember` would need new
descriptions, new quizzes, new translations and a new parity pass, which is the
work that was just finished for `scribe` and `tally`. Using a real open source
repository would cost us control over the planted defects, content parity between
the conditions, and a clean bootstrap, which is too much to give up at twelve
participants. Growing `scribe` and `tally` to 2,500 lines first would pay for
size the participant never reads.

## What is planted in the change

Four graded problems are planted per project, plus one decoy. None of them breaks
a test, because a defect that fails the suite is caught by the suite and measures
nothing. Each is found by a different route, so the two conditions can differ for
a reason we can state.

| | Kind | Found by |
| --- | --- | --- |
| D1 | A default in the new configuration quietly loosens a policy the record states | Reading the record and checking the code, which either condition can do, but one has to know to look |
| D2 | A policy nobody asked about was changed because it was in the way, and the summary omits it | Seeing which intents the session touched, which the summary does not report and the tree does |
| D3 | A locally correct change breaks the project's coupled pair, and the suite stays green | Seeing that the other feature's code moved |
| D4 | A claim in the post-session document is contradicted by the code the claim describes | Visiting the claim's code, which in the baseline points at nothing |
| D0 | A decoy that looks alarming and is correct | Nothing. It is there to price false alarms |

In `scribe` the request is to add a configuration file so the policies can be
overridden per document, and to write a short `report.md` next to the output
saying what was done. D1 drops the repeat share to 0.4 by default, so a
line near the edge of two pages out of five is now removed, while the code has
used 0.6 since it was written and the record says a running header repeats across
most of the document. D2 renumbers
footnotes across the whole document instead of per page, because the report
needed a stable order, and the summary does not mention it. D3 reads the
configuration after `furniture.strip` for configured documents, which inverts the
furniture and heading order that policies 3 and 4 are coupled through, while the
default path keeps the old order so the suite stays green. D4 takes the prefix list that keeps a
hyphen when a broken word is rejoined from the configuration and defaults it to
empty, while the document still says a short list of prefixes keeps its hyphen. D0 replaces the hand-written ligature table with
NFKC normalization, which reads like a behavior change and is equivalent here.

In `tally` the request is to move the merchant rules into `rules.toml` and add a
`--by-week` mode. D1 turns transfer exclusion into a flag that defaults to
including transfers, against a stated policy. D2 aligns weeks on the posted date,
which silently moves month attribution from the made date to the posted date, and
the summary does not mention it. D3 drops the merchant from the dedupe key in the
weekly path, which breaks the coupling between transfers and duplicates. D4 makes an
unmatched merchant an error when the rules move into a file, so the run stops,
while the document still says such a row goes to uncategorised and the run
finishes. D0 replaces the first-match rule loop
with a precompiled ordered mapping, which looks like a precedence change and is
not.

Every planted problem is checked by a probe in `scoring/claims/<project>.json`,
which runs the participant's final code on a sample and looks for one signal, so
what the code does is measured rather than assumed.

One rule keeps the design honest, and it is worth stating precisely because the
first attempt at it was stated too broadly.

The change under review is a **constructed stimulus**. An agent asked to add a
configuration layer is careful by default, checks its own output against the old
output, and lands none of the planted problems, so the recording is steered until
it does. Every steer is written into `notes.md` beside the frames, and the paper
says the change was constructed. Constructing the stimulus is what makes twelve
participants comparable at all.

**What is never authored is codoc's response to it.** The recording runs with the
daemon live, and whatever Loop A produces over that code is what ships. If the
tree fails to surface a planted problem, participants see it fail and the paper
reports that codoc failed to surface it. Writing the tree ourselves would make
the faithfulness claim circular, and faithfulness is the claim the phrase "shared
representation" depends on. The distinction is the whole design: the stimulus is
ours, the record of it is codoc's.

## Recording and replay

Nothing about waiting forty minutes for code to be written is part of our
contribution, and we do not care about the quality of the code the agent produces
while the participant watches. So the whole agent session is recorded once, in
advance, and replayed to each participant in about three minutes. The
participant's time then goes to the surfaces we are actually testing.

Replay is cheap here because of how codoc is built. The local extension is file
based, with no server and no port, and everything the participant sees comes from
files under `.codoc/`, which the extension watches and reparses on change. The
webview is a projection of `tree.doc.json`, the status bar reads `status.json`,
and the proposals come from the control files. Writing recorded copies of those
files back into the workspace drives the entire interface without changing a line
of codoc.

### What gets recorded

First, we run the real session once per project, in a clean workspace, with the
daemon running and with the request above as a single prompt. We nudge only as
far as needed to land D1 to D4 and D0, and we write down what was nudged.

Second, we capture the session as an ordered list of frames. A frame holds the
code files the agent wrote at that point, the `.codoc/` files the daemon wrote in
response, the terminal text Claude Code printed, and a delay. Frames are grouped
into three segments, one per intent, so the participant sees the tree react three
times rather than once.

Third, we derive the baseline condition's record from the same code recording, by
running the documentation maintenance skill over the same diff. Both conditions
then review identical code, which settles the question of whether to record one
session or two. One code recording, two record recordings.

Fourth, we snapshot the end state and check it, by replaying the frames into a
clean workspace and comparing the result against the recorded end state file by
file. The check becomes a gate in the bundle build, next to the parity gate.

### What happens in the room

The participant reads the request card and says in one sentence what they expect
the agent to do, which gives us a cheap before measure and gives them ownership
of a request they did not write. The replay then runs while they watch. The
terminal prints the recorded output, the files change under it, and in the codoc
condition the tree amends, the proposals arrive and the decorations appear,
because we are writing the same `.codoc/` files the daemon wrote during the
recording. The daemon itself stays stopped during replay, so no LLM call runs, no
participant waits for one, and every participant sees the same frames.

At the end of the replay the daemon starts, and the terminal hands over to a live
Claude Code session. We ship the recorded session file into the participant's
Claude Code project directory and resume it, so the participant's first prompt
continues the session that produced the change, with the agent's own context
intact. The recorded transcript is therefore also the scrollback, and reading it
is an honest strategy that both conditions have and that we want to measure.

Everything before the participant's first prompt is recorded, and everything
after it is live. There is no interactive puppetry and nothing to break, because
the only moving part is a script that writes files and prints text.

### What the build settled

Four things were decided while the harness was written, and they are recorded
here because each of them changes what a number means.

First, the change is left uncommitted in the working tree, so `git diff` shows the
whole change and reading it is a route a participant can take. Both conditions
have it, and which route somebody takes is recorded rather than steered.

Second, the index directories under `.codoc/` are copied once into the last frame
rather than into every frame. They are the daemon's own working state, no
participant ever sees them, and a copy per frame would be large for no gain.

Third, the player writes `.codoc/replay.stamp` when it hands the workspace over.
The shipped store already holds the recorded session's own ledger events, so
without a watermark the scoring would count the recording's edits as the
participant's, in the same way that seeding was counted before the last pilot.
`scoring/ledger-actions.py` reads the stamp and drops everything older.

Fourth, the probes that check the planted problems were written against the code
and then run, rather than written from the design. Two of the five planted
problems changed as a result. The furniture rule uses a share of the pages rather
than every page, so the loosened default is a share of 0.4 against 0.6, and the
false claim in `scribe` is now about the prefix list that keeps a hyphen, which
can be checked by running the program.

### What the replay must not corrupt

The interaction logger records what the participant does, so replayed writes must
not be counted as the participant's work. The existing merge rule already covers
it, because the logger keeps an editor edit only when the file is active and
focused, and the shipped transcript owns the agent's actions. The player writes
files while nothing is focused, so its writes land as agent actions, which is
what they are.

The codoc change ledger needs the same care. Seeding events were already excluded
from `ledger-actions.py`, and the recorded session's events have to be excluded
the same way, so that a participant's own accepts and amends are not buried under
the recording's.

## The second half of each task block

Detection on its own under-tests the surface, so after the review the participant
gets a short follow-up request whose obvious implementation conflicts with a
commitment the record already holds. The participant has to notice the conflict
and settle it deliberately, either by changing the commitment and saying so, or
by keeping it and constraining the request. The follow-up is where the handoff
path gets used, and the block ends when the code does what the participant meant
and the record says what the code does.

## What is measured

The gate is that the tests pass at the end, and it is not reported as a result.

For understanding, we measure detection coverage over D1 to D4, rated 0 to 2 and
blind to condition, where 0 means not found, 1 means found, and 2 means found and
correctly attributed to the commitment it contradicts. We also take the time to
the first correct detection, the coverage at fifteen minutes, and the false alarm
count, which is D0 plus any correct change the participant flagged as wrong. A
surface that makes everything look suspicious is not an improvement, and the
false alarm count is what says so. The closed-book quiz stays, with five items
rewritten to ask what the session decided and what else it touched, and the
existing extractor, timing and translation machinery carry over unchanged.

For control, we measure who settled each decision, meaning the participant
directed it, the participant deliberately accepted a proposal, or it stands and
the participant never noticed. The merged event stream and the codoc ledger
supply the evidence. We also measure whether the correction landed in the code.

The headline outcome is the truth of the record at the end. Each project has a
fixed list of claims that can be checked against code, and each participant's
final record is scored claim by claim as true, contradicted, or missing. The
measure is the same argument as the 137 stale references, at the scale of one
session, and it is what earns the phrase "shared representation".

The second outcome runs offline after all sessions. We hand each participant's
final record to a fresh agent in a clean checkout with a further task, and we
score whether the agent's change respects the commitments. The probe answers the
reviewer directly, because it concedes a world where no human reads the record
and still separates a maintained record from a frozen one.

Secondary measures are the strategy codes for what the participant read and in
what order, the questionnaire, and the agent turn overhead.

## Time budget

Recording the session in advance is what makes the budget work. Each condition
takes about 35 minutes, made of 3 minutes of replay, 15 minutes of review, 12
minutes of follow-up, and 5 minutes of quiz. Two conditions come to 70 minutes,
and the introduction, questionnaires and debrief add about 20, which fits the 105
minute session with some slack. Without the replay the two conditions alone would
run past 105 minutes, and 40 of those minutes would be the participant waiting.

## What changes in what already exists

Unchanged: both projects, both policy tables, both coupled pairs, both trees,
both CLAUDE.md files, the parity gate, the zh-Hans translations, the workspace
tarballs, the logger and its merged transcript, `ledger-actions.py`, the
snapshotter, and the bundle build.

Replaced: the task cards, which go from implement to review and ship; the four
rated open decisions, which become four detected problems; the quiz content,
which goes from codebase facts to session facts; and the two `STUDY.md` rubrics.

New: the recorder and the player under `docs/study-materials/replay/`, the frame
files per project and per condition, the planted defect list per project, the
claim list for the record truth audit, the transfer probe harness, and a replay
end state check in the bundle build.

Still open from before and now more urgent: the study is not pre-registered, and
the one frozen threshold in the v2 design is written against a research question,
a codebase and a task that have all been replaced. The threshold has to be
restated against detection coverage and record truth before the first
participant, or the study is exploratory and the paper has to say so.

## Decisions taken, so they are not reopened

The recording is one code session per project, with the baseline record derived
from the same diff, so both conditions review identical code. The participant is
told they wrote the request, and they state their expectation in one sentence
before the replay. Four problems are planted rather than three, because the
replay pays for the time. D4 stays, with one rule attached: if the agent writes
an accurate document on its own, we do not nudge D4 into existence, we drop to
three problems for that project and report which problems were spontaneous and
which were nudged.
