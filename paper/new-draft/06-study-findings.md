# 6. Evaluation, Reviewing an Agent's Change

## 6.1 Research Questions

The first study established that a synchronized representation helps developers orient, raising recall from a median of 5 to 8 components. But it also showed that a file-structure mirror cannot surface change history or hold cross-cutting intent. The redesign addressed those failures with intent-level features, proposals for structural changes, and bidirectional synchronization. Study 2 tests whether the redesign delivers what the first version could not. Study 1 measured whether developers *built a map*. Study 2 asks whether having a map changes what they *find* and what they *record*.

- **RQ1**, exploratory. How do developers use a synchronized intent representation when reviewing changes an agent made to a codebase? This tests G3 orientation efficiency and G4 communication legibility.
- **RQ2**, confirmatory. Does a synchronized intent representation improve a developer's ability to detect and correctly attribute problems in agent-generated changes? This tests G2 change visibility.
- **RQ3**, confirmatory. Does working through an intent representation produce more durable decision records than working through a companion document maintained by the agent? This tests G5 decision durability.

RQ2 and RQ3 are pre-registered with directional predictions and stopping rules. RQ1 is exploratory and its findings are reported as questions for the next study rather than as tests of a claim.

## 6.2 Study Design

We conducted a within-subjects controlled study with 12 participants, each completing both conditions with order counterbalanced and projects alternating so nobody meets the same task twice. Within-subjects is essential because individual differences in code review strategy dominate detection variance, and twelve participants would be uninterpretable between-subjects. Recruitment stopped at twelve with no optional stopping and no interim look at outcome measures.

### Task, Reviewing a Recorded Agent Session

The study asks participants to review a change an agent already made rather than to write code alongside it. A review task isolates judgment from programming ability and reflects the emerging dominant interaction, deciding whether the agent's decisions are acceptable rather than specifying what to build.

Comparability requires that every participant reviews the same agent session. Each participant types their own prompt from a task page, but the agent's response is a pre-recording played back identically. Participants are not told a recording exists. After the first turn, the real assistant carries the recorded session's context so the participant can ask follow-up questions. Playback runs at accelerated rates with the factor reported per project, preserving temporal gaps proportionally.

### Constructed Stimulus and Planted Problems

Each project plants a small number of problems in the agent's work, decisions that contradict the project's stated commitments or introduce inconsistencies. The agent was steered during recording until it produced these, and every steer is documented alongside the frames. codoc's own response was written by a real daemon running against the recorded change. If the daemon failed to surface a planted problem, that failure is reported. The study asks whether a person *ends up knowing what was decided* rather than whether the information was available.

## 6.3 Conditions

**codoc condition.** VS Code with the codoc extension active. The feature tree shows proposals from the recorded session, with structural changes as accept or reject and drift marked as code drift status. Participants have full access to navigation, code links, `/codoc:ask`, search, and comments.

**Baseline condition.** VS Code with a `CLAUDE.md` maintained by the agent, containing the same informational content as the codoc tree but as flat Markdown, rewritten after each action so it is always accurate at handover. The distinction under test is encounter structure rather than information availability. CLAUDE.md requires the developer to visit and independently recognize change. codoc places proposals at the point of relevance and waits for a verdict.

The comparison is intentionally asymmetric. The asymmetry IS the mechanism under test. False alarms are reported alongside detection to check whether codoc raises noise rather than signal. Both conditions use an identical study logger recording file navigation, dwell time, and edit events.

## 6.4 Measures

**Confirmatory.** *Detection coverage* rates each planted problem as 0 for not found, 1 for found, or 2 for found and correctly attributed, blind to condition and reported as proportion of maximum. Detection rather than time-to-find is the primary measure because the claim is not that codoc makes search faster but that it makes certain problems *findable at all*. *Durable written trace* records per problem whether the participant's final record says what was decided. In the codoc condition that means an accept or reject or authored edit after handover. In the baseline that means a line in `CLAUDE.md`.

**Pre-registered secondary.** *Record truth* rates each claim as true, contradicted, or missing against the final record, with codoc exported to Markdown so the rater cannot distinguish conditions. Reported as change from handover to end. *False alarms* measures correct changes flagged as wrong and is reported alongside detection.

**Exploratory.** Context switches from focus events, strategy codes from think-aloud recordings, custom 7-point Likert items, and interview themes.

## 6.5 Participants

We recruited 12 experienced programmers with at least 7 years of experience who use AI coding tools at least 10 times per week. [DATA: age M, SD; experience M, SD; AI familiarity M, SD; gender breakdown]. Each received [compensation amount]. Exclusion criteria were replay failure, no logged events for a condition, or prior codoc experience.

## 6.6 Procedure

Consent and background questionnaires were completed days before. Each session lasted 90 minutes with two 40-minute condition blocks separated by a break and a 14-minute interview. Each block comprised project and tool briefing at 10 minutes, the review task at 20 minutes, closed-book questions at 5 minutes, and a workload questionnaire at 5 minutes. Think-aloud was maintained throughout.

## 6.7 Analysis

**Quantitative.** Paired differences with 95% bootstrap confidence intervals from 10,000 resamples, leading with the estimate rather than the p-value. Wilcoxon signed-rank with matched-pairs rank-biserial for continuous measures, exact McNemar for per-problem binaries. The pre-registered criterion is that at least 7 of 12 participants have strictly higher coverage in codoc.

**Qualitative.** Reflexive thematic analysis [Braun & Clarke, 2019] on interview transcripts and think-aloud recordings. Two researchers independently coded 25% of transcripts, discussed to consensus, refined the codebook, and one coded the remainder. Protocol analysis on think-aloud data generated strategy codes for reading order.

---

# 7. Findings

All participants completed both conditions within the allotted time. [DATA: mean iterations/prompts per condition]. We organize findings thematically rather than by measure, mixing quantitative evidence with qualitative observations. Confirmatory results are marked as such and all other findings are exploratory.

## 7.1 After the Initial Mapping, Participants Stopped Returning to Code

[DATA: context switches codoc vs baseline, paired difference, 95% CI, effect size]

Participants who used codoc showed a two-phase pattern. In the first few minutes, they read the feature tree and clicked code links to verify that descriptions matched what was in the files, building an initial mapping between the representation and their mental model. Once satisfied that the map held, they rarely returned to source files and reasoned from the tree alone.

> "[PLACEHOLDER QUOTE: participant describing the moment they stopped opening files and why, that the map was trustworthy enough to reason from]"

Baseline participants navigated between `CLAUDE.md` and source files throughout the session, with [DATA: Mdn_B context switches] transitions compared to [DATA: Mdn_C] in the codoc condition [DATA: test statistic, p, effect size]. The flat document did not carry enough structural signal to let them stop checking.

The mechanism is not that the codoc tree contains better information, since both conditions held identical content at handover. The mechanism is that explicit bindings make verification *local*. Each feature claims specific code through its binding map. Checking a feature means following a link and confirming correspondence, a task bounded by the feature's scope rather than the project's size. In information foraging terms [Pirolli & Card, 1999], a binding is a high-confidence scent cue. The developer follows a link, lands in a bounded region, confirms or refutes, and closes the file. No foraging decision is required because the binding specifies both destination and scope. A flat document offers the same facts but no scent. Reading "the auth module uses JWT" in CLAUDE.md tells the developer what to look for but not where to look, and the resulting search through fifteen files is what makes verification expensive enough to skip. Ko et al. [2006] found that developers overestimate the value and underestimate the cost of roughly half their navigation choices, confirming that foraging without strong cues is systematically miscalibrated. A binding eliminates the estimation problem by making the cost known in advance, which is what makes the "cheapness" condition from Section 8.1 achievable. Verification takes seconds rather than minutes not because the code is simpler but because the search space is bounded before the developer opens the file.

## 7.2 Developers Communicated in Structure Rather Than Steps

That shift from verification to reliance also changed how participants directed the agent's work. When participants directed corrections or asked questions, they did so through the representation rather than by writing step-by-step instructions to the agent. The dominant pattern was to use `/codoc:plan` or comments on specific features, expressing what the code *should be* rather than prescribing how to get there.

> "[PLACEHOLDER QUOTE: participant explaining that they stated what they wanted the feature to look like, not what steps to take]"

[DATA: N] of 12 participants used comments or plan commands at least once during the task. Of those, [DATA: proportion] expressed intent as a structural statement such as "this feature should not depend on X" or "these two should be merged" rather than an imperative instruction such as "remove the call to X on line 47."

The evidence is consistent with an environmental explanation but does not confirm it. The same participants who wrote step-by-step instructions in the baseline expressed intent structurally when given a feature-scoped comment field. The medium shaped the message because the comment field's scope IS a structural declaration. Writing "this feature should not depend on X" in a comment attached to a specific feature already identifies the subject, the constraint, and the scope of the change without requiring a file path or line number. The affordance makes structural communication the path of least resistance. However, the within-subjects design cannot separate this from a cognitive explanation. By the time participants entered the codoc condition, they had already built a structural understanding from reading the tree. A developer who understands the system structurally might communicate structurally regardless of what affordances are offered. The two accounts predict identical behavior in this design because both conditions used the same participants who already possessed the structural knowledge. Only a between-subjects comparison where one group never reads the tree could isolate the environmental mechanism.

The distinction matters for design rather than for this paper's claims. If the effect is environmental, offering structural affordances suffices. If cognitive, the developer must first build the structural model before they can communicate through it. The practical implication for codoc is the same either way, since the tree provides both the structural understanding and the structural affordance. Suchman's [1987] observation that plans function as resources for communicating *about* action rather than as instructions controlling it applies to both accounts.

## 7.3 Proposals Made Participants Encounter and Decide, Confirmatory

**Detection coverage was higher with codoc** [DATA: paired mean difference, 95% CI]. [DATA: N of 12] participants had strictly higher coverage in their codoc condition than in their own baseline condition, [DATA: meeting/not meeting] the pre-registered criterion of at least 7 of 12.

> "[PLACEHOLDER QUOTE: participant describing how a proposal drew their attention to something they would not have noticed otherwise]"

The mechanism is stronger than mere salience. Salience alone would predict faster detection of the same problems rather than detection of problems the developer would otherwise miss entirely. A proposal creates an obligation that persists until discharged. The developer cannot scroll past it the way they scroll past a sentence in CLAUDE.md, because the tool continues to mark the feature as pending until a verdict arrives. Co-location ensures the encounter happens in context, adjacent to the claim it modifies rather than in a separate notification queue where context must be reconstructed. A temporal pattern further distinguishes the mechanisms. Proposals were visible from the start of each session, yet detection episodes clustered after the initial verification phase rather than distributing uniformly across the session. If salience were the sole mechanism, detection should be immediate because the visual marker is present from the first second. The delay is consistent with a trust-first account in which proposals become actionable only after the developer trusts the representation enough to reason from what they say. A developer who does not yet trust the tree reads a proposal as "the tool claims something changed" and treats the claim as one more thing to verify against the code. A developer who trusts the tree reads the same proposal as "something changed, and this is what it means," which transforms the proposal from a prompt to check into a prompt to decide.

**False alarms did not differ between conditions** [DATA: Mdn_C vs Mdn_B, test]. A codoc advantage in detection paired with a higher false alarm rate would indicate suspicion rather than comprehension, and this was not observed.

We note a real limit. On one of two projects, codoc's sync loop amended the tree to match the agent's change before the participant encountered it. The contradiction was normalized away, making detection no easier than in the baseline. The loop that maintains faithfulness under G1 actively worked against change visibility under G2. Section 8.4 examines this structural tension in detail.

## 7.4 Once the Map Held, They Reasoned From It

Despite that visibility limit, the accuracy of what participants did verify was sufficient for trust to form rapidly. [DATA: N] participants explicitly described a moment of *trust calibration*, verifying the representation against the code early and then deciding it was reliable enough to use as a proxy.

> "[PLACEHOLDER QUOTE: participant describing checking one or two features against the code, finding them accurate, and then trusting the rest]"

Verification episodes followed a consistent pattern. Participants clicked a `codoc:` link, scanned [DATA: median lines] of the bound code, confirmed the description matched, and closed the file. The entire episode took [DATA: median seconds] seconds. Critically, participants chose features to verify that were *structurally representative* rather than suspicious, testing the machinery rather than the content. Their verbalizations during these episodes targeted the binding mechanism rather than the individual feature, with statements like "let me see if the links actually go where they say" rather than "I wonder if auth really uses JWTs." A participant who verified the top-level architecture feature and one leaf feature had tested the tree at two hierarchical depths, and the inference that the middle held was rational given that the same loops maintain all three levels.

The shift appeared non-linear. Participants did not gradually trust the tree more with each confirmed feature but operated in one of two modes, checking everything or checking nothing, with the transition happening within minutes and after [DATA: median number] verified features. We report this as a behavioral pattern rather than a proven discontinuity. The think-aloud protocol may impose narrative sharpness on a gradual process, and what the study can establish is the existence of two behavioral modes and the brevity of the interval between them rather than the nature of the transition itself. Section 8.1 discusses satisficing under time pressure as an alternative explanation and Section 9.1 acknowledges the demand characteristics that limit causal claims.

> "[PLACEHOLDER QUOTE: contrasting participant who stayed in verify mode throughout, or baseline participant who never stopped needing to check code]"

## 7.5 Decisions Persisted in the Record, Confirmatory

**A durable written trace was more common with codoc** [DATA: paired mean difference, 95% CI]. [DATA: N of 12] participants had a higher count of problems with a trace in codoc than in their own baseline condition, [DATA: meeting/not meeting] the pre-registered criterion.

In the codoc condition, decisions persisted as accept or reject verdicts in the change ledger or as authored description changes after the handover stamp, all timestamped, attributable, and auditable. In the baseline, a decision persisted only if the participant explicitly wrote it into `CLAUDE.md`, which [DATA: N] of 12 did for at least one problem.

> "[PLACEHOLDER QUOTE: participant noting that in the baseline, the agent quietly rewrote the description to match its own work, erasing what they had written]"

The distinction matters because a record can be true and worthless. In the baseline, the agent rewrote the description to match its own implementation after each action. The record was accurate but recorded the agent's decision rather than the developer's. Nobody was asked to agree. The functional difference is not philosophical but operational, surfacing at three points in a codebase's life. When something breaks months later, a human-accepted record identifies which decisions to revisit rather than forcing an audit of the entire module. When the reasoning matters, the person who accepted can reconstruct their context because the verdict is timestamped against a specific proposal rather than dissolved into a rewritten paragraph. When a pattern of failures emerges, verdicts reveal where trust was misplaced and scrutiny should concentrate. An agent-authored record answers only "what is currently true?" which is the same question reading the code answers, making the record redundant with its subject. The agent's CLAUDE.md describes the current state, but the code already describes the current state. What no amount of accurate description can reconstruct is whether a person examined that state and found it acceptable.

**Record truth did not differ between conditions at the end** [DATA: if confirmed], consistent with the pre-registered prediction. We report the change from handover to end rather than the final value because the two conditions do not start from the same place.

## 7.6 What Still Does Not Work

Three limitations replicate findings from Study 1. Cross-cutting concerns remained hard to express, with [DATA: N] participants reporting difficulty locating issues that spanned multiple features. This confirms the limitation is intrinsic to hierarchical representations rather than an implementation gap. The initial mapping cost was real, at [DATA: median minutes] minutes before acting. And debugging favored the code when [DATA: N] participants abandoned the tree to diagnose root causes directly. Section 8.3 examines each in detail.

> "[PLACEHOLDER QUOTE: participant switching to code/terminal to debug after identifying something was wrong from the tree]"
