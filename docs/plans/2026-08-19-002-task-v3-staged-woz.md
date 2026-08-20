# Task v3: a staged session the participant works through

Written 2026-08-19, after a pilot run of the v2 replay showed what was wrong with
it. Supersedes the TASK and REPLAY halves of
`2026-08-19-001-task-redesign-v2-reviewing-an-agent-session.md`. The instrument,
the projects, the scorers and the bundle survive unchanged.

## What went wrong with v2

The v2 session played a recorded change from start to finish and then asked the
participant to review the end state. Three things followed from that, and all
three showed up in the first run.

The participant never interacted with codoc during the part codoc is for. They
watched three minutes of output and then met a finished change. Accepting a
proposal mid playback failed, because the daemon was stopped for the whole
playback. So the arm that is supposed to demonstrate deciding and reviewing spent
its most interesting three minutes read only.

The request was a list. "Add a config file, write a report, tidy up how the rules
get their settings" is three instructions, and an agent that carries out three
instructions leaves nothing to interpret. The interesting failure in agent work is
not a typo, it is a reasonable reading of an ambiguous ask that turns out to be
the wrong one, and a numbered list has no ambiguity to get wrong.

There was nothing to debug. The planted problems were all detectable by reading,
which measures reading. A person who works with agents finds this class of
problem by running the thing and looking at the output, and the session gave them
no reason or means to do that.

## The shape of v3

Five stages. The first two are reading, the last three are work. Twenty minutes on
the work, which the researcher calls.

1. **Understand the project.** High level down to the rules, on the page, worked
   example first. Then they open the project and look around with whichever
   description their arm has.
2. **Meet the problem.** One concrete case where the program is wrong, with the
   input, the output it gives, and the output it should give. Stated as a
   problem, not as a task.
3. **Ask for the fix.** They paste one request. It says what they want to be
   true, not what to do.
4. **Watch it work, and answer it.** The agent reads, plans, and stops to ask.
   The plan arrives in codoc as nodes. They accept or reject. Then it implements.
5. **Find out whether it worked.** It did not, quite. They debug it live.

Stage 4 and 5 are the twenty minutes. Roughly ten and ten, and the split is not
enforced, because a person who spots the problem early should get on with it.

## The problem, and why it is the right one

`scribe` drops a line that repeats near the top of enough pages, on the theory
that a line printed on every page is furniture rather than writing. "Enough" is a
share of the pages, and a document whose appendix has its own header defeats it:
the appendix header covers two pages of five, does not reach the share, and
survives into the middle of the prose.

That is a real behaviour of the shipped code, it is visible in one run, and it is
not a bug in the sense of a mistake. Somebody chose a threshold. It is the kind of
thing you find out by using a program on a document its author did not have.

The participant is shown:

```
in    page 1   Coastal Erosion Survey 2026     Marine Institute
      page 2   Coastal Erosion Survey 2026     Marine Institute
      page 3   Coastal Erosion Survey 2026     Marine Institute
      page 4   Appendix A: Site Photographs    Marine Institute
      page 5   Appendix A: Site Photographs    Marine Institute

out   ...within measurement error of no change at all.

      Appendix A: Site Photographs            Marine Institute   ← still here
```

## The request

One sentence, in the participant's voice, describing the state they want rather
than the steps to get there:

> Different documents need different rules, and I should not have to edit the
> source to convert one. Make that configurable, and tell me what the conversion
> actually did.

Two things are deliberately open in it. "Configurable" does not say what is
configurable, and "tell me what it did" does not say where. A capable agent will
make both calls, and one of them will be wrong in a way that only shows up in the
output.

## What the session is made of

The agent's half is written, not recorded (`record.py simulate`). Recording one
cost a key, forty minutes and a lot of steering, and every planted problem was
steered in anyway, so what was being recorded was already an authored stimulus
with a real agent typing it. codoc's half is still not authored: `derive` replays
the written frames into a live workspace and records what the daemon did.

## The planted misinterpretation

The agent reads "different documents need different rules" as licence to change
the default, and lowers the share so that the appendix header is caught too. That
does fix the document in front of it. It also means a real heading that repeats
across a long document is now removed before the heading rule ever sees it, so a
report loses its section titles.

That was the first draft of it, and measuring killed it. On the documents that
ship, lowering the share costs nothing: the threshold is `max(2, int(pages *
share))` and the floor of two wins for every one of them. Lowering the floor as
well does move it, and fails two of the project's own tests, which means the
participant is told by the test suite rather than by their own investigation.

So the misinterpretation is one level up, in what "configurable" was taken to
mean. The agent builds the settings, ships a config file whose default suits the
survey, and wires it into the command line — but the rule itself still reads its
module constant, so the PER DOCUMENT overrides the request actually asked for are
parsed, matched, and then ignored. The default works. Nothing else does.

It has the properties that matter:

- It is a defensible reading. The agent did make it configurable, and the one
  case in front of it does now convert correctly.
- The tests still pass, because they call the library directly and the library's
  defaults are untouched. Only the command line path reads the config.
- It is invisible by reading the diff and obvious the moment somebody gives one
  document its own setting, which is the natural next thing to try.
- codoc has a route to it that the other arm does not: the furniture rule's own
  description still says it reads a module constant, which contradicts the new
  feature's description of per document settings. Two descriptions that cannot
  both be true, sitting next to each other.

## What the participant needs in order to debug

A person cannot debug a program they cannot run, and cannot bisect a rule they
cannot see the effect of. The task page carries, and the workspace contains:

- the four fixtures, with one line saying what each is for, including
  `survey.txt`, which is the document the failure above happens to;
- the command that converts one and prints it, so a run is one paste;
- the command that converts all three and prints a summary line each, which is
  the cheapest way to see a rule change move something it should not have;
- the expected summary line for each fixture BEFORE the change, so a changed
  number is visible without remembering what it used to be.

That last one is the difference between "something feels off" and "the handbook
lost six headings".

## The staged replay

The recording is cut into segments with a checkpoint between them. At a checkpoint
the player stops, hands the workspace back so the daemon runs and the editor is
live, and waits. The participant does the thing the checkpoint is for. Then the
next segment plays.

```
segment 1   the agent reads the codebase          (terminal + codoc activity)
            ↓
checkpoint  the plan arrives as nodes in codoc
            the participant accepts or rejects
            ↓
segment 2   the agent implements                  (code lands, tests run)
            ↓
checkpoint  codoc surfaces the description changes as diffs
            the participant reviews, accepts, rejects, edits
            ↓
live        the participant runs it, finds the appendix case is fixed
            and the handbook has lost its headings, and works with a
            REAL agent from here
```

### Why the accept can stick

The obvious objection to interleaving is that a later frame overwrites what the
participant did. It does not, because of where the cut is.

The recording is made in the same shape: the agent proposes a plan, the plan is
ACCEPTED, and then it implements. Segment 2 was therefore recorded against a store
in which those nodes are live. A participant who accepts puts their store into the
state segment 2 expects, and playback continues consistently.

A participant who rejects has diverged, and that is the point at which the
recording stops being usable for them. That is not a failure case to suppress: it
is the most interesting thing a participant can do, and from there they work with
the live agent. The session records which way they went.

### What must be true of the recording

- It is made with the plan step, so the plan exists as nodes rather than as
  prose in the terminal.
- The terminal shows the reads and the edits. A participant who cannot see the
  agent working has nothing to compare codoc against.
- Loop A must not mint coverage proposals for files the tree already describes.
  Fixed in `loop_a._cover_uncovered_adds`; the previous recording carried four
  (`SETTINGS`, `Notes`, `Paragraphs`, `Text`), each one chunk in a file an
  existing feature already owned, each with an empty description, and a reviewer
  has nothing to answer in a proposal like that.

## Where the arms differ, and where they must not

The baseline arm gets the same five stages, the same request, the same recording,
the same fixtures and the same twenty minutes. What it does not get is the plan as
nodes or the description diff, because CLAUDE.md has neither. Its checkpoint is
the agent asking in the terminal and the participant answering there.

That difference IS the manipulation, so nothing else may differ: not the length of
the reading pages, not the number of steps to start, not the time.

## Decided, and built (2026-08-19)

- **The plan checkpoint is all correct.** No wrong node. The session keeps ONE
  planted problem — the repeat-share misinterpretation — so a participant's
  detection is attributable to it rather than split across two. It makes the plan
  stop closer to a click than to a review, and that is the trade taken.
- **Both stops are in.** `script/scribe/session.json` declares `checkpoints: [5,
  11]` and one `checkpoint_says` per stop: the plan, then what the build did to
  the descriptions. A single string still means "the same at every stop", which is
  what a one-stop recording wants.
- **The plan is proposed, not narrated.** A script step carries a `propose` list
  in `codoc propose`'s own vocabulary, made during `derive` because proposing
  needs a store the neutral workspace does not have. The three nodes land tagged
  `agent plan` — as against Loop A's `code drift` — which is the difference
  between a plan and its aftermath, visible in the tree without a legend.
- **The agent is visible while it works.** `derive` drives `codoc.agent.hook` with
  payloads built from each frame, so `.codoc/activity.json` is written by the code
  that writes it in production, and the player moves its timestamps onto the
  participant's clock. Without it every live surface — the avatar, the shimmer,
  the explorer mark — was dark for the whole replay.

Three things had to be fixed for any of it to work, and each was silent:

- The checkpoint never waited. `pending_proposals` read `by_event` from the top of
  the sidecar, where it has never been — it is nested under `proposals` — so it
  returned 0 whatever was outstanding and every stop passed straight through.
  The second stop also has nothing PROPOSED to answer, only rewrites to keep or
  restore, so it counts `auto_edits` as well.
- An open agent epoch is what tells `codoc watch` to stand down. Opening one for
  the presence and leaving it open suppressed every Loop A pass in the recording:
  twelve frames in which the description never once caught up with the code. A
  frame is now a TURN — opened, worked, ended — and what the frame carries is the
  working state, put back after the daemon has had its falling edge.
- A derive that produced no tree movement now FAILS rather than reporting it.

## Open

- Whether `tally` gets the same shape or a different misinterpretation. It should
  be the same shape, since the projects are matched, but its ambiguity has to be
  found in its own domain. `record-session.sh write tally` is ready for it; the
  script under `script/tally/` is not written yet.
