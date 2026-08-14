# Questions for the ember session

The match to `questions-hearth.md`. Same ten questions in the same order, asked
the same way, about the other project. Read "How to ask them" in that file first.

The short version: ask each question twice, first with everything closed, then
again with the written description open and the code still closed. Record both
answers and how confident they felt, from 1 to 5. Read each question exactly as
written, allow yourself one "can you say more?", and score 0, 1, or 2 from the
table under it.

## Round one, after they explore

### 1. What a second digest run does  [F1]

Asked again in round two.

> You run `ember digest`, change nothing, and run it again. What does the second
> run do?

| Score | The answer says |
| --- | --- |
| 2 | Both halves. Each day's page is skipped because a signature over the items that day chose has not changed, and the archive is skipped because it has a separate signature of its own over every stored item. |
| 1 | It skips the days it has already written, without the second, separate signature for the archive. |
| 0 | It writes everything again, or they do not know. |

### 2. The path one feed entry takes  [S1]

Asked again in round two.

> Walk me through the stages a single feed entry passes through, from XML on disk
> to a line on a digest page.

| Score | The answer says |
| --- | --- |
| 2 | Reading the source, parsing it, tidying it into a stored item, saving it without duplicating what is already there, choosing it for a day, filling in the template, writing the page. It also says the archive is built in a separate pass. |
| 1 | Four or more of those stages in the right order, but the store or the separate archive pass is missing. |
| 0 | Fewer stages than that, or in the wrong order. |

### 3. Why the archive has its own signature  [R1]

Answerable only from the written description, not from the code.

> The archive has a signature of its own rather than sharing the digests'. Why was
> it built that way? What alternative was rejected?

| Score | The answer says |
| --- | --- |
| 2 | One shared signature was rejected. The archive has to be rewritten whenever the whole collection moves, and a day's page only when that day would come out different, so a shared signature made each of them rebuild for the other's reasons. |
| 1 | A plausible reason of their own, such as "it is simpler", with no rejected alternative. |
| 0 | Nothing, or something wrong. |

### 4. When a day's page is rewritten  [R2]

Also answerable only from the written description. This is the same rule the task
will trip over, so compare this answer with question 7 later.

> A day's digest page is not rewritten on every run. How does ember decide when it
> must be, and why was it designed that way?

| Score | The answer says |
| --- | --- |
| 2 | A signature over the fields of the items that day selected, taken from the assembled selection, chosen instead of tracking what each day depends on, because that kept going subtly wrong once an item's date moved it to a different day. |
| 1 | The signature, with no mention of what was rejected. |
| 0 | File timestamps, or a guess. |

### 5. Adding a second output format  [E1]

> To add a second output format, say a JSON file per day alongside the HTML page,
> which modules would change, and which would you leave alone?

| Score | The answer says |
| --- | --- |
| 2 | The step that writes the digest, and possibly the renderer. Fetching, parsing, tidying, storing and the templates are left alone. It also mentions that the new file has to be tracked, or a skipped run will leave it missing. |
| 1 | The right modules, without that last point. |
| 0 | Changes scattered across unrelated parts. |

### 6. The hand-written template engine  [D1]

We are scoring whether they take a position and ground it. Agreeing with the
original decision is a full-marks answer if they say why.

> The template engine is written by hand instead of using a library. Would you
> have made the same call? Why or why not?

| Score | The answer says |
| --- | --- |
| 2 | A position with a tradeoff behind it, such as the cost of a dependency against the cost of writing and fixing it yourself. The recorded reason, that ember is meant to run anywhere Python runs, counts. |
| 1 | A position with nothing behind it. |
| 0 | No position either way. |

## Round two, after the task

Ask questions 1 and 2 again first, in the same words, then these four.

### 7. What their own change rewrites  [F2]

> After your change: someone mutes a feed and runs the digest. Walk me through
> exactly what gets rewritten and why.

| Score | The answer says |
| --- | --- |
| 2 | The mute reaches the point where items are chosen, so the signature moves and every day's page holding that feed is rewritten. An accurate description of their own build scores 2 here even when that build is broken, including saying the pages go stale. Whether the code is right is scored separately. |
| 1 | "The digest is regenerated", with no mention of the signature. |
| 0 | A description that does not match what their build does. |

### 8. Muted feeds and the log  [R3]

The task card says nothing about the notification log, so this is where we find
out whether they made a decision or inherited one.

> Muted feeds and the notification log: what does your build do now, and why that
> way?

| Score | The answer says |
| --- | --- |
| 2 | What it does, plus a reason of their own, such as the log being a record of what arrived rather than of what was shown, or muting being meant to silence everything. Any real ground counts, whichever way they went. |
| 1 | What it does, with "the agent did it that way" as the reason. |
| 0 | They do not know what their own build does. |

### 9. Snoozed feeds  [E2]

> Next month the team wants snoozed feeds, muted until a date and then back to
> normal on their own. Given what you built today, what changes and where?

| Score | The answer says |
| --- | --- |
| 2 | They extend whatever they built for muting, adding a second condition in the same place, and they notice the catch: a date-based rule changes the right answer without anyone editing a file, so the digest has to be run again and the date has to reach the signature. |
| 1 | The right place, without the catch. |
| 0 | A new filter somewhere else. |

### 10. Arguing the other side  [D2]

> You put the mute decision where you did. Argue for the opposite placement. What
> would break, and would anything get better?

| Score | The answer says |
| --- | --- |
| 2 | Both sides of the real tension. Deciding where items are chosen means the signature sees it, but every caller has to know which feeds are muted. Deciding in the renderer is simpler and local, but the signature never sees it, so the pages are never rewritten. |
| 1 | One side only. |
| 0 | They do not engage with it. |

## Notes for whoever analyses this

- Questions 3 and 4 ask about decisions the participant inherited from the written
  description. Questions 8 and 10 ask about decisions they made themselves. Keep
  the two kinds apart when reporting.
- Every participant gets the same questions in the same order, in both conditions.
- Evidence that the task actually trips people up in the way these questions assume
  is in the design doc, section 6.2, not here.
