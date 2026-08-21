# 3. Iterative Design Process

We arrived at codoc's current design through two rounds. A first version studied with 12 developers revealed specific failures that demanded a redesign. Before describing each iteration, we articulate the design goals that served as our internal validation rubric throughout.

## 3.1 Design Goals

Each goal was extracted from a specific breakdown observed in either the first prototype or in existing tools. They are ordered by discovery because each failure revealed the next requirement. Faithfulness came first because without it nothing else mattered. Change visibility emerged second because a faithful representation that silently absorbs changes creates a new problem invisible until faithfulness is solved. A designer who starts with communication legibility without having solved faithfulness builds a language nobody trusts enough to speak.

**G1, Faithfulness.** The representation must map truthfully to the current code. Without faithfulness, the representation is worse than nothing because it creates false confidence. A README that says "authentication uses JWT" when the code switched to session tokens three weeks ago actively misleads. The concept assignment literature [Biggerstaff et al., 1993] showed that intent-implementation mappings are the foundation of comprehension, but every tool that produced them left maintenance to the developer, and the developer never did it.

**G2, Change visibility.** When code changes, the representation must show what changed and why rather than merely reflecting the new state. Git diff shows what changed at the text level but not at the intent level. A function was renamed, and the developer cannot tell from the diff whether that is cosmetic or whether a responsibility moved. What is needed is a surface that describes the change at the level of intent rather than characters. Sarkar et al. [2022] identified this as the missing property when developers validate AI-generated code. They see the result but not the delta that produced it.

**G3, Orientation efficiency.** After a developer maps the representation to their mental model once, subsequent changes should be cheap to pick up without returning to source. Mental models of code are expensive to build [LaToza et al., 2006] and fragile under context switches. Agent-mediated development compounds this because an agent can invalidate a mental model in minutes that took hours to build. In manual development, the mental model stays current as a side effect of authorship. Once an agent writes the code, that coupling breaks.

**G4, Communication legibility.** The representation must serve as a medium that both the developer and the agent read and write through, at the level of abstraction developers actually think in. Developers think in capabilities, responsibilities, and behaviors [Suchman, 1987] rather than in file paths and function signatures. A representation tied to filesystem structure forces translation before expression. We observed this in V1 when participants who wanted to say "make model calls resilient" could not express it because the syntax only afforded file-level references. That translation tax is the adoption barrier that killed every specification language that preceded this one.

**G5, Decision durability.** Every decision must persist in a form that survives the session and can be audited later. Chat histories scatter decisions across turns. A CLAUDE.md records the current state but not the decisions that produced it, and when the agent rewrites it after its own work, the developer's earlier decision vanishes.

The goals form a dependency chain. G1 is prerequisite for G3 because a representation that might be wrong cannot scaffold orientation. G3 is prerequisite for G4 because a developer constantly rebuilding context has no platform from which to communicate. G5 is a structural consequence when the mechanism is designed correctly. The one genuine tension is between G1 and G2, because a representation faithful to the current code may absorb changes silently rather than surfacing them. Section 8.4 examines that tension.

## 3.2 Version 1, A File-Structure Mirror

Our first design mirrored the codebase's file and function structure as an editable specification. Developers authored architecture using lightweight syntax mapping to the module hierarchy, covering directories, files, components, and functions, chosen to echo the anchors developers already use. Synchronization was bidirectional. Specification edits triggered code generation, while code changes were classified and surfaced as feedback.

We evaluated this design in a within-subjects study with 12 experienced developers averaging 7.6 years experience, all frequent AI coding tool users. Each participant completed construction and modification tasks using both the specification interface and a chat-based baseline.

### What worked

Participants recalled significantly more components with the specification at a median of 8 versus 5 with *W* = 78, *p* = .003, and context switches were half those in the baseline. The recall measure was closed-book, so the difference cannot be attributed to reading from a visible reference. Named, hierarchically organized intents give memory a categorical retrieval cue that outperforms file enumeration because a hierarchy supplies both the chunk boundary and the relation between chunks, meaning that recalling one node primes retrieval of its siblings. This validated G1 and G3.

### What failed

Under G2, the specification showed current state but did not distinguish what the developer authored from what the agent added. P4 reported "once a prompt was used, it was hard to track its impact on the final code." Under G4, the syntax could declare what should EXIST but not express what should CHANGE. File-level anchors had no place for cross-cutting concerns. Ten of twelve participants still needed chat for open-ended tasks. Participants appropriated the reference mechanism beyond its design, confirming they wanted richer expressivity than file-level structure offers.

### The core lesson

Both failures traced to mirroring file structure, which was stable and therefore good for G1 but also static and therefore bad for G2 and low-level and therefore bad for G4. P5 noted "with CoDoc I feel more responsible for the structure and quality of the code," which shows the representation made people care but did not give them means to act on that care at the right abstraction. A developer who feels responsible but cannot act on that feeling in the tool will act outside the tool, in chat, where the action disappears. Version 1 had the right foundation, a representation that participants trusted enough to reason from, and the wrong surface, a syntax that could not express what they needed to say. The redesign invested first in keeping trust formation rapid and only second in making the representation expressive enough for the communication patterns trust enables.

## 3.3 Design Response, From Structure Mirror to Intent Representation

The redesign changes what the representation IS. Instead of mirroring the filesystem, the new version maintains a *feature tree*, a hierarchy of named intents where each feature binds to whichever code chunks implement it regardless of which files those chunks live in. A feature is "retry logic" or "authentication" rather than `auth.ts`. One feature may bind to code scattered across many files, and one file's code may belong to several features. The organizing principle shifts from WHERE code lives to WHAT it does and WHY.

We chose a tree over flat tags and a directed graph for a reason rooted in how developers verify rather than how code is structured. Tags satisfy locality but violate uniformity, since each tag is independently maintained and verifying five says nothing about the sixth. A directed graph captures cross-cutting relationships faithfully but violates cheapness, since verification cost grows with connectivity rather than staying bounded by feature scope. A tree sacrifices representational accuracy for surveyability and for the structural uniformity that makes trust calibration rational. Every node is maintained by the same content-hash loop regardless of its depth or topic, so a developer who verifies two features at different hierarchical depths has tested the mechanism at two points and can rationally infer that the same process produced accurate results between them. Section 7.6 reports the cost. Cross-cutting concerns genuinely resist hierarchical expression, and the evaluation confirms this limitation is structural.

This directly addresses the G4 failure. A developer who wants to say "make model calls resilient" can now express that intent on the feature that describes model communication without naming files or functions. The feature's description says what the code SHOULD BE. The feature's bindings say which code currently implements it. The gap between the two is the work order.

### Addressing G2, Changes become proposals rather than silent updates

The first version showed only the latest state, with no way to distinguish "I wrote this" from "the agent wrote this." Notification panels fail because a dismissed notification leaves no persistent record. Diff views fail because holding old and new state simultaneously imposes the same reconstruction cost that motivated G3. The solution is co-location without reconstruction. A *proposal* appears at the position it would occupy if accepted, rendered as part of the tree but visually distinct, so the developer evaluates it in context rather than in a separate queue. The decision point persists until discharged.

### Addressing G5, Every decision enters the ledger

Developers will not write decisions down after the fact. Agent auto-summaries record what happened rather than what was decided. Commit-time rationale prompts ask at the wrong moment and were ignored within days. Both impose a documentation cost separate from the working cost.

The solution is that recording IS acting. Accepting a proposal IS recording a decision. Editing a description IS recording intent. The developer never faces a separate documentation moment because the working actions themselves produce the record. The trade-off is that the record has gaps whenever the developer works outside the tool. Section 7.5 reports under what conditions the record was absent.

### Addressing G4, Communication through description and comment

The redesign offers two channels in natural language. A feature's *description* says what the code IS and SHOULD BE, so editing it to express a desired state mints a directive scoped to that feature's code. A *comment* on a span of prose is a targeted correction where the commented span identifies the claim, the text states the fix, and the bindings scope the code to touch.

Developers in the first study did not want to write specifications. They wanted to say what the code SHOULD BE and have the agent figure out how. A specification language forces *premature commitment* [Green & Petre, 1996] because the developer must decide HOW before they fully understand WHAT. The redesign separates "what it should be" as description from "what to change" as comment and translates both into directives without requiring implementation steps.
