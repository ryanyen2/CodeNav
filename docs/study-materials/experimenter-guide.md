# Running a session

How to run one session of the study, from a machine with nothing installed to a
folder of collected data. The design and the reasoning behind it are in
`docs/plans/2026-08-11-001-user-study-design-v2.md`. This is the operating
instructions only.

## What the study compares

Each participant works twice, once each way, on their own machine over a video
call. Both times they use Claude Code, and both times there is a written
description of the project that they are told to keep current. What changes is how
that description is kept.

- With codoc, they get VS Code and the codoc extension. The description is tied to
  the code, and when codoc proposes a change to it they accept or reject it.
- Without codoc, they get a `CLAUDE.md` holding exactly the same text, and the
  agent is told to update it after every change it makes.

There are two projects, scribe and tally, with matched tasks. Each participant
does one project each way, so nobody solves the same problem twice.

Within a project, the two copies hold identical source and tests and the same 12
commits, so reading the history tells you the same things either way. The only
differences are the ones above.

## The two pages, and how a session runs

There are two web pages and one zip. You use one page, the participant uses the
other, and the zip is what they install.

- Your dashboard is <https://codoc-11b10.web.app/experimenter/>. You sign in with
  your MIT address. You create participants here, you get the link to send them
  here, and you type the sign-off, the who-settled-what record and the question
  scores here during the session.
- Their page is <https://codoc-11b10.web.app/participant/>, reached only through
  the link you send. It walks them through consent, the questionnaires, the task
  cards and the break, one step at a time, and saves as they go.
- The zip is `dist/codoc-study-bundle.zip`. It holds the extension, the four
  projects and a setup script.

A whole session, in order:

1. You press New in the dashboard. It gives you a code, e.g. `p-abcdefghjkmn`,
   and picks the order for you so the four combinations fill evenly.
2. You send them the link and the zip. Both are on the dashboard, ready to copy.
3. They open the link, give consent, answer the background questions, and run the
   setup script with their code. Days ahead, not on the day.
4. You watch the dashboard. Two marks turn green: one when they open the link,
   one when their editor first reports. Until the second one is green, nothing
   they do in the editor will reach you.
5. On the day, they share their screen and you record. They work through their
   page and you fill in your dashboard beside them.
6. Afterwards they run `collect.sh` and send you one file. You export their
   records with `scripts/export-session.mjs` and check the pair is complete.

The code is what ties all of it together. It is not secret and it identifies
nobody. Everything they do is filed against it, and a machine without it records
nothing, which is the one failure that cannot be repaired afterwards.

## Who pays for the model

You do. The study supplies two keys, so a participant never spends their own
money and never needs a Claude plan.

- An **Anthropic key** runs Claude Code, in all four workspaces, on
  `claude-sonnet-5` with thinking set to medium.
- An **OpenAI key** runs codoc, in its two workspaces, on `gpt-5.6-luna` with
  reasoning and verbosity both medium.

Setup asks for both and writes them into the four project folders. Nothing goes
into the participant's shell, so deleting the folders removes the keys and their
own projects are untouched.

An API key beats a claude.ai login, which is what makes this work: a participant
already signed in to their own account still runs the session on ours. Setup then
proves it by asking Claude Code one question through the key it just wrote, and
by asking OpenAI whether that key can see `gpt-5.6-luna`. Both failures are worth
catching before the day. A key that works but landed in the wrong file fails a
session exactly like a bad key, and only a check against the written
configuration tells them apart.

### It does not disturb their own Claude Code

Tested on a machine signed in to a Claude subscription, with a real study key.

- Inside the study workspace, Claude Code answered on the study's key.
- With that key deliberately broken, the same folder failed with a 401 while a
  plain folder beside it still answered on the machine's own login. So the key is
  genuinely what the workspace uses, and genuinely only that workspace.
- `~/.claude/settings.json` and `~/.claude/.credentials.json` came back
  byte-identical. No key appeared in any file under the home directory, and no
  approval was recorded. The subscription login stayed exactly as it was.

The only global file that changed was `~/.claude.json`, which records which
directories have been opened. It held no key.

Nothing here is written to a shell profile, and that is the point rather than
tidiness. A key exported in a profile would follow the participant into their own
projects for as long as the line stayed there.

Codoc would otherwise pick its provider from the environment, so a key in a
participant's own shell profile could move it onto their account. The two codoc
workspaces name the provider outright so nothing is inferred.

### Before you hand keys out

Use keys made for this study, not your own working ones. Put a spend limit on
both. Turn them off when the study ends, and sooner if a participant tells you a
key went somewhere it should not have.

Once a key is on somebody else's machine you cannot get it back, so the limit and
the expiry are the whole of your protection. Neither is set by anything here.

You can read the keys down the call, which is what the prompts are written for,
or put a `keys.env` next to `setup.sh` holding `STUDY_ANTHROPIC_KEY=…` and
`STUDY_OPENAI_KEY=…` and send that separately. Do not put it inside the bundle
zip: the zip is built once and goes to everybody.

## Part 1. Set up your own machine, once

You need node, npm, uv and zip. Then, from the repo root:

```
./docs/study-materials/scripts/build-participant-bundle.sh
```

It builds the VS Code extension, takes the matching codoc wheel out of it, and
writes `dist/codoc-study-bundle.zip`. Build it again whenever codoc changes, so
the extension and the wheel stay the same version.

Try the bundle yourself on a spare machine or a fresh account first. The setup
script inside it is the one participants run, so running it is the only way to
know what their setup will feel like.

## Part 2. Before the session

At least three days ahead, open the dashboard and press New. You get a code and
an order. Then send the participant two things, both from the card at the top of
that participant's page in the dashboard.

1. Their link, which looks like
   `https://codoc-11b10.web.app/participant/?code=p-abcdefghjkmn&order=codoc-first`.
   Ask them to open it and work through it until it tells them to stop. That
   covers consent and the background questions.
2. `dist/codoc-study-bundle.zip`. Their page tells them to unzip it and run the
   setup command, which is the second thing on that card. It has their code and
   order already in it, so it can be pasted as it stands.

You do not need to decide the order yourself. The dashboard picks whichever of
the two is behind, so the combinations fill evenly without you keeping a tally.

Then watch the same card. It has two marks on it. The first turns green when they
open their link. The second turns green when their editor first reports, which
only happens once the setup script has run with their code. Both green means the
handoff worked. If the second one is still not green, ask them to run
`./setup.sh --check`, which says in plain words whether the code is set.

Ask them to send back the last few lines the setup script printed. If it does not
say "Everything is ready", sort it out now rather than during the session. The
common problems and their fixes are at the end of the file inside the zip.

Do not run anyone who says they never read a diff before accepting it. That is
one of the background questions, and you can see their answers in the dashboard.

## Part 3. On the day

Ten minutes before the call, ask them to open a terminal in the folder they
unzipped, run this, and read you the result:

```
./setup.sh --check
```

The line to listen for is the one naming their code. Everything else it checks
can be fixed afterwards; a session that ran without a code recorded nothing.

Then have them share their whole screen and start recording. Open their page in
the dashboard and keep it beside the call for the rest of the session.

### Starting a codoc condition

They open `~/codoc-study/scribe` in VS Code, then open a terminal inside VS Code
and run:

```
~/codoc-study/codoc watch
```

Leave it running for the whole condition. Someone has to start it by hand: the
extension only starts it for you after it has installed codoc itself, and it skips
installing when the project already has a codoc setup, which these do. Everything
else in the extension works off files, so it does not care how the daemon was
started.

Use that full path rather than plain `codoc`. Installing codoc does add it to the
PATH, but only for terminals opened afterwards, and the session runs in the
terminal they already have. The setup script makes `~/codoc-study/codoc` for
exactly this reason.

They then open the description with Cmd+Shift+P and the command "codoc: Open".
They will want a second terminal for Claude Code and a third for running builds.

Check three things before going on. The status bar says codoc is in sync, the
description lists 25 features, and `codoc watch` has not printed an error.

Tally works the same way. Open `~/codoc-study/tally` instead.

### Starting a condition without codoc

They open `~/codoc-study/scribe-baseline`, or `~/codoc-study/tally-baseline`, and
start Claude Code in a terminal. Nothing else runs. `CLAUDE.md` sits in the project
root and Claude Code picks it up on its own.

### Recording the session

The recorder runs on their machine, because that is where the files are. Ask them
to run this at the start of each condition, from the folder they unzipped, using
the code you gave them:

```
./session-log.sh ~/codoc-study/scribe p04-codoc
```

It saves a copy of the whole project every 20 seconds, so the session can be
replayed afterwards. It prints the line that stops it, so have them keep that
terminal open and stop it at the end of the condition. It does not record the
screen or the voice. You record those from the call.

Alongside it, the study logger extension records which files are on screen, for
how long, and how much text changed. It starts on its own when VS Code opens and
needs nothing from you. It runs in both conditions, which is the only reason
navigation can be compared between them at all.

Check it is alive before the task starts. Ask them to run
"Study logger: show what is being recorded" from Cmd+Shift+P. It prints the file
it is writing to. If that file does not exist or is empty after they have clicked
around for a few seconds, stop and fix it, because five of the measures come from
nowhere else. `analysis-plan.md` says which five.

## Part 4. The shape of the session

About 105 minutes. The middle block runs twice, once for each condition.

| Part | Minutes | What happens |
| --- | --- | --- |
| Introduction | 5 | The words are below |
| Walkthrough and warm-up | 6 | The same in both conditions |
| Getting oriented | 6 | They explore, thinking out loud |
| First round of questions | 6 | From the question sheet |
| The task | 17 | Thinking out loud, stop at 20 |
| Sign-off and second round of questions | 6 | The sign-off first |
| Questionnaires | 4 | |
| Break | 3 | |
| Which would you pick, and the interview | 14 | At the end, with both conditions done |

### What to say at the start

Read this out, and use the same words with everyone.

> Today you will try two ways of working with a coding agent. Both are set up
> already. Each time you will get oriented in a small project, answer a few
> questions about it, make a change to it, and answer a few more questions.
>
> Both times there is a written description of the project. It is yours to keep
> current, and the questions afterwards are based on it.
>
> You are responsible for the result being correct, not only for the tests
> passing. I will ask you to explain the code afterwards.
>
> Please think out loud. If you go quiet I will ask what you are thinking, and
> that is the only thing I will interrupt for.

Never call either way of working "our tool", and never say which one we built.

### The warm-up

Six minutes, worded the same in both conditions.

> Find where the template engine is described. Then ask the agent to rename the
> template cache so its name says it holds parsed templates, and deal with
> whatever the written description does afterwards.

That rename is deliberately nowhere near the code the task touches, so it can
neither help nor hinder them later. Leave it in place afterwards.

The warm-up is there so that nobody meets an unfamiliar screen for the first time
while the clock is running. With codoc it makes them touch the description, the
agent, and the accept button. Without codoc it makes them touch the description
and the agent.

## Part 5. The task

Each project has one task card, in `projects/<name>/STUDY.md`. It is on their own
page as a picture, so there is no text to paste at the agent.

- **scribe:** support block quotes.
- **tally:** support split transactions.

The agent writes either one in about a minute. That is deliberate. The task is
easy to implement and hard to decide, and what the participant has to supply is
judgement rather than code.

### What the card deliberately leaves out

Four things per task, and they are the measurement. They are listed with their
rating guide in the project's `STUDY.md`, which is the answer key — do not open
it in front of anybody.

For scribe: what marks a quote, whether de-hyphenation applies inside one,
whether a quote ends the paragraph before it, and what happens to a quote running
across a page break.

For tally: how a split is written in the CSV, whether it counts as one
transaction or two, whether the duplicate rule sees the halves as duplicates, and
what happens when one half matches no category rule.

**The last one in each list is the coupled one.** It is where two rules meet, and
it is reached by deciding rather than by tripping over it. In scribe, page
furniture is stripped before quotes could be found, so the running header sits
between the two halves of a quote that crosses a page. In tally, a split of forty
pounds into two twenties is exactly the shape the duplicate rule matches.

Do not hint at any of this. If they ask whether something matters, say:

> Work from what the card says and what you find in the project.

### What is scored

**The gate.** The change runs and the existing tests pass. Not reported as a
result — a session that fails the gate has no decisions worth rating.

**The primary outcome.** Each of the four decisions rated **0 to 2 for
consistency with what the codebase already believes**. Consistency, not
correctness: there is no single right answer to any of them, only answers that
fit this codebase and answers that contradict it. The rating guide for each is in
`STUDY.md`.

A participant can produce working code that contradicts the codebase. That is the
finding, and it is the thing a description is supposed to prevent.

Rate it during the session, in the dashboard, while you can still remember what
they said. Alongside it, record **who settled each decision**: they decided, the
agent proposed and they accepted, or the agent did it and they never noticed.

### Timing

Thirty-five minutes per task. Say so at the start. If they finish early, that is
a result; if they are still going at forty, ask them to stop where they are.

### The sign-off

When they say they are done, ask, and write the answer down word for word:

> Is this change correct and complete? How confident are you, 1 to 5? And what is
> that resting on?

The number matters less than the last part. "I ran the tests" and "the agent said
so" are different answers.

## Part 6. The questions

Twelve multiple-choice questions per project, four options each, one right. The
participant answers them on their own page — **you do not read them out and you do
not mark them.** They are asked twice, once before the task and once after, and
the change between the two is the measure.

The bands are the four parts of RQ1: what the program is for, why it is the way
it is, why a particular change was made, and what a further change would need
decided first.

Both sittings appear in the dashboard as they answer, with the score and which
option they picked when they were wrong. The wrong option is usually more
informative than the fact that they were wrong, so it is shown rather than a
tick.

There is no feedback either time, on purpose. Telling somebody they were wrong
before the task would teach them the answer, and the second sitting would measure
the telling rather than the session.

## Part 7. Collecting the data

Most of it is on their machine, so ask them to run this before they leave the call,
using the code you gave them:

```
./collect.sh p04
```

It packs the projects, the recordings of the session state, the interaction logs
and the Claude Code transcripts into one zip on their Desktop and prints where it
is. Have them send it while you are still on the call.

Then, before they leave, unpack it, pull down the live copy, and check the two
against each other:

```
node study-app/scripts/export-session.mjs <their code> --out <the unpacked folder>
python3 docs/study-materials/scoring/check-session-complete.py <the unpacked folder>
```

It goes through every measure in `analysis-plan.md` and says whether the data to
compute it arrived. Anything it prints as MISSING is recoverable in the next
thirty seconds and gone forever once the call ends. It cannot see your notes or
the questionnaires and says so.

Keep these yourself:

- The screen and audio recording.
- Your notes: who settled each open decision, the sign-off answer, and the answers
  and scores from both rounds of questions.
- The questionnaires.

Name every folder with the participant code and which condition it was, e.g.,
`p04-codoc`. Never put their name in a filename. Once the zip has arrived, ask them
to delete `~/codoc-study`, so nothing is left behind and they cannot end up in the
study twice.

## Part 8. Scoring the code

Two things, and only the second is reported.

**The gate**, which is mechanical. From inside their finished project:

```
./.venv/bin/python -m pytest tests/ -q
./.venv/bin/scribe check fixtures/          # or: ./.venv/bin/tally check fixtures/
```

Every existing test must still pass and the project must still run over all three
sample inputs. A change that breaks either is a session with nothing to rate.

**The four decisions**, rated 0 to 2 against the guide in the project's
`STUDY.md`. Do this blind: have somebody who does not know which condition a
folder came from read the diff and rate it. The ratings you typed during the
session are your own record of what was said, not the rated outcome, and the two
are compared rather than merged.

The diff is the whole of the evidence for this. Read it against what the codebase
already did, not against what you would have written.

## Part 9. When something goes wrong

**Nothing is updating.** The status bar is stuck and no proposals appear. Check the
`codoc watch` terminal is still open. It is easy to close along with a finished
task.

**The description looks slightly different from what you expected.** On its first
start codoc tidies up the project and reports something like "1 amend, 1 attach".
That is normal and does not change the text. Check it still lists 25 features
before you begin.

**Claude Code is not signed in.** codoc uses the participant's own Claude Code
login, so if they are not signed in, codoc has no model to call. Have them run
`claude` in a terminal, sign in, then start `codoc watch` again.

**They pasted the task card into the agent.** This cannot happen if you show the
card as an image. If it happens anyway, note it, because the instructions they
write are one of the measures and those are now ours.

**A project looks wrong before they start.** A fresh scribe prints
`12 pages, 12 rebuilt, aggregates rebuilt` and passes 233 tests. A fresh tally
reads 36 items and passes 171 tests. Anything else means the copy has been used.
Delete it and run `./setup.sh` again.

## Part 10. Before you run anyone

The materials are finished. Both projects are built and their descriptions
written, the scoring is checked against right and wrong versions of both tasks,
and everything the analysis needs is recorded in both conditions.

What is left is not work on the materials.

- Pre-register the design and the scoring. The two question sheets, their scoring
  tables, and the measure list in `analysis-plan.md` are fixed from that point on.
- Run the three pilot sessions with the full protocol, and run
  `check-session-complete.py` on each one. The pilots are what tell you whether
  105 minutes is enough and whether the questions land, and they are also the only
  way to find out that a log is empty before it matters.
- Decide who does the blind rating, and make sure they never see which condition
  a project came from.

Read `analysis-plan.md` once before the first session. It lists every measure
against the data that produces it, including the three things we decided not to
claim because nothing measures them.

One thing to expect, because the calibration runs already showed it. The agent
solves both tasks correctly whatever it is asked, so almost everyone will finish
with working code either way. The difference the study is looking for is in the
people, not the code: what they can explain afterwards, which decisions they made
themselves, how much of the change they actually looked at, and what their
confidence rested on. Say that in the pre-registration rather than discovering it
in the results.
