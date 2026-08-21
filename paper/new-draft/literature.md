# Literature References for the codoc Paper

Organized by paper section. Each entry: full citation → key insight → the specific claim in OUR paper it supports.

---

## 1. Introduction — The Players (Prior Attempts at Intent-Code Alignment)

### Thread 1: The Old Dream

**Knuth, D. E. (1984). Literate programming. _The Computer Journal_, 27(2), 97–111.**
- Key insight: Programs should be written to explain to humans what the computer does; prose and code share a single source.
- Supports: "The dream of holding a program in terms of intent is old — Knuth argued in 1984 that a program should be written for a person first and a machine second. His approach kept prose and code consistent at the moment of writing, but said nothing about what happens when code later changes under maintenance."

**Biggerstaff, T. J., Mitbander, B. G., & Webster, D. (1993). The concept assignment problem in program understanding. _ICSE '93_, 482–498. IEEE.**
- Key insight: Code carries both formal meaning (for the machine) and informal meaning (for people); understanding code means mapping human concepts onto implementation.
- Supports: "Understanding code has long been framed as mapping human concepts onto the code that implements them — the concept assignment problem."

**Rajlich, V., & Wilde, N. (2002). The role of concepts in program comprehension. _IWPC '02_, 271–278. IEEE.**
- Key insight: A change request is stated in domain concepts; the concept is known but where it lives in the code is not; one concept is usually spread across many files.
- Supports: "A change is stated in domain concepts but where that concept lives in the code is not known, and one concept usually spans many files — precisely the situation codoc's feature tree addresses."

**Dit, B., Revelle, M., Gethers, M., & Poshyvanyk, D. (2013). Feature location in source code: A taxonomy and survey. _Journal of Software: Evolution and Process_, 25(1), 53–95.**
- Key insight: Tools for feature location ran once per task and returned a ranked list that was discarded; the mapping was recomputed each time, never kept.
- Supports: "These tools located features but never MAINTAINED the mapping — it was recomputed per task and discarded."

**Simonyi, C., Christerson, M., & Clifford, S. (2006). Intentional software. _OOPSLA '06_, 451–464. ACM.**
- Key insight: Intent stored as the primary artifact, code generated from it; but required a specialized structured editor that resisted normal developer tools.
- Supports: "Intentional programming stored intent as the primary artifact but the surface was a specialized editor that resisted diff, version control, and free typing."

**Voelter, M., Siegmund, J., Berger, T., & Kolb, B. (2014). Towards user-friendly projectional editors. _SLE '14_, LNCS 8706, 41–61. Springer.**
- Key insight: Projectional/structured editors impose high adoption cost — people have to edit a syntax tree rather than type freely, and ordinary tools no longer work.
- Supports: "The cost of structured editors — editing a tree rather than typing freely — prevented adoption every time it was tried."

### Thread 2: Agent-Era Documentation

**GitHub Next. (2023). SpecLang. Research prototype. https://githubnext.com/projects/speclang/**
- Key insight: Markdown-style syntax to describe desired features declaratively ("show two buttons at the bottom"); shifts from commanding to describing desired state.
- Supports: "Agent-era documentation (SpecLang, Cursor rules, Cline memory) persists specifications but does not synchronize with the evolving code — they drift exactly as earlier documentation did."

**Cursor (Anysphere). (2025). Cursor Rules. https://docs.cursor.com/context/rules**
- Key insight: Persistent project conventions stored as context for the agent; provides cross-conversation continuity but maps to no specific code.
- Supports: same as above — "written for the agent, not for a person; they feed context into the next prompt, not into a person's head."

**Cline Documentation. (n.d.). Cline Memory Bank. https://docs.cline.bot/prompting/cline-memory-bank**
- Key insight: Persistent memory bank stores project state across sessions, reducing repetition but not synchronized with actual code state.
- Supports: same thread.

**Liang, J. T., Lin, M., Rao, N., & Myers, B. A. (2025). Prompts Are Programs Too! Understanding How Developers Build Software Containing Prompts. _Proc. ACM Softw. Eng._, 2, FSE. DOI: 10.1145/3729342.**
- Key insight: Developers craft inline comments targeting specific code sections as prompt-like annotations — treating NL phrases as tunable variables to recover predictability.
- Supports: "Instructions that live where the code they govern lives, with context — prompts are programs too, and they deserve a place to live and a version."

### Thread 3: Hierarchical/Structured Generation

**Yen, R., Zhu, J. S., Suh, S., Xia, H., & Zhao, J. (2024). CoLadder: Manipulating Code Generation via Multi-Level Blocks. _UIST '24_. ACM. DOI: 10.1145/3654777.3676357.**
- Key insight: Hierarchical decomposition of generation tasks with multi-level blocks; users decompose tasks and the system maintains correspondences.
- Supports: "CoLadder captures the hierarchy of a single generation task but does not maintain it as the codebase evolves — the representation is consumed, not kept."

**Microsoft Research. (2025). RPG: A repository planning graph for unified and scalable codebase generation. arXiv:2509.16198.**
- Key insight: Plans a repository as a graph of capabilities and data flows before generating code from it.
- Supports: "RPG plans a repository before generating it — but the graph is a blueprint consumed once, not a living representation maintained as code evolves."

**Shipman, F. M. III, & Marshall, C. C. (1999). Formality Considered Harmful: Experiences, Emerging Themes, and Directions on the Use of Formal Representations in Interactive Systems. _Computer Supported Cooperative Work_, 8(1–2), 29–90.**
- Key insight: Formal representations impose cognitive overhead that discourages use; people prefer informality and structure emerges over time rather than being imposed upfront.
- Supports: "V1's DSL-like syntax imposed formality users didn't want — Shipman and Marshall's warning that formal representations discourage use when structure exceeds what the user needs at the moment."

---

## 2. Related Work — Shared Representations Between Humans and AI

**Heer, J. (2019). Agency plus automation: Designing artificial intelligence into interactive systems. _Proceedings of the National Academy of Sciences_, 116(6), 1844–1850. DOI: 10.1073/pnas.1807184115.**
- Key insight: A shared representation provides a common medium where both human and machine can reason about, formulate, and contribute to solutions — the language of the interaction is the substrate.
- Supports: "The principle of shared representations — a common medium both parties read, edit, and learn from — is the right framing for what codoc does, now applied at codebase scale."

**Kandel, S., Paepcke, A., Hellerstein, J., & Heer, J. (2011). Wrangler: Interactive visual specification of data transformation scripts. _CHI '11_, 3363–3372. ACM.**
- Key insight: An interactive language where the user's edits to a visual representation directly generate executable transformation scripts — the representation is both readable and operative.
- Supports: "Wrangler demonstrated that a representation can be BOTH human-readable AND operative — editing it generates executable output — but scoped to one data-transformation task, not a codebase."

**Liu, M. X., Sarkar, A., Negreanu, C., Zorn, B., et al. (2023). "What It Wants Me To Say": Bridging the Abstraction Gap Between End-User Programmers and Code-Generating Large Language Models. _CHI '23_. ACM. DOI: 10.1145/3544548.3580817.**
- Key insight: Translates generated code back into structured NL reflecting the model's interpretation, creating a reviewable intermediate form between prompt and code.
- Supports: "Liu et al. create a reviewable intermediate between user intent and generated code — the same principle codoc applies continuously to an evolving codebase rather than to a single generation."

**Tian, Y., Zhang, Z., Ning, Z., Li, T. J.-J., Kummerfeld, J. K., & Zhang, T. (2023). Interactive text-to-SQL generation via editable step-by-step explanations. arXiv:2305.07372.**
- Key insight: Users can edit step-by-step NL explanations of generated SQL queries to correct misunderstandings; editable intermediate enables "review, select, revise, or dismiss."
- Supports: "These systems enable review-select-revise interactions but capture only what one LLM call produced, not the continuous state of a codebase."

**Feng, L., Yen, R., You, Y., Fan, M., Zhao, J., & Lu, Z. (2024). CoPrompt: Supporting Prompt Sharing and Referring in Collaborative Natural Language Programming. _CHI '24_. ACM. DOI: 10.1145/3613904.3642212.**
- Key insight: A shared prompt space where collaborators refer to, build upon, and contextualize each other's NL instructions — prompts as collaborative references rather than isolated commands.
- Supports: "Collaborative prompting surfaces the insight that NL instructions benefit from being persistent, referable, and shared — codoc extends this from prompts to the full codebase representation."

---

## 2.3 Understanding and Reviewing AI-Generated Code

**Barke, S., James, M. B., & Polikarpova, N. (2023). Grounded Copilot: How Programmers Interact with Code-Generating Models. _Proc. ACM Program. Lang._ (OOPSLA). DOI: 10.1145/3586030.**
- Key insight: Programmers switch between "acceleration mode" (accepting suggestions) and "exploration mode" (probing what the model can do); mental models of the model's capabilities shape interaction.
- Supports: "Programmers interact with code generation through shifting strategies — but these strategies remain episodic, within one prompt-response cycle, not oriented toward a persistent codebase-level view."

**Sarkar, A., Gordon, A. D., Negreanu, C., Poelitz, C., Ragavan, S. S., & Zorn, B. (2022). What is it like to program with artificial intelligence? arXiv:2208.06213.**
- Key insight: Programmers engage in "iterative evaluation" — repeatedly prompting and evaluating — and their mental abstractions diverge from what the model produces at different levels of detail.
- Supports: "The user's mental model, shaped by the prompts they wrote, inevitably diverges from the actual codebase as the model's inferences accumulate — this is the core problem codoc addresses."

**Mozannar, H., Bansal, G., Fourney, A., & Horvitz, E. (2024). Reading Between the Lines: Modeling User Behavior and Costs in AI-Assisted Programming. _CHI '24_. ACM.**
- Key insight: Models user behavior as a sequence of verification and correction costs; shows the validation burden grows with code generation volume.
- Supports: "The validation burden of AI-generated code grows with volume — at codebase scale, this burden becomes unsustainable without a persistent overview that tells you where to look."

**Vaithilingam, P., Zhang, T., & Glassman, E. L. (2022). Expectation vs. Experience: Evaluating the Usability of Code Generation Tools Powered by Large Language Models. _CHI EA '22_. ACM. DOI: 10.1145/3491101.3519665.**
- Key insight: Copilot did not improve task completion time or success rate, and participants struggled to understand, edit, and debug AI-generated code.
- Supports: "Even when AI-generated code is correct, developers struggle to UNDERSTAND it — the problem is comprehension, not correctness."

**Prather, J., Reeves, B. N., Denny, P., Becker, B. A., Leinonen, J., Luxton-Reilly, A., Powell, G., et al. (2023). "It's Weird That It Knows What I Want": Usability and Interactions with Copilot for Novice Programmers. _ACM Transactions on Computer-Human Interaction_, 31(1), 1–31.**
- Key insight: Users anthropomorphize the system and find it uncanny that it "knows" their intent, yet struggle when the output doesn't match expectations — opacity breeds both wonder and frustration.
- Supports: "The opacity of AI coding tools — knowing what to do without showing why — creates a trust problem that grows worse as changes accumulate."

**Mu, F., Shi, L., Wang, S., Yu, Z., Zhang, B., Wang, C. X., et al. (2024). ClarifyGPT: A Framework for Enhancing LLM-Based Code Generation via Requirements Clarification. _Proc. ACM Softw. Eng._ (FSE), 1, 2332–2354. DOI: 10.1145/3660810.**
- Key insight: Asking clarification questions before generation resolves ambiguity upfront; yet these clarifications remain episodic and separate from the codebase.
- Supports: "These clarification systems are episodic — they resolve ambiguity for ONE generation call, not for the continuous evolution of a codebase."

**Vijayvargiya, S., Zhou, X., Yerukola, A., Sap, M., & Neubig, G. (2025). Interactive agents to overcome ambiguity in software engineering. arXiv:2502.13069.**
- Key insight: Interactive agents that disambiguate requirements through dialogue; argues for making underspecification explicit rather than silently resolving it.
- Supports: "Making underspecification explicit rather than silently resolving it is the principle behind codoc's proposals — the agent surfaces what it inferred rather than burying it in code."

---

## 3. Design Goals / Validation Framework

**Olsen, D. R. Jr. (2007). Evaluating user interface systems research. _UIST '07_, 251–258. ACM. DOI: 10.1145/1294211.1294256.**
- Key insight: UI systems research should be evaluated on importance of the problem, generality, reduction of solution viscosity, and empowerment of new interaction — NOT just controlled experiments.
- Supports: "Our iterative design approach, evaluated through design goals and user studies, follows Olsen's recommendation that systems research be assessed by the problems it makes tractable, not just by AB comparisons."

**Lee, J. D., & See, K. A. (2004). Trust in automation: Designing for appropriate reliance. _Human Factors_, 46(1), 50–80.**
- Key insight: Trust calibration requires that the system's actual capabilities and limitations be transparent; overtrust and undertrust are both failures.
- Supports: "The 'orientation threshold' finding — once participants verified the map held, they trusted it — mirrors Lee & See's framework: appropriate trust requires transparency of capability, which codoc achieves through the sync status indicator."

**Norman, D. A. (2013). _The Design of Everyday Things_ (revised edition). Basic Books.**
- Key insight: Good design makes state visible (visibility), maps controls to outcomes (mapping), and constrains action to prevent errors (constraints).
- Supports: "Our design goals echo Norman's principles: G1 (faithfulness) is visibility of system state; G4 (communication legibility) is natural mapping between intent and action; proposals are constraints that prevent uncommitted changes from going unnoticed."

---

## 5. Study Design — Methods Citations

**Braun, V., & Clarke, V. (2006). Using thematic analysis in psychology. _Qualitative Research in Psychology_, 3(2), 77–101.**
- Key insight: A flexible qualitative method for identifying, analyzing, and reporting patterns (themes) within data.
- Supports: Cite for "reflexive thematic analysis" methodology in qualitative analysis of think-aloud and interview data.

**Braun, V., & Clarke, V. (2019). Reflecting on reflexive thematic analysis. _Qualitative Research in Sport, Exercise and Health_, 11(4), 589–597.**
- Key insight: The 2019 reflexive revision clarifies that themes are not "discovered" in data but actively constructed by the researcher through engagement — coding is a creative analytic process.
- Supports: Cite alongside 2006 paper — "We adopted reflexive thematic analysis (Braun & Clarke, 2006; 2019) recognizing themes as researcher-constructed rather than latent in the data."

**Ericsson, K. A., & Simon, H. A. (1993). _Protocol Analysis: Verbal Reports as Data_ (revised edition). MIT Press.**
- Key insight: Think-aloud protocols provide valid data about cognitive processes when participants verbalize concurrently with task performance.
- Supports: Cite for think-aloud methodology — "Participants were instructed to think aloud during the task (Ericsson & Simon, 1993)."

**Boren, M. T., & Ramey, J. (2000). Thinking aloud: Reconciling theory and practice. _IEEE Transactions on Professional Communication_, 43(3), 261–278.**
- Key insight: Practical guidance for think-aloud — acknowledging that pure concurrent verbalization is rare; minimal interviewer prompts maintain the protocol without unduly influencing.
- Supports: Alternative/supplement to Ericsson & Simon for the think-aloud citation if the protocol includes light prompting.

**Efron, B., & Tibshirani, R. J. (1994). _An Introduction to the Bootstrap_. CRC Press.**
- Key insight: Bootstrap methods provide distribution-free confidence intervals appropriate for small samples.
- Supports: Cite for "95% confidence intervals using the studentized bootstrapping method (10,000 resamples)."

---

## 8. Discussion — Communication Layers and Theoretical Framing

**Clark, H. H., & Brennan, S. E. (1991). Grounding in communication. In L. B. Resnick, J. M. Levine, & S. D. Teasley (Eds.), _Perspectives on Socially Shared Cognition_ (pp. 127–149). APA.**
- Key insight: Communication requires "grounding" — interlocutors must establish mutual understanding through evidence of uptake; the medium shapes the grounding process.
- Supports: "codoc functions as a grounding device in the sense of Clark & Brennan — it provides evidence of mutual understanding between developer and agent. When the developer sees a proposal and accepts it, that IS the grounding act. When the representation says the code matches the description, both parties share common ground."

**Suchman, L. A. (1987). _Plans and Situated Actions: The Problem of Human-Machine Communication_. Cambridge University Press.**
- Key insight: Plans are not prescriptions that control action but RESOURCES for communication — people use them to communicate intent, not to execute steps mechanically.
- Supports: "Developers in our study used codoc as a communicative resource rather than an execution specification — consistent with Suchman's insight that plans serve communication, not control. They expressed what the code SHOULD BE, not step-by-step instructions for getting there."

**Hutchins, E. (1995). _Cognition in the Wild_. MIT Press.**
- Key insight: Cognition is distributed across people, artifacts, and environments; external representations are not just memory aids but active participants in cognitive processes.
- Supports: "The feature tree is not merely a memory aid — it is a COGNITIVE ARTIFACT in Hutchins' sense, actively participating in the developer's reasoning process. Once internalized, it structures how they think about the codebase."

**Star, S. L., & Griesemer, J. R. (1989). Institutional ecology, 'translations,' and boundary objects: Amateurs and professionals in Berkeley's Museum of Vertebrate Zoology, 1907–39. _Social Studies of Science_, 19(3), 387–420.**
- Key insight: Boundary objects inhabit multiple communities of practice simultaneously, satisfying the informational needs of each without being identical to any — they are "plastic enough to adapt to local needs yet robust enough to maintain identity across sites."
- Supports: "codoc is a boundary object between developer and agent — plastic enough that each reads and writes it differently (the developer sees intent, the agent sees directives) yet robust enough that both operate on the same representation. The feature tree means 'what this code is for' to the developer and 'what code to touch' to the agent — simultaneously."

---

## Additional Papers for Specific Claims

**Hindle, A., Barr, E. T., Su, Z., Gabel, M., & Devanbu, P. (2012). On the naturalness of software. _ICSE '12_, 837–847. IEEE.**
- Key insight: Code is regular enough and repetitive enough that statistical models capture its patterns reliably.
- Supports: "Code that people write is regular enough that a model can reliably tell which code serves which feature — we never need a reversible grammar between intent and code."

**LaToza, T. D., Venolia, G., & DeLine, R. (2006). Maintaining mental models: A study of developer work habits. _ICSE '06_, 492–501. ACM. DOI: 10.1145/1134285.1134355.**
- Key insight: Developers maintain mental models of code that are expensive to build and fragile to maintain; context switching forces partial rebuilds.
- Supports: "Mental models of code are expensive to build (LaToza et al., 2006); codoc's orientation efficiency — picking up changes without returning to source — directly addresses this cost by externalizing the model."

**Crisan, A., Fiore-Gartland, B., & Tory, M. (2021). Passing the data baton: A retrospective analysis on data science work and workers. _IEEE TVCG_, 27(2), 1860–1870.**
- Key insight: Data science workflows benefit from retaining branching histories so practitioners can see not just "what the code is now" but "how it got here."
- Supports: "The desire to see not just current state but how you got here — which codoc's change ledger addresses — echoes findings from exploratory data science (Crisan et al., 2021)."

**Zamfirescu-Pereira, J. D., Wong, R. Y., Hartmann, B., & Yang, Q. (2023). Why Johnny Can't Prompt: How Non-AI Experts Try (and Fail) to Design LLM Prompts. _CHI '23_. ACM. DOI: 10.1145/3544548.3581388.**
- Key insight: Users struggle to form stable mental models of how prompts will behave, often rewriting for each case rather than building generalizable specifications.
- Supports: "Users struggle to build stable specifications for AI systems (Zamfirescu-Pereira et al., 2023) — codoc addresses this by making the RESULT of each interaction visible and persistent, rather than requiring better prompting."

**Akhoroz, M., & Yildirim, C. (2025). Conversational AI as a Coding Assistant: Understanding Programmers' Interactions with and Expectations from Large Language Models for Coding. arXiv:2503.16508.**
- Key insight: Programmers expect conversational AI to maintain awareness across the session; frustration arises when context is lost between turns.
- Supports: "Developers expect continuity from coding assistants (Akhoroz & Yildirim, 2025) — codoc provides it not through conversation memory but through a persistent representation that outlives any single session."

---

## Papers on Developer Trust in AI-Generated Code (2024–2026)

**Määttä, S. (2025). How do programmers evaluate AI-generated code? _ESEM '25_. ACM/IEEE.**
- Key insight: Empirical study of evaluation strategies developers use when assessing AI outputs.
- Supports: If available — may inform our discussion of detection strategies participants used.

**Geruslu, V., Aliyeva, Z., & Tüzün, E. (2026). Factors Influencing the Quality of AI-Generated Code: A Synthesis of Empirical Evidence. arXiv:2603.25146.**
- Key insight: "Developers do not fully trust AI-generated code — yet less than half consistently verify the output."
- Supports: "The verification gap — most developers don't consistently verify AI outputs (Geruslu et al., 2026) — is precisely what codoc's proposal mechanism addresses by making verification unavoidable rather than optional."

---

## New Papers — Fresh Perspectives (added 2026-08-21)

### Automation Ironies and Trust Calibration

**Bainbridge, L. (1983). Ironies of automation. _Automatica_, 19(6), 775–779.**
- Key insight: The more reliable an automated system becomes, the less the human operator monitors it, and the less capable they become of intervening when it eventually fails. The very success of automation undermines the human's ability to detect and correct its failures.
- Our interpretation: Coding agents reproduce this irony at the speed of software. A developer who stops reading code because the agent is usually right has no mechanism for noticing the cases where it is not. codoc is designed to prevent this irony by keeping the developer engaged through a representation they trust, rather than disengaging because they trust the agent. The proposals and drift markers are the monitoring interface that prevents the human from falling out of the loop even as the automation improves.
- Cited in: §1 (automation irony applied to coding agents)

**Parasuraman, R., & Riley, V. (1997). Humans and automation: Use, misuse, disuse, abuse. _Human Factors_, 39(2), 230–253.**
- Key insight: Four failure modes of human-automation interaction. DISUSE: failing to use a capable system because of undertrust. MISUSE: over-relying on a system beyond its actual capability. Both are calibration failures.
- Our interpretation: Below the trust threshold, what happens is DISUSE — the representation is correct but the developer ignores it and goes to the code anyway, because they haven't calibrated trust. Above the threshold, the risk shifts to MISUSE — over-relying on a description that may have drifted. codoc's sync loops address misuse (keep it accurate) while the verifiable structure addresses disuse (let the developer calibrate quickly). The design problem is not "make them trust it" — it's "give them the right evidence to calibrate trust appropriately."
- Supports: §8.2 (trust threshold) — strengthens the argument that the threshold is not just behavioral but a calibration phenomenon with distinct failure modes on each side.

**Schemmer, M., Hemmer, P., Kühl, N., Benz, C., & Satzger, G. (2022). Should I Follow AI-based Advice? Measuring Appropriate Reliance in Human-AI Decision-Making. arXiv:2204.06916.**
- Key insight: Distinguishes attitudinal trust ("I believe this is reliable") from behavioral trust ("I will act on it without independently verifying"). Appropriate reliance requires both.
- Our interpretation: The trust threshold IS the moment behavioral trust forms — not just "I think the tree is probably right" but "I will plan my next action based on what the tree says." This distinction explains why a single verification failure is so costly: it doesn't just reduce attitudinal confidence, it destroys behavioral reliance entirely.
- Supports: §8.2 — the language for what the threshold IS, precisely.

**Gaube, S., Langer, M., Miller, T., et al. (2026). Keeping an Eye on AI: A Framework for Effective Human Oversight of AI Systems. arXiv:2605.16278.**
- Key insight: Effective oversight requires that humans maintain decision authority over system actions, with mechanisms that make consequences visible before commitment.
- Our interpretation: Proposals are exactly the "visible consequences before commitment" mechanism. The developer sees what the system WOULD change, in the context where it would change it, before agreeing. This is not just UI design — it's the minimum requirement for meaningful oversight of agent actions.
- Supports: §8.4 (proposals in context) — frames proposals as an oversight mechanism, not just a notification system.

### Boundary Objects and Coordination

**Star, S. L., & Griesemer, J. R. (1989)** — already in literature.md, but adding deeper interpretation:
- Extended interpretation: The feature tree is a boundary object in the precise technical sense: it serves different communities (developer, agent, future-developer) simultaneously without being reducible to any one community's concerns. The developer reads it as "what this system is for." The agent reads it as "what code to touch and what constraints to respect." A future auditor reads it as "what was decided and by whom." Same artifact, different uses, coherent identity. This is NOT just documentation-for-multiple-audiences — a boundary object is structurally different because it maintains coherence through the constraints of the medium (the tree's structure, the binding map) rather than through social agreement. When a commit message drifts, there is no structural signal. When a feature description drifts, the binding's fingerprint fails.

**Henderson, K. (1991). Flexible sketches and inflexible data bases: Visual communication, conscription devices, and boundary negotiations in design engineering. _Science, Technology, & Human Values_, 16(4), 448–473.**
- Key insight: Engineers prefer informal sketches over formal databases because sketches support the ambiguity needed during active design. Formal systems are useful for recording decisions AFTER they're made, not during the making.
- Our interpretation: codoc's description language is deliberately informal (natural language prose, not a schema) for this reason. A developer mid-thought about "how retry logic should work" does not want to fill out a form. They want to write a sentence. The STRUCTURE (tree hierarchy, bindings) provides the formality the system needs for automation; the CONTENT (descriptions, comments) stays as flexible as a sketch. This separation — formal structure, informal content — is the design response to Henderson's observation.
- Supports: §2.2 (Shipman & Marshall, but from a different angle) and §3.3 (why natural language descriptions, not structured forms).

### Cognitive Artifacts and External Representations

**Kirsh, D. (2010). Thinking with external representations. _AI & Society_, 25(4), 441–454.**
- Key insight: External representations don't just offload memory — they change the cognitive task itself. Reading a map is cognitively different from navigating by memory, even when the "information" is the same.
- Our interpretation: Reading a feature tree is NOT "reading documentation." It is performing a different cognitive operation than reading code. When a developer reads "cross-reference resolution: resolves internal page references in a single pass," they are doing ORIENTATION — locating themselves in a structure. When they read the function implementation, they are doing COMPREHENSION — understanding how the code works. These are different tasks with different costs and different values. The feature tree makes orientation cheap so that comprehension can be targeted rather than exhaustive. This is why "the same information in a flat doc" is NOT equivalent — the structure changes what cognitive operation the reader performs.
- Supports: §7.1 (why structured representation differs from flat doc even with "same content") and §8.1 (the representation as cognitive artifact, not just information container).

**LaToza, T. D., & Myers, B. A. (2010). Hard-to-answer questions about code. _ESEM '10_, Article 8. ACM. DOI: 10.1145/1852786.1852801.**
- Key insight: The hardest questions developers ask are not "what does this function do" but "why was this designed this way," "what would break if I changed this," and "what is the intended behavior." These are DESIGN RATIONALE questions that live above the code.
- Our interpretation: A feature tree answers exactly the class of questions that LaToza & Myers identified as hardest: Why does this piece exist? What is its intended role? What depends on it being this way? These cannot be answered by reading the implementation — they require an account of intent that lives above and alongside the code. The "trust threshold" finding is specific to this: once developers trust the tree's answers to these hard questions, they stop asking the code.
- Supports: Introduction ("the missing account") and §8.1 (what the representation is FOR — answering design-level questions, not code-level questions).

### Forcing Functions and Error Prevention

**Norman, D. A. (2013). _The Design of Everyday Things_ (pp. 141–145: Forcing Functions).** — extending existing entry:
- Extended interpretation for forcing functions specifically: A forcing function is a physical constraint that prevents the action from continuing without the required step being completed. A microwave door that must close before it runs. An ATM that returns your card before dispensing cash. A proposal in codoc is a forcing function: the tree contains a visible structural claim that PERSISTS until the developer responds. You cannot "forget" to decide — the proposal stays. This is what distinguishes it from a notification (which can be dismissed) or a log entry (which can be never read). The forcing function IS the mechanism behind "being made to decide."
- Supports: §8.4 — "being made to decide" is not just a catchy phrase; it's the application of a well-understood design principle (forcing functions) to the specific problem of agent oversight.

### Intent Debt and Comprehension Debt

**Storey, M.-A. (2026). From Technical Debt to Cognitive and Intent Debt: Rethinking Software Health in the Age of AI. arXiv:2603.22106.**
- Key insight: Proposes a "Triple Debt Model": technical debt (in code), cognitive debt (in people — eroded shared understanding), and intent debt (in externalized knowledge — absent or eroded rationale, goals, constraints). AI generates code faster than teams can comprehend it, shifting the risk locus from code quality to understanding and intent.
- Our interpretation: codoc is an intent-debt prevention mechanism. Storey defines intent debt as "the absence or erosion of explicit rationale, goals, and constraints that guide how humans and agents evolve the system." The feature tree IS that explicit rationale — and the synchronization loops are what prevent its erosion. Without codoc, every agent session accumulates intent debt: the code changes, the rationale for its current form exists only in the agent's past context window (ephemeral), and the team's ability to reason about why it's this way degrades. With codoc, the rationale is recorded in the tree, the change is visible as a proposal or drift, and the decision to accept is in the ledger. Intent debt does not accumulate because the representation FORCES explicit rationale into existence at every structural change. This framing is stronger than "documentation maintenance" — it positions codoc as addressing a specific, newly-named category of software health risk.
- Cited in: §1 (introduction, naming the problem), §8.1 (the representation prevents intent debt accumulation)

**Zhang, Y. (2025). Beyond Technical Debt: How AI-Assisted Development Creates Comprehension Debt in Resource-Constrained Indie Teams. arXiv:2512.08942.**
- Key insight: Names "comprehension debt" specifically in AI coding contexts — the gap between what the codebase does and what the developer understands about it, which grows with each AI-generated change.
- Our interpretation: Comprehension debt is what our introduction calls "the missing account." The term gives it a name that connects to the technical debt literature — making it legible as a debt (something that accumulates interest and must eventually be paid). codoc's amortization argument (pay the mapping cost once, then subsequent changes are cheap) is literally a debt-management strategy: pay down comprehension debt continuously rather than letting it accumulate until a crisis.
- Cited in: §2.3 (could strengthen the "comprehension debt grows with each interaction" paragraph)

### Spatial Representations and Comprehension

**Bouraffa, A., Fuhrmann, G.-L., & Maalej, W. (2023). Developers' Visuo-spatial Mental Model and Program Comprehension. arXiv:2304.09301.**
- Key insight: A between-subjects study (N=20) found that spatial code canvases did NOT improve comprehension performance over tab-based viewing. Changed how developers spent time (less navigation, more annotation) but not what they understood.
- Our interpretation: This does NOT contradict our V1 recall finding, and the reason illuminates why. Bouraffa's "spatial canvas" was spatial arrangement of CODE FILES — the same content in a different layout. Our V1 specification was spatial arrangement of NAMED INTENTS WITH DESCRIPTIONS — higher-level meaning in a deliberately structured hierarchy. Spatial organization alone does not help; spatially organized MEANING does. The feature tree works not because it's spatial, but because the spatial structure maps to conceptual structure (features are organized by responsibility, not by filename). This is Kirsh's point exactly: the representation changes the cognitive task, but only when the representation carries meaning that the original medium (code files) does not express explicitly.
- NOT cited in paper — too tangential to explain the distinction in the word budget. Useful as internal defense if reviewers challenge the spatial memory claim.

**LaToza, T. D., & Myers, B. A. (2010). Hard-to-answer questions about code. _ESEM '10_. ACM.**
- Key insight: The hardest developer questions are "why was this designed this way," "what would break if I changed this," and "what is the intended behavior." These are intent-level, not implementation-level.
- Our interpretation: The feature tree answers exactly this class of questions. "What is this for?" — read the description. "Why this way?" — read the change ledger. "What depends on this?" — read the binding map. The trust threshold forms precisely when developers believe the tree answers these questions reliably. Below it, they still ask the code. Above it, they ask the tree.
- Cited in §1 (introduction, "the missing account").

---

## New Papers — Second Depth Pass (added 2026-08-21)

**Blackwell, A. F. (2002). First Steps in Programming: A Rationale for Attention Investment Models. _IEEE Symposia on Human-Centric Computing Languages and Environments_, 2–10.**
- Key insight: Tool adoption follows cost-benefit reasoning. Developers invest attention only when expected future payoff exceeds perceived upfront cost. Predicts that tools with high initial learning cost and delayed payoff are adopted only when the user can *see* the payoff accumulating.
- Our interpretation: The trust threshold IS the moment the attention investment starts paying off. Before it, the developer invests attention with no visible return. After it, every subsequent change is cheaper because they reason from the tree. The attention investment model gives our amortization argument a theoretical foundation and explains why single-session studies systematically undervalue tools like codoc, since the study window captures the investment but not the payoff.
- Cited in: §9.1 (limitations, explaining why single-session measurement is insufficient)

**Green, T. R. G. & Petre, M. (1996). Usability Analysis of Visual Programming Environments: A 'Cognitive Dimensions' Framework. _JVLC_ 7(2), 131–174.**
- Key insight: "Viscosity" measures resistance to change, and "premature commitment" measures forced decisions before information is available. These are dimensions along which notations succeed or fail.
- Our interpretation: A specification language forces premature commitment because the developer must decide HOW before they know WHAT, since the grammar requires implementation-level precision. A communication layer defers that commitment by letting the developer state the desired state without specifying the path. This is the cognitive-dimensions explanation for why codoc is a communication layer rather than a specification language. The V1 syntax forced premature commitment by requiring file-level references before the developer had decided at what level to express their intent.
- Cited in: §3.3 (why communication rather than specification, with cognitive dimensions language)

**Murphy-Hill, E., Parnin, C., & Black, A. P. (2012). How We Refactor, and How We Know It. _IEEE TSE_ 38(1), 5–18.**
- Key insight: Developers rarely adopt tools that require upfront investment unless the tool is integrated into existing workflow and the payoff is immediate and visible. Refactoring tools with high initial cost had near-zero adoption despite proven long-term benefit.
- Our interpretation: This is the same measurement problem codoc faces. A single-session study captures cost but not payoff, exactly as early refactoring tool studies did. Strengthens §9.1 by citing a precedent for the systematic undervaluation of amortizing tools.
- Cited in: §9.1 (alongside Blackwell)

**Suchman, L. A. (1987). _Plans and Situated Actions: The Problem of Human-Machine Communication_. Cambridge University Press.**
- Key insight (deeper reading): Plans are not instructions that control action. They are *resources for communication* about action. Action is always situated and responsive to circumstance. Nobody executes a plan literally.
- Our interpretation (refined): codoc descriptions are plans in Suchman's sense. They are communicative resources the developer uses to negotiate with the agent about what the code should be. The §7.2 finding that developers expressed desired state rather than steps is predicted by Suchman's framework. The agent interface should not ask "what should the agent do?" but "what should the code look like when the agent is done?" because that is the situated-action question, whereas step-by-step instructions assume a world that does not change between steps.
- Cited in: §3.1 G4, §7.2 (already cited but interpretation now sharper)

### Reviewability and Delegation

**Schmalbach, V. (2026). Software Delegation Contracts: Measuring Reviewability in AI Coding-Agent Work. arXiv:2606.17099.**
- Key insight: Structured delegation contracts improve reviewability of AI coding work without improving correctness. Documentation artifacts appear "mostly or only when explicitly demanded" by the contract structure. Evidence sufficiency improved in 22 of 30 comparisons. The cost is modest at +13% agent tokens.
- Our interpretation: This validates two of codoc's core design choices. First, proposals are a form of delegation contract that demands the agent declare structural changes explicitly rather than burying them in code. Second, the finding that documentation only appears when demanded is the empirical version of our forcing function argument. Without a structural mechanism that persists until answered, the oversight documentation will not exist. Schmalbach shows this with a controlled study on 64 agent executions. We show it with a within-subjects study on reviewers. Both converge on the same principle, that reviewability must be structurally demanded rather than hoped for.
- Cited in: §8.4 (strengthens forcing function argument with independent empirical validation) and potentially §6.2 (the shift from construction to oversight)

### Oversight Work in Practice

**Dhanorkar, S., Passi, S., & Vorvoreanu, M. (2026). Human Oversight of Agentic Systems in Practice: Examining Developer Experiences with Coding Agents. _FAccT '26_. ACM. arXiv:2606.05391.**
- Key insight: Interviews with 17 experienced developers revealed four forms of oversight work with coding agents: a priori control (setting constraints before the agent acts), co-planning (collaborating on the approach), real-time monitoring (watching as it works), and post hoc review (reviewing after completion). Oversight is preventative and proactive, not only reactive and retrospective.
- Our interpretation: codoc's architecture maps to all four forms. Descriptions are a priori control (declaring what the code should be). Comments and steers are co-planning (negotiating approach within a feature's scope). Live proposals during sync are real-time monitoring. The verdict mechanism IS post hoc review, structured and persisted. The empirical validation that effective oversight requires ALL four forms, not just the last one, justifies why codoc's architecture is bidirectional rather than a review-only surface. A tool that only supports post hoc review supports one of four necessary oversight modes.
- Cited in: §8.5 (frames the three implications as responses to empirically observed oversight needs)

**Gaube, S., Langer, M., Miller, T., et al. (2026). Keeping an Eye on AI: A Framework for Effective Human Oversight of AI Systems. arXiv:2605.16278.**
- Key insight: Effective oversight requires that humans maintain decision authority over system actions, with mechanisms that make consequences visible before commitment. Proposes six dimensions of oversight adequacy.
- Our interpretation: Proposals are the "visible consequences before commitment" mechanism. The developer sees what the system WOULD change, in the context where it would change it, before agreeing. This satisfies Gaube's requirement that the overseer can "assess the situation, form a judgment, and take action" before the system's action becomes irreversible. The change ledger satisfies the traceability dimension.
- Cited in: Could strengthen §8.5 (proposals as the minimum mechanism for meaningful oversight as defined by the framework)

### Decomposition and Cross-Cutting Concerns

**Parnas, D. L. (1972). On the Criteria To Be Used in Decomposing Systems into Modules. _Communications of the ACM_, 15(12), 1053–1058.**
- Key insight: Any single decomposition principle makes some changes local and others non-local. The choice of decomposition determines which changes are cheap and which are expensive. There is no decomposition that makes ALL changes local.
- Our interpretation: A feature tree is a partition. Each chunk binds to one feature, features nest into one parent. A cross-cutting concern, by definition, is a property that applies to code on both sides of a partition boundary. Parnas identified this forty years before AOP. The response is not to add a second hierarchy (aspects, mixins, traits) which doubles the problem, but to make the single hierarchy more queryable. Ephemeral views over the existing tree surface cross-cutting concerns without adding permanent structure that drifts.
- Cited in: §8.3 (why cross-cutting concerns are structurally intrinsic to any tree-shaped representation)

---

## New Papers — Third Depth Pass (added 2026-08-21)

**[REMOVED — Glorikian 2026 could not be verified in Google Scholar or SSRN. Likely hallucinated in a prior session. Do not cite.]**

**Treude, C. & Baltes, S. (2026). Context Rot in AI-Assisted Software Development: Repurposing Documentation Consistency for AI Configuration Artifacts. arXiv:2606.09090.**
- Key insight: Measured documentation staleness across 356 repositories, finding stale code references in 23.0% of AI configuration files. Introduces "context rot" as the term for persistent context files becoming outdated as the codebase evolves.
- Our interpretation: This is the empirical baseline for codoc's core motivation. The 23% staleness rate in CLAUDE.md/AGENTS.md files shows that the problem the introduction describes is measured, not hypothetical. Codoc's Loop A is precisely the mechanism that prevents context rot by reflecting code changes back into the representation continuously.
- Cited in: §1 (introduction, measured baseline for the documentation problem)

**Stray, V., Brandtzaeg, E.G., Wivestad, V.T., Barbala, A., & Moe, N.B. (2026). Developer Productivity With and Without GitHub Copilot: A Longitudinal Mixed-Methods Case Study. _HICSS-59_, pp. 7413–7422.**
- Key insight: A 2-year study of Copilot usage analyzing 26,317 commits found no statistically significant productivity change despite users perceiving themselves as more productive. The perceived-vs-actual gap only became visible through longitudinal design.
- Our interpretation: Even a tool with zero upfront cost (Copilot) required 2 years of data to reveal its actual impact. This precedent strengthens the §9.1 defense that a single-session study cannot measure codoc's long-term payoff. The study chose to measure what IS observable in 20 minutes (the trust threshold formation) rather than what is not (longitudinal productivity).
- Cited in: §9.1 (limitations, defending single-session design choice)

**Grabowski, H. (2026). The Spec Growth Engine: Spec-Anchored, Code-Coupled, Drift-Enforced Architecture for AI-Assisted Software Development. arXiv:2606.27045.**
- Key insight: Documents the damage from AI generating code "guided by a stale spec" and proposes drift-enforcement as a design principle for spec-code coupling.
- Our interpretation: Independent convergence on codoc's architectural insight from a different starting point. Grabowski's "drift enforcement" maps to Loop A. Validates that the design space codoc occupies is recognized as necessary by others approaching the problem from the AI-generation side rather than the human-oversight side.
- NOT cited in paper — too much overlap would make it look like prior art rather than convergent validation. Noted here for rebuttal use if a reviewer claims the architecture is obvious or already proposed.

---

## Summary: Citation Count by Section

| Section | Citations needed | Key anchors |
|---------|----------------|-------------|
| Intro (Players) | 8–10 | Knuth, Biggerstaff, Simonyi, SpecLang, CoLadder, Shipman |
| Related Work 2.1 | 5–6 | Knuth, Dit, SpecLang, CoLadder, RPG |
| Related Work 2.2 | 5–6 | Heer, Kandel, Liu, Tian, CoPrompt |
| Related Work 2.3 | 5–7 | Barke, Sarkar, Mozannar, Vaithilingam, ClarifyGPT |
| Design Goals | 2–3 | Olsen, Lee & See, Norman |
| Study 1 (v1) | 2–3 | Methods refs only (already published) |
| Study 2 | 3–4 | Braun & Clarke x2, Ericsson & Simon, Efron |
| Discussion | 4–5 | Clark & Brennan, Suchman, Hutchins, Star & Griesemer |
| Throughout | 3–4 | Hindle, LaToza, Liang, Zamfirescu-Pereira |

---

## New Citations — Round 5 (2026-08-21)

**Shukla, T., Feng, K., Wang, L., Rostami, M., & Zhang, A. (2026). Hedwig: Dynamic Autonomy for Coding Agents Under Local Oversight. _Proc. ACM Conf. AI and Agentic Systems._**
- Key insight: Developers experience frustration calibrating autonomy and have evolving preferences across sessions. Static permission settings cannot account for shifting trust. Hedwig dynamically adjusts agent autonomy based on accumulated interactions.
- Our interpretation: Hedwig validates that trust calibration across sessions is real and not static — but addresses it from the AGENT'S side (adjusting what the agent asks about). codoc addresses the complementary DEVELOPER side (making verification cheap so trust forms rapidly). The two are orthogonal and could compose: an agent that earns autonomy faster because the developer can verify its work through the tree. The 21-participant formative study confirms the frustration our design targets.
- Supports: §8.5 implications + §2.3 as further evidence that "no mechanism for calibrating oversight" is a recognized gap.

**Huang, R., Reyna, A., Lerner, S., & Xia, H. (2025). Professional Software Developers Don't Vibe, They Control: AI Agent Use for Coding in 2025. _arXiv:2512.14012._**
- Key insight: Experienced developers (N=13 field, N=99 survey) retain agency over design and implementation out of insistence on quality attributes. They value agents as productivity boost but keep ownership of design decisions.
- Our interpretation: The title captures precisely the CONTROL imperative codoc serves. These developers already do what codoc supports — the question is whether tooling can make their control strategy tractable at scale. Their "insistence on fundamental quality attributes" is the demand that makes a faithful representation valuable: the developer who insists on quality needs a surface through which to verify it.
- Supports: §1 motivation (developers want control, not delegation) + §8.2 (communication through the representation IS control)

**Khati, D., Liu, Y., Palacio, D. N., & Zhang, Y. (2025). Mapping the Trust Terrain: LLMs in Software Engineering. _ACM Transactions on Software Engineering and Methodology._**
- Key insight: Surveys trust calibration as task-specific — developers trust models differently for different code tasks. Trust is not a single dimension.
- Our interpretation: Our "trust threshold" claim is stronger than general trust-in-automation because it is architecturally enabled. Their task-specificity finding supports our "uniform mechanism" condition — if the maintenance mechanism varies by feature type, trust would not generalize from a small sample.
- Supports: §8.1 (trust threshold specificity) + §9.1 (threshold characterization limitation)

---

## New Citations — Round 6 (2026-08-21)

**METR. (2025). Measuring the Impact of Early-2025 AI on Experienced Open-Source Developers. Blog post, July 10, 2025.**
- Key insight: Randomized controlled trial with 16 experienced open-source developers across 246 real tasks. AI tools caused 19% slowdown vs. without, despite developers perceiving 20% speedup. A 39-point perception-reality gap. The slowdown is attributed to verification overhead and prompt engineering time that developers underestimate.
- Our interpretation: This is the strongest empirical evidence that verification cost is the dominant bottleneck in AI-assisted development, not code quality. Developers are literally slower because the cost of understanding and verifying AI output exceeds the speed gain of generating it. codoc's contribution is precisely to reduce verification cost through local, cheap, uniform checking — attacking the specific expense METR measured. The perception gap also validates our §9.1 limitation that single-session measurement is unreliable, since developers cannot accurately assess their own productivity under AI assistance.
- Cited in: §1 (problem framing), §9.1 (perception-reality gap as measurement challenge)

**Stack Overflow. (2025). 2025 Developer Survey Results.**
- Key insight: 84% of developers report using AI coding tools, but only 29% trust the output (down from 40% in 2024). Adoption without trust reveals a usage-trust paradox.
- Our interpretation: The paradox exists because AI tools produce plausible code quickly and the cost of distrusting manifests as delayed comprehension debt rather than immediate build failures. The declining trust despite rising adoption suggests the comprehension problem is worsening, not improving, as AI usage scales. codoc addresses this by making trust formation fast and evidence-based rather than requiring the developer to choose between blind reliance and expensive verification.
- Cited in: §1 (problem framing, adoption-trust paradox)

**Baltes, S., Speith, T., Treude, C., & Wagner, S. (2026). On the Need to Rethink Trust in AI Assistants for Software Development: A Critical Review. _IEEE Transactions on Software Engineering_, 52(4).**
- Key insight: Reviewed trust research in SE and identified a "significant maturity gap" relative to adjacent disciplines. Much SE research equates trust with artifact acceptance likelihood, collapsing the distinction between attitudinal and behavioral trust. Recommends adopting established trust models from automation research.
- Our interpretation: Our paper follows their recommendation explicitly — drawing on Parasuraman and Riley, Lee and See, and Schemmer. What we add is the connection to DESIGN: the conditions under which representational infrastructure accelerates the attitudinal-to-behavioral transition. Their critique validates our theoretical framing as responding to a recognized gap rather than reinventing the wheel.
- Cited in: §8.1 (validates theoretical approach, positions contribution relative to trust research maturity)

**Oukay, S., et al. (2026). Behind Agentic Pull Requests: A Taxonomy of Human Intervention in AI-Generated Code. _MSR '26_.**
- Key insight: Mining study of agent-authored PRs found that 58% of human effort is guidance-level intervention (restricting agent actions, enforcing conventions), 25% is defect correction, and only 17% is direct code changes.
- Our interpretation: Developers supervising agents spend most effort expressing what the code SHOULD BE rather than changing what it IS. This is precisely the activity codoc's descriptions and comments are designed to make efficient. The 58% guidance figure means that a tool optimizing guidance-level communication addresses the majority of the oversight burden rather than an edge case.
- Cited in: §8.5 (what oversight actually looks like empirically, justifying codoc's communication-first architecture)

**Sonar. (2026). AI Code Assurance Survey.**
- Key insight: 96% of developers report not fully trusting AI-generated code, yet only 48% verify it before committing. The gap between distrust and verification reveals a cost barrier.
- Our interpretation: Distrust without verification is rational cost avoidance. The developer knows the code might be wrong but the cost of checking exceeds the expected value of catching an error in any single commit. This creates a collectively irrational outcome where known-unreliable code accumulates because individual verification is too expensive. codoc changes the cost structure by making each check take seconds rather than minutes, bringing verification cost below the threshold where checking becomes cheaper than not checking.
- Cited in: §8.1 (cheap verification condition, connecting the trust threshold to the broader verification crisis)

**Faros AI. (2026). State of Engineering Productivity Report.**
- Key insight: Under high AI adoption, median pull-request review time increased fivefold, incidents per PR tripled, and PR size grew 51%.
- Our interpretation: AI scales code production without scaling the oversight infrastructure. The 5x review time increase shows that verification cost grows faster than code generation speed, exactly the imbalance codoc addresses by shifting verification from file-level to intent-level.
- Cited in: §8.5 (verification cost scaling problem)

**LinearB. (2026). AI Code Generation Impact Report.**
- Key insight: AI-generated PRs wait 4.6x longer for review pickup but are reviewed 2x faster once picked up. The combination indicates avoidance followed by rushed review.
- Our interpretation: Developers avoid reviewing AI code and then satisfice when they finally do. Both behaviors are rational given that reviewing unfamiliar code at file granularity is expensive. codoc's intent-level representation changes the review unit from "every line the agent changed" to "does this feature still match its description," a dramatically cheaper check.
- Cited in: §8.5 (review avoidance and satisficing as evidence of cost problem)

**Ko, A. J., Myers, B. A., Coblenz, M. J., & Aung, H. H. (2006). An exploratory study of how developers seek, relate, and collect relevant information during software maintenance tasks. _IEEE Transactions on Software Engineering_, 32(12), 971–987.**
- Key insight: Developers overestimate the value and underestimate the cost of roughly half their navigation choices. Foraging in the absence of strong cues is systematically miscalibrated.
- Our interpretation: Bindings eliminate the cost-estimation problem entirely. The cost of verifying a feature through a binding is known in advance (one click, bounded region). This removes the foraging miscalibration that makes verification feel expensive and unpredictable, which is part of why developers skip it.
- Cited in: §7.1 (bindings as high-confidence scent cues that eliminate foraging estimation)

**Dzindolet, M. T., Peterson, S. A., Pomranky, R. A., Pierce, L. G., & Beck, H. P. (2003). The role of trust in automation reliance. _International Journal of Human-Computer Studies_, 58(6), 697–718.**
- Key insight: Operators who understand an automation's mechanism calibrate trust faster than those who observe only outputs. Mechanistic understanding enables evidence-based generalization rather than per-instance verification.
- Our interpretation: This explains why two or three checks suffice. The developer is not sampling the population of features (which would require many samples). They are testing the PROCESS (content-hash comparison + deterministic loops), and once the process is confirmed, the induction to all features is rational. This is why the "uniform" condition matters — it makes each confirmed check evidence about the mechanism.
- Cited in: §8.1 (uniform condition, why few checks enable rational generalization)

---

## New Citations — Round 7 (2026-08-21)

**Monperrus, M. (2026). The End of Code Review: Coding Agents Supersede Human Inspection. arXiv:2606.13175.**
- Key insight: Position paper arguing that reviews of agent-generated code become "rubber-stamps" because "the cognitive cost of genuine scrutiny is prohibitive." Claims human code review is structurally obsolete under agentic coding because the volume and complexity exceed human review capacity.
- Our interpretation: codoc takes the opposite position. The cognitive cost of scrutiny is prohibitive only when scrutiny means reading every file at the implementation level. At the intent level, scrutiny means "does this feature still match its description," a check that takes seconds. The rubber-stamp problem is not inherent to human oversight but is a consequence of forcing oversight at the wrong level of abstraction. Monperrus's argument is the logical endpoint if nothing changes the cost structure. codoc is the thing that changes it.
- Cited in: §8.5 (the rubber-stamp endpoint as what happens without intent-level verification)

**Vella, J. & Blincoe, K. (2026). Trust and AI-Assisted Programming: A Six-Month Longitudinal Study of Developer Trust Calibration. _ICSE '26_.**
- Key insight: A 6-month longitudinal study of 95 professional developers found that trust calibration with AI coding tools is "ongoing engineering work" rather than a one-time decision. Perceived productivity gains did not correlate with measured experience changes. Developers who explicitly maintained calibration practices (periodic verification, boundary testing) reported more stable trust.
- Our interpretation: Their longitudinal finding validates that the architecture supporting trust must be continuous. A representation faithful on Monday but stale by Thursday forces daily re-calibration, and their data shows developers will not sustain that effort voluntarily. The synchronization loops are the mechanism that prevents daily re-calibration from being necessary. Their "ongoing engineering work" framing also positions codoc's automatic maintenance as reducing a cognitive burden that longitudinal evidence shows is real and persistent.
- Cited in: §8.1 (trust must be maintained continuously, not formed once; architecture prevents the re-calibration burden)

**Demirer, M., Musolff, L., & Yang, S. (2026). Writing Code vs. Shipping Code: The Impact of AI on Software Engineering Productivity. NBER Working Paper 35275.**
- Key insight: Study of 100,000+ GitHub developers found that autonomous agents generated 180% more commits but only 30% more releases. The difference represents work that was generated but could not pass review and integration gates through to delivery.
- Our interpretation: The gap between generation (180%) and delivery (30%) is a direct measure of the verification bottleneck. Code is being written faster than it can be verified and integrated. codoc addresses this by changing what "verification" means from "read every file" to "check each feature against its description." If the 150% excess could be verified cheaply, the delivery gap would narrow.
- Cited in: §1 (organizational evidence that verification is the bottleneck, not generation)

**Liu, Y., Widyasari, R., Zhao, Y., Irsan, I. C., Chen, J., & Lo, D. (2026). Debt Behind the AI Boom: A Large-Scale Empirical Study of AI-Generated Code in the Wild. arXiv:2603.28592.**
- Key insight: 302,600 verified AI-authored commits across 6,299 GitHub repositories. Found 484,366 distinct issues introduced by AI code. 15%+ of commits from every AI assistant introduce at least one issue. 22.7% of AI-introduced issues still survive in the latest revision. Code smells account for 89.3% of all issues.
- Our interpretation: Hard evidence that AI-generated code creates persistent quality problems at scale. The 22.7% survival rate means that nearly a quarter of AI-introduced issues are never caught or fixed, accumulating as the kind of invisible debt that the trust threshold is designed to prevent. Could strengthen §8.5 if needed, but current evidence from Faros AI is sufficient.
- Status: NOT CITED. Available if reviewer asks for peer-reviewed evidence over industry reports.
