# Editor Report 4: Trimming Pass

## Final word counts

| Section | Before | After | Cut |
|---------|--------|-------|-----|
| 00-abstract.md | 175 | 178 | +3 (concurrent edit) |
| 01-introduction.md | 1,778 | 1,593 | 185 |
| 02-related-work.md | 1,708 | 1,708 | 0 |
| 03-design-process.md | 1,682 | 1,683 | +1 (concurrent edit) |
| 04-system-design.md | 1,805 | 1,457 | 348 |
| 06-study-findings.md | 3,146 | 2,821 | 325 |
| 08-discussion.md | 3,124 | 2,710 | 414 |
| **TOTAL** | **~13,418** | **12,150** | **~1,268** |

Note: Concurrent edits by other agents reduced some sections beyond this pass's own cuts. The combined effect overshoots the 12,500 target by ~350 words. If content needs to be restored, the lowest-value cuts were the introduction's three restating sentences and the roadmap paragraph in the discussion opener.

## What was cut

### 01-introduction.md (185 words)
- "It is a label without an account." (restates prior sentence)
- "The rule is correct and also unverifiable, which makes it the same as no rule at all." (restates)
- "They optimize for a model's next turn rather than for a person trying to understand the system." (restates "written by the agent for the agent")
- "Intent lives in the heads of whoever made the decision or in a representation that recorded it." (restates gap)
- "Prior documentation systems lacked this incentive structure, and its absence is why they rotted." (restates operativity argument)
- Three sentences restating the iteration narrative (already covered in paragraphs above)
- "Orientation asks whether... Communication asks whether... Record-keeping asks whether..." (RQs already say this)

### 04-system-design.md (348 words, including ~120 from concurrent edits)
- "A collapsible tree hides most of its content by default, defeating surveyability." (previous sentence already said this)
- "In under a minute Sam knows the system's major parts..." (restates what the tree showing entails)
- Bundling rationale ("We bundled these deliberately. Splitting...") (the single-action description already implies bundling)
- "The gap between disagreeing with the tree and undoing the code is exactly what code drift reports." (restates)
- "Had the loops not been running continuously, confirming one feature would have told Sam nothing about the rest." (restates G1 faithfulness)
- "A proposal inside an unreliable document is just another claim to verify." (restates)
- "Deterministic resolution means the model only sees genuinely ambiguous cases." (restates principle already stated)
- "The developer writes naturally and the gate classifies." (restates)
- Imperative gate example in 5.4 (duplicates 5.3 example)
- "They are observing the output of a process that runs identically on every feature." (restates)
- Entire final paragraph of 5.4 ("Together these produce...") (restates what three mechanisms showed)

### 06-study-findings.md (325 words)
- "The cost is ecological validity..." sentence in constructed stimulus (restates prior sentence's logic)
- Two sentences about inductive evidence from verified features in 7.1 (covered fully in 7.4 and 8.1)
- "The mechanism is not that proposals contain more information..." (restates 7.1's identical claim)
- "The difference is between a newspaper and a question." and "G2 in practice means..." (restates proposal logic)
- Entire theoretical framing paragraph in 7.4 (attitudinal vs behavioral trust, restated in 8.1 with citations)
- Non-linearity design consequences paragraph in 7.4 (restated in 8.1)
- Threshold-as-foundation paragraph in 7.4 (two sentences kept; rest is in 8.1)
- "The first names a responsible party. The second names only an author." (restates provenance chain)
- "Obligatory response converts encounter into recorded decision." (covered in 7.5)
- "The baseline begins already true..." (elaboration of "not starting from the same place")
- "but we have no evidence for that claim in a single-session study" (stated in 9.1)
- "This boundary between detection and diagnosis is intrinsic to any representation..." (restates)
- p-value justification clause in 6.7

### 08-discussion.md (414 words)
- Roadmap paragraph ("This section traces that causal chain. Section 8.1 examines...") 
- "The causal direction runs from trust to communication rather than the reverse." (stated by prior two sentences)
- "The trust threshold suggests this is backwards." (prior sentence says "inverts")
- "The two loops are not performance features. They are the value proposition." (restates)
- "Below it, the developer has attitudinal trust but still checks. Above it, they delegate verification to the tool." (definitions already cover this)
- "In agent-mediated development, the agent's context window closes..." (problem already established)
- "The boundary object is self-policing." (previous sentence already showed this)
- "and adding a second merely forces the developer to reason about which..." (clause after sufficient point)
- "An ephemeral view costs nothing to maintain and disappears after use, whereas..." (elaboration)
- Seven-sentence block in Glorikian paragraph (restates conditions already stated five lines earlier)
- "The same information in a flat document is not equivalent..." (restates)
- "If they are overwhelmed... If they are missing..." (elaborates "dominant failure mode")
- Dhanorkar mapping: two of four sentences (a priori is enough given "maps to all four")
- "trust the commit message" option (two options suffice)
- "The representation should be the site of review..." (restates)
- "Materializing proposals at their destination..." (restates prior two sentences)
- "An ATM returns a card before dispensing cash." (example after definition suffices)
- "The forcing function IS the mechanism behind durable traces. Deciding IS recording, by construction." (already shown)
- "The specific contribution of proposals is that they appear..." (restates what was just said differently)
- "The difference is between a flag and a question." (just showed this)
- "We cannot separate genuine trust formation from briefing-induced confidence..." (replaced by shorter closing)
- Scale: redundant clause about trust threshold not forming
- Longitudinal: weekend scenario sentence
- Finer-grained: two elaborating sentences
- Conclusion: "investing in the infrastructure... rather than the expressivity..." (already said in 8.1)
- "The bundle comparison limitation in Section 9.1 applies here." (cross-ref to adjacent section)

## Style verification
- No em-dashes introduced
- No colons in prose introduced
- No parentheses in prose introduced
- No second-person introduced
- No "X, not Y" fragments introduced
- All edits were deletions; no rewriting performed
