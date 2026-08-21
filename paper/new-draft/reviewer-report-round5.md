# CHI Review, Round 5

**Rating:** Borderline Accept

**Summary:** This paper presents codoc, a VS Code extension maintaining a bidirectionally synchronized feature tree representing codebase intent. Two iterative studies (N=12 each) ground a design that shifts from file-structure mirroring to intent-level features with proposals, verdicts, and a change ledger. The central finding is a "trust threshold" where developers stop verifying the representation against code after two or three successful checks and begin reasoning from the map alone. The paper argues this threshold is enabled by three structural properties of the architecture (locality, uniformity, cheapness) and that investment in faithfulness infrastructure matters more than investment in expressivity.

---

## 1. Shallow Paragraphs

Most paragraphs in this draft explain WHY rather than merely stating WHAT. The related work and discussion are particularly strong in grounding claims. Two exceptions stand out.

Section 7.2 (lines 81-89 of the findings) asserts that participants "communicated in structure rather than steps" and attributes this to an "environmental rather than cognitive explanation," but the paragraph offers no evidence distinguishing the two. A cognitive explanation (developers who understand the system think structurally) and an environmental one (the comment field shaped the utterance) predict the same outcome in this design. The Suchman citation gestures at the distinction without resolving it.

Section 7.5 (lines 115-125) reports the durability finding but the explanation of WHY codoc's record is qualitatively different from the baseline's accurate-but-agent-authored record stays at one sentence about provenance chains. The reader needs more on what makes a human-accepted record functionally different from an agent-authored one in downstream use.

## 2. Trust Threshold Argument (Section 7 through Section 8.1)

The logical chain holds. Section 7.1 establishes the two-phase behavioral pattern. Section 7.4 reports what participants verified and how quickly they shifted. Section 8.1 theorizes why through three conditions (locality, uniformity, cheapness) and connects to Dzindolet et al.'s finding about mechanistic understanding accelerating calibration. The Parasuraman/Riley taxonomy and the Schemmer attitudinal/behavioral distinction are well chosen.

The weakest link is the claim of discontinuity. The paper says participants did not gradually trust more but "operated in one of two modes" (Section 7.4, line 111). That is a strong empirical claim. The limitations section acknowledges the think-aloud protocol may impose narrative sharpness on a gradual process, but the discussion in 8.1 proceeds as if the binary nature is established rather than hypothesized. The satisficing alternative is addressed in two sentences. A reader who finds the binary framing unconvincing will find the rest of the theorizing weakened because it depends on threshold rather than gradient.

## 3. Related Work Differentiation

Section 2 is the paper's strongest contribution as writing. The three-property gap (maintained, editable, operative) is precise and falsifiable. The Schmalbach [2026] integration is particularly effective because it provides independent evidence that operativity alone is insufficient, showing that without a forcing mechanism the representation is never encountered. The Shipman/Marshall and Henderson citations explaining the formality-informality tension give the reader a framework for understanding why prior systems failed rather than merely listing them. A reader finishes Section 2 knowing exactly what codoc claims to add.

## 4. Logical Gaps

The paper's largest logical gap is between the uniformity argument and the evidence for it. Section 8.1 claims that developers test "the mechanism rather than the content," and that induction from two features to all features is therefore rational. But Section 7.4's evidence for this is observational ("participants chose features that were structurally representative rather than suspicious"). The inference that they did so deliberately in order to test the mechanism is one interpretation of a behavior that satisficing also explains (they checked two convenient features and stopped because twenty minutes is short). The paper needs either stronger evidence that participants were testing machinery, or a more qualified claim.

The bundle comparison problem (acknowledged in Section 9.1) is real and the paper handles it honestly, but the discussion in 8.1 and 8.2 sometimes writes as if the trust threshold is isolated when it has not been experimentally isolated from proposals, co-location, and the forcing function.

## 5. Section 8.4 Tension Treatment

This reads as genuine intellectual honesty. The paper reports a concrete failure (the sync loop normalized away a planted problem before the participant encountered it), identifies the structural reason (G1 and G2 cannot be simultaneously maximized), describes the rejected alternative (diff-size threshold), explains the chosen boundary (structural significance), and acknowledges the boundary's empirical uncertainty. The treatment is not a buried limitation. It is a contribution to the design space. Any synchronized representation must choose where to put this boundary and the paper gives future designers a principled place to start.

---

## Three Specific Weaknesses

**W1.** The extensive DATA placeholders throughout Section 7 (lines 71, 77, 87, 93, 105, 109, etc.) make the confirmatory claims formally unevaluable. The paper asks a reviewer to accept a pre-registered directional hypothesis on the basis of a structure that says "[DATA: meeting/not meeting] the pre-registered criterion." The qualitative argument is convincing but the quantitative skeleton is empty.

**W2.** The binary threshold claim is under-evidenced relative to its centrality. The paper's title finding and entire Section 8.1 depend on a discontinuity that is reported observationally from think-aloud coding without formal transition analysis. The limitations name the alternative (Section 9.1, demand characteristics) but the discussion proceeds as if it were settled.

**W3.** Section 7.2 (lines 81-89) claims participants communicated structurally because of the medium rather than because of their understanding, but the within-subjects design cannot separate these explanations. Both conditions used the same participants who had already read the tree. The "environmental" framing is asserted without a test that would distinguish it from a cognitive one.

## Three Specific Strengths

**S1.** The three-property gap statement in Section 2.3 (lines 28-33) is precise, falsifiable, and genuinely useful to the field. It gives future work a clear criterion for evaluating whether a new tool advances the state. The integration of Schmalbach [2026] as independent evidence strengthens it further.

**S2.** The iterative design narrative (Section 3) is unusually honest about what failed and why. The V1 recall data (median 8 vs 5) validates one property while the qualitative failures (P4, P9 quotes) motivate the redesign. The "core lesson" paragraph (Section 3.2, lines 36-37) articulates precisely what file-structure mirroring achieves and what it cannot. This is a model of how to report formative work at CHI.

**S3.** Section 8.4 treats the faithfulness-visibility tension as a genuine design dilemma rather than a limitation to apologize for. The structural argument (lines 48-54 of the discussion) that diff-size thresholds miss semantic significance while structural-significance thresholds miss behavioral changes is a contribution other designers can use immediately.

---

## Minor Notes

- The abstract's "2-3 checks" specificity is stronger than the evidence warrants given the DATA placeholders in Section 7.4.
- Section 5.4's connection between architecture and trust conditions is well argued but reads as if written after the study rather than predicted before it. If the three conditions were pre-registered predictions, saying so would strengthen the argument.
- The Glorikian [2026] citation in Section 8.1 provides converging evidence but the paper does not say whether that finding was from a peer-reviewed venue or an industry report. Provenance matters for a claim this central.
