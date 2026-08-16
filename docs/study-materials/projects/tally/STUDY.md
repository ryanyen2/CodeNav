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

Twelve questions, four options, one right. Closed book, before the task and again
after. Wrong options are plausible to somebody who read the code and never learned
why it is that way.

### Purpose

**Q1. What is tally for?**
- a) Connecting to a bank and downloading transactions
- b) **Turning a CSV of transactions into a summary of what was spent, by month and category** ✓
- c) Checking a statement for fraud
- d) Preparing a tax return

**Q2. What does tally assume about its input?**
- a) It comes from one particular bank
- b) It is already sorted by date
- c) **It is a CSV whose columns may be named any of several ways** ✓
- d) It has been checked for errors first

**Q3. Which of these is out of scope?**
- a) Deciding which month a transaction belongs to
- b) Spotting a payment that comes round every month
- c) Leaving out money moved between your own accounts
- d) **Telling you whether you can afford something** ✓

**Q4. Who is the output for?**
- a) An accountant reconciling against the bank
- b) **The person whose statement it is, asking what they spent** ✓
- c) A budgeting app that will import it
- d) A tax authority

### Rationale

**Q5. Why does the first matching category rule win, rather than requiring exactly one?**
- a) It is faster
- b) Banks guarantee only one will match
- c) **Requiring one would stop the whole summary over a single ambiguous merchant** ✓
- d) The rules are guaranteed not to overlap

**Q6. Why is a transaction filed under the date it was made rather than posted?**
- a) The posting date is often missing
- b) **A card payment on the 31st can post on the 2nd, and the summary should match what the person remembers doing** ✓
- c) It is what the bank's statement does
- d) Posting dates are unreliable across banks

**Q7. Why does recurring detection need the amount to match, not just the merchant?**
- a) Merchant names change between statements
- b) **Merchant alone calls a supermarket recurring, which is true and useless** ✓
- c) Amounts are easier to compare than text
- d) To avoid matching refunds

**Q8. Why does rounding happen at the total rather than on each row?**
- a) It is faster
- b) Decimals cannot be rounded twice
- c) **A hundred small transactions would accumulate a hundred small errors** ✓
- d) The bank rounds that way

### Change

**Q9. Why does `drop_duplicates` treat transfers differently?**
- a) Transfers are not real spending
- b) **A transfer between your own accounts is two rows that look exactly like a duplicate** ✓
- c) Transfers have no category
- d) Banks export them twice by mistake

**Q10. Why does `sign_convention` guess rather than ask?**
- a) Asking is impossible in a command line tool
- b) The guess is always right
- c) **The tool is for one person's own statements, where the convention never changes** ✓
- d) The bank does not say which way round it is

**Q11. Why does `COLUMNS` list "transaction date" before "date"?**
- a) It is alphabetical
- b) **A bank exporting both would otherwise give the posting date and shift every month-end transaction** ✓
- c) "date" is a reserved word
- d) The order does not matter; it is arbitrary

### Extension

**Q12. To make a refund reduce the month the purchase was in, rather than the month the refund arrived, what has to be decided first?**
- a) Which category the refund belongs to
- b) Whether refunds should be positive or negative
- c) **What happens when the refund arrives after that month's summary has already been read** ✓
- d) Whether to store the original purchase's date

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
