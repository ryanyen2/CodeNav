# Fresh Papers for the Codoc CHI Paper (2025-2026)

Searched 2025-08-21. Three categories aligned to the paper's central claims.

---

## 1. Trust Calibration: Threshold/Binary Effects in Human-AI Systems

### Glorikian, H. (2026). Proofseconds: How Verification Behavior Decays in AI-Assisted Knowledge Work. SSRN, Paper No. 6473740.

**Key finding:** Introduces "proofseconds" as a unit measuring verification effort in AI-assisted knowledge work. Documents a "step-function collapse" in verification behavior: once a user crosses a trust threshold after a single high-salience interaction, proofseconds drop from high investment to near-zero. This is not gradual decay but an abrupt phase transition from diligent checking to near-total reliance.

**How it strengthens the codoc paper:** This is the closest existing validation of the paper's central "trust threshold" claim. Glorikian's "step-function collapse" is the same phenomenon the codoc study observes (developers stop verifying the representation against code and start reasoning from it alone). Cite in the Discussion to show the binary behavioral shift is not idiosyncratic to codoc but a general property of AI-assisted knowledge work. The "proofseconds" framing also gives vocabulary for the verification cost the paper already measures.

**Section:** Discussion (trust threshold interpretation), Related Work (trust calibration)

---

### Ding, S., Pan, X., Hu, L., & Liu, L. (2025). A new model for calculating human trust behavior during human-AI collaboration in multiple decision-making tasks: A Bayesian approach. Computers & Industrial Engineering, Elsevier. (Cited by 21)

**Key finding:** Proposes a Bayesian model for trust that captures dynamic changes in human self-confidence and confidence in AI across repeated tasks. Reports 97.6% prediction accuracy. The model posits the existence of a performance threshold that governs when trust dynamically shifts during AI interaction, with trust behavior formation explained as a cognitive process triggered by exceeding or falling below this threshold.

**How it strengthens the codoc paper:** Provides a computational modeling perspective that validates the threshold mechanism. If the codoc paper frames its trust threshold as a behavioral observation, Ding et al. offers the mechanistic explanation: a Bayesian agent that accumulates evidence until a threshold tips the decision from "verify" to "rely." Useful for the Related Work section to ground the observation in existing decision-theoretic models.

**Section:** Related Work (trust models in HCI)

---

## 2. Developer Tool Adoption and Longitudinal Effects

### Stray, V., Brandtzaeg, E.G., Wivestad, V.T., Barbala, A., & Moe, N.B. (2026). Developer Productivity With and Without GitHub Copilot: A Longitudinal Mixed-Methods Case Study. Proceedings of the 59th Hawaii International Conference on System Sciences (HICSS-59), pp. 7413-7422.

**Key finding:** A 2-year longitudinal study of Copilot usage at NAV IT analyzing 26,317 commits from 703 repositories. Found no statistically significant changes in commit-based activity for Copilot users after adoption, despite users perceiving themselves as more productive. The discrepancy between subjective and objective measures only became visible through longitudinal design; a short-term study would have captured only the perceived gains.

**How it strengthens the codoc paper:** Directly supports the Limitations argument that a 20-minute session captures cost but not payoff. Stray et al. show that even a tool with no upfront investment (Copilot) required 2 years to reveal the gap between perceived and actual productivity. Codoc has a higher upfront cost (building the tree), so the argument that benefits accrue on a longer timescale than a lab session is well-precedented. Also validates the paper's use of mixed methods (behavioral + interview).

**Section:** Limitations (study duration defense), Discussion (cost-benefit timescales)

---

### Ribeiro, D., Alves, B., Souza, G., Franca, C., & Souza, A. (2026). ADEMM: A Longitudinal Method for Monitoring Developer Efficiency in Industry. arXiv:2608.16580.

**Key finding:** A mixed-method longitudinal study with 27 developers over twelve survey cycles. Argues that "most studies rely on one-time assessments or fixed instruments, limiting the ability to monitor how barriers emerge and change over time." Found that certain tasks become "more difficult, costly, or slow, even when developers eventually adapt," demonstrating an initial cost period before benefits.

**How it strengthens the codoc paper:** The "initial cost before benefits" finding is the exact dynamic the codoc study's 20-minute window captures the wrong end of. Ribeiro et al.'s twelve-cycle design illustrates what a proper longitudinal evaluation of codoc would require. Cite when defending the study design choice: the paper chose to measure the trust threshold (which is observable in a single session) rather than the long-term productivity payoff (which is not).

**Section:** Limitations (study duration), Future Work (longitudinal evaluation design)

---

## 3. Documentation Decay and AI-Maintained Documentation

### Treude, C. & Baltes, S. (2026). Context Rot in AI-Assisted Software Development: Repurposing Documentation Consistency for AI Configuration Artifacts. arXiv:2606.09090.

**Key finding:** Empirically measured documentation staleness across 356 repositories, finding stale code element references in 23.0% of repos in AI configuration files (CLAUDE.md, AGENTS.md, .cursorrules). Introduces the term "context rot" for the phenomenon where persistent context files become outdated as the codebase evolves. Demonstrates that existing documentation consistency tools can detect but not prevent this rot.

**How it strengthens the codoc paper:** This is the empirical baseline for codoc's core motivation. Codoc's bidirectional synchronization (Loop A reflects code changes into the representation) is precisely what Treude & Baltes show is missing: a mechanism that prevents context rot rather than merely detecting it. The 23% staleness rate in static documentation gives a concrete number to contrast against codoc's synchronized approach. Cite in the Introduction (motivation: why static documentation fails) and Related Work (documentation consistency).

**Section:** Introduction (documentation problem), Related Work (AI-era documentation)

---

### Grabowski, H. (2026). The Spec Growth Engine: Spec-Anchored, Code-Coupled, Drift-Enforced Architecture for AI-Assisted Software Development. arXiv:2606.27045.

**Key finding:** Documents the damage from AI generating "lines per minute guided by a stale spec" and proposes coupling specifications directly to code to prevent drift. Introduces "drift enforcement" as a design principle for specifications that must stay synchronized with their implementation.

**How it strengthens the codoc paper:** Grabowski arrives at the same architectural insight as codoc (specifications must be coupled to code) from a different angle (preventing AI from building on stale assumptions). This validates codoc's design without being the same system. The "drift enforcement" framing maps to codoc's Loop A, and the documented failure mode (AI building on stale specs) is what codoc's trust threshold protects against: developers trusting the representation only AFTER they have verified it is synchronized.

**Section:** Related Work (synchronized documentation), Discussion (design validation)

---

## Summary: Citation Strategy

| Claim in paper | Best supporting citation |
|---|---|
| Trust is non-linear (binary shift) | Glorikian 2026 (step-function collapse in proofseconds) |
| The threshold is a general phenomenon, not codoc-specific | Ding et al. 2025 (Bayesian threshold model, 97.6% prediction) |
| 20-minute session captures cost but not payoff | Stray et al. 2026 (2-year study needed for Copilot); Ribeiro et al. 2026 (initial cost period before benefits) |
| Static documentation decays; synchronization is needed | Treude & Baltes 2026 (23% staleness in 356 repos) |
| Code-coupled representation is the right architecture | Grabowski 2026 (drift-enforced specs; damage from stale AI context) |
