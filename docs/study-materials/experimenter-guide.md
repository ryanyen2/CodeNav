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

> **Cut-over.** The task was rewritten on 2026-08-19. The participant now asks for
> the change themselves and then reviews what came back, the open-book question
> round that used to run before the task is gone, and the task runs twenty minutes.
> A session run before that date is a different session and cannot be pooled with a
> later one, so re-run any pilot that predates it.
>
> `/codoc:ask` and tree search were added on 2026-08-17 and are part of the codoc
> arm. The participant may ask either of them about the codebase while the task is
> running, the same as they may ask the agent anything else. The questions
> afterwards are closed book, so by then nothing is pasted into either one.

There are two projects, scribe and tally, with matched tasks. Each participant
does one project each way, so nobody solves the same problem twice.

The task in both is to ask a coding agent for a change, and then to review what it
did and decide what to keep. The change is recorded once, in advance, and replayed
to every participant, so everybody reviews the same thing and nobody spends forty
minutes watching code get written. The participant types the request themselves
and is never told a recording exists. What is being compared is what a person can
see and change about a decision an agent made for them.

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
  pages and the break, one step at a time, and saves as they go.
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

Two lines matter here. The first is the one naming their code, because everything
else it checks can be fixed afterwards and a session that ran without a code
recorded nothing. The second is the pair of lines about the change: it says, per
folder, that the session it reviews is there, and it fails a folder that has
already been played into. A folder that has been played into holds the change
before the participant has asked for anything, and it cannot be used.

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

The order to do things in is the checklist in Part 5, and the dashboard shows the
same steps with this participant's folder already in them. What follows is the
reasoning behind the parts that surprise people.

They open the codoc folder from the table above in VS Code, answer the trust
prompt, open the description with Cmd+Shift+P and the command "codoc: Open", and
open one terminal inside VS Code. That is the whole setting up, and the other
condition is the same shape. A condition that costs more setting up than the other
one differs from it in something the study is not comparing.

Nobody starts the daemon by hand any more. The launcher `./claude-study` stops it
before the recorded session plays, because the player will not write into a
workspace a live daemon owns. It starts the daemon again behind the session
afterwards, with its output going to `.codoc/watch.log` rather than to a terminal
somebody is watching. Until the first turn has run there is no daemon at all, and
that is fine for the reading part. Everything the extension shows comes from
files, so the description opens and reads normally without one.

Check two things before going on. The description opens, and it lists 15 features
for scribe or 23 for tally.

Nothing should ask them to approve anything. Setup pre-approves codoc's MCP
server in the assistant's profile and runs `codoc install-hooks` in each codoc
workspace, so the `/codoc:*` commands are already there. If a trust prompt does
appear, answer it and tell me. It means the profile did not land, and
`./setup.sh --check` will say so.

### Starting a condition without codoc

They open the other folder, open `CLAUDE.md` from the file tree, and open one
terminal inside VS Code. Nothing else runs. `CLAUDE.md` sits in the project root
and Claude Code picks it up on its own.

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

About 105 minutes. Consent and the questions about them are done on their own
page days ahead, so the session starts at the introduction. The block in the
middle runs twice, once for each condition, and takes forty minutes each time.

| Part | Minutes | What happens |
| --- | --- | --- |
| Introduction | 5 | The words are below |
| **One condition, run twice** | **40 each** | The five rows below |
| Opening the project and reading about it | 5 | On their own page |
| The way of working | 5 | On their own page, one page per condition |
| The task | 20 | They send the request, then review what came back |
| Sign-off, then five questions about the change | 5 | The sign-off first |
| The questionnaire | 5 | The workload block has a definition under each item; let them read it |
| Break | 5 | Between the two conditions |
| Which would you pick, and the interview | 14 | At the end, with both conditions done |

Nothing on the participant's page runs a clock. You watch the time and call it,
and the two five-minute reading pages and the twenty-minute task are the three
places where it matters.

### What to say at the start

Read this out, and use the same words with everyone.

> Today you will try two ways of working with a coding agent. Both are set up on
> your machine already.
>
> The two halves have the same shape. You get to know a small project and the way
> of working on it, you ask your coding agent for a change, and then you decide
> what to keep. A few questions follow.
>
> Both times there is a written description of the project. It is yours to keep
> current, and the questions afterwards are based on it.
>
> You are responsible for the result being correct, and I will ask you to explain
> the code afterwards.
>
> Please think out loud. If you go quiet I will ask what you are thinking, and
> that is the only thing I will interrupt for.

Never call either way of working "our tool", and never say which one we built.

### The two reading pages

There is no warm-up task any more. Its job is done by the two pages the
participant reads at the start of each condition, five minutes each, and both are
on their own page.

The first is about the project: what it is for, one worked example, four rules,
what it does not do, and how to run it. The second is about the way of working,
and it is where somebody meets the description for the first time. The codoc
version has four steps, which are how to read a feature and the code it owns and
search the tree with `Cmd+F`, how to ask a question with `/codoc:ask`, how a
proposal arrives, and how to comment on a sentence. The baseline version has the
same four, done the ordinary way with `CLAUDE.md`.

Both pages have the same shape and the same number of steps, on purpose. A page
that teaches more in one arm makes the comparison about the page.

Answer questions while they read, but do not add to either page. What you say to
one participant and not another is a difference you cannot account for later.

## Part 5. The task

There is no task card any more. The task page carries the whole occasion: one
case where the project behaves unhelpfully, what they are therefore asking for,
the exact request in a copy block, and what to do once the agent stops. They send
the request themselves, which is what makes the change theirs to decide about.

- **scribe:** a config file, a short report beside the Markdown, and a tidy-up of
  how the rules get their settings.
- **tally:** a rules file, a weekly view, and a tidy-up of how the rules get
  their settings.

The change is recorded in advance and replayed, so every participant reviews the
same change and nobody waits forty minutes for code that is not what we are
measuring. **The participant is not told any of that.** They type the request,
the recording plays as the agent's answer, and every turn after the first is the
real assistant carrying the recorded session's context, so they can ask it about
anything it did.

Nothing on the page says the change is wrong, and nothing says what to check.
Either would hand over the thing being measured.

### What `./claude-study` does on the first run

`./claude-study` is a launcher that setup writes into each project folder. On its
first run with no arguments, when the folder holds no `.claude-study/handover.json`
and a recording for that folder is present, it hands the turn to
`~/codoc-study/replay/agent.py`, which does five things:

1. It prints the assistant's own opening screen, recorded on that machine during
   setup and kept in `.claude-study/welcome.ansi`.
2. It takes the request in an input box of its own.
3. It stops the codoc daemon if one is running, because the player will not write
   into a workspace a live daemon owns.
4. It plays the recording, which takes about three minutes.
5. It writes `.claude-study/handover.json` and starts the daemon again in the
   background.

The launcher then runs the real assistant with `--continue`, so every turn after
the first is live and carries the recorded session's context. The handover file is
what stops the first turn happening twice. Once it exists, every later run goes
straight to the assistant.

The recorded frames carry the request as the agent's own first line, so what plays
is the recorded request rather than the text that was typed. A participant who
mistyped their paste still sees the request the change was actually made from, and
that is also the one the assistant is resumed on. What they typed is kept in
`handover.json`, which `collect.sh` takes with the rest of the workspace.

### Starting a condition, step by step

The same steps are on the participant's page in the dashboard, under **Starting
the condition**, with their own folder already written into each command. Copy
them from there rather than retyping.

1. They open `~/codoc-study/scribe` in VS Code and answer the trust prompt with
   "Yes, I trust the authors". Until they do, VS Code turns every extension off
   and the session records nothing.

2. Codoc: they open the description with Cmd+Shift+P and "codoc: Open", and open
   a terminal inside VS Code. Without codoc: they open `CLAUDE.md` from the file
   tree, and open a terminal inside VS Code.

3. They run "Study logger: show what is being recorded" from Cmd+Shift+P, and read
   you the snapshot count. Anything above zero is fine. A zero here is the one
   fault that cannot be repaired afterwards.

4. They read the project page, then the page about the way of working. Five
   minutes each. Answer questions, but do not add to either page.

5. On the task page they start the agent with `./claude-study` and paste in the
   request the page gives them. It works for about three minutes. Let them watch
   it.

6. Twenty minutes from there. Say "about ten minutes gone" once, at the halfway
   point, and call time at twenty.

Which folder carries codoc for this participant is in the table in Part 3. There
is no daemon to start and no frames argument to get right. Setup wrote the
recording for that folder's own condition into the launcher when it made it.

#### What to say when the agent starts working

> That is it running. Watch what it does, and when it stops, decide what you want
> to keep.

Do not say the session was recorded, and do not say whether anything in the change
is right or wrong. If they ask, tell them to work from the request and what they
find in the project.

#### Rehearsing it on your own machine

Never rehearse in a folder a participant is going to use. The recording plays once
and leaves `handover.json` behind, and the change is then sitting in the workspace
before anybody has asked for anything. `./setup.sh --check` fails a folder that has
been played into, and says "the session has already been played there".

To see the first turn as a participant meets it, opening screen and input box and
all, run the same program the launcher runs, against an empty folder:

```
python3 docs/study-materials/replay/agent.py play <an empty folder> docs/study-materials/replay/frames/scribe/codoc
```

To watch only the replay, `play.py` takes the same two arguments and skips the
first turn. `--speed 2` plays it faster, `--step` waits for Enter between frames,
and `--no-reset` leaves the current state alone. Use them for a rehearsal and not
in a session.

```
python3 docs/study-materials/replay/play.py <an empty folder> docs/study-materials/replay/frames/scribe/codoc
```

Give both of them an empty folder. The player restores the state the recording
started from, which means deleting whatever else is there.
`docs/study-materials/replay/README.md` explains how a recording is made and what
keeps it honest.

### What is planted in the change

Three problems and one decoy per project, listed with their rating guides in the
project's `STUDY.md`, which is the answer key. Do not open it in front of a
participant. None of them breaks a test, so the suite passing tells the
participant nothing about whether the change is right.

**Do not hint at any of it.** If they ask whether something is deliberate, say:

> Work from the request and what you find in the project.

The terminal says the agent has finished and the tests pass, at the end of the
replay. Nothing they read says anything is wrong, and nothing says everything is
fine.

### What is scored

**The gate.** The change runs and the existing tests pass at the end. It is not
reported as a result.

**The primary outcome.** Each planted problem is rated **0 to 2**. 0
is not found, 1 is found, and 2 is found and correctly attributed to the
commitment it contradicts. The rating guide for each is in `STUDY.md`.

**The false alarms.** Record every correct part of the change the participant
called wrong, including the decoy. A surface that makes everything look suspicious
is not an improvement, and nothing else in the analysis would catch that.

Rate it during the session, in the dashboard, while you can still remember what
they said. Alongside it, record **who settled each problem**: they directed it,
they accepted a proposal deliberately, or it stands and they never noticed.

### Timing

Twenty minutes for the task, starting when the agent stops. There is no clock on
the participant's page, so you watch the time and call it. Say "about ten minutes
gone" once, at the halfway point, and call time at twenty.

The twenty is meant to run in two halves, and their page says so. The first is
spent working out what the agent changed and how the project works now. The second
is spent deciding what to keep, and leaving the project in a state they would be
happy to ship. Do not extend it. If they finish early, the time they took is
recorded as part of the result.

The time to their first correct detection is a measure, so note the clock when
they first name something as wrong, and note whether they were right.

### The sign-off

When they say they are done, ask, and write the answer down word for word:

> Is this change correct and complete? How confident are you, 1 to 5? And what is
> that resting on?

The confidence number matters less than what the confidence rests on. "I ran the
tests" and "the agent said so" are very different answers.

## Part 6. The questions about the change

Five multiple-choice questions, closed book, straight after the sign-off. The
participant answers them on their own page. **You do not read them out and you do
not score them during the session.** They have right answers and the dashboard
scores them as they answer, so nothing here needs marking by hand.

**Ask them to close the code, the description and the agent first**, and say why.
What is being looked at is what they carried out of the task, so an answer they
went and looked up says nothing. Their page says the same thing, but it lands
better from you. The page also stops the text being selected, which removes the
thoughtless path rather than enforcing anything.

Each one turns on a consequence of the change meeting a rule that was already
there, so it can be answered by somebody who understood the codebase or who made
the decision themselves and watched what it did, and not by somebody who let the
agent write it and did not look. The five run from easy to hard in that order, so
a participant who did the work is not scored as though they did none.

Alongside the five is one scale: how much of it they were sure of rather than
working out on the spot. A fluent reconstruction and a real memory look the same
in a set of answers, and only they can say which it was.

There is no feedback on anything. Telling somebody they were wrong would teach
them how the second half works.

The open-book round that used to run before the task is gone. It asked about the
codebase, and the task now asks somebody to review a change to that codebase, so
the first ten minutes of the task were the question round over again with the
clock running twice.

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

The gate is mechanical. The three detection ratings are the reported outcome.

**The gate.** From inside their finished project:

```
./.venv/bin/python -m pytest tests/ -q
./.venv/bin/scribe check fixtures/          # or: ./.venv/bin/tally check fixtures/
```

Every existing test must still pass and the project must still run over all three
sample inputs. A change that breaks either is a session with nothing to rate.

**The planted problems**, rated 0 to 2 against the guide in the project's
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

**The agent starts and no work arrives.** The recording for that folder is
missing, so the launcher skipped the first turn and went straight to a live
assistant. Have them run `./setup.sh --check` in their bundle, which says per
folder whether the session it reviews is there. Note it on the session sheet
either way, because that participant did not review the same change as everybody
else.

**It stopped partway.** They run `./claude-study` again. The handover record is
only written once the recording has played all the way through, so a run that was
interrupted starts again from the beginning, restoring the starting state as it
goes, and the participant sees nothing a second run does not show them again.

**The change is already in the folder.** `./setup.sh --check` says the session has
already been played there. Somebody has run `./claude-study` in that folder, and
the change is sitting in it before the participant has asked for anything. Do not
run the session there. Delete `~/codoc-study` and run `./setup.sh` with their code
and order again.

**Nothing is updating.** The status bar is stuck and no proposals appear. The
daemon runs behind the session with no terminal of its own, so read
`.codoc/watch.log` in the project folder for what it last printed.

**The description looks slightly different from what you expected.** On first
start, codoc tidies up the project and reports something like "1 amend, 1
attach". This is normal and does not change the text. Check that it still lists
15 features (scribe) or 23 (tally) before you begin.

**The assistant asks them to sign in.** They should not have to. `./claude-study`
sets the assistant's config directory to `.claude-study` inside the project folder
and reads the study's key from a helper there, and it clears any key of their own
out of the environment first. If it asks anyway, the profile did not land, and
`./setup.sh --check` will say which folder has no key.

**codoc has no model to call.** codoc reads its own key from `.env` in the project
folder, separately from the assistant, and only the codoc folder has one.
`./setup.sh --check` reports it as a missing OpenAI key.

**They typed their own request instead of pasting the one on the page.** The
recording plays the request the change was actually made from, so the change is
the same either way and nothing is lost. What they typed is kept in
`.claude-study/handover.json`. Note it if it was very different from the request
on the page, because what they thought they were asking for is part of how they
read the result.

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

- Post `pre-registration.md` to OSF. It is written, and from that point the two
  question sheets, their scoring tables, and the measure list in
  `analysis-plan.md` are fixed.
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
actually looked at, and what their confidence rested on. `pre-registration.md`
states it, rather than leaving it to be discovered in the results.
