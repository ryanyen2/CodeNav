# Review R5: Targeted Gap Check Against R3

## 1. V1-to-V2 Empirical Closure (R3 item 5.2)

**Status: Partially addressed.** Section 6.1 says "Study 2 tests whether the redesign delivers what the first version could not." This is a forward-looking claim about what the study TESTS, not what it FOUND. Nowhere in Sections 7 or 8 does a sentence say "V2 participants communicated through the representation where V1 participants could not" or "proposals surfaced change history that V1's mirror could not show." The closure is implied by juxtaposition but never stated.

**Where:** End of the first paragraph of Section 7.3, after the detection-coverage sentence.

**Fix:** Add one sentence: "This directly addresses the G2 failure reported in Section 3.2, where the file-structure mirror showed current state but could not distinguish what the developer authored from what the agent changed."

## 2. Temporal Ordering of Trust and Communication (R3 item 2.2)

**Status: Partially addressed.** Section 9.1's "Bundle comparison" paragraph acknowledges that the causal chain is "a plausible interpretation of the observed pattern, not an isolated experimental demonstration of mechanism." However, it does not name the specific alternative that affordance availability rather than trust enabled communication, nor does it acknowledge the absence of temporal-ordering evidence. The claim in 8.2 ("A developer who does not trust the representation will not write into it") still reads as a finding rather than an inference.

**Where:** Section 8.2, second paragraph, after the quoted sentence.

**Fix:** Add: "We cannot isolate this direction from the alternative that the tool's communication affordances preceded and enabled trust rather than the reverse, because the study did not code the temporal ordering of verification and communication episodes."

## 3. Alternative Explanations in Section 7.4

**Status: Not addressed.** Section 7.4 asserts the binary shift without any sentence acknowledging satisficing, fatigue, or demand effects. The demand-characteristics paragraph in 9.1 covers this well, but 7.4 itself reads as an unqualified claim. The reviewer asked for at least one sentence here.

**Where:** After the sentence "The transition between modes happened within minutes and after [DATA: median number] verified features."

**Fix:** Add: "Section 9.1 discusses alternative explanations for the apparent sharpness of this transition, including satisficing under time pressure and the think-aloud protocol's tendency to narrativize gradual shifts as discrete decisions."

## 4. Conclusion Scenario vs. Study Findings (R3 item 5.4)

**Status: Mostly addressed but slightly blurred.** The conclusion opens "The developer from the introduction comes back the next morning," which anchors it as the intro's motivating scenario rather than a study finding. The transition to "The mechanism is not any individual feature..." in the next paragraph shifts to empirical claims without a marker separating the design illustration from the study finding.

**Where:** Between the scenario paragraph and the "mechanism" paragraph.

**Fix:** Begin the second paragraph with: "The study does not test this overnight scenario, but it tests the mechanism behind it."

## 5. New Problems from Recent Edits

**Style:** Section 8.1 contains "A tool with three features the developer trusts is worth more than one with thirty they do not" (acceptable as comparison, not the flagged "X, not Y" imperative pattern). Section 8.2 has "not that participants used codoc to *understand* code but that they used it to *communicate*" which is the "not X but Y" construction the style guide flags. Section 8.5 uses "not just code" and "not just about producing documentation" in consecutive principles.

**Logical gap:** None found. Cross-references between sections are intact.

**Redundancy:** Sections 5.4 and 8.1 both discuss conditions for trust formation. They are differentiated (5.4 is engineering mechanisms, 8.1 is behavioral conditions) and 8.1 explicitly back-references 5.4. No action needed.
