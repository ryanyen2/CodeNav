---
description: Answer a question about this codebase by drawing a walkthrough in the feature tree.
argument-hint: <question>
---

You are answering this question **in the reader's own document**, not in chat:

> $ARGUMENTS

codoc exposes the `codoc` MCP server. The tree is a map of intent the reader
already has open. Your job is to draw the path through it that answers the
question, so they end up oriented in the map rather than holding a paragraph you
wrote and no idea where it came from.

## 1. Find the answer

- Start with `codoc_context(files=[…])` when the question names files or symbols,
  otherwise `codoc_tree` for the whole outline. These give you titles,
  descriptions, bindings, and nearby graph edges.
- **Read the actual code.** The tree says what each feature is *for*; the question
  is usually about what it *does*. Follow the bindings (`file.py::symbol`) with
  Read and Grep. A walkthrough that only paraphrases descriptions the reader can
  already see teaches them nothing.
- `codoc_history(feature_id)` is the fastest route to a *why* question — it
  carries who changed a feature, when, and the rationale they gave.
- Answer the question you were asked. If the honest answer is "the tree does not
  cover this", say so plainly and do not draw a walkthrough over nodes that only
  nearly answer it.

## 2. Draw the walkthrough

Call `codoc_walkthrough(question, answer, steps)` once, with:

- **`answer`** — one or two sentences. The whole answer, stated up front. Someone
  who reads only this line should already be better off; the steps are for
  showing them where it lives.
- **`steps`** — the few features that actually carry the answer, **in the order a
  reader should visit them**, which is usually the order things happen, not tree
  order. Each step:
  - `feature_id` — from the reads above.
  - `note` — ONE short line on what *this* node contributes to the answer. Not a
    summary of the node: the reader can see its title and description. Write the
    thing they could not have known to look for. "Runs before the quote rule, so
    the header is already gone" is a note; "handles furniture" is not.
  - `quote` — a span copied **verbatim** from that feature's title or description,
    which the IDE highlights. Pick the clause that answers the question. It is
    checked against the store; a quote that is not there comes back in
    `unresolved_quotes` and its step renders without a highlight.
  - `group` — the stage of the procedure this step belongs to, e.g. `"stripping
    the page furniture"`. Consecutive steps sharing a group are numbered `1a`,
    `1b`, `1c`, and the group is drawn as a heading above them. **Give every step
    a group or give none of them** — a half-grouped path reads as a mistake.
  - `file` / `symbol` / `line` — the code this step points at, so the step becomes
    a jump into the source.

**Keep it short.** Three to seven stops is a walkthrough; twelve is a table of
contents, and the reader already has one. The tool keeps the first 12 and tells
you if it truncated.

Check the result. `dropped` means a step named a feature that does not exist —
re-read and call again rather than leaving a path with a hole in it.

## 3. Tell them what you drew

Two or three sentences in chat: the answer, and that the tree now shows the path.
Do not re-list the steps — they are on screen, numbered, with your notes under
them. Mention anything you found that the tree does *not* currently say, and
offer to record it with `/codoc:plan` — a question that exposed a gap in the
description is the cheapest chance to fix one.

The overlay writes nothing to the tree, replaces any previous walkthrough, and
the reader dismisses it themselves. `codoc_walkthrough_read` tells you what is on
screen now; `codoc_walkthrough_clear` takes it down.
