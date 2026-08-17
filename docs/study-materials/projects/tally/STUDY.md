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

Twelve questions, four options, one right. Asked before the task and again after,
so the change is the measure. Matched to scribe band for band and level for
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

**Q1. (easy) Jane wants tally to tell her whether she can afford a holiday. Can it?**
- a) Yes, from the recurring payments and the monthly totals
- b) Yes, if she gives it a target to save towards
- c) **No: it reports what was spent and has no opinion beyond that** ✓
- d) No, but it flags the months where spending rose

**Q2. (medium) Raj makes the transfer rule stricter, so fewer rows count as transfers. Which other rule starts behaving differently?**
- a) Categorisation, because transfers have no category
- b) Rounding, because the totals change
- c) **Duplicate removal, because transfers are the rows it was told to leave alone** ✓
- d) Nothing else: the two are applied to different columns

**Q3. (hard) Raj moves the sign-flipping step so it runs last instead of first. What breaks?**
- a) Nothing: flipping signs is the same operation whenever it happens
- b) The totals come out positive instead of negative
- c) **Every rule that reads an amount has already read it the wrong way round** ✓
- d) Refunds stop netting, because a refund is recognised by its sign

### Rationale: which way it went, and why

**Q4. (easy) A merchant matches no category rule at all. What happens to that transaction?**
- a) The row is dropped
- b) The run stops and asks
- c) **It is counted in a bucket of its own, which appears in the summary** ✓
- d) It is guessed at from the amount

**Q5. (medium) A payment is made on the 31st of January and posted by the bank on the 2nd of February. Which month does tally put it in?**
- a) February, the month the bank processed it
- b) **January, the month it was made** ✓
- c) Both, split across the boundary
- d) February, unless January's summary has already been written

**Q6. (hard) A row's merchant matches both the utilities rule and the fuel rule. What happens?**
- a) It is reported as ambiguous and the run stops
- b) **Whichever rule is listed first wins** ✓
- c) It goes to the uncategorised bucket, because the answer is unclear
- d) It is counted under both, and the total is adjusted

### Change: what happened, and what it cost

**Q7. (easy) The same merchant charges £11.99 in each of three months. Does tally call that recurring?**
- a) No: three months is not long enough to be sure
- b) No: only a payment the bank marks as a standing order counts
- c) **Yes: same merchant, same amount, three months** ✓
- d) Yes, and it would be recurring at three different amounts too

**Q8. (medium) Transfers are exempted from duplicate removal. What would go wrong without that exemption?**
- a) Every transfer would be counted twice in the spending
- b) The two legs would end up in different months
- c) **One leg of each transfer would be dropped, and the money would look like it went somewhere it did not** ✓
- d) Transfers would be categorised as spending

**Q9. (hard) Rounding happens once, at the total, rather than on each row. What does that cost?**
- a) Nothing: the two come to the same figure
- b) **A total that does not add up line by line against a printed statement** ✓
- c) Amounts under a penny are lost
- d) The recurring detection stops matching on amount

### Extension: what a further change would need

**Q10. (easy) Jane's bank exports a column tally does not recognise. What does she change?**
- a) The CSV itself, to rename the column
- b) **The list of names in `rows.py` that each field is matched against** ✓
- c) `summary.py`, where the pipeline runs
- d) Nothing: an unknown column is worked out from what is in it

**Q11. (medium) Adding `shell energy` to the category rules means deciding one thing beyond the pattern itself. What?**
- a) Which month it starts applying from
- b) **Where in the list it goes, because the first rule that matches wins** ✓
- c) Whether it counts as a recurring payment
- d) What to do when the amount is positive

**Q12. (hard) Jane wants a refund to reduce the month the purchase was in. What stands in the way?**
- a) The refund row does not record which purchase it is for
- b) Refunds are recognised by sign, so income would be reduced too
- c) **A summary for that month may already have been read, and there is no answer for what happens then** ✓
- d) The two months could be in different files

## The after-task questions

Six questions, four options, one right, asked straight after the task with the
code, the description and the agent CLOSED. Matched to scribe's set one for one,
band for band and level for level. The reasoning for both is written out once, in
`../scribe/STUDY.md`.

### Purpose: what your change actually does

**Q1. (easy) Your change lets one purchase be split across two categories. Where does a split have to be written down?**
- a) In a separate file the run is pointed at
- b) **In the transaction's own row or rows, because that is where every other fact about it lives** ✓
- c) On the command line, as an argument
- d) In the summary, after the fact

**Q2. (medium) A transaction that is now split in two. Which existing rule is most likely to treat it differently than before?**
- a) The month it is counted in
- b) Whether it is recognised as recurring
- c) **Duplicate removal, because two equal halves on one day at one merchant is the shape it matches** ✓
- d) The sign convention applied to its amount

### Rationale: why that way and not the other

**Q3. (medium) You decided whether a split counts as one transaction or two. What makes the count a decision rather than an implementation detail?**
- a) It changes how the rows are stored
- b) It changes which month the halves land in
- c) **The number is in the summary the person reads, so a loop deciding it by accident still publishes it** ✓
- d) It changes whether the duplicate rule fires

**Q4. (hard) `categorise` sends anything unmatched to `uncategorised`, and `summary` counts that bucket. For a split where one half matches a rule and the other does not, what does that mean?**
- a) The whole transaction is dropped, because it is ambiguous
- b) The whole transaction goes to `uncategorised`, because any doubt sends it there
- c) **Each half can be categorised on its own, so only the unmatched half lands in the bucket** ✓
- d) The run stops and asks which category to use

### Change: what it cost, and what it touched

**Q5. (hard) Suppose you had made the duplicate key finer — adding the reference — so the halves stopped matching. What else would that have changed?**
- a) Nothing: the key is only used for splits
- b) Transfers would stop being exempted
- c) **Duplicate detection would loosen for every ordinary transaction, not just for splits** ✓
- d) The months would be recomputed

### Extension: what a next person needs

**Q6. (medium) Somebody adds a new category rule tomorrow. What do they have to decide that they would not have to in a codebase of independent rules?**
- a) Which file to put it in
- b) Whether it applies to refunds
- c) **Where in `categories.RULES` it goes, because the first rule that matches wins** ✓
- d) Whether to write a fixture for it

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
