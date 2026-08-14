# Study materials

Everything needed to run the study. The design and the reasoning behind it are in
`../plans/2026-08-11-001-user-study-design-v2.md`. These files are what you use on
the day.

## Start here

Read `experimenter-guide.md`. It takes you from a machine with nothing installed to
a folder of collected data, and it points at everything else listed below.

## What is here

Two projects, hearth and ember, with matched tasks. Each participant does one of
them each way, so most files come as a pair.

**For you to run the session**

| File | What it is |
| --- | --- |
| `experimenter-guide.md` | Setup, the shape of the session, what to say, what to record |
| `questions-hearth.md` | The ten questions for a hearth session, with how to score each answer |
| `questions-ember.md` | The same ten questions for ember |

**To send or show the participant**

| File | When |
| --- | --- |
| `participant-before-the-session.md` | Sent days ahead. What the study is and how to set up their machine. Also goes in the bundle as `README.md`. |
| `participant-about-hearth.md` | At the start of a hearth condition. What the project is and the commands they will use. |
| `participant-about-ember.md` | The same, for ember. |
| `participant-task-hearth.md` | The hearth task. Show it as an image, never as text they can copy. |
| `participant-task-ember.md` | The ember task. Same rule. |

**The projects and the tools**

| File | What it is |
| --- | --- |
| `workspaces/` | The four project copies, packed as archives, with notes on what is in them |
| `scripts/` | Four scripts, described below |
| `scoring/` | Three scripts, described below |
| `baseline/doc-maintenance/SKILL.md` | The instructions given to the agent in the condition without codoc. Nobody reads this during a session. Edit it here and the bundle picks it up. |

## The scripts

`scripts/build-participant-bundle.sh` runs on your machine and makes the zip you
send to a participant. Run it again whenever codoc changes, so the extension and
the codoc command inside stay the same version.

`scripts/setup.sh` runs on the participant's machine, from inside the unzipped
bundle. It installs everything and sets up all four project copies, then checks
they work. `./setup.sh --check` runs only the checks.

`scripts/session-log.sh` saves a copy of the project every 20 seconds during a
session, so it can be replayed afterwards. It runs on the participant's machine,
because that is where the files are, so it ships in the bundle.

`scripts/collect.sh` packs a finished session into one zip for the participant to
send back. Also on their machine, also in the bundle.

## The scoring scripts

`scoring/check-hearth.py` and `scoring/check-ember.py` check a finished project
against the three things the task is designed to test, and confirm the existing
tests still pass. Each was tried against a correct and an incorrect
version of its task, and they tell the two apart.

Each needs a small settings file per participant, saying how that person's code is
driven, because the task deliberately leaves that open. Write it after reading
their code. If you describe it wrongly, nothing happens when the script runs, and
that looks exactly like their code failing. The scripts detect this and say so
rather than recording a false result. Part 8 of the guide has the details.

`scoring/check-descriptions-match.py` confirms both conditions still carry the
same words. Run it before every session, and after any change to either description.
Both projects pass today.

## Before you run anyone

- Build the participant bundle again, so it matches the version of codoc you are
  studying.
- Run `setup.sh` yourself on a spare machine or a fresh account. It is the
  participant's entire experience of setup, so it is worth feeling once.
- Pre-register the design and the scoring. The two question sheets and their
  scoring tables are fixed from then on.
- Run the three pilot sessions with the full protocol.
- Build the two missing logs described in section 10 of the design doc, so what
  people open and click is recorded in both conditions and not only in codoc.
