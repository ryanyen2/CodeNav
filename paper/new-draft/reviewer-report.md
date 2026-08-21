# CHI R2 Review — codoc: Bidirectionally Synchronized Intent Representation

## Summary

The paper presents codoc, a VS Code extension maintaining a bidirectionally synchronized "feature tree" of codebase intent, and claims a "trust threshold" design principle where investment in synchronization infrastructure matters more than representation expressivity.

## Weaknesses

### 1. The trust threshold claim exceeds its evidentiary base (SEVERE)

The paper's central contribution is the "trust threshold" as a non-linear, binary phenomenon. The authors claim participants "did not gradually trust the tree more with each confirmed feature" and "operated in one of two modes, checking everything or checking nothing." This is a strong quantitative claim about the shape of a function, stated from N=12 participants observed for 20 minutes in a lab with qualitative coding. The data cannot distinguish a step function from a steep sigmoid, cannot rule out habituation to the interface (reduced verification because the UI became familiar rather than because trust formed), and cannot distinguish trust in the representation from trust in the experimental setup (participants may assume planted materials are correct because an experimenter prepared them). Calling this a "design principle" rather than a suggestive observation from a small qualitative sample overstates the evidence class. The authors should either present it as a hypothesis warranting dedicated study or provide a formal operationalization that future work can falsify.

### 2. The evaluation cannot attribute effects to the claimed mechanism (MAJOR)

codoc bundles proposals, drift markers, inline bindings, feature hierarchy, code links, navigation tools, comments, and a change ledger. The baseline is flat Markdown with no interactive features. The paper acknowledges this is a "bundle comparison" in limitations, yet the entire discussion argues AS IF the trust threshold specifically caused the results. The comparison tells us that a rich interactive structured tool with forcing functions outperforms a static document. That finding, while useful, does not require any of the paper's theoretical apparatus. An ablation removing proposals alone, or removing bindings while keeping structure, would test the claimed mechanism. Without one, the causal chain from "trust threshold" through "local verification enables rapid calibration" to "proposals force decisions" is a plausible interpretation of a confounded comparison, not evidence for a mechanism. The authors should be explicit about what their design tells them and what it does not, and should not frame the discussion as if mechanism is established.

### 3. The "trust threshold" concept is inadequately distinguished from existing constructs (MODERATE)

Trust-in-automation has a 30-year literature. Lee and See [2004] already describe trust as formed through observation of accuracy, broken by single failures, and rebuilt slowly. Muir's model already predicts the asymmetry the authors report. Dzindolet et al. [2002] already showed that explanation of mechanism promotes trust calibration. The paper cites Parasuraman and Riley and Schemmer et al. but never explains what is novel about reframing these as a "threshold." Is the claim that the transition is faster than prior work predicts? That it happens from fewer observations? That the structural properties (local, uniform, cheap) are a NEW set of enablers not described before? The paper asserts novelty without contrasting against what is already known. The boundary between "applying existing trust theory to a new domain" and "discovering a new phenomenon" is never drawn. If the contribution is application, say so explicitly. If it is a new phenomenon, provide the differential evidence.

## Overall Assessment: Borderline Accept

The system design is thoughtful, the iterative process is well-documented, and the forcing-function argument is compelling as design rationale. But the paper asks to be evaluated on the trust threshold as a design principle, and the evidence for that specific claim is thin enough that reviewers will reasonably disagree about whether it clears the bar for a contribution. The paper would be stronger if it framed the trust threshold as a grounded hypothesis rather than an established principle, and if the discussion were more honest about the confounded comparison.
