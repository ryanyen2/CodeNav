# Recording and replaying the agent session

The study asks a participant to review a change an agent made, so every
participant has to see the same change. Waiting forty minutes for an agent to
write code is not part of what we are testing, and the quality of that code is
not what we are measuring, so the session is recorded once and replayed in about
three minutes.

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
already dirty, and starts the watcher. Then run the agent in that folder with the
request in `requests/scribe.txt`. Steer it until it lands the planted problems
and write every steer into `notes.md`. `record-session.sh stop scribe neutral`
turns the snapshots into frames and copies the transcript next to them.

**Second, derive each condition.** With the daemon running in a clean codoc
workspace:

    python3 record.py derive frames/scribe/neutral ~/codoc-recording/scribe-codoc \
        frames/scribe/codoc

It replays each code frame into that workspace, waits for the daemon to finish
reacting, and records what moved under `.codoc/`. The same command against a
baseline workspace with the maintenance skill produces that condition's frames.
Both conditions then hold the same code and the same transcript, and differ only
in the record beside it.

**Third, check.** `record-session.sh check scribe codoc` replays the frames into
an empty directory and compares the result against the workspace the recording
ended in, file by file. A recording that does not pass `check` is not shipped.

What the scribe recording actually contains, as a worked example: 42 frames,
1,094 seconds of real session compressed to 180 of playback at 6.1x, three
pending ADD proposals the daemon raised, and a tree that visibly catches up with
the code twice during the replay. The round trip reproduces the recorded end
state across 39 files.

Two more gates run against a finished recording. `test_handover.py` drives a copy
of the derived workspace through accepting a proposal, rejecting one, editing a
description and leaving a comment, and fails if any of them sets off nothing. The
extension's `recorded-frames.test.ts` reads every frame the way the webview does
and fails if the daemon's own document renders differently from the daemon's own
export, which would make the webview emit commands nobody typed.

## Replaying

    docs/study-materials/replay/play.py ~/codoc-study/scribe \
        docs/study-materials/replay/frames/scribe/codoc

The player restores the starting state, prints the recorded terminal text, writes
the recorded files, and installs the recorded session where `claude --resume`
will find it. The participant's first prompt then continues the session that
produced the change, with the agent's own context intact, and the recorded
transcript is also the terminal scrollback.

The daemon has to be stopped while the player runs, and the player refuses to
start if a live daemon owns the workspace. Start the daemon again once the replay
has finished, because everything after the participant's first prompt is live.

Useful options: `--speed 2` plays twice as fast, `--step` waits for Enter between
frames for a dry run, and `--no-reset` leaves the current state alone.

## Files

`record.py` holds the watcher and the frame builder. `play.py` is the player.
`record-session.sh` drives a recording end to end. `requests/` holds the prompt
each recorded agent was given. `frames/<project>/<condition>/` holds the frames,
the manifest, the transcript and the notes.

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
