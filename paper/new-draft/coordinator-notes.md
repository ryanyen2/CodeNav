# Coordinator / Meta-Reviewer Notes

## Status
- [x] Abstract (00-abstract.md) — 178 words, stable
- [x] Introduction (01-introduction.md) — Drop → World-Building → Players → Player One → Deal → Loot structure
- [x] Related Work (02-related-work.md) — three areas, argumentative framing
- [x] Design Process / Section 3 (03-design-process.md) — design goals, V1 study + failures, redesign response
- [x] System Design + Architecture / Sections 4-5 (04-system-design.md) — walkthrough, goal composition, architecture
- [x] Study + Findings / Sections 6-7 (06-study-findings.md) — pre-registered study, thematic findings with [DATA] placeholders
- [x] Discussion + Limitations / Sections 8-9 (08-discussion.md) — trust threshold, communication, limits, implications
- [x] Figure 1 (figures/figure1-teaser.svg) — left/right comparison: without codoc vs with codoc
- [x] Figure 2 (figures/figure2-architecture.svg) — two-loop architecture diagram

## Word Count: ~13,344 (target: ~12,500, CHI limit: ~14,000) ✅
Within CHI limits. When [DATA] placeholders are replaced (~250 words of markers become ~50 words of actual data), total should settle around ~13,144. Acceptable. Background agents may compress further.

## DATA Placeholders to Fill After Study Runs
All [DATA: ...] markers in 06-study-findings.md and 00-abstract.md need real numbers once the 12 participants complete.
Placeholder quotes need real transcript excerpts.

## Reviewer Critique Status (from review-r3.md and review-r5.md)

### Addressed (2026-08-20/21)
- [x] Demand characteristics paragraph in §9.1
- [x] Bundle comparison limitation in §9.1
- [x] Temporal ordering acknowledgment in §8.2
- [x] V1-to-V2 empirical closure in §7 opening
- [x] Alternative explanations forward-reference in §7.4
- [x] Notification-alone confound paragraph in §8.5
- [x] ~~Glorikian [2026]~~ REMOVED — hallucinated citation, cannot verify
- [x] §7.2 environmental-vs-cognitive distinction qualified per R5-W3
- [x] §8.1 trust threshold qualified as "design hypothesis" not confirmed mechanism per R5-W2
- [x] §7.5 functional difference of human-accepted record explained per R5 shallow-paragraph note
- [x] Hallucinated citations (Glorikian, Oukay) identified and removed
- [x] Conclusion scenario bridged to evaluation ("The evaluation suggests why...")
- [x] "Compounding" language softened throughout (now "accumulate")
- [x] Causal language in §8.2 weakened appropriately
- [x] §5.4/§8.1 overlap confirmed as complementary (mechanism vs phenomenon)

### Blocked on study data
- [ ] Fill [DATA] placeholders with actual results
- [ ] Operationalize threshold with coding scheme (needs protocol details)
- [ ] Describe planted problems in detail (needs study materials)
- [ ] Fill §6.5 participant demographics
- [ ] Replace [PLACEHOLDER QUOTE] entries
- [ ] Show empirical closure between V1 failures and V2 improvements with data

### Structural choices (acceptable as-is)
- Paper presents itself as design argument supported by study (per reviewer recommendation in R3 §6.2)
- Contributions clearly separate engineering from empirical
- Quantitative evidence positioned as consistency check, qualitative as primary

## Fresh Citations Integrated (2026-08-20/21)
- Treude & Baltes [2026] — context rot, 23% staleness in 356 repos (§1)
- Stray et al. [2026] — 2-year Copilot study, perceived-vs-actual gap (§9.1)
- Dhanorkar et al. [2026] — four oversight modes (§8.5)
- Schmalbach [2026] — forced documentation in delegation contracts (§2.3, §8.5)
- METR [2025] — RCT, 19% slower with AI tools, 39-point perception gap (§1, §9.1)
- Stack Overflow 2025 — 84% adoption, 29% trust, declining (§1)
- Baltes, Speith et al. [2026] — trust maturity gap in SE, adopt established models (§8.1)
- Faros AI [2025] — 91% review time increase, 154% PR size growth under AI adoption (§8.5)
- Shukla et al. [2026] Hedwig — dynamic autonomy, trust evolves across sessions (§8.5)
- Huang et al. [2025] — professionals insist on control regardless of AI quality (§2.3)
- ~~Glorikian [2026]~~ — REMOVED, could not verify in Google Scholar/SSRN
- ~~Oukay et al. [MSR 2026]~~ — REMOVED, could not verify in Google Scholar
- Faros AI [2026] — 5x review time under high AI adoption (§8.5)
- LinearB [2026] — 4.6x wait, 2x rush in AI PR review (§8.5)
- Ko et al. [2006] — foraging miscalibration, overestimate value of nav (§7.1)
- Monperrus [2026] — "end of code review," rubber-stamp argument (§8.5)
- Vella & Blincoe [2026] — 6-month longitudinal trust calibration as "ongoing engineering work" (§8.1)
- Demirer, Musolff, & Yang [2026] — 180% more commits but only 30% more merged PRs (§1)

## Consistency Checks (verified)

### Terminology
- "feature tree" — consistent across all sections
- "trust threshold" — consistent
- "communication layer" — consistent
- "codoc" — lowercase throughout
- "code drift" — prose form in narrative
- "proposals" — consistent
- "change ledger" — consistent

### RQ Chain
- RQ1 stated §1 → answered §7.1, §7.2, §7.4 → discussed §8.1 (trust threshold), §8.2 (communication)
- RQ2 stated §1 → answered §7.3 → discussed §8.4 (G1/G2 tension), §8.5 para 2 (proposals in context)
- RQ3 stated §1 → answered §7.5 → discussed §8.5 para 3 (forcing functions)

### Design Goal Threading
- G1 (Faithfulness): §3.1 → §4.3 → §5.4 → §7.4 → §8.1
- G2 (Change Visibility): §3.1 → §4.3 → §7.3 → §8.4
- G3 (Orientation Efficiency): §3.1 → §4.3 → §7.1 → §8.2
- G4 (Communication Legibility): §3.1 → §4.3 → §7.2 → §8.2
- G5 (Decision Durability): §3.1 → §4.3 → §7.5 → §8.5

## Style Pass — All Violations Removed
Applied four hard rules across all sections:
1. No colon in prose (except pseudocode)
2. No parentheses in prose (except pseudocode/statistics)
3. No em-dash
4. No terse "X, not Y" fragments
5. No second-person (you/your) except quoted hypothetical

Verified clean as of 2026-08-21 final check.
