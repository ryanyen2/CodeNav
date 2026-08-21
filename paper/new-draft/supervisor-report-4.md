# Supervisor Report 4 — Deepening Pass

Five edits, net reduction of approximately 27 words.

## Edit 1 — Causal mechanism for proposal-driven detection (§7.3)

Added two sentences after the "newspaper vs. question" line separating the two mechanisms that drive the detection advantage. Co-location ensures the developer *encounters* the discrepancy while reading. Obligatory response ensures that encounter becomes a *recorded decision*. The paper previously conflated these under one vague gesture at "forcing." The false-alarm null result is the evidence that co-location drives detection (participants found real problems without over-reporting), while the forcing function drives durability.

Deleted: "G2 in practice means making a decision unavoidable rather than making information available" (redundant summary sentence).

## Edit 2 — Methodological reasoning for detection as the second study's DV (§6.1)

Replaced the bare statement "The dependent variables shift from recall to detection" with a sentence explaining *why*. Measuring recall again would replicate Study 1. Detection asks a different cognitive question, whether a trusted map enables noticing contradictions. This makes the progression from Study 1 to Study 2 read as a methodological argument rather than a list.

## Edit 3 — Sharp distinction from literate programming (§4.1)

Replaced "The tree is not generated from code and discarded" with a sentence that draws the line against literate programming in one breath. The distinguishing property is continuous machine maintenance across changes by any party. Literate programming's explanation is woven once and abandoned to drift. This preempts the "isn't this just literate programming with loops?" reviewer question by placing the answer in the system design section where readers form their first understanding of what codoc is.

## Edit 4 — Trust threshold failure mode / automation surprise (§8.1)

Added one sentence after the behavioral-trust definition addressing what happens when the system fails *after* threshold formation. The asymmetry: decisions made post-threshold were made without independent verification, so a single stale description retroactively undermines all of them. This engages the automation-surprise literature (Parasuraman and Riley's "misuse" category, already cited in the same paragraph) without adding new citations.

Deleted: "Below it, the developer has attitudinal trust but still checks. Above it, they delegate verification to the tool" (already stated in the preceding sentences).

## Edit 5 — Contribution framing (§1)

Rewrote the contributions paragraph to break the circularity. The old framing listed (a) a design principle and (b) evidence for it, but the principle was derived from the evidence. The new framing separates clearly: (a) is engineering — an architecture that holds three properties no prior system achieved simultaneously; (b) is empirical — evidence that this architecture produces the trust threshold. The connecting principle (synchronization > expressivity) is stated as the link between the two rather than as a free-standing contribution, making clear it is a claim the architecture instantiates and the study supports rather than an a priori axiom.
