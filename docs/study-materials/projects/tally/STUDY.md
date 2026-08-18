# tally, as a study instrument

Not shipped to participants. Matched to `scribe/STUDY.md` in shape: one task, four
open decisions rated 0–2, one coupled pair, twelve quiz questions in four bands.

## The task card

> **Support split transactions.**
>
> One purchase sometimes belongs in two categories: a supermarket trip that was
> half groceries and half a birthday present. Let a transaction be split.
>
> Decide anything this card does not specify, and be ready to explain your
> decisions.

## The four open decisions

Rated for **consistency with what tally already does**, blind to condition. The
rating is about consistency, not correctness. None of these has a single right
answer. There are only answers that fit the codebase and answers that contradict
it.

### 1. How a split is written in the CSV

| | |
| --- | --- |
| **2, consistent** | Extra rows sharing a reference, or an extra column, recognised loosely the way `COLUMNS` recognises everything else. `rows.py` already assumes banks disagree and matches headers against a list of things they actually call each field. |
| **1, defensible** | A fixed new column name, required exactly. Stricter than the rest of the reader, but not in conflict with it. |
| **0, contradicts** | A separate file, or an argument on the command line. Every other fact about a transaction comes from its row, and a split held elsewhere could not survive re-exporting the statement. |

### 2. One transaction or two

| | |
| --- | --- |
| **2** | One. The person made one purchase, and `transactions` counts what happened rather than how it was recorded. |
| **1** | Two, said out loud, on the argument that each half is separately categorised. |
| **0** | Two, with the count left to fall out of the loop by accident. The number is in the output; nobody deciding on purpose would let it be decided by an implementation detail. |

### 3. Does the duplicate rule see the halves as duplicates

**A split of £40 into two £20 halves on one day at one merchant has exactly the
same shape as what `dedupe.key` matches.**

| | |
| --- | --- |
| **2** | Exempted, the way transfers already are, and for the same stated reason. |
| **1** | Avoided by making the key finer, e.g. by including the reference. Works, and quietly changes duplicate detection for every ordinary transaction too. |
| **0** | Not considered. Half of every even split silently disappears. |

### 4. A half that matches no category rule

**The coupled decision.** `categorise` sends anything unmatched to
`uncategorised`, and `summary` counts that bucket. A split where one half matches
a rule and the other does not has to decide what happens to the whole
transaction.

| | |
| --- | --- |
| **2** | Each half categorised on its own, so the matched half lands correctly and only the other half is uncategorised. Consistent with the bucket being visible rather than fatal. |
| **1** | The whole transaction goes to `uncategorised`, said out loud as a choice. |
| **0** | The whole thing takes the first half's category, so money silently lands somewhere nobody chose. |

**Also recorded per decision:** who settled it. The three possibilities are: they
decided, the agent proposed and they accepted, or the agent did it and they never
noticed.

## The quiz

Five questions, four options, one right, asked before the task. Matched to scribe band for band and level for
level, which `extract-questions.mjs` refuses to let drift.

Answered open book with a clock running, and the difficulty tags work the same
way as scribe's. The reasoning for both is written out once, in
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

**Q1. (easy) You really did buy the same £3 coffee twice on the same day at the same shop. What does the summary show?**
- a) Both, because they are two separate purchases
- b) **One, because nothing in the row tells a real repeat from a repeated row** ✓
- c) Both, with the second marked as a possible duplicate
- d) It stops and asks which one to keep

### Rationale: which way it went, and why

**Q2. (medium) A row whose description mentions a transfer is left out of duplicate removal. Why?**
- a) Because a transfer is not spending, so it never reaches the totals anyway
- b) **Because the two legs of a move between your own accounts look exactly like a duplicate** ✓
- c) Because the two legs arrive on different dates and would never collide
- d) Because the bank marks transfers already, so the rule is not needed

**Q4. (hard) A bank export lists every amount as a positive number, spending included. What does tally do?**
- a) Refuses the file, because the direction cannot be known from it
- b) Leaves the amounts alone and reads the direction from a separate column
- c) **Takes the file's own shape as the convention and flips every sign** ✓
- d) Treats the largest amounts as spending and the rest as money coming in

### Change: what happened, and what it cost

**Q3. (medium) Amounts are rounded once at the summary rather than on every transaction. What does that give up?**
- a) Speed, because every exact amount has to be carried until the end
- b) **Agreeing line by line with a printed receipt** ✓
- c) Being able to show the totals in another currency
- d) Accuracy, because many small amounts drift further apart this way

### Extension: what a further change would need

**Q5. (hard) You add a rule for one coffee shop, but a broader "cafe" rule already matches it. What decides which one applies?**
- a) The more specific pattern wins, whichever order they are in
- b) **Where it sits in the list, because the first pattern that matches wins** ✓
- c) Both apply, and the amount is split between them
- d) Neither: two matching rules send it to the uncategorised bucket

## The after-task questions

Five questions, four options, one right, asked straight after the task with the
code, the description and the agent CLOSED. Matched to scribe's set one for one,
band for band and level for level. The reasoning for both is written out once, in
`../scribe/STUDY.md`.

### Purpose: what your change actually does

**Q1. (easy) Your change lets one purchase be split across two categories. Where does a split have to be written down?**
- a) In a separate file the run is pointed at
- b) **In the transaction's own row or rows, because that is where every other fact about it lives** ✓
- c) On the command line, as an argument
- d) In the summary, after the fact

### Rationale: why that way and not the other

**Q2. (medium) A purchase that your change splits in two. Which existing rule most likely treats it differently now?**
- a) The month it is counted in
- b) Whether it is recognised as a recurring payment
- c) **Duplicate removal, because two equal halves on one day at one merchant is exactly the shape it matches** ✓
- d) The sign convention applied to its amount

**Q4. (hard) After your change, one half of a split matches a category rule and the other half matches none. What happens?**
- a) The whole transaction is dropped, because it is ambiguous
- b) The whole transaction goes to the uncategorised bucket, because any doubt sends it there
- c) **Each half is categorised on its own, so only the unmatched half lands in that bucket** ✓
- d) The run stops and asks which category to use

### Change: what it cost, and what it touched

**Q3. (medium) Your change decides whether a split counts as one transaction or two. Why is that a decision rather than a detail?**
- a) It changes how the rows are stored on disk
- b) It changes which month the halves land in
- c) **The number of transactions is in the summary somebody reads, so a loop that settles it by accident still publishes it** ✓
- d) It changes whether the duplicate rule fires

### Extension: what a next person needs

**Q5. (hard) Suppose you had made the duplicate check finer — adding a reference — so the two halves stopped matching. What else would that have changed?**
- a) Nothing: that check is only used for splits
- b) Transfers would stop being left out
- c) **Duplicate detection would loosen for every ordinary transaction, not only for splits** ✓
- d) The months would all be recomputed

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
