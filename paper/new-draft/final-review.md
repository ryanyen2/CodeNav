# Final Pass: Three Weakest Paragraphs

## Style violations

None found. No colons in prose, no parentheses outside statistics, no em-dashes, no terse "X, not Y" fragments, no second-person outside quoted hypothetical speech.

---

## 1. Section 2.2, "Shipman and Marshall [1999] identified the tension..."

File: 02-related-work.md, line 17, starting "Shipman and Marshall [1999] identified the tension that makes codebase-scale representations hard."

**Why it is weak.** The paragraph is ~230 words and does two jobs. The first half grounds the formal-vs-informal tension in prior literature. The second half, from "This is exactly the trap our first version fell into," presents codoc's own design resolution in detail. That resolution is stated again, with the same framing and the same formal/informal factoring, in Section 3.3. Related work should establish what prior work shows, not preview this paper's solution. The result is redundancy across sections and a paragraph that reads as neither pure literature review nor pure design argument.

**Improvement.** Cut from "This is exactly the trap" to the end. The paragraph's job is done once it establishes that Henderson and Shipman/Marshall identified the formal-informal tension. Let Section 3.3 own the design response. Saves ~130 words.

## 2. Section 8.2, "Clark and Brennan's theory of common ground clarifies..."

File: 08-discussion.md, paragraph beginning "Clark and Brennan's theory of common ground clarifies why code alone fails."

**Why it is weak.** Decorative theory application. The preceding paragraph already stated concretely that participants communicated through the tree once they trusted it, and Section 7.3 already showed that proposals create encounters and verdicts record agreement. Labeling this "grounding in Clark and Brennan's sense" names what the reader already understands without generating new analytical power. The paragraph asserts a mapping between a communication theory and an observed behavior but does not use the theory to explain anything the observation left unclear.

**Improvement.** Merge the one load-bearing sentence, that accepting a proposal functions as visible evidence of shared understanding, into the preceding paragraph as a closing clause. Drop the Clark and Brennan framing unless it generates a prediction the data confirms independently.

## 3. Section 8.2, "codoc's feature tree functions as what Star and Griesemer [1989] call a boundary object..."

File: 08-discussion.md, paragraph beginning "codoc's feature tree functions as what Star and Griesemer."

**Why it is weak.** This applies a second theory label to the same observation as the paragraph before it, and its closing clause re-introduces "intent debt" from Storey [2026], a term already established in the introduction. The three-reader enumeration (developer / agent / auditor) restates what Sections 4 and 5 already demonstrated architecturally. The paragraph adds vocabulary without adding insight. Two consecutive paragraphs both decorating the same point with different theory citations weakens the argument by signaling that neither citation is doing real analytical work.

**Improvement.** If the boundary-object framing matters, use it to generate a claim the paper does not already make, such as a prediction about how the tree will function differently for maintainers vs. contributors in multi-user settings. Otherwise, cut entirely and let the concrete findings stand. The intent-debt callback can close Section 8.2 in a single sentence joined to the preceding material.
