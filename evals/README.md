# N1/N2/N3 — codoc against a generated CLAUDE.md

Three measures, from Naur's account of programming as theory building:

| | Measure | Instrument | Expected contrast |
|---|---|---|---|
| **N1** | what it does, how it relates to the world | functionality and structural-recall questions | small — a CLAUDE.md summarizes this well |
| **N2** | why each part is as it is | rationale questions, generated from commit history | largest — a CLAUDE.md almost never records why, and it is not recoverable from code |
| **N3** | constructive modification | a change task, scored on correctness, localization, and invariants broken | medium — where the bindings should pay |

N1 and N2 are implemented here. N3 is specified in §5 and not yet built: it
needs a different harness (apply a change, run the corpus's own tests), and
building it before N1/N2 produce a signal would be premature.

## 1. Running it

```bash
python -m evals.run prepare --corpus requests   # clone + build both arms (costs money)
python -m evals.run items   --corpus requests   # generate + filter questions (costs money)
python -m evals.run score   --corpus requests   # read + judge both arms (costs money)
python -m evals.run report                      # table across corpora
```

Stages are separate because their costs differ by an order of magnitude and so
do their failure modes. **Read `out/<corpus>/items.jsonl` before scoring.** A
question set nobody looked at is not ground truth, and it is cheaper to discard
a bad one than to score two arms against it.

Everything is resumable; `--fresh` forces a stage to rerun.

## 2. The two arms

Both are built from the same checkout, with history present, at the same model
tier, over the same subtree (`Corpus.subdir`).

- **`claude_md`** — `claude -p /init` in the checkout. Generated, not borrowed:
  a project's shipped CLAUDE.md ranges from excellent to absent, so using it
  would make the baseline a survey of other people's diligence. `/init` is also
  what a user actually has on day one. Any pre-existing CLAUDE.md is moved to
  `CLAUDE.md.preexisting` first, so `/init` cannot reformat someone else's work
  and be credited for it.
- **`codoc`** — `codoc init`, producing `tree.codoc`.

The baseline is built **first**, before `.codoc/` exists, so it never describes
codoc's own output.

## 3. Why the questions come from commits

Ground truth must not come from either artifact. Generating questions from the
codoc tree and then asking whether the codoc tree answers them measures nothing.
N2 items are therefore generated from commit messages — a third source both arms
could have consulted and neither is scored on directly.

**The filter matters more than the generator.** A "why does this retry?"
question is worthless if the answer is legible in the code, because then it
measures reading comprehension and any arm carrying a code pointer wins. So
every candidate goes to a reader that sees only the current code and no history;
if that reader recovers the recorded reason, the item is discarded. What
survives is the set of questions whose answers exist only in the project's
record. That is the N2 claim, stated as a filter.

It costs about two extra model calls per surviving item. It is not optional.

## 4. Known objections, and what answers them

- **"codoc just copies the commit text."** Every result carries
  `verbatim_overlap` — the longest run of the answer found verbatim in the
  artifact, word-level. High score with high overlap is relaying; high score
  with low overlap is synthesis. Two different claims; they are never reported
  as one number.
- **"the questions come from commits codoc read at bootstrap."** They do, and
  so could the baseline: `/init` runs in a repo with `.git` present and is
  allowed `git log`. The claim under test is not access, it is whether a method
  systematically harvests what is there. Reporting arms this way makes that
  falsifiable — if the baseline also collects rationale, the contrast vanishes.
- **"the judge is an LLM."** It is, and it needs its own validity evidence.
  Each run writes `human_sample.jsonl`, stratified per (corpus, measure, arm)
  cell rather than sampled at random, for hand-scoring. Report the agreement.
- **"self-evaluation on your own repo."** The `codenav` corpus is a **ceiling
  arm**, reported separately and never pooled with the third-party ones. Its
  commits were written by people who knew codoc would read them, and averaging
  that together with `requests` would hide exactly that fact.
- **"a truncated artifact was scored as a failure."** `artifact_truncated` is
  recorded on every result and surfaced in the summary.

## 5. N3, when it is built

The design that keeps ground truth independent: take a commit **after** the
point both artifacts describe, revert it, and give the reader the task its
message describes. Score three things:

1. **correctness** — the corpus's own test suite;
2. **localization** — how close the touched files are to the real commit's;
3. **invariants broken** — whether the change contradicts a constraint stated
   in a *different* commit's message. This is the measure the third element of
   codoc's description contract ("what must hold") is aimed at, and the only one
   of the three that is not already served by a good summary.

Checking out at a parent commit and rebuilding both arms there is what keeps
the artifacts from having seen the answer.

## 6. Files

| file | what it is |
|---|---|
| `corpora.py` | clone specs; clone depth exceeds codoc's commit-scan window so depth never becomes a hidden variable |
| `arms.py` | building the two artifacts |
| `items.py` | question generation and the code-only-answerability filter |
| `score.py` | the reader (artifact only, never the code), the judge, verbatim overlap |
| `run.py` | the four stages |
