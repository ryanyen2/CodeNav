# Outline — the section above "The System"

A reflective section that sits between Related Work and The System. It tells the reader
that we built an earlier version of CoDoc, what it committed to, what we learned from
living with it, and why the current design departs from it. The tone should match the
system section: calm, measured, honest about tradeoffs, examples after the point, no
punchy hooks. Working title options: "An earlier design and what it taught us", or
"Background: a first version of CoDoc". Every factual claim below is grounded in the
CHI26-RR submission (CoDoc__CHI26__RR.pdf); points marked [reflection] are our reading
of why it fell short, to be stated as judgment rather than fact.

---

## 1. Framing (one short paragraph)

- We did not arrive at the current design directly. An earlier version of CoDoc was built,
  studied with 12 developers, and then largely redesigned. This section explains what it was
  and why we changed it, because the current design is in several places a reaction to it.
- Set the honest register: the earlier version worked, and the redesign was about a few
  choices that did not hold up, rather than a failure of the whole idea.

## 2. What the earlier version committed to (the design in brief)

Ground each in the PDF; keep it descriptive, not yet critical.

- **A document that mirrored the code's structure.** People authored the document in a
  lightweight syntax where a line stood for a directory, a file, a component, or a function
  (`/src`, `Register.tsx`, `%Register`, `$handleSubmit()`, references `@useAuth`, notes
  `#comment`). Any content that parsed into the intermediate representation could drive
  synchronization. [PDF §4.1, Fig. 3]
- **Grounded in how people already point at code.** A formative study of 21 real projects
  found that files and function names were the anchors people reached for, and the syntax was
  built to formalize exactly those anchors, while leaving finer implementation detail
  deliberately underspecified. [PDF §3.2 findings, §3.3 V3]
- **Eager, tightly coupled synchronization.** Editing the document parsed in real time and
  immediately created scaffolds (new files, placeholder functions) before generation; the
  system volunteered "feedforward" suggestions for code it inferred was needed; AI-made code
  changes surfaced back as diff-highlighted feedback classified by architectural significance;
  and undo in the document stepped the code backward in lockstep. [PDF §4.1–4.3, Fig. 5]
- **The framing problem was architectural drift.** Invisible, accumulated LLM inferences that
  a person never saw or approved. [PDF §1, §2]

## 3. What we kept

Signal continuity so the redesign reads as evolution, not repudiation.

- The core commitment to a **persistent, editable representation the person holds**, kept in
  sync with the code in both directions. [kept]
- **Review-first surfacing** of what the model did, so changes are seen and approved rather
  than absorbed silently. This survived and became the proposal and tracked-change model. [kept]
- The insight that people **anchor on real code elements**, reinterpreted as bindings rather
  than as the organizing syntax of the document. [kept, transformed]

## 4. Why we changed it (the tradeoffs — the heart of the section)

Four moves, each stated as "the earlier choice → what it cost → what the current design does".
(a) is grounded in the PDF's own acknowledged limitation; (b)–(d) are [reflection].

- **(a) Organizing by code structure vs. by feature.** Mirroring the code made the document a
  second view of the code rather than a home for purpose. Cross-cutting concerns had no place,
  which the earlier paper itself acknowledged (e.g. "redirect to error page for all utility
  functions" was hard to express). → The current design organizes by feature and treats the
  code mapping as a derived index, so a cross-cutting purpose is one entry binding many files.
  [PDF §3.3 V3 acknowledges this]
- **(b) A learned syntax vs. plain prose.** [reflection] The mirroring syntax was a small
  language people had to learn and stay inside. → The current surface is ordinary markdown
  prose with clickable code links, so there is nothing to learn before writing.
- **(c) Eager automation vs. an explicit hand-off.** [reflection] Immediate scaffolding,
  volunteered suggestions, and undo-in-lockstep moved the code before the person had decided
  they were ready, and coupled the document's history to the code's history. → The current
  design holds edits as drafts by default and changes code only on an explicit hand-off, run
  in the person's own session.
- **(d) Inferring work from a document diff vs. explicit, identity-keyed edits.** [reflection]
  Classifying document changes structurally to trigger generation was brittle. → The current
  design records edits as identity-keyed commands and never infers a code request from a text
  diff, and it resolves new code with a single whole-outline model pass instead.

## 5. What the study told us (short, honest)

- The within-subjects study (12 developers) found the earlier version gave people **more
  control and clearer mental models than a chat baseline**, and no significant difference on
  standard usability or workload measures. [PDF §5.4]
- It also surfaced the limits that pushed the redesign: difficulty expressing **cross-cutting
  changes**, and the **absence of version history** in the representation. [PDF §5, contributions]
- Frame this as: the study validated the core idea (a person-held, synced representation helps),
  and pointed at the specific commitments that needed to change.

## 6. Transition into The System (one or two sentences)

- The current design keeps the goal of a faithful, person-held view synchronized with the code,
  and changes two things: what the view is organized around (features and purpose, not code
  structure) and how conservative the automation is (a held draft and an explicit hand-off,
  not eager generation). The next section describes that design.

---

## Notes for drafting

- Cite the earlier submission as `\citep{codoc-prior}` throughout, same key used in
  system-section.tex, so the reflections there and the fuller account here line up.
- Keep the critical points fair. State (a) as the earlier paper's own acknowledgement, and mark
  (b)–(d) clearly as our judgment in hindsight.
- If a figure is available, a small side-by-side of the earlier code-mirroring syntax against
  the current feature outline would carry point (a) faster than prose. Optional, only if page
  budget allows.
