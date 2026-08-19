# Recording and replaying the agent session

The study asks a participant to review a change an agent made, so every
participant has to see the same change. Waiting forty minutes for an agent to
write code is not part of what we are testing, and the quality of that code is
not what we are measuring, so the session is recorded once and replayed in about
three minutes.

The participant asks for the change themselves and is never told a recording
exists. They type the request into what looks like the assistant's own input box,
and the recording plays as the answer. How that first turn works is in "The first
turn" below. Everything before it is about making the recording.

Replay is cheap because of how codoc is built. The local extension is file based,
with no server and no port, and everything the participant sees comes from files
under `.codoc/`, which the extension watches and reparses when they change. The
webview draws `tree.doc.json`, the status bar reads `status.json`, and the
proposals come from the control files. Writing recorded copies of those files
back into the workspace drives the whole interface, and codoc needs no changes at
all.

## What is honest about it

The change under review is a constructed stimulus. An agent asked to add a
configuration layer is careful by default and lands none of the planted problems
on its own, so the recording is steered until it does, and every steer is written
into `notes.md` beside the frames. Every participant reviewing the same change is
what makes their detection counts comparable at all.

What is never authored is codoc's response. Every frame under `.codoc/` was
written by a real daemon during the recording, and nobody edits one by hand. If
Loop A failed to surface a planted problem, participants see it fail and the
paper reports that codoc failed to surface it. Writing the tree ourselves would
make the faithfulness claim circular, and faithfulness is the claim the study
rests on. The stimulus is ours; the record of it is codoc's.

## The recording has to be neutral, and that is checked rather than assumed

Both conditions read the recorded transcript as their terminal scrollback, so it
must not name either tool. A baseline participant who finds `.codoc/tree.codoc`
in their own scrollback has been told which tool the study is about, and a codoc
participant who finds `CLAUDE.md` has been told there is another condition.

The first scribe recording failed this in two ways, and both were the harness
rather than the agent. The neutral workspace is made neutral by deleting the tool
files and folding the deletion into the last commit, and when that last commit
holds nothing but the tool files, git refuses to amend it into an empty commit.
The failure was not checked, so the agent began in a tree holding eight staged
deletions, ran `git status`, and wrote their names into the transcript. Separately,
Claude Code prints absolute paths, and the recording workspace is
`~/codoc-recording/<project>-neutral`, so every `Read(...)` line named the tool
and announced that a recording was being made.

Three things now stand between that and a participant. `strip_tools` drops the
commit when amending it would leave it empty, so the tree is genuinely clean.
`build` writes the recording's own directory into the terminal text as
`{{WORKSPACE}}` before anything truncates a long line, and the player expands it
to the participant's own path. And the player refuses to run at all if either
tool is still named after that, because what the harness put there is already
gone, so what is left is the agent having said it, and that is a recording to
make again rather than a line to quietly delete.

The check runs with the participant's own path taken out, because their workspace
is `~/codoc-study/<project>` and they see it all session in both conditions.

`record.py retext <frames-dir>` renders a finished recording's scrollback again
from its own transcript, without touching the frames. It exists because the
scrollback is cheap to regenerate and the frames cost an hour of daemon time, so
a fault in what the participant reads should not mean deriving both conditions
again.

## The code is recorded once, with neither tool present

The session runs in a workspace with no `.codoc`, no `CLAUDE.md`, and no agent
configuration, and `derive` then replays it into each condition and records what
that condition's own machinery did in response.

Both conditions have to review the same code, or a detection count cannot be
compared between them, and two separate agent runs never produce the same code.
The transcript is read by participants in both conditions, so it must not mention
either tool: an agent left in a codoc workspace explores it, finds
`.codoc/tree.codoc` and the codoc skill, and says so in its own output. That was
found by running it.

Delays are scaled by one factor, which the manifest records. The lag between a
code edit and the tree reacting to it therefore survives playback in proportion,
rather than collapsing to nothing and making codoc look instant. Report the
factor.

One thing is removed rather than scaled. A recording is made by a person sending
the agent a follow-up once the last one has landed, and the pause between them is
that person reading, deciding and typing, which is not the agent working. Gaps
longer than two minutes are clipped to two minutes, so every lag that is actually
about the tools survives untouched while the dead air does not. The manifest
records how much was taken out and it is reported next to the factor. Without it
the first tally recording spent more of its timeline waiting for the experimenter
than watching the agent.

Only one watcher may record into a raw directory at a time, and it takes
ownership with a pid file. Two of them destroy a recording in a way the round trip
does not catch: each keeps its own counter and its own idea of what changed last,
so they overwrite each other's snapshots and each records half the diff. The end
state still replays, because the last frame and the final copy carry it, while the
middle of the recording runs backwards. `build` refuses a recording whose clock
goes back and says which snapshot to look at.

The change is left uncommitted in the working tree, so `git diff` shows the whole
change. Reading the diff is an honest way to review, both conditions have it, and
we want to know who chooses it.

## Recording, in three steps

Recording happens once per project, on the experimenter's machine, and it needs
an API key. It has three steps, because the code is recorded once and each
condition's record is derived from it.

**First, record the code.** `record-session.sh start scribe neutral` unpacks a
workspace with no codoc, no description and no agent configuration, folds the
removal into the last commit so the agent does not begin in a tree that is
already dirty, hides the editable install's build output in `.git/info/exclude`
so the agent does not write a filter to work around it, and starts the watcher.
Then run the agent in that folder with the request in `requests/scribe.txt`. It
needs `--allowedTools` naming Bash, because an agent that cannot run the tests
stops and asks questions instead of working. Steer it until it lands the planted
problems and write every steer into `notes.md`. `record-session.sh stop scribe
neutral` turns the snapshots into frames and copies the transcript next to them.

**Second, derive each condition.** With the daemon running in a clean codoc
workspace:

    python3 record.py derive frames/scribe/neutral ~/codoc-recording/scribe-codoc \
        frames/scribe/codoc --pace --settle-every 4

It replays each code frame into that workspace, waits for the daemon to finish
reacting, and records what moved under `.codoc/`. Use `--pace`: without it the
frames go in as fast as the disk allows, the daemon coalesces the lot into one
pass, and the description moves once at the very end, so a participant watches
nothing happen and then everything happen at once.

The baseline's record is written by an agent at the end of a session rather than
by a daemon as it goes, so it derives with `--after` naming a command that runs
the maintenance skill once after the last frame. Export the API key into the
environment first and leave it out of the command string, because `--after` is a
shell line that ends up in logs:

    python3 record.py derive frames/scribe/neutral ~/codoc-recording/scribe-baseline \
        frames/scribe/baseline --after "…run the maintenance skill…"

Both conditions then hold the same code and the same transcript, and differ only
in the record beside it.

**Third, check.** `record-session.sh check scribe codoc` replays the frames into
an empty directory and compares the result against the workspace the recording
ended in, file by file. A recording that does not pass `check` is not shipped.

What the scribe recording actually contains, as a worked example: 48 frames,
1,389 seconds of real session of which 212 was the experimenter waiting between
turns, compressed to 180 of playback at 6.5x. The description visibly catches up
with the code 16 times during the replay, leaving seven ADD proposals pending as
ghost rows. The round trip reproduces the recorded end state across 21 files in
the neutral recording and 37 in the codoc condition.

Two more gates run against a finished recording. `test_handover.py` drives a copy
of the derived workspace through accepting a proposal, rejecting one, editing a
description and leaving a comment, and fails if any of them sets off nothing. The
extension's `recorded-frames.test.ts` reads every frame the way the webview does
and fails if the daemon's own document renders differently from the daemon's own
export, which would make the webview emit commands nobody typed.

## The first turn

Nobody runs the player during a session. The participant runs `./claude-study`,
which is a launcher `setup.sh` writes into their project folder. On its first run
with no arguments it hands the turn to `agent.py`:

    agent.py play <workspace> <frames> --codoc-bin <codoc>

`agent.py` prints the assistant's own opening screen, takes the request in an
input box of its own, stops the codoc daemon if one is running, plays the
recording, writes `.claude-study/handover.json`, and starts the daemon again in
the background. The launcher then runs the real assistant with `--continue`, so
every turn after the first is live and carries the recorded session's context.

Asking for the change is the one thing a recording cannot supply, and it is what
makes the change theirs to decide about. The version before this had a researcher
start the player, having told the participant they had asked for the change
earlier and gone out. Now the request comes from them, in the same box every
later turn comes from.

The recorded frames carry the request as the agent's own first line, so nothing
that was typed is echoed back. A participant who mistyped their paste still sees
the request the change was actually made from, which is also the one the assistant
is resumed on. What they typed is kept in `handover.json` with the moment of the
handover, and `collect.sh` takes it with the rest of the workspace.

The handover record is also the guard. The launcher takes the first turn only when
no handover record exists, so the recording plays once per folder and a bare
`./claude-study` afterwards goes straight to the assistant. It is written only
after the recording has played all the way through, so a run that was interrupted
starts again from the beginning. `setup.sh --check` fails a folder that already
holds one, because such a folder holds the change before anybody has asked for
anything.

The daemon is handled here rather than by a person. The player refuses to write
into a workspace a live daemon owns, so `agent.py` stops it first and starts it
again once the participant takes over, with its output going to `.codoc/watch.log`
rather than to a terminal somebody is watching.

### The opening screen is recorded, not written

    agent.py capture <workspace>

`capture` runs the real assistant once, on the participant's own machine, during
setup, and keeps the bytes it drew before its input box in
`<workspace>/.claude-study/welcome.ansi`. `play` prints those bytes back. An
assistant that changes its welcome changes it here too, and nobody has to keep a
copy of somebody else's layout up to date.

The cut is the last top-left box corner in the stream. The input box is the last
thing drawn on an empty session and nothing follows it, so everything above the
corner is the part that is drawn once and worth keeping. The box itself has to be
ours, because it is redrawn on every keystroke and the captured bytes are a
picture rather than a program.

A capture is refused rather than kept if it holds a first-run question, e.g. the
theme picker, the login choice, or the question about a key found in the
environment. None of those is a welcome screen, and showing one would put a setup
question in front of somebody at the moment they are supposed to be asking for a
change. Setup runs the assistant once before capturing, which is what gets it past
them. A machine with no usable capture draws a plain welcome instead, and nothing
else about the session changes.

## Replaying by hand

    docs/study-materials/replay/play.py ~/codoc-study/scribe \
        docs/study-materials/replay/frames/scribe/codoc

`play.py` is what `agent.py` calls, and running it directly is how a recording is
rehearsed or checked without the first turn. It restores the starting state,
prints the recorded terminal text, writes the recorded files, and installs the
recorded session in the assistant's project history, under a name made from the
participant's own workspace path.

Give it an empty folder. Restoring the starting state means deleting whatever else
is in the workspace, and playing into a folder a participant is going to use burns
that folder.

Run it by hand and the daemon is your problem. The player refuses to start if a
live daemon owns the workspace, and it does not start one again afterwards.

Useful options: `--speed 2` plays twice as fast, `--step` waits for Enter between
frames for a dry run, and `--no-reset` leaves the current state alone. `agent.py
play` takes `--speed` as well, and is how the first turn is rehearsed with the
opening screen and the input box in place.

## Files

`record.py` holds the watcher and the frame builder. `play.py` is the player.
`agent.py` is the participant's first turn, and also captures the opening screen.
`record-session.sh` drives a recording end to end. `requests/` holds the prompt
each recorded agent was given, which is word for word the request the participant
is given to paste. `frames/<project>/<condition>/` holds the frames, the manifest,
the transcript and the notes.

Run the tests with `python3 docs/study-materials/replay/test_replay.py`. The test
that matters is the round trip, because a participant reviews the replayed state
while the planted problems are rated against the recorded one, so the two have to
be the same state.

## What the replay must not corrupt

The interaction logger records what the participant does, so replayed writes must
not count as the participant's work. The logger already keeps an editor edit only
when the file is active and focused, and the shipped transcript owns the agent's
actions, so the player's writes land as agent actions, which is what they are.

The codoc change ledger needs the same care. Seeding events are already excluded
in `scoring/ledger-actions.py`, and the recorded session's events have to be
excluded the same way, or a participant's own accepts and amends are buried under
the recording's.


## Recording a session the participant works THROUGH

A recording used to play start to finish and hand over a finished change. The part
of the session codoc is for went past read only, and a participant who tried to
accept a proposal during it was told the verdict was not picked up, because the
daemon was stopped for the whole run.

A recording is now cut at the point the agent stops to ask, and playback waits
there for an answer.

### Make it in the shape it will be played in

1. Record neutrally, as before, but drive the agent so it **plans first**: it
   reads the codebase, sketches what it intends as feature nodes, and stops.
2. **Accept the plan** while recording. Everything after the cut is recorded
   against a store in which those nodes are live, which is what lets a
   participant's own accept put their store into the state the next segment
   expects.
3. Let it implement, run the tests, and finish.
4. `derive` into each condition as before.

### Cut it, BEFORE deriving

```
python3 record.py checkpoint frames/scribe/neutral 23 \
  --says "I have sketched this as a plan in the tree. Accept the parts you want
          and I will build them."
```

On the NEUTRAL frames, and before `derive`, because `derive` needs to know. The
daemon restarts at a checkpoint and projects the tree from the STORE, and the
store is otherwise carried once at the very end, since it is most of the bytes and
nobody sees it. A checkpoint with a stale store shows the participant a tree with
none of the plan in it. `derive` therefore keeps the store at each stop, settles
the daemon there whatever `--settle-every` says, and copies the checkpoints
through to each condition, so both arms pause at the same point in the same work.

The frame number is a judgement about the session, not something to infer from
file writes: it is the frame after the plan has landed and before the first edit
of the implementation. Watch the recording back and pick it.

`checkpoint` with no frame numbers clears them, and a recording with none plays
straight through exactly as it always did.

### What happens at the checkpoint

The player stops writing, deletes `.codoc/replay.lock` so the editor's daemon
comes back, prints what the agent "says", and waits until one of the pending
proposals has been answered. Then it takes the workspace back and plays on.

A participant who REJECTS has diverged from the recording, and that is the most
interesting thing they can do rather than a failure to handle: the session
continues with the live agent from there, and which way they went is recorded.

If nobody answers within fifteen minutes the player carries on regardless. A
session that hangs because somebody did not click is worse than one that
continues without the answer.
