---
name: bootstrap-dedup-fix
description: Three compounding bootstrap bugs caused 947 features (only 286 unique) in test/loci; fixes landed on transactions branch.
metadata:
  type: project
---

Bootstrap produced a 947-node tree for test/loci (548KB, 7904 lines) with only ~286 unique features — a 3.3× duplication ratio. Root cause was three compounding bugs:

1. **`_walk` fan-out** (`runner.py:373-374`): child sub-clusters were re-walked once per proposed feature, not once total. Fixed by hoisting child recursion outside the fp loop; first proposed feature becomes "head" for internal groups, extras nest under it, children walked once.
2. **`cluster_into_parents` file-level HAC** (`semantic_cluster.py:350`): re-clustered files, not groups, so the same original group could become a child of multiple merged parents. Fixed by clustering groups as atomic units (centroid embeddings) so each group belongs to exactly one parent.
3. **No hard dedup guard**: LLM hint was the only protection against duplicate slugs. Fixed by adding `emitted_slugs: set[str]` that blocks any repeat emission within a bootstrap run.

Anchor granularity also improved: Python (`python.py`) and TypeScript (`typescript.py`) adapters now emit `file::NAME` chunks for simple public module-level assignments (`NAME = value`, `const NAME = ...`) instead of absorbing everything into `::__module__`.

**Why:** The `_index.codoc` projection is the human-facing navigable artifact. With 947 nodes and 65% duplicates it was unnavigable — editing one copy left 31 stale, breaking the "single source of authored intent" promise.

**How to apply:** Before touching bootstrap pipeline, reflective pipeline, or lang adapters: read this plus [[project_codoc_cocoindex_pivot]] for indexing context.

User also flagged broader concern: even ~286 unique nodes may be too large, and the incremental update flow (reflective pipeline) needs the same dedup discipline — novel chunks should update existing features at the correct tree position rather than create duplicates. The reflective "novel" escalation path needs a semantic similarity check against existing features before proposing INTRODUCE.
