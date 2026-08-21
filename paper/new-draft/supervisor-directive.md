# Supervisor Directive

## 1. Thesis Clarity

The paper advances one thesis: a persistent codebase representation becomes useful not when it gains expressivity but when it crosses a trust threshold, the point at which a developer stops verifying against source and reasons from the representation alone. That thesis is stated well in the introduction and the abstract. A reader who skims only those two sections will get it.

The problem is that the thesis competes with the system description for the reader's attention. Sections 4 and 5 together occupy substantial space describing the architecture, and the trust threshold does not appear as a named concept until Section 8.2. A reader going front to back encounters four pages of loop mechanics and data models before reaching the finding that explains why any of it matters. The thesis should be named and defined earlier, at minimum in the introduction's "what we found" paragraph, so that the architecture sections read as "here is why the loops produce trust" rather than "here is how the loops work."

The introduction currently lists four contributions in its final paragraph. These should collapse into two. The design process across two versions and the system itself are vehicle, not contribution. The contributions are the trust threshold as a design principle and the pre-registered evidence that a synchronized intent representation improves detection and decision durability. Everything else is apparatus.


## 2. Intellectual Contribution

The paper gives the field one idea it did not have before: that the engineering investment in faithfulness infrastructure, specifically the synchronization loops, the binding fingerprints, and the hold set, pays off more than investment in the representation language, because the entire value proposition hinges on a trust threshold that forms in seconds and collapses on a single inaccuracy. This is the inverse of how developer tools are conventionally designed, where the assumption is that more features deliver more value. The trust threshold says that three features the developer trusts outweigh thirty they do not.

This idea is genuine and memorable. A reader a year from now would remember "the trust threshold paper" the way they remember Suchman's "plans are communicative resources" or Norman's "forcing functions." But the paper does not yet earn that status because it treats the trust threshold as a discussion-section observation rather than the central intellectual move. Section 8.2 is where the idea lives, and it reads as "here is an interesting pattern we noticed." It should read as "here is the design principle the field has been missing."

A secondary contribution worth elevating is the forcing function argument from Section 8.4. The claim that "being made to decide IS the mechanism behind durable traces" is sharp and testable. It currently lives in the implications subsection. It deserves to be stated as a principle in its own right, not nested under implications.


## 3. Structural Integrity

The paper's argument flows well from problem through design iteration through system through evaluation through interpretation. Two sections serve the template rather than the argument.

Section 5, Architecture, reads like system documentation. A CHI reader does not need to know that chunks carry a content hash h_tok and a shape hash h_ast, or that the changeset is computed from ADDED, REMOVED, and MODIFIED. What they need to know is why the architecture produces trust. The pseudocode and the data model should be cut to what is strictly necessary for understanding the evaluation. The rest belongs in supplemental material.

Section 3.1, Design Goals, is well written but formulaic. Five goals each with a paragraph of motivation creates a checklist rather than an argument. The goals matter because they explain why V1 failed and what V2 had to achieve. If the goals were woven into the V1 failure analysis rather than stated in advance as a rubric, the section would read as narrative rather than as a validation framework imposed on itself.

The transition from Section 7 to Section 8 is where the paper gains altitude. Section 7 presents findings. Section 8 interprets them. That transition is clean. But Section 8.1 currently opens with "the results confirm what the design goals predicted" and then acknowledges this is circular. A section that begins by admitting its own circularity should be rewritten to begin with the non-circular thing it actually says, which is that the representation functioned as a communication medium rather than a comprehension aid.


## 4. What Is Missing

Four things would make this paper stronger.

First, the trust threshold needs a conditions account. The paper says participants crossed it after verifying 2-3 features. It does not say what properties those features had, how long each verification took, or what would predict whether a given developer will cross the threshold or remain below it. Without conditions, the threshold is an observation rather than a design principle. The paper should specify at minimum: what makes a feature verifiable in seconds, what structural properties enable rapid calibration, and what the failure mode looks like when the threshold does not form. Section 7.4 has this data in the think-aloud protocols and should report it.

Second, the paper does not address what happens at scale. The evaluation uses a 4000-line project with a feature tree that fits on two pages. The claim that the representation amortizes comprehension cost depends on the tree remaining surveyable. At 200 features, surveyability breaks. The paper should acknowledge this limit explicitly and state whether the design scales or whether it serves a bounded problem. A single paragraph in limitations would suffice, but its absence lets reviewers write the objection themselves.

Third, the relationship between the two studies is under-theorized. Study 1 is presented as a design study that produced design insights. Study 2 is a confirmatory evaluation of the redesigned system. But the paper does not make explicit what the confirmatory study inherits from Study 1 and what it tests fresh. The transition between Section 3 and Section 6 should state clearly: Study 1 told us X. Study 2 tests whether the redesign based on X actually delivers Y.

Fourth, the paper does not adequately address the tension between G1 and G2 that Section 7.3 identifies. The finding that Loop A sometimes normalizes a planted problem away, by updating the description to match the agent's change, is the most important design tension in the system. It deserves more than a single paragraph of acknowledgment. This tension, between keeping the map faithful and keeping changes visible, is the core tradeoff the system navigates, and the paper should treat it as such in the discussion rather than as a caveat in the findings.


## 5. The One-Sentence Test

The sentence a PC member should write: "This paper shows that a synchronized codebase representation becomes useful not by gaining expressivity but by crossing a trust threshold, earned through 2-3 verified features, after which developers stop consulting source and reason from the representation alone."

The paper currently makes a different sentence easier to write: "This paper presents codoc, a bidirectionally synchronized feature tree that improves detection of problems in agent-generated code." That sentence describes a system evaluation, not an intellectual contribution. The difference between a borderline paper and a strong accept is whether the PC member's sentence names the idea or describes the artifact.

To make the right sentence easy to write, the trust threshold must be named in the abstract, defined in the introduction, motivated by the architecture, demonstrated by the findings, and theorized in the discussion. Currently it is named in the abstract, mentioned in the introduction, absent from the architecture, demonstrated in the findings, and theorized in the discussion. The gap is the architecture sections, which describe loop mechanics without connecting them to what produces trust.


## 6. Concrete Edits

**00-abstract.md.** The abstract currently opens with a dependent clause describing the trust threshold. This is the right move. No change needed to the abstract's thesis statement.

**01-introduction.md, final paragraph.** The four contributions should become two. Replace "an iterative design process across two versions and 24 total participants revealing why structure-mirroring fails while intent-mapping succeeds, the codoc system itself as a bidirectionally synchronized communication layer between developer and agent" with a single clause that treats these as apparatus. The two contributions are the trust threshold as a design principle and the pre-registered evidence for detection and durability gains.

**01-introduction.md, "What we found" subsection.** Currently states "the threshold at which trust formed was surprisingly low." This should define the threshold precisely: what cognitive operation changes, what behavioral evidence indicates crossing, and what the immediate consequence is for tool usage. The phrase "trust threshold" should be introduced here with enough precision that it carries through the rest of the paper as a technical term rather than a metaphor.

**04-system-design.md, Section 5.** The pseudocode blocks and the data model subsection should be cut to supplemental material. What remains should be restructured around the question "how does the architecture produce conditions for the trust threshold to form?" The answer is: deterministic resolution means the developer can verify any binding in seconds, the hold set means human edits are never overwritten, and incremental loops mean the tree is always current. State those three points and cite the mechanisms. Remove the chunk-level hashing details.

**04-system-design.md, Section 5.4.** This subsection currently argues that codoc holds three properties simultaneously. It should instead argue that holding those three properties is what enables rapid trust calibration. The connection to trust is in the final paragraph but it reads as an aside. It should be the thesis of the subsection.

**06-study-findings.md, Section 7.3, final paragraph.** The tension between G1 and G2, where Loop A normalizes away a planted problem, is currently a parenthetical. It should be elevated to a named finding with its own heading, such as "7.3.1 Where Faithfulness Works Against Visibility." This is the most intellectually interesting finding in the section because it reveals a real design tradeoff rather than confirming a prediction.

**06-study-findings.md, Section 7.4.** Currently states that participants verified 2-3 features before trusting. It should report what those verification episodes looked like: how long they took, what the participant did during them, what the features had in common, and whether any participant failed to reach the threshold and why.

**08-discussion.md, Section 8.1, opening sentence.** Replace "The results confirm what the design goals predicted" with a direct statement of the section's argument: that the representation functioned as a communication medium rather than a comprehension tool, and this reframes what codebase representations are for.

**08-discussion.md, Section 8.2.** This section is the intellectual core of the paper. It should be promoted. Consider restructuring the discussion so that 8.2 comes first, before 8.1, since the trust threshold is the more fundamental claim and the communication-layer observation is a consequence of it. A representation that is trusted becomes a communication layer because people are willing to write into it. A representation that is not trusted cannot serve as a communication layer no matter how expressive it is. The causal direction runs from trust to communication, not the reverse.

**08-discussion.md, Section 8.3.** The paragraph on cross-cutting concerns is strong. The paragraphs on debugging and initial cost are not doing enough work. They should either connect to the trust threshold account, showing that debugging breaks trust because it reveals the representation's boundaries, or be moved to limitations. Their current position in the discussion suggests they are theoretically important, but the paper does not theorize them.

**08-discussion.md, new subsection needed.** The G1-versus-G2 tension deserves a discussion subsection of its own. When the loop updates a description to match code that should have been questioned, the system optimizes for faithfulness at the cost of oversight. This is not a bug. It is a fundamental tradeoff that any synchronization system must navigate. The paper should name this tradeoff, state the design choice codoc made, and acknowledge that the opposite choice has a defensible rationale. Reviewers will ask about this. The paper should answer before they ask.

**09, Limitations.** Add a paragraph on scale. State clearly: this evaluation tested a representation of a small project. The design claims amortization over time, which the study window cannot measure. The design also assumes surveyability of the tree, which fails at sufficient feature count. Both are acknowledged limits, not fatal flaws.
