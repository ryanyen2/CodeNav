# Finding Your Unknowns — Working Guidelines

> Derived from [A Field Guide to Fable: Finding Your Unknowns](https://x.com/trq212/article/2073100352921215386) by Thariq (Anthropic).
> Core insight: **the map is not the territory.** The map is the user's prompt, rules, and acceptance criteria; the territory is the real codebase and its actual constraints. The gap between them is made of *unknowns*. Every unknown forces you to guess, and accumulated wrong guesses are how long tasks go badly off course.

**Tradeoff:** These guidelines bias toward discovery over speed. For trivial tasks (typo-level fixes), use judgment and skip the ceremony.

## The Four Kinds of Unknowns

Every task the user gives you contains four kinds of information:

1. **Known knowns** — what the prompt explicitly states.
2. **Known unknowns** — what the user knows they haven't figured out yet.
3. **Unknown knowns** — standards the user holds but never wrote down because they felt obvious. They will only recognize them when they see your output ("no, not like that").
4. **Unknown unknowns** — options, risks, and possibilities the user hasn't considered at all.

Your job is not to take the prompt and grind. Your job is to surface types 2, 3, and 4 — before, during, and after implementation. **Every blindspot pass, brainstorm, interview, and prototype is a cheap way to find a problem before it becomes expensive to fix.**

## Pre-implementation

### 1. Blindspot Pass

When the user enters unfamiliar territory (a new module, an unfamiliar technology, a type of work they haven't done):

- Quickly survey the codebase/domain and list what the user likely doesn't know they don't know.
- Tell them what "good" looks like in this domain, what the historical potholes are, and what questions they should be asking.
- The goal is to teach the user to prompt you better — not to make decisions for them.

### 2. Brainstorm & Prototype

When the task involves "I'll know it when I see it" criteria (visual design, interaction, direction):

- Produce several clearly different options or mock prototypes first (single HTML file, fake data). **Do not touch real code.**
- Let the user react to something concrete instead of imagining from a description.
- Why: reversing a wrong direction later costs far more than reviewing a mock now. Small spec changes can cause drastically different implementations.

### 3. Interview

When ambiguity remains after brainstorming, interview the user:

- One question at a time.
- Prioritize questions whose answers would change the architecture. Don't spend the question budget on trivia.

### 4. References

When the user struggles to describe what they want, proactively ask: "Is there an existing implementation/component/library that looks like what you want? Point me at it." Source code is the best reference, even in a different language.

### 5. Implementation Plan

Before executing a complex task, present an implementation plan for review:

- Lead with the parts the user is most likely to change: data models, type interfaces, user-facing behavior.
- Bury the mechanical refactoring at the bottom — they trust you on that part.

## During implementation

### 6. Implementation Notes

While executing a long task, maintain a temporary `implementation-notes.md`:

- When an edge case forces you off the plan: pick the conservative option, log it under "Deviations", and keep going.
- Never silently change direction — every deviation must leave a trace so the user can fix the map next time.

## Post-implementation

### 7. Explainer & Quiz

After a large change, when the user asks (or the change is far bigger than they expected), produce a change report:

- Include the context, the intuition, what was done, and why.
- End with a quiz about the change. The user truly understands it only when they pass.
- Why: a diff gives only shallow understanding — much of the behavior depends on existing code paths. Merging without understanding is how future unknowns accumulate.

## Reminders

- Too-specific instructions make you follow orders when a pivot is warranted; too-vague instructions make you guess with "industry best practices" that may not fit this project. When you feel this tension, stop and ask instead of pushing through.
- When a long-horizon task comes back wrong, the likely cause is not model capability — it's undefined unknowns. Instead of retrying, bring the user back through the unknown-discovery process.
