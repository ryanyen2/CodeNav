# Papers behind codoc's generation pipeline

Two lines of work bear directly on what codoc is trying to do, and this folder
holds the reading notes plus the design conclusions drawn from them.

| Note | Line of work | What codoc takes from it |
|---|---|---|
| [01-codebase-understanding.md](01-codebase-understanding.md) | Program comprehension: how developers build a theory of a codebase | What a description has to answer, and why the tree needs distinct *altitudes* rather than uniform prose |
| [02-continual-learning-from-user-edits.md](02-continual-learning-from-user-edits.md) | NLP: learning latent preference from user edits without fine-tuning | The **style memory**: infer preference text from each human edit, retrieve by context, inject into the prose prompts |
| [03-rationale-and-why.md](03-rationale-and-why.md) | Design rationale recovery, commit intent, doc/code co-evolution | Grounding rules for *why* claims, and what a change record must carry to answer "why did this change" |
| [04-design-implications.md](04-design-implications.md) | — | The bridge: each finding turned into a concrete change in `codoc/` |

## Provenance of the citations

Entries marked **[verified]** were retrieved through the `paper_search` skill in
this session (raw output in `raw/`); the note carries the URL and the citation
count the API reported. Entries marked **[canonical]** are foundational works
recalled without a live lookup because the search APIs rate-limited on the older
literature. Their titles, authors, and years are stated only where confident, and
no numeric result from them is quoted.

The raw search transcripts are kept in `raw/` so a later reader can see what the
queries were and how thin some of the recall was.
