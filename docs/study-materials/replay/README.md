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

Every frame under `.codoc/` was written by a real daemon during the recording,
and nobody edits a frame by hand. If Loop A failed to surface one of the planted
problems during the recording, participants see it fail, and the paper reports
that codoc failed to surface it. Authoring the tree ourselves would make the
faithfulness claim circular, and faithfulness is the claim the study rests on.

Delays are scaled by one factor, which the manifest records. The lag between a
code edit and the tree reacting to it therefore survives playback in proportion,
rather than collapsing to nothing and making codoc look instant. Report the
factor.

The change is left uncommitted in the working tree, so `git diff` shows the whole
change. Reading the diff is an honest way to review, both conditions have it, and
we want to know who chooses it.

## Recording

Recording happens once per project and per condition, on the experimenter's
machine, and it needs an API key.

    docs/study-materials/replay/record-session.sh start scribe codoc

The script unpacks a clean workspace, starts the daemon for the codoc condition,
starts the watcher, and prints the request to paste. Run `claude` yourself in a
second terminal, because the session may need nudging before it lands the planted
problems, and every nudge has to be written into `notes.md` next to the frames. A
problem the agent produced on its own is stronger evidence than one it was steered
into, and the paper reports which is which.

When the agent has finished and the tests pass:

    docs/study-materials/replay/record-session.sh stop  scribe codoc
    docs/study-materials/replay/record-session.sh check scribe codoc

`stop` turns the snapshots into frames and copies the session transcript next to
them. `check` replays the frames into an empty directory and compares the result
against the workspace the recording ended in, file by file. A recording that does
not pass `check` is not shipped.

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
