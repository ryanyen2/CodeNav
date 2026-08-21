# Review R3: CHI AC-Level Review of "codoc" Paper

## 1. OVERALL ASSESSMENT

The paper's strongest contribution is naming and characterizing the *trust threshold* as a design principle for developer tools in the agent era: the claim that investment in synchronization infrastructure outweighs investment in representation expressivity, because a binary behavioral shift from verification to delegation either forms or it does not, and everything else is downstream. This is a genuinely useful framing that could influence how the field thinks about tool design. What would make me reject the paper is that the empirical evidence for the threshold's existence as described (binary, non-linear, forming after exactly two or three verifications) rests on twelve participants in a twenty-minute session with placeholder data throughout Section 7, and the causal architecture the discussion builds from this observation (trust causes communication, which causes durability) substantially exceeds what the study design can isolate. The paper reads as a strong design argument illustrated by a study, but presents itself as a study-grounded contribution, and that mismatch is the central problem.

---

## 2. MISSING ARGUMENTS

### 2.1 The binary/non-linear nature of the threshold

**Claim (Section 7.4):** "Participants did not gradually trust the tree more with each confirmed feature. They operated in one of two modes, checking everything or checking nothing. The transition between modes happened within minutes and after [DATA: median number] verified features."

**What is missing:** No operationalization of "modes" is given. How were "checking everything" and "checking nothing" coded? What counts as a verification episode versus incidental file opening? The paper asserts binary behavior but provides no coding scheme, no inter-rater reliability on mode assignment, and no analysis showing bimodality rather than a continuous decline in verification frequency. The phrase "non-linear" implies a statistical claim (a step function rather than an exponential decay) but no model comparison or even a plot of verification frequency over time is reported.

**What would fix it:** A time-series plot of file-open events per participant with an explicit coding rubric for "verification episode" versus other file access, plus a formal comparison between a step-function model and a gradual-decay model of checking behavior. Even descriptive statistics (e.g., "9 of 12 participants had zero code-checking events after minute X, with no participant showing intermediate rates") would ground the binary claim.

### 2.2 The causal direction from trust to communication

**Claim (Section 8.2):** "A developer who does not trust the representation will not write into it, because writing into an unreliable surface is writing into nothing."

**What is missing:** This is asserted as if self-evident, but the alternative direction is equally plausible: participants communicated through the tree because the tool's affordances (comments, /codoc:plan) made communication easy and salient, and this ease of use was interpreted as trustworthiness. The study provides no manipulation that separates trust from affordance availability. Both conditions have the same information, but codoc has richer interaction affordances *independent of trust*. The paper acknowledges the "bundle comparison" limitation (Section 9.1) but then builds the entire discussion as though mechanism isolation were achieved.

**What would fix it:** Either (a) an ablation where synchronization is disabled but the interaction affordances remain (testing whether communication still occurs without trust), or (b) at minimum, a systematic analysis of the temporal ordering showing that communication acts cluster AFTER verified-trust episodes rather than before. If participants used comments before they verified any feature, the causal claim collapses.

### 2.3 The "near-total accuracy" requirement

**Claim (Section 7.4):** "A system that is 80% accurate does not earn 80% of the trust. It earns none, because the developer cannot tell which 20% is wrong and therefore must verify everything. The threshold requires near-total accuracy or it never forms."

**What is missing:** This is a strong empirical claim presented without evidence. No participant encountered an inaccurate feature in the codoc condition (or if they did, it is not reported). The paper cannot claim that 80% accuracy earns zero trust because it never tested degraded accuracy. The claim is a logical extrapolation from the Lee and See [2004] framework, not a finding of this study.

**What would fix it:** Acknowledge this as a design hypothesis rather than a finding. Alternatively, report whether any participant DID encounter the faithfulness-visibility tension from Section 7.3 (where the loop absorbed a change), and if so, whether their verification behavior changed afterward. That would be partial evidence.

### 2.4 The compounding claim

**Claim (Section 8.1):** "each lookup that does not require opening a file compounds" and (Section 9.2): "The trust threshold, if real, should compound."

**What is missing:** No measurement of compounding exists in a single-session study. The paper repeatedly uses "compounds" as though it were demonstrated, but the study shows at most that context switches are lower in one condition. Compounding requires measurement across repeated uses, which was not done.

**What would fix it:** Either remove the compounding language entirely and state that a longitudinal study is needed to test it, or clearly mark every instance as a prediction rather than a finding.

### 2.5 The claim about what the baseline lacks

**Claim (Section 6.3):** "codoc actively prompts through proposals while CLAUDE.md does not. This asymmetry IS the mechanism under test."

**What is missing:** The paper argues this is a fair test of its design thesis, but it means the study cannot distinguish "synchronized representations produce trust and therefore detection" from "any notification mechanism that forces encounters improves detection." A simpler tool that highlights CLAUDE.md sections that changed since last read, with no synchronization infrastructure at all, might produce the same detection improvement. The paper never engages with this possibility.

**What would fix it:** A paragraph in the discussion explicitly addressing the "forcing function alone" alternative: if a simple diff-highlight on CLAUDE.md would have produced equivalent results, what does that say about the trust-threshold claim? The paper touches this in 8.5's third principle but does not connect it back to the confound in its own design.

### 2.6 Glorikian [2026] and the "step-function collapse"

**Claim (Section 8.1):** "Glorikian [2026] documents the same 'step-function collapse' in verification behavior across AI-assisted knowledge work."

**What is missing:** This citation is doing heavy lifting. If it genuinely demonstrates the same phenomenon independently, the paper should explain the methodology and context in enough detail for the reader to judge whether the analogy holds. As written, it reads as a citation of convenience, using someone else's name for a phenomenon to imply that your own observation is confirmed externally without showing the reader whether the evidence quality is comparable.

**What would fix it:** Two to three sentences summarizing Glorikian's methodology, population, and the specific claim being borrowed. Does that paper study developers? Knowledge workers in general? What is the domain? Without this, the citation functions as authority rather than evidence.

### 2.7 The ecological validity of "planted problems"

**Claim (Section 6.2):** "The agent was steered during recording until it produced these, and every steer is documented alongside the frames."

**What is missing:** No description of what kinds of problems were planted, how many, or at what severity. The reader cannot judge whether the detection task is trivially easy (a contradiction between the first line of the description and the function name) or realistically difficult (a subtle semantic shift in timeout behavior). Without knowing the planted problems, the detection coverage measure is uninterpretable.

**What would fix it:** A table or appendix listing each planted problem, its type (semantic contradiction, missing behavior, architectural violation), and which mechanism in codoc should surface it (proposal, drift marker, description mismatch). The paper gestures toward this with "If the daemon failed to surface a planted problem, we report that failure" but never actually reports it.

---

## 3. DEMAND CHARACTERISTICS

The paper does not adequately address demand characteristics. Specific concerns:

**Participants knew they were evaluating a research tool.** The codoc condition comes with a 10-minute briefing on the tool. Participants who have been told about proposals, drift markers, and the trust architecture may behave as the briefing instructs rather than as they naturally would. A participant briefed that "the tree is synchronized with the code" has been handed the conclusion the experimenters want them to reach. This is not acknowledged anywhere.

**The think-aloud protocol interacts with verification behavior.** Thinking aloud about trust ("I'm going to check this one... okay it matches... I think I can trust the rest") may make the threshold MORE salient and MORE binary than it would be in naturalistic use. A participant who would have gradually reduced checking in silence may instead narrate a discrete "decision to trust" because the think-aloud protocol demands a narrative account. Section 7.4's characterization of the threshold as binary may be an artifact of think-aloud narrativization rather than an observation of underlying cognition.

**Where should this be acknowledged:** Section 9.1, as a new limitation paragraph after "Constructed stimulus." The paper should note: (a) the briefing may have primed trust formation, (b) the think-aloud protocol may have sharpened the apparent binary nature of the shift, and (c) the experimenter-designed tool creates a social pressure to use its features that would not exist in voluntary adoption. At minimum, one sentence should appear in Section 7.4 alongside the binary-mode claim, noting that think-aloud may have contributed to the apparent sharpness of the transition.

---

## 4. ALTERNATIVE EXPLANATIONS FOR THE TRUST THRESHOLD

The paper treats the trust threshold as evidence that synchronization infrastructure is what matters. But several alternative explanations could produce the same observed behavior (participants stop opening files after a few minutes):

**4.1 Satisficing under time pressure.** Participants had 20 minutes. After spending the first few minutes learning the interface (the "initial mapping cost" the paper acknowledges), they may have simply decided to stop checking because time was running out and they needed to produce answers. This would look identical to "trust formed" but would mean "time ran out." The paper could partially rule this out by reporting WHEN in the 20-minute block the transition occurred and whether participants who transitioned earlier performed better than those who transitioned later.

**4.2 Cognitive fatigue from mode-switching.** Opening files, comparing to descriptions, and returning to the tree is effortful. Participants may have stopped not because they trusted the tree but because the checking process was exhausting relative to the alternative of just reading the tree. This explanation predicts the same context-switch reduction and would not require any trust formation at all.

**4.3 Social desirability / experimenter expectation.** Participants briefed on a "synchronized" tool who are being observed may stop checking because they infer that "the system is supposed to be trusted." This is a specific form of demand characteristic (Section 3) but deserves mention as an alternative to genuine trust formation.

**4.4 Anchoring to the first verification.** If the first two or three features a participant checks happen to be accurate, that is entirely expected from a system designed for accuracy. The "threshold" may simply be the point at which the participant ran out of initial skepticism rather than a point at which they accumulated sufficient evidence. This matters because the paper claims the phenomenon is "about what representational infrastructure enables" (Section 8.1), but if it is just normal skepticism decay, it would occur with any reasonably accurate tool regardless of architecture.

**Does the paper rule these out?** No. Section 8.1 cites Lee and See [2004] and Parasuraman and Riley [1997] to frame the phenomenon but does not present evidence distinguishing trust from satisficing, fatigue, social pressure, or skepticism decay. The binary framing in 7.4 is asserted but not tested against any of these alternatives.

---

## 5. STRUCTURAL WEAKNESSES

### 5.1 The [DATA] placeholders make the paper unreviable as a confirmatory study

Sections 7.1 through 7.5 contain critical missing data: effect sizes, confidence intervals, counts, test statistics, and participant quotes. The confirmatory claims (RQ2, RQ3) are pre-registered with a criterion of "at least 7 of 12 participants have strictly higher coverage," but whether this criterion was met is literally "[DATA: meeting/not meeting]." A reviewer cannot assess the strength of the confirmatory evidence without the numbers. The qualitative findings rest on "[PLACEHOLDER QUOTE]" entries that are supposed to illustrate behavioral patterns. This is a structural weakness because the review must assume the numbers will eventually support the claims, which is not a valid basis for acceptance.

### 5.2 The logical gap between V1 and V2

Section 3.2 reports V1's recall finding (median 8 vs 5, W=78, p=.003). Section 3.3 describes the redesign. But the paper never presents evidence that the redesign improved on V1's *failures*. Did V2 participants communicate through the representation where V1 participants could not? Did V2 surface change history where V1 did not? The argument from V1 failure to V2 success is asserted via design logic (features instead of files, proposals instead of silent updates) but the connection is never closed empirically. The reader takes the authors' word that the redesign addressed the failures.

### 5.3 The faithfulness-visibility tension is discovered but not resolved

Section 7.3 reports that "on one of two projects, codoc's sync loop amended the tree to match the agent's change before the participant encountered it." This is presented as a known limitation explored in Section 8.4. But it directly undermines the trust threshold's value. If the system can silently absorb changes, then a developer who trusts the tree is trusting a representation that may have been auto-corrected, which is exactly the "false confidence" the paper warns about in G1. The paper acknowledges the tension but does not resolve it, and does not explain why this failure mode does not invalidate the threshold claim. If the threshold can form in a system with this failure mode, then what exactly is the developer trusting?

### 5.4 The Conclusion's opening scenario is not demonstrated by the study

The conclusion opens: "With codoc, the feature tree shows two proposals. One says the agent added a configuration layer... The developer accepts the first and rejects the second." This is the walkthrough from Section 4.2, not a finding from the study. The study measured detection coverage on planted problems in a recorded session, not the scenario described. The elision between "we designed for this" and "the study shows this works" is subtle but present.

---

## 6. WHAT WOULD MAKE THIS A BEST PAPER

### 6.1 Operationalize and test the threshold as a phenomenon, not just name it

The paper's most striking contribution is naming and characterizing the trust threshold. But right now it is a post-hoc interpretive label applied to a behavioral pattern. To reach best-paper status, the threshold needs to be operationalized rigorously (a coding scheme for verification episodes, a formal definition of the two modes, a statistical test of bimodality) and then tested against the alternative explanations in Section 4 above. Even a simple analysis showing that participants who verified more features before transitioning did NOT perform better than those who verified fewer (suggesting the threshold is about calibration rather than accumulated knowledge) would substantially strengthen the claim. The distance between "we observed a pattern and named it" and "we characterized a phenomenon and showed its boundary conditions" is the distance between an accept and a best paper.

### 6.2 Fill the data and let the numbers speak, even if they are modest

The [DATA] placeholders currently prevent assessment. But assuming the numbers are modest (likely with N=12), the paper would be stronger if it led with the qualitative observation of the threshold, used the quantitative data as a consistency check rather than a primary claim, and explicitly stated that the study's contribution is characterizing a phenomenon rather than demonstrating an effect size. A best paper at CHI can have twelve participants if the insight is sharp enough and the reporting is honest about what the numbers can and cannot say. The current framing, with pre-registered criteria and confirmatory language, invites the reader to judge it as an underpowered experiment. Reframing the quantitative component as supporting evidence for a design principle (rather than the primary deliverable) would better serve the paper's actual strength.

### 6.3 Show one trust failure and its consequences

The paper claims the threshold is "fragile" and "collapses on a single failure" (Section 8.1). If ANY participant encountered an inaccuracy in the codoc condition and reverted to verification mode, that single case would be the most powerful evidence in the paper. It would demonstrate both the threshold and its fragility in one observation. If no participant encountered a failure, the paper should acknowledge that the fragility claim is a prediction from theory rather than a demonstrated property. Either way, engaging with this directly, showing the reader what happens at the boundary, would transform the threshold from an assertion into a demonstrated phenomenon with known failure modes, which is what best papers do.

---

## Summary of Actionable Recommendations

| Priority | Section | Action |
|----------|---------|--------|
| Critical | 7.1-7.5 | Fill all [DATA] placeholders and quote slots |
| Critical | 7.4 | Operationalize "two modes" with a coding scheme and bimodality test |
| High | 9.1 | Add demand characteristics paragraph (briefing priming, think-aloud effects) |
| High | 8.1-8.2 | Weaken causal language or provide temporal-ordering evidence for trust-before-communication |
| High | 6.2 | Describe planted problems in sufficient detail for the reader to judge difficulty |
| Medium | 8.1 | Expand Glorikian [2026] citation with methodology summary |
| Medium | 7.4 | Acknowledge satisficing and fatigue as alternative explanations |
| Medium | 8.5 | Address "forcing function alone" as confound |
| Low | 3.3 | Show empirical closure between V1 failures and V2 improvements |
