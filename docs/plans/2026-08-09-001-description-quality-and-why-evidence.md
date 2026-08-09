# 2026-08-09-001 — Description quality: evidence for the why, restraint in revision

**Goal.** Make a codoc description carry what a `CLAUDE.md` cannot: why each
part is as it is, in the author's own register, revised only where it became
untrue. This is the pipeline half. The evaluation half (N1/N2/N3 against a
`CLAUDE.md` baseline) follows, and depends on this landing first — there is no
point measuring rationale recall against descriptions that had no access to a
rationale.

---

## §1 The diagnosis

The prompts already asked for the why. `tree_update.txt` Rule 6 said "the key
design choice and its why"; `bootstrap_file.txt` said the same. The instruction
was not the problem. The inputs were:

| Pass | What the model saw | Why-evidence |
|---|---|---|
| Bootstrap | `symbol_path` + capped source + call/contain edges | **none** |
| Loop A | 600-char source snippets, subtree descriptions, graph edges, impacted features, `author_intent` (≤3 prompts, 2h, hook-dependent) | thin, agent-path only |

Three consequences worth stating plainly:

1. **The initial tree — the artifact a user judges codoc by, and the one an N2
   eval queries — had no rationale evidence at all.** Asked for a why with
   nothing to ground it, a model supplies the most plausible-sounding story. A
   confabulated why fails N2 *and* poisons N3: a fabricated invariant is
   exactly the kind of claim a careful modifier would respect and be wrong to.
2. **Four sources existed and none were read.** No `git log` anywhere in
   `codoc/`. The realize directive that *caused* a change was linked by
   `caused_by` but its text never rode back into the amend — codoc knew the
   answer and discarded it. Prior `rationale` strings sat in the events ledger
   unseen, so each amend re-derived the why and could contradict the recorded
   one.
3. **Revision eroded voice.** `amend` carries a whole replacement description;
   `is_small_amend` gated on a ≤30% character-change ratio. That proxy fails in
   both directions — a rewrite in the model's own register scores under 30% and
   lands silently, while a large true addition is blocked though it destroys
   nothing. `merge3` protects *concurrent* edits; nothing protected *sequential*
   voice erosion. And no style or altitude signal existed anywhere:
   `subtree.py` passed `{id, title, description, parent_id, bindings}`.

## §2 What descriptions should say, and why those three things

Grounding, so the contract is not just taste:

- **Naur (1985), *Programming as Theory Building*.** The theory is why the
  program is as it is and how it relates to the world — the part that dies when
  the team leaves, and the part no artifact reconstructs from code.
- **Letovsky (1987).** Programmers reading unfamiliar code ask *why*, *how*, and
  *what-if* questions; why-questions dominate and are the least answerable.
- **Sillito, Murphy, De Volder (2006/2008), the 44 questions asked during change
  tasks.** A large share are about what a change would affect and what
  constraints hold — not about what the code does.
- **von Mayrhauser & Vans.** Comprehension runs top-down from a domain
  hypothesis; a description's first job is to hand over that hypothesis.
- **Ko et al. (2007).** Rationale is among the most-sought and least-available
  kinds of information in a codebase.

That yields three elements, and their order:

1. **Purpose — always.** The problem it solves for the user or for the rest of
   the system, in plain words. This is the top-down hypothesis and the
   world-relation. (Maps to N1, where `CLAUDE.md` is already decent.)
2. **Why it is this way — when it can be said.** The design choice and the
   constraint, failure, or tradeoff it answers. (N2, the largest expected gap.)
3. **What must hold — when it can be said.** What breaks if someone changes
   this without knowing. This is the element that earns the bindings, and the
   one an N3 task scores directly ("did they break an invariant recorded
   elsewhere"). Nothing in the old contract was about invariants.

Written as one flowing paragraph, 1–3 sentences. A description that reads as
three answers to three questions has failed — the elements are a checklist for
the writer, not a shape for the reader.

## §3 The assertion register — how to infer without overclaiming

The decision: **always attempt a why, never flag confidence, let the writing
carry the certainty.** No provenance badges, no "(inferred)" markers, no
`presumably`. A reader should be able to trust the prose without decoding a
notation, which means the prose has to be honest on its own.

The rule that makes this checkable:

> You may describe what the code achieves. You may not narrate decisions or
> history you were not shown.

- Evidence present → assert flatly. *"Retries only on timeout, because the
  server can duplicate a non-idempotent post."*
- No evidence → describe the consequence, which needs no warrant. *"Computes
  the total on write, so a reader never sees a stale number."*
- Never, with or without evidence → invented history, people, or rejected
  alternatives. *"Added after users reported…"*, *"the team chose X over Y"*.

Hedging words are banned alongside invention: they are a way of stating an
unsupported claim while appearing not to. The register does the work.

## §4 What was built

### `codoc/loop/why.py` — the evidence channel

Three sources, in descending order of how directly they speak to a decision:

- **`commit_rationales`** — one repo-wide `git log` over the recent window
  (600 commits), parsed once and cached with a TTL, then sliced per file. One
  subprocess per bootstrap rather than one per file, which is what makes this
  affordable where it matters most. Records/fields are RS/US-separated so a
  multi-line body cannot be mistaken for the `--name-only` list. Noise subjects
  (`wip`, `fix typo`, `bump`, merges, sub-12-char subjects) are dropped but
  still spend their per-file budget — a free skip would let one file's ancient
  history crowd out every other file's recent history. Only the opening
  paragraph of a body is kept, trailers stripped.
- **`directive_rationales`** — the `Author asked:` / `New intent:` lines from
  `realized.jsonl`, scoped to the features under change. Not inference at all:
  the author said it, codoc queued it, an agent implemented it.
- **`prior_rationales`** — rationale earlier passes recorded on these features,
  newest first, so an amend extends a running theory instead of re-deriving one.

Every source is capped and the assembled block is capped again (4500 chars),
dropping commits before directives — a commit merely touched a file the feature
binds, a directive was aimed at the feature. Nothing raises; a repo with no git
yields `{}` and the key is omitted, which is what the prompt's register keys off
to stay hedged.

### Relevance-ranked intent (`codoc/loop/intent.py`)

`recent_intent` answers "what was the user just doing" — right for a status
line, wrong for a description: in a session touching four areas, recency
attributes every change to whatever was typed last. `relevant_intent` scores
each captured prompt against the changed symbols, splitting camelCase and
snake_case on both sides so "make the ollama client retry" matches
`OllamaClient.complete`. The newest prompt is always kept regardless of score,
because a follow-up turn ("now do the same over there") shares no words with the
diff and recency is the only signal it leaves.

### Authorship and voice

- `subtree.py` entries now carry `written_by` (from `feature_writers.role`).
  Absent when unrecorded — unknown authorship is not the same as the loop's, and
  guessing would put a person's prose under the loose gate.
- `store.human_written_descriptions()` supplies `changes["author_voice"]`: up to
  two descriptions a person actually wrote, shown as the register to match.
  Samples beat derived metrics — telling a model "the author writes short
  sentences at a high level" produces its idea of that; showing it two of their
  paragraphs produces theirs. A new node has no prose of its own to take a cue
  from, which is where this pays.

### The amend gate (`codoc/loop/apply.py`)

`preserved_ratio(old, new)` replaces overall similarity as the decision input:
the sum of matching runs ≥24 chars, over `len(old)`. Long runs mean preserved
clauses; short scattered ones are just the vocabulary any two descriptions of
the same code share. This separates the two cases the old ratio got backwards —
a re-say in the model's voice scores as similar, a true addition scores as a
large change.

| Prose written by | Bar for auto-apply |
|---|---|
| a person | `preserved_ratio ≥ 0.85` |
| the loop / an agent | `preserved_ratio ≥ 0.50`, else the old ≤30% size rule |

A rewrite is not refused — it becomes a pending proposal with the before/after
in front of the author. That is the whole difference between a tool that
maintains someone's document and one that gradually replaces it.

### Prompts

- `tree_update.txt`: Rule 5 gains "an amend is a repair, not a rewrite"; Rule 6
  becomes the three-element contract; Rule 7 is the assertion register; Rule 8
  is altitude and voice matching. `rationale` now has a contract of its own —
  one plain sentence a person reads in the feature's history. Legend entries for
  `why_evidence`, `author_voice`, `written_by`; a second amend example showing a
  surgical repair. All edits stayed inside the frozen cache-prefix segment (one
  one-time recache; no placeholder crossed a `<<<CACHE_BREAK>>>`).
- `bootstrap_file.txt`: same contract and register, plus a `{why}` block in the
  volatile tail carrying that file's commit rationale.
- `bootstrap_org.txt`: the register rule for theme descriptions.

## §5 Tests

`tests/loop/test_why.py` (32), `test_amend_preservation.py` (18),
`test_author_voice.py` (11), plus `TestRelevantIntent` in `test_intent.py` (7).
Full suite: **1472 passing**, up from 1404.

Most of the why-evidence tests pin what gets *thrown away* — the channel exists
so a description can state a reason, so anything it surfaces that is not a
reason costs more than it saves.

## §6 What this does not do

- **Docstrings and `NOTE:`/`Rationale:` comments are not extracted.**
  Deliberately out of scope for this pass. They are already inside the source
  blob, so the model can use them, but nothing directs it to prefer a stated why
  over an inferred one, and nothing surfaces them at all for a chunk truncated
  at 600 chars.
- **Sentence-level authorship.** `written_by` is per feature. A description
  half-written by a person and half by the loop is treated as the person's,
  which is the safe direction but is not precise.
- **No back-fill.** Existing trees keep their existing descriptions; the new
  contract applies to the next amend or a fresh `codoc init`. The eval needs a
  freshly bootstrapped tree to measure against.

## §7 Next — the evaluation

Design sketch, to be built in the next pass:

- **N1 (functionality, structural recall).** Questions generated from the store:
  which feature owns this symbol, what does this feature do, where would you add
  X. Expected contrast: small — `CLAUDE.md` summarizes this well.
- **N2 (rationale).** Questions generated *from the evidence*, not from the
  descriptions — take a commit body or a realized directive stating a reason,
  ask the reason, and score whether it is recoverable from the artifact under
  test. This makes the ground truth independent of the thing being scored, and
  is only possible because §4 collects the evidence in the first place.
  Expected contrast: largest.
- **N3 (constructive modification).** A Stage-2 change task, scored on
  correctness, localization, and whether it broke an invariant recorded
  elsewhere. The third description element is aimed squarely at this.

An honesty check belongs in the harness regardless of arm: sample descriptions
and ask whether each causal claim is supported by evidence available at
write time. The register in §3 is only worth having if it is measured.
