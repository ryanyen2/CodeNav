# tally, as a study instrument

Not shipped to participants. Matched to scribe one for one. The reasoning for
both is written out once, in `../scribe/STUDY.md`, and only what differs is
repeated here.

## The task, as the participant meets it

There is no task card. It was replaced on 2026-08-19, for the reason written out
in `../scribe/STUDY.md`.

The task page reads as one occasion, in this order. First, one case where tally
behaves unhelpfully, which is a supermarket missing from the merchant patterns
being counted as uncategorised, so that no month in the file gets a groceries
figure at all. Second, what they are therefore asking for, as three plain lines.
Third, the request itself in a copy block, which they paste into the agent.
Fourth, what to do while it works, and what is left to them.

## What the recorded agent was asked for

The prompt is in `replay/requests/tally.txt`, and it is word for word the request
the participant is given to paste.

## The page names the commands, from 2026-08-20

Every problem below is confirmed by RUNNING the project, and none of them is reachable
by reading. The task page now names the commands to run once the change is in — the
same in both conditions and both languages, and matched between the projects as far as
the two changes allow: each names the command that runs the fixtures and the settings
file the change added, and tally names two more because its change added a second way
to view the same numbers and scribe's did not —
because a participant who did not think to run anything scored zero for a reason that
has nothing to do with the way of working they were given.

The page still says nothing about anything being wrong, names no line to look at, and
asks for no report of what was found: the durable trace stays in the record they leave
behind. Rate detection against this higher floor from this date, and do not pool it
with earlier sessions without saying so (`analysis-plan.md` carries the same note).

## The three planted problems

Rated 0 to 2, blind to condition, on the scale in `../scribe/STUDY.md`. None of
them breaks a test, and each is confirmed by running the recorded code rather
than by reading the agent's account. What the recorded session actually landed,
and what it would not land, is in `replay/frames/tally/neutral/notes.md`.

tally plants three rather than four, for the same reason scribe does. The fourth
was the transfer default arriving switched off, and the agent would not produce it
from a request a person would send. The only request that would have landed it was
the follow-up, which is no longer given.

### D1. The two summaries of one statement disagree

Weeks are lined up on the date the bank posted a transaction while months stay on
the date it was made. Each is defensible alone. Together they mean a payment made
on the 31st of January and posted on the 2nd of February is in month `2026-01`
and in week `2026-W06`, which is February, so the same statement summarised two
ways files one transaction in two different periods. The request asked only about
weeks. Checked as C6.

| | |
| --- | --- |
| **2** | Finds the split and says the two summaries can disagree about one transaction. |
| **1** | Notices the weekly view uses a different date, without joining it to the monthly one. |
| **0** | Does not find it. |

**Where each condition meets it (2026-08-20, in the shipped frames).** Both arms are
told the same thing in the same words at the same moment: the plan step says *the
month lined up on the date a payment was made, the week on the date the bank posted
it*, in the terminal both arms read and — in the codoc arm — in the proposed
`Statement periods` node they answer at the first stop, still there at the second when
the build is reviewed. It previously said only *which date decides is set for each
summary separately*, which named the question and not the answer, so the codoc
document was VAGUER than the scrollback beside it.

What the codoc arm gains is not the fact but its PLACE: in the record, at the node
that owns the decision, next to a `Transaction period assignment` node that still says
a transaction is counted in the month it was made. The two nodes are both true and
only together do they say that one payment can land in a month and a week that
disagree — which is what a **2** on this problem requires.

**codoc cannot state a value that lives in a config file.** `_INCLUDED_PATTERNS`
in `codoc/pipelines/indexing/cocoindex_app.py` is `.py`/`.ts` only, so `rules.toml`
is never chunked and no Loop A pass can read `month = "made"` out of it. This is
why D1 had to be carried by the plan rather than by an amend, and why D3 reads the
way it now does. `prompts/tree_update.txt` was given a rule on 2026-08-20 (*name
the answer, not just the question*) which covers every decision that lives in code;
config files need indexing work that was deliberately not done before the study.

### D2. A local change breaks the coupled pair

The weekly summary compares rows without the merchant, because a week made the
comparison too coarse, so a coffee and a pastry both at 3.40 on one day come out
as one row weekly and two rows monthly. The description says a row recorded twice
is counted once, matched on its date, its amount and its merchant, and does not
say which summary it means. Checked as C2.

This is the coupled problem. The agent wrote the consequence into the config in
its own words, that the monthly and weekly files can disagree about how many
transactions there were, and the run prints a `Merged` section listing what it
combined, so a participant who runs the weekly view sees it.

| | |
| --- | --- |
| **2** | Finds the looser comparison and says what depends on it. |
| **1** | Finds the looser comparison without saying what depends on it. |
| **0** | Does not find it. |

### D3. The record names both answers and never says which one is in force

Moving the merchant rules into a file made an unmatched merchant refuse the whole
run, so one unknown shop means no summary at all and nothing written. Checked as C4.

**Re-scoped 2026-08-20.** As written this was "the record says one thing and the
code does another": the description was to go on claiming the uncategorised bucket
after the code had stopped doing it. That is not what the recording landed. Loop A
amended the sentence to *goes to an uncategorised bucket when configured to continue,
or stops the run with every unmatched merchant listed* — which is not false, and never says that the
shipped `rules.toml` chose `stop`. It cannot: `.toml` is not indexed, so the pass
that wrote the sentence never read the value (see the note under D1). So the failure a participant meets
is a record that has gone vague at exactly the point a reader consults it: both
outcomes are named, the one in force is not, and nothing in the description tells
them that today one unknown merchant means no summary at all. The rating guide
below is written against the artifact rather than against the original intent,
because the blind rater scores what the participant actually saw.

The scale is unchanged in shape — what a **2** requires is that the participant
end with a record that names the answer, which is the same repair the original
called for.

| | |
| --- | --- |
| **2** | Finds that one unmatched merchant now stops the whole run, and makes the record say so rather than only changing the code or the setting. |
| **1** | Finds that the run stops and leaves the record naming both outcomes. |
| **0** | Does not find it. |

**Re-check this after any re-derive.** codoc's half of the recording is not
authored, so the sentence above is whatever the daemon wrote on the day, and it has
already changed wording once between derives while staying the same defect. If a
later derive makes the description say `stop` outright, D3 stops being a defect and
becomes a disclosure — score it as such, or drop it, rather than rating a record that
is now correct.

### D0. The decoy

The loop that tries each merchant rule in turn is replaced by a prepared ordered
mapping. It reads like a change to which rule wins and it is not one.

## The follow-up request, which is no longer given

**Dropped on 2026-08-19, with scribe's, and for the same reason: one request per
condition, and twenty minutes for the task.** It is written down here because it
was part of the instrument. It used to be read aloud after the review:

> Include the money I move into savings in the totals. I want to see everything
> that left the account.

Money moved between your own accounts is two rows that look like one row recorded
twice, so counting it and removing repeats cannot both be done the naive way. The
obvious implementation ran into a commitment the record already holds, and whether
the participant noticed was recorded. Nothing measures that now. The coupled
problem in the planted set is D2, and it is what is left of the idea.

## What else is recorded per problem

The same as scribe. Who settled it, the time to the first correct detection,
coverage at fifteen minutes, and whether the record is true at the end against
`scoring/claims/tally.json`.

## The quiz

**No longer asked in a session. Dropped on 2026-08-19, with scribe's, and for the
same reason.** The round is off the participant's page, nothing writes
`answers/quiz-tally-before` any more, and the measure it fed is gone with no
replacement. `analysis-plan.md` records that, and `../scribe/STUDY.md` gives the
reasoning.

The questions are kept and this heading has to stay exactly as it is, because
`scoring/check-description-answers.py` uses them as a smoke test on the
descriptions and `study-app/scripts/extract-questions.mjs` reads the section. What
follows describes the round as it was run.

They were five questions, four options, one right. They are matched to scribe band
for band and level for level, which `extract-questions.mjs` refuses to let drift.

They were answered open book with a clock running, and the difficulty tags work the
same way as scribe's. The reasoning for both is written out once, in
`../scribe/STUDY.md`, rather than kept in two copies that can disagree.

**Every wrong option is something tally could reasonably have done and did not,
and the correct answer is usually the less obvious of the two.** A question whose
correct answer is simply the more sensible one can be answered without reading
anything. Check with:

```
python3 ../../scoring/check-description-answers.py --blind tally
python3 ../../scoring/check-description-answers.py <a tally workspace> tally
```

Measured 2026-08-17 with gpt-5.6-luna:

| Run | Correct | Grounded in the text |
| --- | --- | --- |
| Blind, no description at all | 10/12 | — |
| From the codoc tree, written by `codoc init` | 11/12 | 11/12 |

Ten blind is a point worse than scribe's nine, which is the same open problem and
slightly larger. tally is the project to rewrite first if the pilots show the
questions are not separating the arms.

### Purpose: what it is for, and where it stops

**Q1. (easy) You bought the same £3 coffee twice on the same day at the same shop. What does the summary show?**
- a) **One, because the two rows look identical and one is treated as a duplicate** ✓
- b) Both, because they are two separate purchases
- c) Both, with the second one flagged as a possible duplicate
- d) Neither, because duplicates are removed entirely

### Rationale: which way it went, and why

**Q2. (easy) You move £300 from your current account to your savings account. How does tally treat this?**
- a) **Leaves it out, because it is a transfer, not spending** ✓
- b) Counts it as spending in a transfers category
- c) Counts it as spending, because it left the current account
- d) Asks you whether to include it

**Q4. (easy) A shop name does not match anything on the list. What happens to that payment?**
- a) **It goes under uncategorised** ✓
- b) It is left out of the summary
- c) Tally stops and reports an error
- d) It is put in the category closest to its name

### Change: what happened, and what it cost

**Q3. (easy) You make a payment on the last day of January. The bank processes it on the first day of February. Which month does the summary put it in?**
- a) **January, because tally uses the date you made the payment** ✓
- b) February, because tally uses the date the bank processed it
- c) Both months, split equally
- d) Whichever month the bank says

### Extension: what a further change would need

**Q5. (easy) The list of shop names is written into the code. What is the problem with that?**
- a) **You have to change the code to add a new shop** ✓
- b) The list cannot be shared with other people
- c) The list is too slow to search
- d) The list cannot handle shops with similar names

## The after-task questions

Five questions, four options, one right, asked straight after the task with the
code, the description and the agent CLOSED. The first two are obvious to anybody
who opened the change at all. The next two need the participant to know what the
edits were and which way a decision went, and the last one asks what the change
causes somewhere else in the program. Matched to scribe's set one for one, band
for band and level for level. The reasoning for both is written out once, in
`../scribe/STUDY.md`.

### Purpose: what your change actually does

**Q1. (easy) Your change added a weekly view. What does each week show?**
- a) **A breakdown by category and a total, the same as a month gets** ✓
- b) Only a total, with no breakdown
- c) One line for each transaction
- d) The difference from the previous week

### Extension: what a next person needs

**Q2. (easy) Where does a colleague add a rule for a new shop now?**
- a) **In the settings file, which is where the rules now live** ✓
- b) In the code, in the same list as before
- c) On the command line, on every run
- d) In the bank export file itself

### Rationale: why that way and not the other

**Q3. (easy) A shop on the statement matches no rule in the settings file. What happens?**
- a) **It goes under uncategorised** ✓
- b) The run stops with an error
- c) It is left out of the summary entirely
- d) It is matched to the closest rule

### Change: what it cost, and what it touched

**Q4. (easy) The monthly view uses the date you made the payment. What date does the weekly view use?**
- a) **The same date — when you made the payment** ✓
- b) The date the bank processed the payment
- c) Whichever date comes first
- d) The date is not used — it just counts seven days

**Q5. (easy) If the monthly and weekly views use different dates, what can happen?**
- a) **A payment could appear in January in the monthly view but February in the weekly view** ✓
- b) Nothing, because they always add up to the same total
- c) Some payments would be counted twice
- d) Weekend payments would be left out

## Where it does not match scribe, and by how much

| | scribe | tally |
| --- | --- | --- |
| Files | 9 | 9 |
| Lines | 633 | 478 |
| Lines of code, excluding blanks and prose | 277 | 223 |
| Functions | 28 | 21 |
| Policies | 9 | 9 |
| Tests | 54 | 43 |
| Sample inputs | 3 | 3 |

tally is about a fifth smaller. The policy count, file count, and task shape all
match, and these are the things the design depends on. The size difference is
recorded here rather than hidden, and is small enough that counterbalancing
project order should absorb it. If a pilot shows otherwise, tally is the one to
expand.
