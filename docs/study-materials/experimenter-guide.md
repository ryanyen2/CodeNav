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

- With codoc, they use VS Code with the codoc extension. The description is
  connected to the code, and when codoc proposes a change they accept or reject it.
  The codoc arm also has two reading aids the baseline does not: `Cmd+F` search and
  replace across the tree, and `/codoc:ask`, which answers a question about the
  codebase by drawing a numbered path through the description. Both are part of the
  manipulation — codoc is meant to make a codebase easier to understand, and these
  are how — so they are on by default and are not a defect to hide.
- Without codoc, they use a `CLAUDE.md` file holding exactly the same text. The
  agent is told to update it after every change it makes.

> **Cut-over.** `/codoc:ask` and tree search were added on 2026-08-17. They change
> what the codoc arm can do during the understanding quiz, so a session run before
> that date is not comparable to a later one on the quiz's *time*, and the codoc arm
> must be described as it now stands. Re-run any pilot that predates it before
> pooling. `/codoc:ask` is subject to the same rule as any other way of asking the
> agent: the participant may ask it about the codebase, but pasting a quiz question
> or its options into it is barred exactly as pasting one into the terminal is —
> that would measure the agent, not the pair. Watch for it the same way.

There are two projects, scribe and tally, with matched tasks. Each participant
does one project each way, so nobody solves the same problem twice.

The task in both is to review a change an agent has already made and to decide
what to keep. The change is recorded once, in advance, and replayed to every
participant, so everybody reviews the same thing and nobody spends forty minutes
watching code get written. What is being compared is what a person can see and
change about a decision an agent made for them.

Within a project, the two copies hold identical source and tests and the same 12
commits, so reading the history tells you the same things either way. The only
differences are the ones above.

## The two pages, and how a session runs

There are two web pages. You use one, the participant uses the other, and the
bundle they install is downloaded from theirs.

- Your dashboard is <https://codoc-11b10.web.app/experimenter/>. You sign in with
  your MIT address. You create participants here, you get the link to send them
  here, and you type the sign-off, the who-settled-what record and the question
  scores here during the session.
- Their page is <https://codoc-11b10.web.app/participant/>, reached only through
  the link you send. It walks them through consent, the questionnaires, the task
  cards and the break, one step at a time, and saves as they go.
- The bundle is at `/bundles/codoc-study-bundle.zip` on the same site, with a
  Download button on their setup step. It holds the extension, the four projects
  and a setup script. You never send it: publishing the site publishes it.

A whole session, in order:

1. You press **+ Participant** in the dashboard, or **+ Pilot** for a practice
   run. The kind is chosen here because it cannot be recovered later: it is
   baked into the code, and a pilot created as a participant would quietly end
   up in the analysis. It gives you a code, e.g. `p-abcdefghjkmn`,
   and picks the order for you so the four combinations fill evenly.
2. You send them the link. The download button is on it, so the link is the only
   thing you need to send.
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

You do, and the participant never sees a key.

Put them in once, in the dashboard under **Session keys**. Every participant
created afterwards gets their own copy, and their setup script fetches it using
the code that is already on their study page. The participant never pastes a key. Pasting keys by hand during a call is the
step most likely to go wrong, and the key can end up in the wrong window.

Setup builds an assistant profile inside the study folder. The participant's own
`~/.claude` is not read or written, so the study cannot change their personal
setup and their personal configuration cannot leak into a session. Authentication
goes through a key helper in the study folder rather than `ANTHROPIC_API_KEY`,
because setting that variable makes Claude Code ask whether to trust the key,
which is a pointless prompt in the middle of a session. The launcher
`./claude-study` also clears any key the participant already has from the
environment, so their own key is never used or billed.

Two other things are pinned in the profile. The model is set in two places so it
cannot change between sessions. The auto-updater is also off, because the
assistant's version is part of the condition, and an upgrade between participant
three and participant four would be a confound you could not reconstruct
afterwards.

### Before you hand keys out

Use keys made for this study, not your own working ones. Put a spend limit on
both. Turn them off when the study ends, and sooner if a participant tells you a
key went somewhere it should not have.

Once a key is on someone else's machine you cannot get it back. The spend limit
and the expiry date are your only protection, and nothing in this setup sets
either one for you.

Anyone who has a participant's link can read that participant's copy of the keys.
Use study-specific keys with a hard spending cap for exactly this reason. Each
participant's page has a **Revoke** button in case a key leaks. Press it, and
then also revoke the key at the provider, because the button only stops the key
from being handed out again.

## Part 1. Set up your own machine, once

You need node, npm, uv and zip. Then, from the repo root:

```
./docs/study-materials/scripts/build-participant-bundle.sh
```

It builds the VS Code extension, takes the matching codoc wheel out of it, and
writes `dist/codoc-study-bundle.zip` and a copy into `study-app/bundles/`,
which is what the site serves. Build it again whenever codoc changes, so the
extension and the wheel stay the same version, and deploy the site afterwards or
participants keep downloading the old one.

Try the bundle yourself on a spare machine or a fresh account first. The setup
script inside it is the one participants run, so running it is the only way to
know what their setup will feel like.

### Piloting a change to the pages

A pilot code is left out of the figures, and the pilot's page also carries a bar
along the bottom with **Fill and skip** and a menu of every step. You can jump to
the step you want to test in a couple of clicks instead of answering twenty-five
scales to reach it. Everything the bar fills in is marked `autofilled` in the
same document as the real answers, so the marker survives an export.

Use pilot codes whenever you change the participant page. Before this existed,
the last steps were the ones tested least, even though they are the ones nobody
has ever walked through.

## Part 2. Before the session

At least three days ahead, open the dashboard and press New. You get a code and
an order. Then send the participant **one thing**: their link, which looks like

```
https://codoc-11b10.web.app/participant/?code=p-abcdefghjkmn&order=codoc-first
```

Ask them to open it and work through it until it tells them to stop. That covers
consent, the questions about them, and setting their machine up. The download is
on that page, and so is the setup command with their own code and order already
in it.

The bundle used to be emailed separately from the link. When the bundle was
rebuilt, the new version did not reach anyone who had already received the old
one, and nothing on either side warned about the mismatch. The bundle is now
served from the site, so publishing the site publishes the bundle.

To rebuild and publish it:

```
./docs/study-materials/scripts/build-participant-bundle.sh
cd study-app && npm run build && npx firebase deploy --only hosting
```

You do not need to decide the order yourself. The dashboard picks whichever of
the two is behind, so the combinations fill evenly without you keeping a tally.

Then watch the same card. It has two marks. The first turns green when they open
their link. The second turns green when their editor first reports, which only
happens once the setup script has run with their code. Both green means the
handoff worked. If the second one stays grey, ask them to run
`./setup.sh --check`, which prints whether the code is set.

Ask them to send back the last few lines the setup script printed. If it does not
say "Everything is ready", sort it out now rather than during the session. The
common problems and their fixes are at the end of the README in the bundle.

Do not run anyone who says they never read a diff before accepting it. The
question is on their page, and their answer is visible in the dashboard. The page
does not say which answer excludes, because knowing that would change the answer.

Their name and email go on their page in the dashboard, under "Who this is".
The name and email are stored separately from the session data and are never
exported with it, so whoever analyses the results cannot see who a session
belonged to.

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

### Which folder is which

There are two folders, `~/codoc-study/scribe` and `~/codoc-study/tally`, and they
are named for the project alone. Which one carries codoc depends on the
participant's order:

| Their order | `scribe` is | `tally` is |
| --- | --- | --- |
| `codoc-first` | codoc | without codoc |
| `baseline-first` | without codoc | codoc |

The folders used to be called `scribe-baseline` and `tally-baseline`, which meant
half of every session was spent typing "baseline" into a terminal before
answering a questionnaire comparing the two. The word ranks the two ways of
working. Setup writes the condition into each folder's `.vscode/settings.json`,
and the end of a setup run prints which folder is the codoc one.

### Starting a codoc condition

They open the codoc folder from the table above in VS Code, then open a terminal
inside VS Code and run:

```
~/codoc-study/codoc watch
```

Leave it running for the whole condition. Someone has to start it by hand. The
extension normally starts it automatically after installing codoc, but it skips
installing when the project already has a codoc setup, and these projects do.
Everything else in the extension reads from files, so it does not matter how the
daemon was started.

Use the full path `~/codoc-study/codoc` rather than plain `codoc`. Installing
codoc adds it to the PATH, but only for terminals opened after the install. The
session runs in the terminal they already have open, which does not have the
updated PATH. The setup script creates `~/codoc-study/codoc` for this reason.

They then open the description with Cmd+Shift+P and the command "codoc: Open".
They will want a second terminal for Claude Code and a third for running builds.

Check three things before going on. The status bar says codoc is in sync, the
description lists 15 features for scribe or 23 for tally, and `codoc watch` has
not printed an error.

Nothing should ask them to approve anything. Setup pre-approves codoc's MCP
server in the assistant's profile, and the daemon installs any `/codoc:*` command
the archive is too old to carry. If a trust prompt does appear, answer it and tell
me — it means the profile did not land, and `./setup.sh --check` will say so.

### Starting a condition without codoc

They open the other folder and start Claude Code in a terminal. Nothing else runs.
`CLAUDE.md` sits in the project root and Claude Code picks it up on its own.

### Recording the session

Nothing to start. The study logger extension records which files are on screen,
for how long, and how much text changed, and every 20 seconds it records the
whole project so the session can be replayed afterwards. It starts on its own
when VS Code opens and runs in both conditions, which is the only reason
navigation can be compared between them at all. It does not record the screen or
the voice. You record those from the call.

This used to be a script somebody had to start by hand, in its own terminal, at
the start of each condition. It was the only step that leaves no mark on the
screen when it was skipped: the session runs normally and looks fine, and the gap
turns up at collection, hours after the one moment it could have been fixed. On
the first pilot nobody started it in either condition, so there is no replay of
it. `scripts/session-log.sh` still exists as a fallback and is not part of the
normal run.

**One check, once per condition, before the task starts.** Ask them to run
"Study logger: show what is being recorded" from Cmd+Shift+P. It prints the log
file and how many snapshots it has taken. You want a log file that exists and a
snapshot count above zero. If it says snapshots are off or failing, or the log is
empty after they have clicked around for a few seconds, stop and fix it — five of
the measures come from nowhere else, and `analysis-plan.md` says which five.

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
| Questionnaires | 5 | The workload block has a definition under each item; let them read it |
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

The rename is deliberately nowhere near the code the task touches, so it can
neither help nor hinder them later. Leave it in place afterwards.

The warm-up exists so that nobody encounters an unfamiliar screen for the first
time while the clock is running. In the codoc condition it makes them use the
description, the agent, and the accept button. In the baseline condition it makes
them use the description and the agent.

In the codoc condition, also show them the two reading aids once, here, so they are
not discovering them on the clock: `Cmd+F` to search the description, and one
`/codoc:ask` about the project (its own question, not a quiz question) so they have
seen a walkthrough appear and stepped through it. Point out the `‹ ›` stepper and
`✕`. Do not demonstrate these in the baseline condition — it has neither, and a
demonstration of something the participant will not have is its own confound.

## Part 5. The task

Each project has one task card, in `projects/<name>/STUDY.md`. It is on their own
page as a picture, so there is no text to paste at the agent. Since 2026-08-19 the
task is to review a change an agent already made, rather than to make one.

- **scribe:** review the config file, the report and the settings tidy-up.
- **tally:** review the rules file, the weekly view and the settings tidy-up.

The change is recorded in advance and replayed, so every participant reviews the
same change and nobody waits forty minutes for code that is not what we are
measuring.

### Playing the recorded session

Do this after they have read the card and said what they expect, and before they
touch anything. Stop the codoc daemon first, because the player writes the files
the daemon owns and it refuses to start while the daemon is running.

```
~/codoc-study/replay/play.py ~/codoc-study/scribe \
    ~/codoc-study/replay/frames/scribe/codoc
```

It takes about three minutes. The terminal prints what the agent printed, the
files change under it, and in the codoc condition the tree fills in as the daemon
did during the recording. Let them watch. Start the daemon again when it finishes.

Everything after their first prompt is live. The recorded session is installed
where `claude --resume` finds it, so their first prompt continues the session that
made the change, with the agent's own context.

If the player refuses or stalls, the fallback is in Part 9.

### What is planted in the change

Four problems and one decoy, listed with their rating guides in the project's
`STUDY.md`, which is the answer key. Do not open it in front of a participant.
None of them breaks a test, so the suite passing tells the participant nothing
about whether the change is right.

**Do not hint at any of it.** If they ask whether something is deliberate, say:

> Work from what the card says and what you find in the project.

The card says the agent finished and the tests pass. It does not say anything is
wrong and it does not say everything is fine.

### What is scored

**The gate.** The change runs and the existing tests pass at the end. It is not
reported as a result.

**The primary outcome.** Each of the four planted problems is rated **0 to 2**. 0
is not found, 1 is found, and 2 is found and correctly attributed to the
commitment it contradicts. The rating guide for each is in `STUDY.md`.

**The false alarms.** Record every correct part of the change the participant
called wrong, including the decoy. A surface that makes everything look suspicious
is not an improvement, and nothing else in the analysis would catch that.

Rate it during the session, in the dashboard, while you can still remember what
they said. Alongside it, record **who settled each problem**: they directed it,
they accepted a proposal deliberately, or it stands and they never noticed.

### The follow-up request

After the review, read the follow-up out loud. It is in the project's `STUDY.md`
and it is not on the card. Its obvious implementation runs into a commitment the
description already holds, and what is recorded is whether they noticed the
conflict and which way they settled it.

### Timing

Thirty minutes per task, on top of the three minutes the replay takes. About
fifteen for the review and about twelve for the follow-up, and say so at the
start. If they finish early, the time is recorded as part of the result. If they
are still going at thirty-five minutes, ask them to stop where they are.

The time to their first correct detection is a measure, so note the clock when
they first name something as wrong, and note whether they were right.

### The sign-off

When they say they are done, ask, and write the answer down word for word:

> Is this change correct and complete? How confident are you, 1 to 5? And what is
> that resting on?

The confidence number matters less than what the confidence rests on. "I ran the
tests" and "the agent said so" are very different answers.

## Part 6. The questions

Two sets, one before the task and a different one after. The participant answers
both on their own page. **You do not read either out and you do not score them
during the session.**

### Before the task: five questions, open book, six minutes

Five multiple-choice questions about the project, four options each, one
correct. They may read the description, read the code, run the project and ask
the agent. The one thing barred is pasting a question or its options at the
agent, which would measure the agent rather than the pair. Nothing enforces it,
so watch the screen, and the transcript shows it afterwards.

The clock is on their page and **ends the sitting**: at zero it stamps whatever is
answered and moves to the next step by itself, with a thirty-second warning first.
You do not have to call time, and should not extend it — how long five answers
took is half the measure, so a sitting that ran long is not the same result as one
that did not. An unanswered question is recorded as `timedOut` rather than as a
blank, so the analysis can tell "ran out" from "left it".

**Both the score and the time are results.** Either way of working can reach
every answer eventually, so what separates them is what it costs to get there.
Both appear in the dashboard as they answer, along with which option they picked
when they were wrong, which is usually more informative than the fact that they
were wrong.

The bands are the four parts of RQ1: what the program is for, why it is the way
it is, why a particular change was made, and what a further change would need
decided first. With five questions one band carries two; scribe and tally match
band for band and level for level, so neither project is the harder draw.

### After the task: five questions, closed book

Multiple choice, about the change they just made. **Ask them to close the code,
the description and the agent first**, and say why: what is being looked at is
what they carried out of the task, so an answer they went and looked up says
nothing. Their page says the same thing, but it lands better from you.

Each one turns on a consequence of their change meeting a rule that was already
there, so it can be answered by somebody who understood the codebase or who made
the decision themselves and watched what it did, and not by somebody who let the
agent write it and did not look. They have right answers, and the dashboard scores
them as they answer. Nothing here needs marking by hand.

Alongside the five is one scale: how much of it they were sure of rather than
working out on the spot. A fluent reconstruction and a real memory look the same
in a set of answers, and only they can say which it was.

There is no feedback on anything. Telling somebody they were wrong before the
task would teach them the answer.

## Part 7. Collecting the data

Most of it is on their machine, so ask them to run this before they leave the call,
using the code you gave them:

```
./collect.sh p04
```

It packs the projects, the recordings of the session state, the interaction logs
and the Claude Code transcripts into one zip on their Desktop and prints where it
is. Have them send it while you are still on the call.

Then, before they leave the call, unpack it, pull down the live copy, and check
the two against each other:

```
node study-app/scripts/export-session.mjs <their code> --out <the unpacked folder>
python3 docs/study-materials/scoring/check-session-complete.py <the unpacked folder>
```

The checker goes through every measure in `analysis-plan.md` and says whether the
data needed to compute it arrived. Anything it prints as MISSING can be recovered
in the next thirty seconds. Once the call ends, it is gone. Both halves are
visible to the checker: the export carries the questionnaires from the
participant's page and your sign-off, settlement record, and question scores from
the dashboard. The checker compares them against the collected folder.

Keep the screen and audio recording yourself. No export can recover it.

Name every folder with the participant code and which condition it was, for
example `p04-codoc`. Never put the participant's name in a filename. Once the zip
has arrived, ask them to delete `~/codoc-study`, so nothing is left on their
machine and they cannot accidentally end up in the study twice.

## Part 8. Scoring the code

The gate is mechanical. The four decision ratings are the reported outcome.

**The gate.** From inside their finished project:

```
./.venv/bin/python -m pytest tests/ -q
./.venv/bin/scribe check fixtures/          # or: ./.venv/bin/tally check fixtures/
```

Every existing test must still pass and the project must still run over all three
sample inputs. A change that breaks either is a session with nothing to rate.

**The four planted problems**, rated 0 to 2 against the guide in the project's
`STUDY.md`. Have someone who does not know which condition a folder came from read
the diff and rate it. The ratings you typed during the session are your own record
of what was said and are separate from the rated outcome. The two are compared,
not merged.

**Whether the description is still true**, which is the headline outcome:

```
python3 scoring/score-record-truth.py <their finished project>
```

It runs their finished code on a sample to find out what the code actually does,
and pulls the sentences from their finished description that talk about the same
policy. Mark each claim true, contradicted, or missing. The sheet does not say
which condition it came from, and the codoc description is exported to Markdown
first, so the two read alike.

**Whether the description still works as the agent's memory**, after every
session has been run:

```
python3 scoring/transfer-probe.py prepare <their finished project> <a probe folder>
python3 scoring/transfer-probe.py run   <a probe folder>
python3 scoring/transfer-probe.py score <a probe folder>
```

It gives their description to a fresh agent with a further task in a clean copy of
the project, and counts how many commitments the agent's change kept.

## Part 9. When something goes wrong

**VS Code is in Restricted Mode.** The first time a folder is opened, VS Code
asks whether you trust its authors. Until that is answered, it runs with every
extension disabled. Nothing looks wrong: the files open, the terminal works, and
the study logger and codoc simply never start, so the session records nothing.
Watch for the banner across the top of the window when they open each folder, and
have them click "Yes, I trust the authors" before anything else. Their page tells
them this too. To confirm afterwards, `./setup.sh --check` reports whether the
logger has ever run in each workspace.

**The replay refuses to start.** It says the daemon is running. Stop the
`codoc watch` terminal and run it again. The player will not write into a
workspace a live daemon owns, because the two would race for the same files.

**The replay stopped partway.** Run it again. It restores the starting state
first, so a half-finished replay is safe to repeat, and the participant has seen
nothing that a second run does not show them again. If it fails twice, put the
recorded end state in place by hand with `--speed 1000`, tell the participant the
agent finished while they were away, and note it on the session sheet, because
that participant did not see the change arrive.

**Nothing is updating.** The status bar is stuck and no proposals appear. Check
that the `codoc watch` terminal is still open. It is easy to close accidentally
when closing a finished task's terminal.

**The description looks slightly different from what you expected.** On first
start, codoc tidies up the project and reports something like "1 amend, 1
attach". This is normal and does not change the text. Check that it still lists
15 features (scribe) or 23 (tally) before you begin.

**Claude Code is not signed in.** codoc uses the participant's Claude Code login.
If they are not signed in, codoc has no model to call. Have them run `claude` in
a terminal, sign in, and then start `codoc watch` again.

**They pasted the task card into the agent.** Showing the card as an image
prevents this. If it happens anyway, note it, because the instructions they write
to the agent are one of the measures, and pasted text would be yours rather than
theirs.

**A project looks wrong before they start.** A fresh scribe passes 54 tests, and
`scribe check fixtures/` reports three documents (`report.txt: 3 pages, 8 headings,
12 paragraphs, 6 bullets, 2 notes, 6 lines of furniture`). A fresh tally passes 43
tests. Anything else means the copy has been used. Delete it and run `./setup.sh`
again.

These numbers were hearth's until 2026-08-18 — the codebase before this pair — so
a correct workspace looked broken and a used one looked fine.

## Part 10. Before you run anyone

The materials are finished. Both projects are built, their descriptions are
written, the scoring is checked against correct and incorrect versions of both
tasks, and everything the analysis needs is recorded in both conditions.

The remaining work is about the process, not the materials.

- Pre-register the design and the scoring. The two question sheets, their scoring
  tables, and the measure list in `analysis-plan.md` are fixed from that point on.
- Run the three pilot sessions with the full protocol, and run
  `check-session-complete.py` on each one. The pilots tell you whether 105
  minutes is enough, whether the questions make sense, and whether any log is
  empty.
- Decide who does the blind rating, and make sure that person never sees which
  condition a project came from.

Read `analysis-plan.md` once before the first session. It lists every measure
against the data that produces it, including the three things we cannot claim
because nothing measures them.

One thing to expect, because the calibration runs already showed it: the agent
solves both tasks correctly no matter what it is asked, so almost everyone will
finish with working code in both conditions. The difference the study is looking
for is in the people, not in the code. Specifically: what they can explain
afterwards, which decisions they made themselves, how much of the change they
actually looked at, and what their confidence rested on. State this in the
pre-registration rather than discovering it in the results.
