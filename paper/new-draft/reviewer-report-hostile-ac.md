# Hostile AC Assessment: Final Decision Review

**Role:** Associate Chair, CHI 2027. 200+ reviews across HCI, CSCW, developer tools. Voting disposition: skeptical lean-reject until proven otherwise.

---

## Overall Assessment

This paper builds a tool (codoc), evaluates it, and claims its central contribution is a *design principle* (the trust threshold). The writing is strong and the self-awareness about limitations is above average. But the paper has structural problems that no amount of qualification can fix, because the qualifications themselves reveal the gap between what is claimed and what is demonstrated.

**Recommendation: Borderline. Three weaknesses below determine the vote.**

---

## Weakness 1: The "trust threshold" contribution is tautological under scrutiny (FATAL if unaddressed)

The paper's central claim is:

> "The representation becomes useful not when it gains capabilities but when it crosses a trust boundary."

Strip the theoretical framing and this says: *people stop checking things they believe are correct.* That is not a contribution. That is the definition of trust. The paper wraps this observation in Schemmer, Lee & See, Parasuraman & Riley, Bainbridge, Dzindolet, and Vella & Blincoe to create the appearance of theoretical depth, but the underlying observation remains: *if information looks right when checked, people stop checking it.*

The paper tries to escape this by claiming the contribution is not the threshold itself but the *three conditions* that enable rapid formation (local, uniform, cheap). But those conditions are descriptions of the system's architecture, not independently testable predictions. Consider:

> "The conditions were design predictions, not post-hoc rationalizations of the evaluation."

This sentence claims pre-registration of the *architectural conditions*, not of the *trust threshold*. But the conditions were baked into the system before evaluation, so there is no condition in which locality, uniformity, or cheapness was absent. The "prediction" is unfalsifiable within this study because there is no comparison condition that has one without the others. The paper acknowledges this ("not experimentally isolated from it") but then continues to reason from the conditions as though they were established. The qualification is there; the reasoning ignores it.

**What would fix it:** An ablation showing that removing uniformity (e.g., a condition where some features use different maintenance mechanisms) delays or prevents threshold formation. Without that, the "three conditions" are architectural descriptions, not validated design principles.

---

## Weakness 2: The contribution is architecture disguised as generalizable knowledge (Weakens but survivable)

The paper claims:

> "The principle is not specific to code intent. Any persistent representation that a person must trust to be useful...faces the same engineering choice between expressivity and verifiability. The three conditions are testable predictions."

But the evidence is entirely from one system in one domain with one task. The generalization to "data lineage, infrastructure state, or organizational knowledge" is speculative. More problematically, the argument for generalization is structural ("the same logic applies") rather than empirical. Structural arguments are cheap because they can be constructed for any system. I could equally argue that a well-maintained wiki with good search satisfies locality and cheapness, and that consistent formatting satisfies uniformity, so trust should form there too. It doesn't. Something else is going on that the three conditions don't capture, and the paper cannot identify what because it has only one system instance.

The paper would be stronger if it admitted the contribution is a *specific architecture for a specific problem* (maintaining intent-code consistency for agent-era development) rather than overreaching for a general design principle. The principle may indeed generalize, but this paper cannot show that.

---

## Weakness 3: The evaluation measures *something* but not necessarily *trust* (FATAL if unaddressed)

The confirmatory measures are detection coverage and durable written trace. Neither directly measures trust. Detection coverage measures whether participants found planted problems. Durable trace measures whether they wrote things down.

The trust threshold is observed through *behavior* (stopped opening files) coded from think-aloud. But here's the gap: the paper cannot distinguish:

1. "I trust this, so I stop checking" (the claimed mechanism)
2. "The tree is faster to use than the code, so I use it for efficiency" (an alternative)
3. "I've oriented myself enough to do the task, so I stop exploring" (task completion, not trust)

Interpretation (2) is never seriously addressed. A developer who finds the tree faster may never open files regardless of trust, because the tree is simply a more efficient navigation surface. The paper acknowledges this for the communication finding ("the study cannot isolate it from the communication affordances that trust unlocks") but does not apply the same logic to the threshold itself. The *threshold* might be the moment the developer has enough orientation to work, not the moment they decide the tool is trustworthy.

Interpretation (3) is dismissed with the satisficing argument:

> "Satisficing predicts a gradual decline in verification rate"

But task completion also predicts a rapid cessation of exploration once sufficient orientation is achieved. A developer reading a new codebase explores until they have "enough" context, then stops. This would produce the exact same behavioral signature as a trust threshold: rapid transition from exploring to working, with no further file-opening. The paper never addresses this alternative.

**What would fix it:** A measure that distinguishes trust from sufficiency. For example: after the supposed threshold crossing, present a *challenge* (new information suggesting the tree might be wrong). If participants revert to checking, that supports trust (fragile, challengeable). If they don't notice or care, that might be over-reliance or simple task completion. The study does not include such a challenge probe.

---

## Weakness 4: The "contamination property" is asserted rather than demonstrated (Weakens but survivable)

The paper states:

> "a single staleness failure poisons the entire artifact"

And builds the trust threshold's fragility argument on this. But the evaluation never tested it. No participant encountered a stale description during the study (except the one case where Loop A absorbed a change before the participant saw it, which is a different mechanism). The fragility of the threshold is claimed from theory (Lee & See recovery asymmetry, Schemmer behavioral trust collapsing on failure) rather than observed.

This matters because the contamination property is the *foundation* of the argument that the three conditions are necessary rather than nice-to-have. If the threshold is actually robust to occasional inaccuracy (which many real-world trust relationships are), then the extreme investment in faithfulness machinery may be over-engineered relative to a simpler tool that is occasionally wrong.

---

## Weakness 5: The within-subjects design confounds order with tool for the qualitative findings (Weakens but survivable)

The paper states order was counterbalanced. But qualitative findings like "once across the threshold, participants used codoc to communicate" cannot be counterbalanced. A participant who saw codoc first brings structural vocabulary to their baseline condition. A participant who saw baseline first already tried step-by-step instructions and found them tedious before encountering codoc's feature-scoped comments. The qualitative themes are contaminated by order effects that averaging cannot fix because each participant contributes different themes depending on what they saw first.

The quantitative measures survive counterbalancing. The qualitative narrative that gives the paper its intellectual coherence does not, and the paper never discusses this.

---

## Summary of Vote-Determining Issues

| # | Weakness | Severity | Can be fixed in camera-ready? |
|---|----------|----------|-------------------------------|
| 1 | Trust threshold is tautological; three conditions are untested | Fatal | No (requires new study) |
| 3 | Behavior measured is not distinguishable from sufficiency/efficiency | Fatal | No (requires probe/challenge design) |
| 2 | Generalization is speculative, contribution is actually architectural | Survivable | Yes (scope down claims) |
| 4 | Contamination fragility is theorized not observed | Survivable | Yes (acknowledge explicitly) |
| 5 | Order effects on qualitative themes | Survivable | Yes (discuss in limitations) |

**Final vote: Borderline Reject (3).**

The architecture is interesting and well-designed. The evaluation is honest about its limits. But the paper's central intellectual contribution, the trust threshold as a generalizable design principle, rests on an observation that cannot be distinguished from the null hypothesis (people stop checking things that look correct) given this study design. The paper would be a solid contribution to the developer tools community at ICSE or FSE if it framed itself as "here is an architecture that solved a hard problem and here is evidence it works." Framing it as a cognitive design principle requires evidence about cognition that a system evaluation cannot provide.

---

*Note: The [DATA] placeholders are not evaluated here as the AC assumes they will be filled with real numbers. If the numbers are weak (e.g., 7/12 barely meeting criterion, or detection difference < 1 problem), that changes the assessment substantially.*
