# Paper Skeleton: codoc

**Thesis**: Developers think about codebases in terms of structure and intent — what the code IS and what it SHOULD BE — not in terms of step-by-step plans for changing it. When coding agents change code faster than a person can follow, no existing surface gives the developer a trustworthy, editable map of that intent. codoc provides one: a representation that stays synchronized with the code in both directions, through which a person orients, communicates, and decides.

**Target**: CHI 2027 (full paper, ~10k words)

**Writing register**: Clear, obvious, resonant. Every sentence should feel like something the reader already half-knew but hadn't articulated. No jargon for its own sake. No overclaiming. When we state a finding, it should feel inevitable given the design.

---

## 1. Introduction (Drop → World Building → Players → Player One → Deal → Loot)

### 1.1 The Drop — Why does this matter?
- Open with a concrete scenario every developer using coding agents recognizes: you ask an agent to add a feature, it changes 14 files, and when you come back to the code next week you can't tell what it did or why. The change is in the code but the ACCOUNT of the change — what was decided, what was traded off — lives nowhere.
- The stakes: as agents generate more code, comprehension shifts from "can I read this function" to "can I tell what the system IS now and how it got here." That is a fundamentally different problem.

### 1.2 World Building — What is the problem exactly?
- The problem is not that agents write bad code. It's that the faster they change things, the more a developer's ability to debug, iterate, and evaluate depends on an account of the code that nobody maintains.
- Current accounts (CLAUDE.md, Cursor rules, memory banks) are written BY the agent FOR the agent — they feed context back into the next prompt, not into a person's head. They stay current but stop being trustworthy or editable by a human.
- The deeper issue: chat is ephemeral, code is too detailed, and documentation drifts. There is no surface where a developer can see "what is this codebase" at the level of intent and know that what they see maps to what the code actually does right now.
- Define the problem crisply: we need a representation that is (a) faithful to the code, (b) readable as a map of intent, (c) editable to express what should change, and (d) operative — editing it actually causes code to change.

### 1.3 The Players — What has been done before?
- Thread 1: The old dream of maintaining intent alongside code (literate programming, concept assignment, intentional programming) — always failed because keeping the map current was manual labor.
- Thread 2: The agent-era attempts (SpecLang, Cursor rules, Cline memory, CoLadder) — these persist specifications but don't SYNCHRONIZE with the evolving code, so they drift; or they capture one interaction, not the continuous state.
- Thread 3: Shared representations as a design pattern (Heer's work on bridging human and machine reasoning through a shared language) — the right framing, not yet applied to codebase-level intent.
- The gap: no existing tool maintains a human-editable representation of codebase intent that stays synchronized with code in both directions AND serves as the primary communication channel between person and agent.

### 1.4 Player One — What are we doing differently?
- We designed codoc: an editable map of a codebase's intent, synchronized bidirectionally with the code. A person reads it to understand the system, edits it to say what should change, and reviews the agent's changes through it.
- The key design insight (earned through two rounds of iteration): the representation must serve as a COMMUNICATION LAYER, not a specification language. Developers don't plan in steps — they think in structure. They need to express "this is what the code should BE" and have the agent figure out how to get there.
- A second insight: once a developer maps the representation to their mental model of the code, they can pick up subsequent changes without re-reading source files. The representation becomes a TRUSTED ORIENTATION DEVICE.
- We arrived at this through an iterative design process: a first version (studied with 12 developers at CHI'26) taught us what developers need and where a file-structure mirror fails; a redesigned system addresses those failures; a second study (12 developers, within-subjects) evaluates whether the redesigned system delivers on its promise.

### 1.5 The Deal — Research questions
- RQ1: How do developers use a synchronized intent representation when reviewing and directing changes an agent made to a codebase? (exploratory, qualitative — strategies, appropriation, communication patterns)
- RQ2: Does a synchronized intent representation improve a developer's ability to detect and attribute changes in agent-generated code, compared to standard documentation? (confirmatory — detection coverage)
- RQ3: Does working through an intent representation produce more durable decision records than working through chat and companion documents? (confirmatory — durable written trace)

### 1.6 The Loot — What did we find?
- Brief preview: participants used codoc primarily as an orientation and communication device (not as a specification language). After an initial mapping, they picked up agent changes significantly faster. Detection coverage was higher. Decision records persisted. The representation worked not because it was expressive, but because it was TRUSTWORTHY — once they verified the map held, they could reason from it without returning to code.
- Contributions:
  1. An iterative design study revealing what developers need from a codebase representation for agent-assisted work, and why file-structure mirrors fail
  2. codoc: a synchronized intent representation implemented as a VS Code extension, designed as a communication layer between developer and agent
  3. Empirical evidence from a controlled study (N=12) showing that the representation improves change detection and decision durability when reviewing agent-generated changes
  4. Design implications for codebase representations in the age of coding agents

---

## 2. Related Work

### 2.1 The Intent-Code Gap: From Literate Programming to Agent-Era Documentation
- Paragraph 1: The recurring desire to hold a codebase in terms of its intent (Knuth, concept assignment, intentional software) — each failed for a specific reason we can now name.
- Paragraph 2: Agent-era documentation (CLAUDE.md, Cursor rules, Cline memory, SpecLang) — persistent but not synchronized; written for the agent, not for a person.
- Paragraph 3: CoLadder and RPG — hierarchical or graph-based, but capture one generation task, not the continuous evolving state. The representation is consumed, not maintained.

### 2.2 Shared Representations Between Humans and AI
- Paragraph 1: Heer's agency+automation framework and the principle of shared representations — a common medium both parties read, edit, and learn from.
- Paragraph 2: Applied instances: Wrangler (data transforms), text-to-SQL explanations, Liu et al.'s code-to-NL summaries — all share the property that the representation is editable and operative, but scoped to one interaction, not a codebase.
- Paragraph 3: What "shared" means at codebase scale: the representation must persist across sessions, update incrementally, and be trustworthy enough that a person reasons FROM it rather than through it.

### 2.3 Understanding and Reviewing AI-Generated Code
- Paragraph 1: The comprehension burden of agent-generated code (Barke et al., Sarkar et al., Mozannar et al.) — validation burden, mental model divergence, opacity of rationale.
- Paragraph 2: Approaches to making AI decisions visible (ClarifyGPT, clarification questions, step-by-step explanations) — episodic, not persistent; explain one generation, not the evolving codebase.
- Paragraph 3: The gap we address: persistent visibility into what the codebase IS (not just what one interaction produced), plus the agency to contest or redirect from that view.

---

## 3. Iterative Design Process

### 3.0 Design Goals
- Paragraph 1: Frame the multi-dimensional design space. What must a codebase representation deliver? We articulate five design goals drawn from the literature and refined through iteration:
  - **G1: Faithfulness** — the representation must map truthfully to the current code state; if it says something, the code must back it up.
  - **G2: Change visibility** — when code changes, the representation must show WHAT changed, not just the new state. (File explorers show structure, not evolution.)
  - **G3: Orientation efficiency** — after a developer maps the representation to their mental model once, subsequent changes should be cheap to pick up without returning to source.
  - **G4: Communication legibility** — the representation must serve as a medium both the developer and the agent read and write through, at the right level of abstraction.
  - **G5: Decision durability** — when a decision is made (accept, reject, revise), it must persist in a form that survives the session and can be audited later.
- Paragraph 2: These goals form our internal validation rubric. At each design iteration, we evaluate whether the system delivers on each, and where it falls short tells us what to redesign.

### 3.1 Version 1: A File-Structure Mirror (CHI'26 Study)
- Paragraph 1: Our first design mirrored the codebase's file and function structure as an editable document. Developers authored architecture using syntax that mapped directly to directories, files, and function signatures. The system synchronized bidirectionally: edits to the specification triggered code generation; code changes surfaced as feedback in the document.
- Paragraph 2: Study design (12 experienced developers, within-subjects vs chat baseline, two 20-min tasks — construction + modification, think-aloud + interview + questionnaire + recall test). Cite methods concisely.
- Paragraph 3: What worked — findings that validate the design direction:
  - Participants recalled more components and with higher structural accuracy (Mdn=8 vs 5, p=.003; structural accuracy d=1.21)
  - "I can control the code structure writing the system... I know where each function belongs" (P2)
  - Significantly fewer context switches (23 vs 49, p<.001)
  - Participants reported using codoc as a reference map: once they mapped the representation to the project, they could reason from the document without returning to code
- Paragraph 4: What failed — candidly:
  - **G2 failure (change visibility):** The file-structure mirror showed current state, not what changed. Added or removed function names were visible but their MEANING was not — you had to guess from a name like `$formatTrackDuration()` what it did and why it appeared. Several participants reported not noticing agent-generated utilities until they happened to inspect them.
  - **G4 failure (communication legibility):** The syntax was tied to structural anchors (files, functions). Cross-cutting concerns (error handling, logging, naming conventions) had no natural place to live. 10/12 participants said they still needed chat for open-ended tasks. "Sometimes I want to add something like picture fields in all functions, which is hard to describe in CoDoc" (P9).
  - **Expressivity ceiling:** The specification language was precise but narrow. It worked for declaring what SHOULD EXIST, not for expressing what should CHANGE about things that already exist.
- Paragraph 5: The core lesson — the representation was useful as a MAP (orientation, recall, spatial memory) but insufficient as a COMMUNICATION CHANNEL. Developers needed to say "this is what the code should BE" in richer ways than file structure allows, and they needed to SEE not just what exists but what was DECIDED and what HAPPENED.

### 3.2 Design Response: From Structure Mirror to Intent Representation
- Paragraph 1: The redesign changes what the representation IS. Instead of mirroring file structure, it maintains a FEATURE TREE — a hierarchy of named intents, each binding to whichever code chunks implement it across many files. A feature is "authentication" or "retry logic," not "auth.ts." This addresses G4: a developer can say what they mean at the level they think, not the level the filesystem imposes.
- Paragraph 2: Addressing G2 (change visibility): code changes are now classified by significance and surfaced as proposals in the representation — not just reflected silently. Structural changes (new features, moved responsibilities) become visible proposals that require a verdict (accept/reject). The developer is MADE TO DECIDE rather than having changes slip past.
- Paragraph 3: Addressing G5 (decision durability): every verdict, every authored edit, every agent-generated change is recorded in an append-only change ledger with actor, mode, and causality chain. The representation carries its own history — not as a separate changelog, but as retrievable provenance on any sentence.
- Paragraph 4: Addressing the communication insight: developers in the first study thought in terms of what the code SHOULD BE, not steps toward it. The redesigned system lets them edit the intent description directly (what it should be) and uses comments/steers as targeted directives (what to change about what it is). The representation becomes the medium of negotiation between developer and agent.

---

## 4. System Design

### 4.1 Overview (one paragraph)
- codoc is a VS Code extension that maintains a feature tree — a human-authored hierarchy of named intents — bidirectionally synchronized with the code. A person reads it to understand the system, edits descriptions to express intent, reviews the agent's changes as proposals within it, and directs work through comments. The agent implements intent expressed in the tree and surfaces its work back as visible changes to review.

### 4.2 Walkthrough: Reviewing an Agent's Change
- Use the NEW study's task as the running example (not the construction task from CHI26). A developer opens a project they haven't seen, with a recorded agent session that changed the code. Walk through:
  - Opening the tree: immediate orientation — the feature hierarchy gives the shape of the system in under a minute. Each feature links to specific code.
  - Seeing proposals: the agent's changes are visible as proposals on the affected features. A new utility function appears as a proposed child feature. A changed responsibility shows as an amend proposal on the relevant description.
  - Deciding: Accept/Reject on each proposal. The decision persists in the ledger. The feature description updates to reflect the accepted state.
  - Noticing discrepancies: a feature's description says one thing, the code does another (code_drift status). This is how you find a problem the agent introduced — the representation tells you something doesn't match.
  - Directing corrections: a comment on the feature steers the agent to fix it. The comment becomes a directive scoped to the feature's bound code.

### 4.3 Key Capabilities (purpose-first, not feature-list)
- **Orientation without reading code** — feature descriptions + code links give you the what and where; structural hierarchy gives you the how-it-fits. Purpose: you can navigate a codebase you didn't write by reading 2 pages of prose instead of 50 files of code.
- **Change visibility through proposals** — structural code changes become proposals in the tree, visually distinct, requiring a verdict. Purpose: nothing the agent does is invisible; everything demands a decision.
- **Communication through the representation** — editing descriptions expresses what the code should BE; comments express what should CHANGE. Both are scoped to features and their bound code. Purpose: instructions live where the code they govern lives, with context.
- **Trust through synchronization** — the representation stays current because both loops keep running. If code drifts from the description, the status tells you. Purpose: once you've verified the map is accurate, you can reason from it instead of through it.
- **Durable decision records** — every accept/reject, every authored edit, every agent change is in the ledger with who, when, what, and why. Purpose: next week you can ask "who decided this and why" and get an answer.

---

## 5. Architecture (The Synchronization)

### 5.1 Data Model (brief)
- Chunk set, feature tree, binding map, change ledger — one paragraph defining each. Keep formal but brief (not the full method.tex treatment).

### 5.2 Loop A: Code → Tree (reflecting changes)
- What it does: detects code changes, classifies them, applies safe operations automatically, surfaces judgment calls as proposals.
- Key property: determinism first (moves, renames, fingerprint refreshes are mechanical); only the residual goes to an LLM. Full title set in one call prevents duplicate features.
- Algorithm pseudocode (condensed, ~15 lines — not the full method.tex version).

### 5.3 Loop B: Tree → Code (realizing intent)
- What it does: drains human edits and verdicts, classifies whether each implies a code change, assembles directives for the live coding session.
- Key property: the imperative gate — descriptive prose merely persists; imperative prose, a new plan node, or a comment mints a directive. The agent implements; Loop A closes the cycle.
- The hold set and conflict resolution: document-ahead intent always wins over code-side observations. A person's in-flight edit is never overwritten.

### 5.4 What Makes This Different from a Linter or a Diff Tool
- One paragraph: it's not one-shot analysis (linter) or a comparison of two snapshots (diff). It's continuous, bidirectional, and intent-first: the representation of what the code SHOULD BE is the primary artifact, and code is measured against it, not the other way around.

---

## 6. Study 2: Evaluating the Redesigned System

### 6.1 Research Questions (restated for the study section)
- Same RQs from the intro, with brief rationale connecting them to the design goals.

### 6.2 Study Design
- Within-subjects, 12 participants, counterbalanced order.
- Task: review a recorded agent session that changed a codebase (not build from scratch — this is the reviewing task).
- Two projects (scribe, tally), each with planted problems in the agent's change.
- Conditions: codoc vs baseline (CLAUDE.md maintained by the agent's maintenance skill, same content, same structure).
- The recorded session is identical across participants — everyone reviews the same change.
- Time budget: 5 min project briefing + 5 min tool briefing + 20 min task + 5 min sign-off + 5 min questionnaire.

### 6.3 Measures
- **Detection coverage** (confirmatory): how many planted problems found and correctly attributed. Rated 0/1/2 blind to condition.
- **Durable written trace** (confirmatory): whether the record the participant finished with says what was decided about each problem.
- **Record truth** (exploratory): measured as change from handover to end, not as a final value (because the baseline starts true by design).
- **False alarms** (safety check): correct changes flagged as wrong.
- Strategy codes, context switches, think-aloud themes, questionnaire, interview (all exploratory).

### 6.4 Participants
- 12 experienced programmers (7+ years, frequent AI coding tool users). Cite demographics briefly.

### 6.5 Analysis
- Quantitative: paired differences, bootstrap CIs, Wilcoxon signed-rank. Lead with the estimate, not the p-value.
- Qualitative: reflexive thematic analysis (Braun & Clarke, 2006; 2019 reflexive revision) on think-aloud + interview. Two coders on 25%, consensus, one coder finishing.

---

## 7. Findings

**Style**: Each finding is a paragraph-title summary (the claim in plain language, no overclaim) followed by evidence (quotes interleaved with quantitative support). Like the Memolet style shown in the reference image.

### 7.1 Orientation: "After the First Pass, I Stopped Opening Files"
- Participants used codoc primarily as an orientation device. After an initial read-through mapping features to their mental model, they could pick up agent changes by reading the tree alone.
- Quote + quantitative evidence (fewer context switches, recall scores).
- Contrast with baseline: participants who used CLAUDE.md still relied heavily on file navigation and the diff viewer.

### 7.2 Communication: Developers Think in Structure, Not Steps
- When directing changes, participants expressed intent through the representation (what the code should BE) rather than writing step-by-step instructions.
- Codoc:plan through Claude Code was the dominant pattern — the representation as communication layer, not as a direct specification language.
- Quote evidence showing the structural thinking pattern.

### 7.3 Change Visibility: Proposals Made Participants Decide
- Detection coverage was higher with codoc. The proposals mechanism forced participants to encounter each structural change and make a verdict.
- Quote + paired comparison + CI.
- Mechanism: "I saw the proposal and immediately knew something was wrong because the description still said X but the code now does Y" — the discrepancy is visible in the representation itself.

### 7.4 Trust: Once the Map Held, They Reasoned From It
- Participants verified the representation against code early in the session. Once they confirmed it was accurate, they stopped checking and reasoned from the representation alone.
- This is the orientation efficiency finding: the first mapping is expensive, subsequent pickups are cheap.
- Quote about trust + the moment they stopped opening files.

### 7.5 Decision Durability: The Record Survived the Session
- Durable written trace was higher with codoc. Accept/reject verdicts persisted in the ledger; authored description changes persisted as the new state.
- In the baseline, the agent rewrote the record to match its own decisions, silently.
- Quote + paired comparison.

### 7.6 What Still Doesn't Work
- Cross-cutting concerns remain hard to express (replicates the V1 finding).
- Expressivity for debugging: when the problem is "something is wrong but I don't know what," the tree is less useful than just running the code.
- The initial mapping cost: participants who had never seen the project spent several minutes just reading the tree, during which the baseline's CLAUDE.md (plain text, no hierarchy) was actually faster to scan.

---

## 8. Discussion

### 8.1 Codebase Representations as Communication Layers
- The finding that developers use codoc primarily to COMMUNICATE (with the agent and with their future selves) rather than to SPECIFY is the central design lesson.
- Implications for the design of future developer tools: the representation should optimize for legibility and trust, not expressivity. A narrow, trustworthy map beats a rich, unverifiable specification.

### 8.2 The Orientation Threshold: Why Trust Matters More Than Features
- The "once the map held, they stopped opening files" finding suggests a threshold effect: the representation becomes useful not when it gains features, but when it crosses a trust threshold.
- Design implication: invest in FAITHFULNESS infrastructure (the sync loops, the conflict resolution) before adding expressivity. The loops are the value.

### 8.3 When a Map Fails: The Limits of Structural Representations
- Cross-cutting concerns, debugging, and the initial mapping cost are real limitations.
- These are not fixable by making the representation more expressive (that was v1's instinct, and it failed by becoming a DSL nobody wanted to learn).
- They may be addressable by making the representation more QUERYABLE — /codoc:ask, search, provenance — rather than more editable.

### 8.4 Implications for the Design of AI Coding Tools
- Agents that write code should also maintain a trustworthy representation of what they did and why — not as a chat log, but as a persistent, auditable, navigable account.
- The representation should be the SITE OF REVIEW, not a separate artifact consulted alongside review. This means proposals belong in the representation, not in a separate diff view.
- "Being made to decide" is a feature, not a burden. The cost of explicit verdicts is far lower than the cost of discovering, months later, that nobody agreed to what the agent did.

---

## 9. Limitations and Future Work

- Single-session: cannot assess whether the representation holds up over weeks/months.
- 12 participants: the quantitative estimates are honest (we report CIs, not just p-values) but imprecise.
- Recorded session: realistic (everyone reviews the same change) but sacrifices the experience of watching your own agent work.
- The initial mapping cost may be amortized over time (a developer who has been using codoc for a week starts each session already oriented) but we have no evidence for this.
- Future: longitudinal deployment study; multi-user coordination through the shared representation; extending the representation below the function level.

---

## Figures (planned)

- **Figure 1**: Motivated example / teaser — the problem (agent changes 14 files, developer has no overview) vs the solution (codoc showing the same change as proposals in a navigable feature tree). Split composition, clean and modern.
- **Figure 2**: System overview / architecture — the two loops, one page, showing the data flow from code through indexing through the feature tree back to code.
- **Figure 3**: The walkthrough — annotated screenshots showing: (a) the feature tree with proposals, (b) a code drift indicator, (c) an accept/reject interaction, (d) a comment/steer.
- **Figure 4**: Quantitative results — detection coverage and durable trace comparisons (paired differences with CIs, Memolet style).
- **Figure 5**: Design iteration comparison — v1 (file structure mirror) vs v2 (feature tree), showing the same codebase in both, making the difference tangible.
