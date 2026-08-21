# Where "why" actually comes from

Reading notes. The question this literature answers for codoc is: *what licenses
a claim about intent, and what must a change record carry so that a later reader
can ask why?*

## 1. Rationale is not in the code, and mining it is lossy

The design-rationale line (Burge & Brown's rationale-capture work, the
architectural-knowledge-vaporization literature) reports the same finding from
several directions: the reason for a decision is rarely written down at the point
of the decision, and where it exists at all it lives in issue threads, review
comments, and mailing lists rather than in the artifact **[canonical]**.

Search recall on this topic was thin in this session (`raw/q6-rationale-intent.txt`
returned almost nothing usable), so this section leans on recalled canonical work
and states no numbers.

**Consequence for codoc.** The grounding rule already in `tree_update.txt`
Rule 7 — assert a why only where evidence states one, otherwise describe what the
code achieves — is the correct reading of this literature, and it should be
strengthened rather than relaxed. Where codoc differs from a mining tool is that
it is *present at the moment of the decision*: the author's prompt to their coding
agent is the rationale, captured live. That is a genuinely better source than a
commit message written afterwards, and `author_intent` is the channel for it.

## 2. Commit messages carry intent unevenly

Work on commit-message generation and on classifying change intent finds that a
large share of real commit subjects state *what* changed and not *why*, and that
the why-bearing minority is where the value is **[canonical]**.

**Consequence for codoc.** `why.py` already ranks evidence
(directives > commits > prior rationale). The ranking is right. What is missing is
recording, on each amend, *which* evidence licensed the claim, so that a later
reader hovering the provenance card sees not just "a pass changed this" but "this
sentence rests on that directive." The timeline machinery already renders a chain;
it just is not told what the prose was warranted by.

## 3. Doc/code co-evolution: drift is the default

**Analyzing the co-evolution of comments and source code** — Fluri, Würsch,
Giger, Gall, *Software Quality Journal* 2009. **[verified]** 93 citations.
Also relevant: the code-comment inconsistency detection line, which frames
staleness as a classification problem over (code change, comment) pairs
**[canonical]**.

**Consequence for codoc.** Two design points.

First, **a stale description is worse than no description**, because a reader who
trusts it makes a wrong change. So Loop A's amend precision matters more than its
recall, and where codoc cannot tell whether prose went stale it should say so
rather than guess — the same discipline the timeline already applies to
unreconstructible revisions.

Second, the reverse direction is the interesting one and nobody in this
literature has it: codoc's plan channel means a description can be *ahead* of the
code. The settlement model already draws this (planned wording on a red ground =
agreed and not built). That composition is a contribution, and it is only sound
if the code claim is computed independently of the plan claim, which
`settlement.ts` already guarantees.

## 4. Why this matters for version control

The goal this work is aimed at: a change record that answers *why*, not just
*what*. The chain codoc can already assemble is

    prose span → the event that wrote it → the directive that asked for it
               → the prompt a person typed → the session → the base commit
               → the code diff

Nothing in the literature assembles that chain, because nothing else sits between
the author's intent and the code at the moment both exist. The missing link is
the *warrant*: which evidence a claim rests on. Adding that turns the chain from
a provenance trail into an argument a reader can check.
