# Editor Report

## Summary

Sentence-level editing pass across all seven sections. Estimated net cut of 350-400 words.

## What was cut and why

### Redundant sentences deleted
- "It was a photograph rather than a time-lapse" (Section 3.2, repeats the point about showing current state vs change)
- "The pattern is consistent." (Section 2.3, adds nothing before the sentence that already demonstrates the pattern)
- "This is what makes a maintained representation worth its cost." (Section 3.1 G3, the next sentence already says why)
- "The question is not whether faithfulness matters but whether it can be maintained without manual labor." (Section 3.1 G1, the preceding sentences already establish this)
- "They knew what they meant and the representation could not hold it." (Section 3.1 G4, already conveyed by the example)
- "The representation was the medium, not the destination." (Section 8.2, terse "X, not Y" fragment; the surrounding sentences say this)
- "It is not that the tool is fast but that the developer has decided to reason from the tool's assertions rather than from the code itself." (Section 7.4, restates what the preceding sentence says)

### Long sentences split or compressed
- Abstract opening sentence (50+ words) split into two
- Section 2.2 "Trust requires *observable accuracy*, meaning..." split at the comma-gloss
- Section 5.2 Loop A design principle sentence restructured from one dense sentence into two
- Section 8.1 threshold definition stripped of its appositive gloss

### Vague verbs replaced
- "provides" -> "carries" / "offers" (context-dependent)
- "enables" -> concrete verbs throughout
- "addresses" -> specific action verbs
- "provide at most two" -> "deliver at most two"

### Style violations fixed
- "It is a label, not an account" -> "It is a label without an account" (no terse "X, not Y")
- "These artifacts are written by the agent, for the agent" -> "by the agent for the agent" (no comma splice creating terse fragment)
- "The representation was the medium, not the destination" -> deleted (terse fragment)
- "Being made to decide is a feature, not a burden" -> "Being made to decide is a feature" (dropped the terse contrast)
- Section heading "codoc:" with colon -> "codoc," with comma
- All em-dashes previously present have been removed by concurrent edits
- All parentheses in prose previously present have been removed by concurrent edits

### Structural improvements
- Related work opening list restructured from one dense colon-list sentence into three short declarative sentences
- Section 2.2 formality-informality paragraph restructured to separate structure from content more clearly
- Section 8.2 communication direction expressed as two separate sentences rather than comma-heavy compound
- Several "This is because..." weak openers removed by starting directly with the claim

## Sections not touched
- Placeholder quotes and DATA markers left untouched (awaiting real data)
- Statistics blocks left untouched (parentheses permitted there)
- Pseudocode blocks left untouched (colons permitted there)
