---
description: Answer a question about this codebase as a reading path over the codoc feature tree.
argument-hint: <question>
---

Answer this question in chat, as a short reading path through the feature tree
the person already has open:

> $ARGUMENTS

This command reads and writes nothing in the tree.

## 1. Find the answer

- `codoc_context(files=[…])` when the question names files or symbols, otherwise
  `codoc_tree` for the whole outline. Each feature comes with its title, its
  description and the code it binds.
- Read the code the bindings name (`file.py::symbol`). The tree says what a feature
  is for; the question is usually about what it does.
- `codoc_history(feature_id)` answers a question about why: who changed a feature,
  when, and what they asked for.
- If the tree does not cover the question, say so and answer from the code alone.

## 2. Answer

1. One or two sentences that answer the question.
2. A numbered list of the three to seven features that carry the answer, in the order
   a reader should visit them, which is usually the order things happen. Each item is
   the feature's title as the tree shows it, then one line on what that feature
   contributes to the answer. Write what the reader could not have known to look for,
   not a summary of the description they can already read.
3. Anything you found that the tree does not say. Offer to record it with
   `/codoc:plan`.
