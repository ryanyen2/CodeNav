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

There are two projects, hearth and ember, with matched tasks. Each participant
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

They open `~/codoc-study/hearth` in VS Code, then open a terminal inside VS Code
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

Ember works the same way. Open `~/codoc-study/ember` instead.

### Starting a condition without codoc

They open `~/codoc-study/hearth-baseline`, or `~/codoc-study/ember-baseline`, and
start Claude Code in a terminal. Nothing else runs. `CLAUDE.md` sits in the project
root and Claude Code picks it up on its own.

### Recording the session

The recorder runs on their machine, because that is where the files are. Ask them
to run this at the start of each condition, from the folder they unzipped, using
the code you gave them:

```
./session-log.sh ~/codoc-study/hearth p04-codoc
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

Show `participant-task-hearth.md` or `participant-task-ember.md` as an image or on
paper, whichever project this condition uses. Do not give them the text. If they
paste our wording into the agent, the agent is working from our instructions
instead of theirs, and the instructions they write are one of the things we
measure.

### What the card deliberately leaves out

For each of these, write down who settled it and how. There are three
possibilities: they decided before the agent acted, the agent proposed it and they
accepted, or the agent did it and they never noticed.

With hearth:

1. What marks a post as a draft, e.g., a setting in the post or a separate folder.
2. Whether drafts stay out of the feed and the sitemap. The agent usually handles
   these without being asked, so this is where it reaches past what it was told.
   Credit is for deciding on purpose, not for a particular answer.
3. How the preview differs from a real build, e.g., a flag, an environment
   variable, or a setting.
4. Whether a draft is not built at all, or built but not linked to.

With ember:

1. Where a mute is configured, e.g., against the feed or in the settings file.
2. Whether muted items still reach the notification log and the counts that
   `ember status` prints. Same as above, this is where the agent reaches past what
   it was told.
3. Whether a day whose only items were muted still gets a page, now empty.
4. Whether the "latest" page follows the same rule as a dated page.

### The three things being scored

Every task has the same three, and they are the same shape in both projects. The
codes in brackets are the ones the analysis and the scoring script use.

**The hidden rule (H1).** Written down in the description and nowhere in the code.
Both projects only redo their summary pages when a fingerprint of what those pages
list has changed, and both work out that fingerprint at the point where the list is
assembled. So a filter added further downstream, inside the code that renders a
page, never reaches the fingerprint. The tool reports there was nothing to do, and
the summary pages quietly keep showing what they showed before. In hearth those are
the home, tag, archive, feed and sitemap pages. In ember they are the daily digest
pages. It only shows up on a second run, because building from scratch hides it.

**The open decision (H2).** The feed and sitemap question in hearth, the
notification log question in ember. The card says nothing either way, so this
measures whether a decision got made at all.

**The stated requirement (H3).** Ordinary care. In hearth, the preview shows drafts
and the real build hides them. In ember, the archive and the search file keep the
muted feed's items. Most people should get this one, and if they do not, the task
was too hard.

With hearth there is a fourth thing worth noting: whether the drafted post's own
page is removed from the output, or left sitting at its old address.

Do not hint at any of this. If they ask whether they should worry about what gets
skipped on a second run, say:

> Work from what the card says and what you find in the project.

### Timing

At 15 minutes say "about two minutes left, start wrapping up". Stop at 20.

### The sign-off

Ask this the moment they stop, before anything else, and write the answer down
word for word.

> Is this change correct and complete? How confident are you, 1 to 5? And what is
> that resting on?

The number matters less than what it rests on. Note which of these they say: they
ran the tests, they read the diff, they read the description, or the agent told
them.

## Part 6. The questions

The questions and how to score them are in `questions-hearth.md` and
`questions-ember.md`. Use the one for the project this condition used. Those files
say how to ask them, and the short version is: ask each one twice, first with
everything closed and then with the description open, and record both answers.

Score as you go, in the dashboard. Each question has its scoring table beside it,
and the closed book and open book answers are kept apart, because the change
between them is the result.

The questionnaires appear on the participant's own page at the right moment, so
there is nothing to hand over. At the very end, once both conditions are done,
their page asks which way of working they would pick for each situation, and then
you run the interview.

The dashboard shows what is still missing for the condition you are on. Clear that
list before moving on, because a sign-off is not recoverable after the call.

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

There is one script per project. Run it against the copy from the zip they sent,
once per condition.

```
python3 docs/study-materials/scoring/check-hearth.py       <project> --adapter p04.json
python3 docs/study-materials/scoring/check-ember.py <project> --adapter p04-ember.json
```

Each one checks the three things above and whether the existing tests still pass.
Both were tried against a right and a wrong version of their task, and they tell
the two apart, which is the whole reason to trust them.

The checks themselves never change. What does change is how each participant's code
is driven, because the card leaves that open on purpose. So you write a small
settings file per participant after reading their code. For hearth it says how to
mark a post as a draft and what command builds a preview:

```json
{
  "draft_marker": {"kind": "frontmatter", "key": "draft", "value": "true"},
  "prod_build":   ".venv/bin/hearth build",
  "dev_build":    ".venv/bin/hearth build --drafts"
}
```

Use `{"kind": "folder", "path": "content/_drafts"}` instead if they used a folder.
Ember's file is the same shape, saying how to mute a feed. Keep the settings file
with the results, because writing it is a judgment call and someone should be able
to check it.

**Read their code before you write it.** If you mark a draft in a way their code
does not look at, nothing gets marked, nothing changes, and that looks exactly like
their code failing. The scripts catch this: when the item is still there afterwards,
they build again from scratch and check your marker takes effect at all. If it does
not, they say so and print that the hidden rule was not measured, instead of
recording a failure that is yours. If you see that line, fix the settings file and
run it again.

The scripts put the sample content back when they finish and never touch the
participant's own source, so they are safe on a copy where nothing was committed.
Work on a copy anyway.

Before a session, check the two conditions still say the same thing:

```
python3 docs/study-materials/scoring/check-descriptions-match.py <codoc-copy> <other-copy>
```

The rest of the scoring is by hand, against the design doc: whether each open
decision was settled on purpose, the answers to the questions, and what the
sign-off rested on.

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

**A project looks wrong before they start.** A fresh hearth prints
`12 pages, 12 rebuilt, aggregates rebuilt` and passes 233 tests. A fresh ember
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
