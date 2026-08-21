# Learning the author's voice from their edits

Reading notes. The question this literature answers for codoc is: *when a person
rewrites a description we generated, how do we stop generating that way?*

## 1. The load-bearing paper: PRELUDE / CIPHER

**Aligning LLM Agents by Learning Latent Preference from User Edits** — Gao,
Taymanov, Salinas, Mineiro, Misra. NeurIPS 2024, arXiv:2404.15269.
**[verified]** 96 citations. <https://arxiv.org/abs/2404.15269>

The setting is exactly codoc's: an agent drafts text, the user edits it, and the
edit is free supervision that arrives as a byproduct of normal use rather than
from an annotation pass.

**PRELUDE** is the framework — infer a *natural-language description* of the
user's latent preference from historic edits, and use those descriptions as
prompt material for future generations. The paper is explicit about why it
refuses gradient updates: fine-tuning is costly, does not scale per-user, and can
degrade the model elsewhere. A textual preference buys something a weight update
cannot — the user can read it and correct it.

**CIPHER** is the algorithm, and it has two steps:

1. **Infer.** When an edit lands, ask the LLM to name the preference that
   explains the gap between the draft and the edited version, *tied to that
   context*.
2. **Retrieve and aggregate.** At generation time, pull the inferred preferences
   from the *k* nearest contexts in history and merge them into one conditioning
   description.

Retrieval is context-conditioned rather than global because preferences are
"complex, subtle, and vary based on context" — one global style string does not
hold. The objective is **edit-distance cost**: the metric of success is that the
user has less to change over time. CIPHER reports the lowest edit distance among
its baselines with small query overhead, and the learned preference text is
reported as significantly similar to the ground-truth latent preference. Users
were simulated with GPT-4 across summarization and email-writing environments;
the abstract does not fix *k*, the similarity metric, or the exact edit distance.

**Consequence for codoc.** This is directly implementable and is the core of what
this work builds. Every human edit to a title or description is a
(context, draft, revision) triple that codoc already records in the change
ledger and simply throws away as learning signal. The store already has the
before-text (`_record_displaced`), the after-text, and the authorship. What is
missing is the inference step, a place to keep the descriptors, retrieval by
context, and injection into the four prose prompts.

Two adaptations codoc needs beyond the paper:

- **Context here is structural, not lexical.** The nearest context for a feature
  node is not "similar prose" but *the same region of the tree and the same kind
  of code* — a sibling under the same parent, a node binding the same package.
  So retrieval keys on parent path and binding paths, with prose similarity only
  as a tiebreak.
- **Preferences must be separable from content.** An author who rewrites a
  description because it was factually wrong has taught codoc nothing about
  style. The inference step must therefore classify the edit before it
  generalizes from it, and discard the content-only ones. This is the single
  biggest failure mode: learning "the author prefers to mention retry limits"
  from an edit that was correcting one specific wrong claim.

## 2. Corroborating work on the same shape

**Meetalk: Retrieval-Augmented and Adaptively Personalized Meeting
Summarization with Knowledge Learning from User Corrections** — KnowFM 2025.
**[verified]**
<https://www.semanticscholar.org/paper/a4c607a8ea3599d7fb777febd2bc5df7ebc7a0ec>
Same architecture applied to summarization: user corrections are distilled into
retrievable knowledge rather than trained in. Confirms the pattern generalizes
past the PRELUDE environments.

**Aligning Language Models from User Interactions** — Kleine Buening, Hübotter,
Pásztor et al., arXiv:2603.12273, 2026. **[verified]** 15 citations.
Treats ordinary interaction traces, not just explicit edits, as the alignment
signal.

**Teaching Language Models to Evolve with Users: Dynamic Profile Modeling for
Personalized Alignment** — 2025. **[verified]** A maintained *profile* that
updates over time rather than a static preference string; relevant to the
question of when a codoc style descriptor should be revised versus appended.

**Towards Faithful and Controllable Personalization via Critique-Post-Edit
Reinforcement Learning** — 2025. **[verified]** 3 citations. Names the tension
codoc has to hold: personalization pulling against faithfulness. Their
separation of a critique step from an edit step is a useful shape — decide *what
is wrong* before deciding *how to write it*.

**Catch Me If You Can? Not Yet: LLMs Still Struggle to Imitate the Implicit
Writing Styles of Everyday Authors** — EMNLP 2025. **[verified]** 11 citations.
The sobering result. Implicit style imitation from examples alone is weak, which
argues for codoc's descriptors being **explicit and named** rather than a bag of
few-shot samples. Showing the model three of the author's paragraphs is less
effective than telling it "this author opens on the caller's problem and never
names a class in the first sentence."

**Step-Back Profiling: Distilling User History for Personalized Scientific
Writing** — 2024. **[verified]** Distil history into a compact profile rather
than retrieving raw history — the compaction step codoc will need once an author
has made hundreds of edits.

**Training Language Models with Language Feedback** — 2022. **[verified]**
Natural-language feedback as the learning channel, upstream of this whole line.

**Dr Genre: Reinforcement Learning from Decoupled LLM Feedback for Generic Text
Rewriting** — 2025. **[verified]** 4 citations. Decouples the reward across
rewriting dimensions, which supports treating codoc's style axes separately
rather than as one score.

## 3. What the literature says about *not* over-learning

The recurring warning across this line is that a preference inferred from one
edit is a hypothesis, not a fact, and applying it globally produces confident
wrong style. PRELUDE's answer is context-conditioned retrieval; Step-Back
Profiling's is distillation with abstraction; the critique-post-edit line's is a
separate faithfulness check after personalization.

**Consequence for codoc.** Three guards, all cheap:

1. **Evidence count.** A descriptor inferred once is *provisional* and is not
   injected into prompts until a second edit corroborates it. Codoc has the
   ledger to count this.
2. **Scope.** A descriptor records where it was learned (subtree, package) and is
   retrieved for that region first, global only when it has corroboration from
   several regions.
3. **Faithfulness wins ties.** Style guidance is injected as *how to say it*,
   never as *what to say*, and the prompt says so explicitly, because the whole
   value of codoc is that the tree is true to the code.

## 4. The metric to steer by

PRELUDE's edit-distance cost is the right north star and codoc can compute it
without simulation, because real edits are the data. For any generated
description that a human subsequently edited, the normalized edit distance
between generated and final text is the cost. Falling mean cost over time is the
claim; a flat line means the learning is doing nothing. This is the eval that
[04-design-implications.md](04-design-implications.md) specifies.
