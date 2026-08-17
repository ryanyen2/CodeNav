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

Rated for **consistency with what tally already believes**, blind to condition.
Consistency, not correctness: there is no single right answer, only answers that
fit this codebase and answers that contradict it.

### 1. How a split is written in the CSV

| | |
| --- | --- |
| **2 — consistent** | Extra rows sharing a reference, or an extra column, recognised loosely the way `COLUMNS` recognises everything else. `rows.py` already assumes banks disagree and matches headers against a list of things they actually call each field. |
| **1 — defensible** | A fixed new column name, required exactly. Stricter than the rest of the reader, but not in conflict with it. |
| **0 — contradicts** | A separate file, or an argument on the command line. Every other fact about a transaction comes from its row, and a split held elsewhere could not survive re-exporting the statement. |

### 2. One transaction or two

| | |
| --- | --- |
| **2** | One. The person made one purchase, and `transactions` counts what happened rather than how it was recorded. |
| **1** | Two, said out loud, on the argument that each half is separately categorised. |
| **0** | Two, with the count left to fall out of the loop by accident. The number is in the output; nobody deciding on purpose would let it be decided by an implementation detail. |

### 3. Does the duplicate rule see the halves as duplicates

**A split of £40 into two £20 halves on one day at one merchant is exactly the
shape `dedupe.key` matches.**

| | |
| --- | --- |
| **2** | Exempted, the way transfers already are, and for the same stated reason. |
| **1** | Avoided by making the key finer, e.g. by including the reference. Works, and quietly changes duplicate detection for every ordinary transaction too. |
| **0** | Not considered. Half of every even split silently disappears. |

### 4. A half that matches no category rule

**This is the coupled one.** `categorise` sends anything unmatched to
`uncategorised`, and `summary` counts that bucket. A split where one half matches
and the other does not has to decide what the whole thing is.

| | |
| --- | --- |
| **2** | Each half categorised on its own, so the matched half lands correctly and only the other half is uncategorised. Consistent with the bucket being visible rather than fatal. |
| **1** | The whole transaction goes to `uncategorised`, said out loud as a choice. |
| **0** | The whole thing takes the first half's category, so money silently lands somewhere nobody chose. |

**Also recorded per decision:** who settled it — they decided, the agent proposed
and they accepted, or the agent did it and they never noticed.

## The quiz

Twelve questions, four options, one right. Asked before the task and again after,
so the change is the measure. Matched to scribe band for band.

**Every wrong option is something tally could defensibly have done and did not,
and the right answer is usually the less obvious of the two.** A question whose
right answer is simply the more sensible one is answerable without reading
anything. Check with:

```
python3 ../../scoring/check-description-answers.py --blind tally
```

### Purpose — what it is for, and where it stops

**Q1. Somebody moves the sign-flipping step so it runs last instead of first. What breaks?**
- a) Nothing: flipping signs is the same operation whenever it happens
- b) The totals come out positive instead of negative
- c) **Every rule that reads an amount has already read it the wrong way round** ✓
- d) Refunds stop netting, because a refund is recognised by its sign

**Q2. Somebody makes the transfer rule stricter, so fewer rows count as transfers. Which other rule starts behaving differently?**
- a) Categorisation, because transfers have no category
- b) Rounding, because the totals change
- c) **Duplicate removal, because transfers are the rows it is told to leave alone** ✓
- d) None: the two are applied to different columns

**Q3. Which piece of information does tally read from the bank's file and then never use again?**
- a) The amount
- b) **The posting date, once a transaction date has been found** ✓
- c) The merchant description
- d) The account name

**Q4. A summary cannot both match the bank's statement line by line and match what the person remembers doing. Which did tally choose?**
- a) The bank's statement, because that is the authoritative record
- b) **What the person remembers, because it is their own statement they are reading** ✓
- c) Neither: it reports both dates side by side
- d) The bank's, for dates, and the person's, for categories

### Rationale — which way it went, and why

**Q5. A transaction is made on the 31st of January and posted on the 2nd of February. Which month is it in?**
- a) February, the month the bank processed it
- b) **January, the month it was made** ✓
- c) Both, split across the boundary
- d) February, unless the summary for January has already been written

**Q6. The same merchant is charged £11.99 in each of three months. Is it recurring?**
- a) No: three months is not long enough to be sure
- b) No: only a payment with a reference the bank marks as a standing order counts
- c) **Yes: same merchant and same amount, in three months** ✓
- d) Yes, and it would be recurring at three different amounts too

**Q7. A row's merchant matches both the utilities rule and the fuel rule. What happens?**
- a) It is reported as ambiguous and the run stops
- b) **Whichever rule is listed first wins** ✓
- c) It goes to the uncategorised bucket, because the answer is unclear
- d) It is counted under both, and the total is adjusted

**Q8. A merchant matches no rule at all. What happens?**
- a) The row is dropped
- b) The run stops and asks
- c) **It is counted in a bucket of its own, which appears in the summary** ✓
- d) It is guessed at from the amount

### Change — what happened, and what it cost

**Q9. Transfers are exempted from duplicate removal. What would go wrong without that?**
- a) Every transfer would be counted twice in the spending
- b) The two legs would end up in different months
- c) **One leg of each transfer would be dropped, and the money would look like it went somewhere it did not** ✓
- d) Transfers would be categorised as spending

**Q10. Rounding happens once, at the total, rather than on each row. What does that cost?**
- a) Nothing: the two come to the same figure
- b) **A total that does not add up line by line against a printed statement** ✓
- c) Amounts under a penny are lost
- d) The recurring detection stops matching on amount

**Q11. A refund arrives in February for something bought in January. What do the two months show?**
- a) January shows nothing, February shows nothing: they cancel
- b) January shows the purchase reduced by the refund
- c) **January shows the purchase, February shows the money coming back** ✓
- d) Both show the purchase, and the refund is listed separately

### Extension — what a further change would need

**Q12. You want a refund to reduce the month the purchase was in. What is the obstacle?**
- a) The refund row does not record which purchase it is for
- b) Refunds are recognised by sign, so income would be reduced too
- c) **A summary for that month may already have been read, and there is no answer for what happens then** ✓
- d) The two months could be in different files

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

tally is about a fifth smaller. The policy count, the file count and the shape of
the task match, which are the things the design depends on; the size gap is
recorded here rather than smoothed over, and is small enough that project order
should absorb it. If a pilot shows otherwise, tally is the one to grow.
