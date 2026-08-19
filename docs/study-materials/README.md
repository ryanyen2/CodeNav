# Study materials

Everything needed to run the study. The design and the reasoning behind it are in
`../plans/2026-08-11-001-user-study-design-v2.md`. These files are what you use on
the day.

## Start here

`研究執行筆記.md` is the one-page version in Traditional Chinese, enough to run a
session from. `experimenter-guide.md` is the full English one and is where the
reasoning lives; read it before the first session rather than during one. It takes you from a machine with nothing installed to
a folder of collected data, and it points at everything else listed below. The
section called "The two pages, and how a session runs" is the short version.

There are two web pages as well as these files. Your dashboard is at
<https://codoc-11b10.web.app/experimenter/> and the participant's page is at
<https://codoc-11b10.web.app/participant/>, reached only through a link you send
them. Their source is in `../../study-app/`.

## What is here

Two projects, scribe and tally, with matched tasks. Each participant does one of
them each way, so most files come as a pair.

**For you to run the session**

| File | What it is |
| --- | --- |
| `experimenter-guide.md` | Setup, the shape of the session, what to say, what to record |
| `analysis-plan.md` | Every measure, and the data it is computed from. Read once before the first session. |

**To send or show the participant**

| File | When |
| --- | --- |
| `participant-before-the-session.md` | Sent days ahead. What the study is and how to set up their machine. Also goes in the bundle as `README.md`. |
| `projects/scribe/ABOUT.md` | What a participant reads at the start of a scribe condition. Also on their own page. |
| `projects/tally/ABOUT.md` | The same, for tally. |
| `projects/<name>/STUDY.md` | **The answer key.** The task card, the four planted problems and their rating guides, the follow-up request, and both five-question sets. Never shown to a participant. |
| `projects/<name>/CLAUDE.md` | The description both arms start from. The baseline gets it as a file; the codoc arm gets the same content as a feature tree. |

**The projects and the tools**

| File | What it is |
| --- | --- |
| `workspaces/` | The four project copies, packed as archives, with notes on what is in them |
| `replay/` | The recorded agent session every participant reviews, and the tools that record and play it |
| `scripts/` | Four scripts, described below |
| `scoring/` | The scorers, described below |
| `logger/` | The study logger extension, installed in both conditions |
| `baseline/doc-maintenance/SKILL.md` | The instructions given to the agent in the condition without codoc. Nobody reads this during a session. Edit it here and the bundle picks it up. |

## The scripts

`scripts/build-participant-bundle.sh` runs on your machine and makes the zip the
participant downloads. It writes `dist/codoc-study-bundle.zip` and a copy into
`study-app/bundles/`, which is what the site serves. Run it again whenever codoc
changes, so the extension and the codoc command inside stay the same version, and
deploy the site afterwards or participants keep getting the old one.

`scripts/setup.sh` runs on the participant's machine, from inside the unzipped
bundle. It takes their code and their order, e.g.
`./setup.sh p-abcdefghjkmn codoc-first`, and asks for them if they are left out.
It installs everything, unpacks the two workspaces that participant needs,
files each one under the code, then checks they work. `./setup.sh --check` runs only the checks, and says
whether the code is set.

`scripts/test-setup.sh` runs on your machine and tests the part of `setup.sh`
that files a machine under a code. Only that part, because the rest takes minutes
and needs the network. It is the step that decides whether a session records
anything at all, so it is the one worth a test.

`scripts/session-log.sh` is a fallback, and is not part of a normal session. The
logger extension saves a copy of the project every 20 seconds by itself
(`logger/snapshot.js`), in both conditions, from the moment VS Code opens; this
script does the same thing by hand if the logger reports that snapshots are off
or failing. It ships in the bundle for that case.

`scripts/collect.sh` packs a finished session into one zip for the participant to
send back. Also on their machine, also in the bundle.

## The scoring scripts

Detection is rated by hand, blind to condition, against the guide in each
project's `STUDY.md`. No script can read a diff and say whether somebody
understood what was wrong with it.

The two outcomes that are mechanical, or half mechanical, have scripts:

`scoring/score-record-truth.py` asks whether the description a participant
finished with is true of the code they finished with. It runs their code on a
sample to find out what the code does, which is the half a person would get
wrong, and hands the rest to a blind rater as a sheet.

`scoring/transfer-probe.py` asks whether that description still works as the
agent's memory. It gives the description to a fresh agent with a further task in a
clean copy of the project, and counts how many commitments the agent's change
kept. Run it after every session, not during one.

What is fully mechanical is the gate: the existing tests still pass and the
project still runs over all three sample inputs. Two commands, in Part 8 of the
guide.

`scoring/check-descriptions-match.py` confirms both conditions still carry the
same words. Run it before every session, and after any change to either description.
Both projects pass today.

`scoring/check-session-complete.py` takes a finished session and says, measure by
measure, whether the data to compute it arrived. Run it while the participant is
still on the call.

## The logger

`logger/` is a small VS Code extension that records which file is on screen, which
lines, for how long, and how much text changed. It never records the text itself.

It installs in **both** conditions, and this is the reason it is separate from
codoc. If only one condition logged navigation, every navigation result would
describe the tool rather than the person. Five measures come from this log and
from nowhere else. `check-session-complete.py` names them when the log is
missing.

## Before you run anyone

- Build the participant bundle again, so it matches the version of codoc you are
  studying, and deploy the site so that is the one they download.
- Put the two keys in under **Session keys** in the dashboard. Without them a
  setup run reaches the key step, tells the participant to come back to you, and
  the rest of their setup is wasted.
- Run `setup.sh` yourself on a spare machine or a fresh account. It is the
  participant's entire experience of setup, so it is worth feeling once.
- Post `pre-registration.md` to OSF. It is written and its thresholds are frozen,
  and the two question sheets and their scoring tables are fixed from then on.
- Run the three pilot sessions with the full protocol.
- Build the two missing logs described in section 10 of the design doc, so what
  people open and click is recorded in both conditions, not only in the codoc
  condition.
