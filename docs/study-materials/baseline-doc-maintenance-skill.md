# Baseline condition — the document-maintenance skill

This is the skill installed in the BASELINE workspace (`.claude/skills/doc-maintenance/SKILL.md`).
It must be strong: the comparison is only meaningful if the baseline agent genuinely
tries to keep the document alive. Pilot this before anything else (design doc §3.2).

Deployment note: the baseline workspace gets this skill + the exported CLAUDE.md and
NOTHING else codoc-related (no MCP server, no hooks, no /codoc commands).

---

```markdown
---
name: doc-maintenance
description: Keep CLAUDE.md — the feature-level description of this codebase — current
  whenever code changes. Use after EVERY change you make to source files, and when you
  discover code that no section describes.
---

# Maintaining CLAUDE.md

CLAUDE.md is the team's feature-level account of this codebase: each section names a
feature, says what it is for, why it is built the way it is, and which code carries it
(`file.py::symbol` references). The team treats it as the source of truth for intent.
Your job is to keep it TRUE after every change you make.

## After every code change, before you consider the task done

1. **Update affected sections.** For each section whose code you touched: re-read it,
   and rewrite whatever the change made false. Keep the author's wording wherever it is
   still true — repair, don't rewrite.
2. **Record the why.** When your change embodies a decision (a layer chosen, a
   tradeoff taken, an alternative rejected), write one sentence of rationale into the
   section: what was decided, and why, and what was rejected if you considered
   alternatives. The user's stated requirements are rationale — cite them.
3. **Keep code references current.** Update `file.py::symbol` references for code you
   moved, renamed, added, or deleted. A reference to a symbol that no longer exists is
   a bug you introduced.
4. **Claim new code.** If you added code no section describes, either extend the
   best-fitting section or add a new section (title + 1–3 sentence description +
   references). Never leave new behaviour undescribed.
5. **Flag what you could not verify.** If a change might have made OTHER sections
   stale (a changed contract, a moved responsibility), check them; if you cannot be
   sure, add `> STALE?` above the section with one line saying why.

## Rules

- Descriptions state what the code does FOR the system and why — not a line-by-line
  narration, not a changelog. One paragraph per feature.
- Never delete a section's recorded rationale ("why / rejected alternative") unless
  the change genuinely invalidates it — then replace it with the new rationale.
- If the user makes a decision in chat (scope, behaviour, naming), record it in the
  relevant section — decisions that live only in the conversation are lost when the
  session ends.
- Do all of this in the SAME turn as the code change. Do not batch it for later; do
  not ask permission to update the document.
```

---

## Pilot checks for this skill (before participant 1)

1. Run the C2 calibration (design doc §6) and diff CLAUDE.md before/after: does the
   agent actually update sections, or only append? Does rationale survive?
2. Deliberately make a change that stales a *distant* section — does rule 5 fire?
3. Token/turn overhead: count agent turns spent on doc maintenance per task; report
   alongside CoDoc's overhead (design doc §2.1 confound).
4. If the skill fails silently in ≥2 of 5 runs, strengthen the trigger (e.g. add a
   post-change checklist prompt) BEFORE concluding anything about the baseline.
