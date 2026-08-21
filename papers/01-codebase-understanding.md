# How developers build a theory of a codebase

Reading notes. The question this literature answers for codoc is: *what does a
reader actually need from a description, and at what altitude?*

## 1. Comprehension is not one process, it is a switching between two

**Program Comprehension During Software Maintenance and Evolution** — von
Mayrhauser & Vans, *IEEE Computer*, 1995. **[verified]** 678 citations.
<https://www.semanticscholar.org/search?q=Program%20Comprehension%20During%20Software%20Maintenance%20and%20Evolution>

Their integrated metamodel says a maintainer holds three models at once and
switches between them opportunistically rather than working through one:

- a **program model** — control flow, the text of the code as it reads;
- a **situation model** — the data flow and the real-world domain entities;
- a **top-down / domain model** — hypotheses about what the system is *for*,
  refined by looking for beacons that confirm or kill them.

The switching is the finding. A reader forms a hypothesis top-down, drops into
the code to test it, and returns. Earlier work established each half
separately — Brooks' hypothesis-driven top-down account, Pennington's and
Shneiderman's bottom-up chunking, Soloway & Ehrlich's plans-and-beacons
**[canonical]** — but the metamodel is what says a maintainer needs *both* at
hand, not one.

**Consequence for codoc.** A feature tree is the top-down model made explicit and
persistent, which is the half a codebase does not ship. So the tree's job is to
support hypothesis formation *and* the drop into code — which is why a
description without an inline `codoc:` citation is a worse description even when
its prose is right: it gives the reader nowhere to test the claim. It also means
descriptions at different depths should not read alike. A top-level node feeds
hypothesis formation and should stay in domain words; a leaf feeds the beacon
search and should name symbols and values.

## 2. Comprehension proceeds as a sequence of questions

**Questions programmers ask during software evolution tasks** — Sillito, Murphy
& De Volder, FSE 2006. **[verified]** 306 citations.
**Asking and Answering Questions during a Programming Change Task** — same
authors, *IEEE TSE* 2008. **[verified]** 347 citations.
<https://www.semanticscholar.org/paper/Questions-programmers-ask-during-software-evolution>

They observed developers on change tasks and catalogued 44 questions in four
groups that escalate:

1. **finding a focus point** — "where is this feature implemented?"
2. **expanding that point** — "what does this call, who calls it, what is its
   scope?"
3. **understanding a subgraph** — "how are these related, what is the control
   flow between them?"
4. **questions over groups of subgraphs** — "what is the mapping between these
   two structures, what happens if I change this?"

Ko, Myers and colleagues report the same escalation from the debugging side
**[canonical]**, and Ko's *Information foraging / Six learning barriers* line
frames the cost of each hop as the thing that kills comprehension.

**Consequence for codoc.** Groups 1 and 2 are what the tree plus bindings answer
already. Group 3 — *how are these related* — is answered badly by prose and well
by a diagram, which is the strongest argument for the diagram block being part of
the generation pipeline rather than an ornament. Group 4 — *what happens if I
change this* — is exactly what the dependency graph's `impacted` set knows and
what no description currently states.

## 3. Abstraction level is the axis, and mixed altitude is the failure

Codoc's own experience matches what the summarization literature reports: the
commonest defect in generated documentation is altitude drift — a node that
should describe purpose instead narrates a function body, or a node that should
name a threshold stays vague.

**Agent4cs: A Multi-agent System for Code Summarization in Large Hierarchical
Codebases** — Tang, Sarikayak, Tuncel et al., 2026. **[verified]**
<https://www.semanticscholar.org/paper/0ea3160339bd731ab6375c418671ea9fe5ae64f6>
Treats the hierarchy explicitly: different agents summarize at different levels
of a large codebase rather than one pass over a flat corpus.

**RepoAgent: An LLM-Powered Open-Source Framework for Repository-level Code
Documentation Generation** — EMNLP 2024. **[verified]** 109 citations.
Bottom-up over the call graph in topological order, so a caller's documentation
can cite its callees'. Its unit is the *code element* (function, class), not the
feature, and its output mirrors the directory tree.

**RepoSummary: Feature-Oriented Summarization and Documentation Generation for
Code Repositories** — Zhu, Zhao, Li et al., arXiv:2510.11039, 2025.
**[verified]** <https://arxiv.org/abs/2510.11039>
The closest published work to codoc. Argues directly that summarizing *by
directory tree is insufficient*, because a functional feature is implemented by
methods scattered across files, and what a developer needs is the traceability
link from feature to method. Reported against the HGEN baseline: feature coverage
against hand-written documentation 61.2% → 71.1%, file-level traceability recall
29.9% → 53.0%. Recall only; precision is not reported.

**Consequence for codoc.** Two things. First, codoc's central bet — feature nodes
that cross files, not a directory mirror — is the same bet RepoSummary makes and
validates. Second, the metric that matters is **coverage of the features a human
would have written**, not similarity to a reference text. That is the eval codoc
should have and does not: take a repo whose maintainers wrote real architecture
docs, and measure what fraction of the features they named appear in the
generated tree.

## 4. What a good summary contains, as opposed to what a metric rewards

Human studies of code summaries repeatedly find that readers want the *why* and
the *what for*, and that generated summaries supply the *how*
**[canonical]** — the "what does it do" is usually already legible from the
identifier names, so restating it adds nothing. Testing the effect of code
documentation on LLM code understanding, 2024 **[verified]**, reports the
reverse direction of the same asymmetry.

**Consequence for codoc.** `prompts/style.txt` already encodes most of this
(purpose first, name the answer not the question, no restating the title). What
it does not encode is the *negative* rule with teeth: a description whose every
sentence could be recovered by reading the identifier names has failed, and that
is checkable mechanically — see the redundancy check in
[04-design-implications.md](04-design-implications.md).

## 5. Documentation drifts, and drift is measurable

**Analyzing the co-evolution of comments and source code** — Fluri, Würsch,
Giger, Gall, *Software Quality Journal* 2009. **[verified]** 93 citations.
Comments and the code they describe change together only part of the time;
newly-added code is frequently left uncommented and existing comments are not
updated when their code changes.

**Consequence for codoc.** Loop A exists precisely to close this gap, so the
thing to protect is the loop's *precision*: an amend that fires on cosmetic
change trains the author to ignore amends, which reintroduces drift with extra
noise. The classifier's decision table is therefore a quality surface, not just
plumbing.
