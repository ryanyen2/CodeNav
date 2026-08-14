# Questions for the hearth session

Ten questions in two rounds. Round one comes after the participant has explored
the project and before they start the task. Round two comes after the task.

## How to ask them

Ask each question twice. First with the code and the written description both
closed, and write down the answer along with how confident they feel, from 1 to 5.
Then ask the same question again with the description open and the code still
closed, and write down what changed. What changed between the two answers is the
result we are after, so record both even when the second answer is the same.

Read each question exactly as written. If an answer stops short you may say "can
you say more?" once. Say nothing else. Do not hint, agree, correct, or fill a
silence.

Score each answer 0, 1, or 2 using the table under it. Two of the questions are
asked again in round two, so ask those in the same words both times.

The tables are fixed before the first session and do not change once the study has
started. The codes in brackets are for the analysis and are not read out.

## Round one, after they explore

### 1. What a second build does  [F1]

Asked again in round two.

> You run `hearth build`, change nothing, and run it again. What does the second
> run do?

| Score | The answer says |
| --- | --- |
| 2 | Both halves. Pages whose content has not changed are skipped, and the home, tag and feed pages are skipped as well, because a signature taken over the list of posts has not changed. |
| 1 | It skips work it has already done, without the second half about the listing pages. |
| 0 | It builds everything again, or they do not know. |

### 2. The path one post takes  [S1]

Asked again in round two.

> Walk me through the stages a single post passes through, from a file on disk to
> HTML in `_site`.

| Score | The answer says |
| --- | --- |
| 2 | Finding the file, reading its settings block, turning the markdown into HTML, working out its title and address, filling in the template, writing it out. It also says the listing pages are built in a separate pass. |
| 1 | Four or more of those stages in the right order, but the separate pass for the listing pages is missing. |
| 0 | Fewer stages than that, or in the wrong order. |

### 3. Why the dev server works the way it does  [R1]

This one is answerable only from the written description, not from the code.

> Why does the dev server serve files from the build output instead of rendering
> pages on request? What alternative was rejected?

| Score | The answer says |
| --- | --- |
| 2 | Rendering each page as it is requested was rejected, so that the preview and the published site can never disagree. |
| 1 | A plausible reason of their own, such as "it is simpler" or "it is faster", with no rejected alternative. |
| 0 | Nothing, or something wrong. |

### 4. When the listing pages are rebuilt  [R2]

Also answerable only from the written description. This is the same rule the task
will trip over, so the answer here is worth comparing with question 7 later.

> Index and tag pages are not rebuilt on every build. How does hearth decide when
> they must be, and why was it designed that way?

| Score | The answer says |
| --- | --- |
| 2 | A signature taken over the fields of the assembled list of posts, chosen instead of tracking which output depends on which input, because that kept going subtly wrong after a post was deleted. |
| 1 | The signature, with no mention of what was rejected. |
| 0 | File timestamps, or a guess. |

### 5. Adding a second output format  [E1]

> To add a second output format, say a JSON file per post, which modules would
> change, and which would you leave alone?

| Score | The answer says |
| --- | --- |
| 2 | The build step that writes output, and possibly page assembly. Finding files, reading settings, rendering markdown and filling templates are all left alone. It also mentions that the record of what each build wrote has to know about the new file. |
| 1 | The right modules, without that last point. |
| 0 | Changes scattered across unrelated parts. |

### 6. The hand-written markdown renderer  [D1]

We are scoring whether they take a position and ground it. Agreeing with the
original decision is a full-marks answer if they say why.

> The markdown renderer is written by hand instead of using a library. Would you
> have made the same call? Why or why not?

| Score | The answer says |
| --- | --- |
| 2 | A position with a tradeoff behind it, such as the cost of a dependency against the cost of writing and fixing it yourself. The recorded reason, that hearth is meant to deploy as one file, counts. |
| 1 | A position with nothing behind it. |
| 0 | No position either way. |

## Round two, after the task

Ask questions 1 and 2 again first, in the same words, then these four.

### 7. What their own change rebuilds  [F2]

> After your change: someone flips a published post to draft and runs an
> incremental build. Walk me through exactly what rebuilds and why.

| Score | The answer says |
| --- | --- |
| 2 | The change reaches the assembled list, so the signature moves and the listing pages are rebuilt. An accurate description of their own build scores 2 here even when that build is broken, including saying that the listing pages go stale. Whether the code is right is scored separately. |
| 1 | Only the post's own page. |
| 0 | A description that does not match what their build does. |

### 8. Drafts and the feed  [R3]

The task card says nothing about the feed or the sitemap, so this is where we find
out whether they made a decision or inherited one.

> Drafts and the RSS feed: what does your build do now, and why that way?

| Score | The answer says |
| --- | --- |
| 2 | What it does, plus a reason of their own, such as what a subscriber would expect or that the card did not say. Any real ground counts, whichever way they went. |
| 1 | What it does, with "the agent did it that way" as the reason. |
| 0 | They do not know what their own build does. |

### 9. Scheduled posts  [E2]

> Next month the team wants scheduled posts, where a future date keeps a post
> hidden until that date passes. Given what you built today, what changes and where?

| Score | The answer says |
| --- | --- |
| 2 | They extend whatever they built for drafts, adding a second condition in the same place, and they notice the catch: a date-based rule changes the right answer without anyone editing a file, so the build has to be run again and the date has to reach the signature. |
| 1 | The right place, without the catch. |
| 0 | A new filter somewhere else. |

### 10. Arguing the other side  [D2]

> You put the draft decision where you did. Argue for the opposite placement. What
> would break, and would anything get better?

| Score | The answer says |
| --- | --- |
| 2 | Both sides of the real tension. Deciding early keeps the listing pages correct but means the dev server needs a way to ask for drafts. Deciding late makes the preview trivial but the signature never sees it. |
| 1 | One side only. |
| 0 | They do not engage with it. |

## Notes for whoever analyses this

- Questions 3 and 4 ask about decisions the participant inherited from the written
  description. Questions 8 and 10 ask about decisions they made themselves. Keep
  the two kinds apart when reporting.
- Every participant gets the same questions in the same order, in both conditions.
  The ember session asks the matching set in `questions-ember.md`.
- Evidence that the task actually trips people up in the way these questions assume
  is in the design doc, section 6.2, not here.
