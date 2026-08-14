# Technical evaluation design

Status: draft. Companion to `2026-08-11-001-user-study-design-v2.md` (the CHI
human study, which carries the contribution) and `evals/README.md` (N1/N2).

## 0. What this is for, and what it is not

This is **not** a contribution section. It is a robustness section: two
paragraphs showing the system holds up on real repositories under real change.
The contribution lives in the user study and the design.

That framing decides everything else. It means we optimize for *a small number
of clean, uncontestable numbers*, not for coverage or for beating anyone. Any
metric that needs hand labelling, a competitor integration, or a paragraph of
methodology defence is out of scope by definition, because it cannot fit in two
paragraphs and it invites a fight we do not need to have.

One exception is worth chasing on the side. If codoc measurably helps an agent
find the right code faster than what people do today, that is a real result and
worth more than the robustness section. It is scoped separately in §6 and it
does not block anything.

## 1. The protocol: shake out first, then report

The instinct here is correct and it is the most important decision in this
document. A first run over real commit history will surface bugs. Numbers
produced by a run that was *also* finding and fixing bugs are worthless, because
the fixes and the measurements are entangled.

So: two phases, with a hard freeze between them.

**Phase 1, shakeout.** Run the replay harness as a debugging instrument. Expect
it to fail. Every failure is triaged into a real bug, a design gap, or a
harness artefact. Fix, improve, repeat until failures stop being interesting.
**Report no numbers from this phase.** Its output is a fix list, not a result.

**Phase 2, the reporting run.** Freeze the code. Freeze the metric definitions.
Freeze the repo list. Run once. Report what comes out, including whatever is
still imperfect.

### 1.1 Hold out repositories, or the freeze means nothing

This is the part that makes the two-phase protocol credible rather than
cosmetic. If we shake out on the same repositories we report on, a reviewer
asks whether we fixed bugs until the numbers looked good, and the honest answer
is yes.

Split the corpus up front:

- **Development set**, roughly 5 repositories. Used for Phase 1. Beaten on
  freely. Never reported.
- **Reporting set**, roughly 5 repositories. **Not run, not looked at, not even
  indexed until the code is frozen.** Phase 2 runs on these.

Say so in the paper, in one clause. It costs nothing and it converts "we tested
until it worked" into a defensible protocol. If a held-out repository exposes a
new bug in Phase 2, that is a finding and it gets reported, not fixed and
re-run. Fixing it means starting a new Phase 2 on a fresh held-out set.

## 2. What gets measured

Slim, because two paragraphs. All of it comes from one harness: check out T₀,
`codoc init`, then walk commits forward applying each as a changeset.

**(a) Address resolvability.** Every binding points at a file and a symbol.
After each commit, how many of those addresses still resolve? Report the rate
after N commits. This is the headline number: it needs no ground truth, it
cannot be argued with, and it is the direct measure of the thing codoc claims to
prevent. We already compute it (`codoc_status()` reports `dead_refs`); the work
is persisting it.

**(b) Attribution accuracy on mechanical change.** Stratified by change class:
edits in place, moves, renames, deletions. These four should be near-perfect and
should require no model call at all. Ground truth comes from `git diff -M -C`
(line-similarity based rename and copy detection) and optionally PyRef.

**This must not use codoc's own signal.** Loop A detects relocations via
`tokens_hash` and `types_hash`. Building ground truth from the same hashes
measures nothing. Git's similarity detection is genuinely independent, which is
why it is the primary source here rather than a fallback.

**(c) How much change needed judgment.** The fraction of commits resolved with
zero model calls, and the fraction of changed chunks that were new code rather
than relocated code. This is the number that makes (b) meaningful: it says how
much of real change the mechanical path actually covers.

**(d) Cost.** Tokens and wall-clock per commit. Needs plumbing we do not have —
`complete()` returns a bare string and discards the provider's usage object
(`codoc/config.py:195`). Fix this before Phase 1 or it cannot be backfilled.

That is the whole list. Four numbers, one harness.

## 3. Explicitly out of scope

Written down so it does not creep back in:

- **New-code attribution accuracy.** No cheap ground truth exists; hand
  labelling 300+ additions is not worth it for a robustness paragraph. We report
  *how often* judgment was needed (2c), not how well it did. Say that plainly in
  the paper rather than hiding it.
- **Comparison against RepoDoc, CodeWiki, RepoAgent, RPG-Encoder.** Integration
  cost is large and we are not claiming to beat anyone here.
- **Identity stability and human-prose survival.** Real claims, but they belong
  with the authorship argument, not the robustness section. Revisit if a
  paragraph turns out to have room.
- **Benchmark runs (SWD-Bench and similar).** Only relevant if §6 turns into a
  real result.

## 4. Build order

1. Thread the provider's `usage` through `complete()`. Cannot be backfilled.
2. Persist `LoopAResult` per pass to JSONL. One line of plumbing, unlocks (c).
3. The replay harness. Reuse `evals/corpora.py`'s `Corpus` dataclass and
   `materialize()`, and `evals/run.py`'s staged resumable structure.
4. Phase 1 on the development set. Fix things. This is where the time goes.
5. Freeze. Phase 2 on the reporting set. Write the two paragraphs.

## 5. Implementation notes that will cost a day each if missed

- **`run_loop_a` cannot produce RETIRE** (`allow_retire=False`,
  `loop_a.py:1072`) and does not AMEND (`amend_on_change=False`). The deletion
  class only fires through `reconcile_drift`. Replaying with `run_loop_a` alone
  makes deletions look broken when they are not, and that will eat a day of
  Phase 1 as a phantom bug.
- **cocoindex's App is a per-process singleton**, one `codoc_dir` per process
  (`tests/loop/test_end_to_end.py:5-9`). Not a blocker: one repo's whole history
  uses one `codoc_dir`, so run one long-lived process per repo and parallelize
  across repos, not commits.
- **`compute_changeset` diffs the on-disk index, not two trees.** Drive it by
  checkout: checkout parent, `compute_changeset`, checkout child,
  `compute_changeset(file_scope=<git diff --name-only>)`. The second return
  value is the commit's changeset. Bound it with `file_scope` or re-indexing
  dominates runtime.
- **`apply_changeset`** (`loop_a.py:456-506`) is the clean injection point, with
  an injectable `propose`. `tests/bdd/world.py` is a working precedent.
- **`propose_never`** (`tests/bdd/world.py:46-60`) asserts the LLM was not
  consulted. Use it on the mechanical classes so "needed no model call" is a
  hard assertion rather than a soft count.
- **`run_agent` memoizes on prompt** (`codoc/agent/base.py:252-283`, LRU 32,
  process-local). Count model calls at the provider boundary or they
  under-report.
- **`test/` corpora have no `.git`.** Clone fresh, as `evals/corpora.py` does.

## 6. The optional upside: does codoc help an agent find code faster?

Separate track, does not block the robustness section. This is the one place a
real result is available.

The experiment: give an agent a change request drawn from a real merged pull
request, count how many steps it takes to reach the files and symbols that pull
request actually touched. Ground truth is free, since it is the merged diff.

Arms:

| Arm | What it is |
|---|---|
| no aid | floor |
| codoc, live | the tree plus maintained bindings, via MCP |
| codoc, frozen | the *same tree, same prose*, exported to CLAUDE.md at T₀ and never updated |

The third arm is the interesting one. It holds content, wording and authorship
exactly constant and varies only whether the addresses are maintained. Run the
same tasks at 0, 50 and 200 commits after T₀. If the frozen arm degrades and the
live one does not, that is a clean result attributable to maintenance rather
than to having a document.

Metrics: steps to target, plus file-level precision, recall and F1 against the
merged diff. Temperature 0, at least 3 runs per task.

Two warnings worth knowing before spending money here:

- A published study (arXiv 2602.11988) found that giving coding agents context
  files does not generally improve task success and adds over 20% inference
  cost, and that repository *overviews* specifically do not help. A plain
  "with and without codoc" comparison has a real chance of returning null. The
  live-versus-frozen comparison is the version that survives this, because it
  isolates addresses rather than prose.
- Prompt detail dominates model choice in localization tasks (arXiv 2603.26137
  found file-level F1 moving 0.20 to 0.81 purely on prompt granularity). Keep
  prompts minimal and fixed, or the ceiling erases any effect.

## 6.5 Phase 1 log

Running record of what the shakeout found. Numbers here are from the DEV set and
are **not reportable** — they exist to decide what to fix.

### Fixed in codoc (real defects, not harness artefacts)

1. **Unaddressable bindings were written without any validation.** A bootstrap
   of psf/requests produced one binding of 765 whose `symbol_path`
   (`tests/test_idna_without_version_attribute`) could not match any chunk — the
   model dropped the basename out of the middle of the path. Nothing on *any*
   write path checked a proposed binding against the index: not bootstrap, not
   Loop A, not `codoc_attach`, not `codoc propose`. Such a binding is invisible
   to the temporal diff, which only reasons about chunks the index knows, so it
   dangles forever.
   Fixed at three levels: a structural guard in `apply.py` (`_addressable` —
   every indexed chunk is `<file>::<qualified>`, so a symbol_path lacking that
   prefix is rejected at the one chokepoint every writer passes through); an
   index-membership filter in `bootstrap_hier._ensure_file_coverage`, where the
   per-file whitelist already existed; and removal of the `(b, b)` fallback in
   `mcp/tools._parse_binds` and `agent/propose.py`, which manufactured
   unmatchable symbol paths by design. Regression tests in
   `tests/loop/test_apply.py`. Flask then bootstrapped with 0 unresolvable
   bindings against 1084 chunks.
2. **`LoopAResult.llm_calls` under-reported the single-call case**, staying 0
   whenever the pass made exactly one model call. Escalation rate is one of the
   four numbers we report, so this would have been silently wrong.
3. **Token usage was discarded entirely.** `complete()` returns a bare string and
   every provider's usage object was dropped. Added a process-global accumulator
   (`config.usage_snapshot()` / `reset_usage()`) that all four providers record
   into, so a caller can scope cost to one pass by subtracting two snapshots.
   Recording never raises. This could not have been backfilled after a run.

### Fixed in the harness (would have corrupted the result)

4. **File-level ground truth was blind to the class codoc is best at.**
   Screening five repositories over 1200 commits found 29 file renames against
   2069 modifications — in real development files are rarely renamed, while
   functions move between files that both persist. Git reports that as two
   unrelated modifications. Symbol-level truth recovers it: flask 99 moves vs 4
   file renames, altair 2112 vs 25. Without this the eval would have reported
   "no data" for relocation while the system was relocating constantly.
5. **A ground-truth granularity mismatch produced 13 phantom failures.** The
   first symbol extractor emitted only functions and classes, so codoc's
   module-level assignments (`TypeVar`s, constants) and its per-file
   `__module__` chunk were unmodelled — and every one of them scored as a missed
   rename. After aligning the *unit of analysis* (not the matching signal), the
   extractor models 100% of codoc's chunks across 60 flask files. The
   independence that matters is untouched: codoc matches on content and AST-shape
   hashes, this matches on names alone and never reads a body.
6. **Resume was broken and silently double-counted.** `replay()` computed a
   resume offset and never used it, so a resumed run re-walked from the start
   and appended a second row per commit — 163 rows for 118 commits. The
   duplicates were no-ops (the tree was already past them), which is exactly
   what makes them dangerous: invisible in a summary, and they drag every rate
   toward whatever a no-op scores.
7. **`prepare` rewound HEAD, so the replay range collapsed to nothing.** The tip
   has to be pinned before the rewind.
8. **Scoring changes cost a full paid re-run.** Now every commit writes a
   `traces.jsonl` line carrying the classifier's exact inputs, and `prepare`
   snapshots the post-bootstrap `.codoc`. A scoring fix is `rescore` (seconds,
   free); a re-run restores the baseline instead of paying for `codoc init`.

9. **The trace format dropped the evidence for its own invariant.** Traces
   initially kept only bindings in touched files, on the reasoning that bindings
   elsewhere cannot change. That reasoning is circular — "cannot change" *is*
   the untouched-file invariant, so dropping those rows deletes the only data
   that could falsify it, and a rescore reported the invariant as having no data
   rather than as holding. Now the whole map is kept (a few MB per corpus).
   Related: a lossy `rescore --replace` silently zeroed those fields before the
   guard existed, which is why rescore now refuses to overwrite a field it
   cannot recompute.
10. **Rescore must recompute ground truth from git, not read it back.** Reading
    the stored `moved` map back would carry forward exactly the ground-truth bug
    the rescore exists to fix. Deriving it from git costs a second; what the
    trace preserves is the expensive half, the binding snapshots.

### The metric that had to be split

Reporting one "move followed" rate conflated two very different outcomes, and
the conflation made codoc look worse than the evidence supports. flask's
`sansio` refactor moved code from `src/flask/blueprints.py` to
`src/flask/sansio/blueprints.py`; in every apparent failure the binding **did**
arrive at the new address, under a *different* feature. Nothing rotted — the
tree reorganized around the refactor, which may well be correct.

So moves are now scored three ways:

* **followed** — the same feature owns it at its new address
* **rebound** — bound at the new address, different feature (re-attribution)
* **lost** — not bound at the new address at all

Only *lost* is the failure the system exists to prevent, and it is the number
the paragraph should lead with. *Rebound* is worth reporting honestly next to
it: `method.tex` claims relocations re-attach deterministically to the prior
feature, so a nonzero rebound rate is a real deviation from the stated design
even when it is not rot. Deciding which of those two the paper claims is a
question for Phase 2, and it should be settled before the freeze, not after
seeing the number.

### Design question this raised

**Coverage and the pending backlog have to be reported separately.** Coverage
fell from 1.00 to 0.985 on flask at a single commit that added four files. The
cause was correct behaviour: the model proposed a *new feature node*, which is
structural, so it became a proposal awaiting a human verdict rather than being
auto-applied. An unattended replay has no human, so those chunks stay unbound.
That is the absence of a reviewer, not lost attribution, and reporting coverage
alone would charge the system for it. `pending_after` is now recorded alongside.

**Decided (2026-08-13): report both arms.**

* **unattended** — nobody reviews. This is the system's actual claim: bindings
  survive on their own. Structural proposals pile up and coverage falls, and
  that is reported as a backlog, not as a failure.
* **attended** — every proposal is accepted through the real verdict path
  (`inbox.append_verdict` then `run_loop_b`, the same path as the IDE's Accept
  and `codoc accept`). This is what the tree looks like in use.

Safe to automate: `classify.edit_mints_directive` only queues code-writing work
for an ADD_NODE that is an unrealized *plan* placeholder, and Loop A's drift
proposals describe code that already exists. Retires cannot appear at all —
`run_loop_a` passes `allow_retire=False`.

Confirmed on flask: the single proposal that drops unattended coverage to 0.985
is accepted at the commit that raises it, pending returns to 0, and coverage
stays at **1.000**. The two arms differ by exactly the thing they are meant to
isolate, which is the evidence that the unattended dip is the absence of a
reviewer rather than lost attribution.

### 11. A well-formed binding that names nothing (found by the attended arm)

The attended arm immediately earned its keep: it ended with 2 unresolvable
bindings where the unattended arm had 0. Accepting a proposal wrote bindings
the model had authored when the proposal was raised, and Loop B re-checked
nothing — it passed no lookup to `apply_op` at all, so every accepted binding
was also written with an empty fingerprint.

On flask's sansio split the accepted proposal bound
`sansio/app.py::App._make_timedelta` and `sansio/app.py::create_jinja_environment`.
Both are correctly *shaped*, so the `_addressable` guard passed them; neither
names a symbol that exists (the first is not a method of `App`, the second is
not module-level). **Shape is not enough — membership in the index is the real
check.** `apply_op` now takes an optional `index_keys` set, and Loop B supplies
it lazily (one index read per drain, only when an accepted op carries bindings).
Re-running the arm: 2 → **0**.

Worth noting for severity: unlike the bootstrap case, `reconcile_drift` *would*
eventually have swept these, since it compares bindings against the index key
set. They were repairable, just not by any path the daemon runs routinely.

12. **Ground truth cost more than the thing it was scoring.** On altair the
    harness slowed to a crawl while Loop A itself was fine (median 2.5s/commit).
    The cause was one `git show` subprocess per touched file per side, so a
    commit touching dozens of files spawned a hundred processes. Replaced with a
    single `git cat-file --batch` per commit, plus a parse cache keyed by
    `(sha, path)` — in a sequential replay a file's child blob at commit N is its
    parent blob at commit N+1, so half the parsing was duplicate work. Verified
    identical output against the per-file version over 130 blobs before
    switching. Ground truth now costs 0.42s/commit on altair.

13. **The same defect again on Loop A's path, and only the stress corpus
    exposed it.** flask and httpx both ended with zero unresolvable bindings.
    altair — three times the size, an order of magnitude more churn —
    accumulated **28**, in steps that never came back down: 0 → 7 → 22 → 25.
    Every one had an empty fingerprint and named a symbol that does not exist
    (`tools/schemapi/utils.py::T1`, `altair/typing.py::__module__`), clustered
    in the files the model was asked about most. Loop A validates nothing about
    a model-supplied binding beyond its shape, and the temporal diff can never
    see such a binding again, so they are permanent.

    The fix is nearly free: `cs.rows` already carries the whole post-update
    index for the graph rebuild, so `index_keys` costs one set build and no I/O.
    It could NOT be derived from the fingerprint lookup instead — `fp` is
    changeset-scoped, so a legitimate binding onto an untouched chunk also has
    an empty fingerprint. The coverage net is deliberately left unvalidated: it
    builds every binding from a real `ChunkRef`, so it names a chunk by
    construction.

    Re-running altair to the same point: unresolvable **25 → 0**, untouched-file
    violations **13 → 2**.

    The lesson for corpus selection is the sharper finding. Two clean corpora
    said the system was perfect on this axis. Only the one chosen for churn
    disagreed, and it disagreed about a defect that had been present all along.
    A held-out set of five moderate repositories could plausibly have reported
    zero and been wrong.

14. **One malformed model reply killed the whole pass.** A truncated response on
    altair's `altair.datasets` commit (`Expecting ',' delimiter` at char 3751)
    raised out of `parse_solution` and aborted the entire Loop A pass —
    discarding the deterministic refresh / relocate / detach work that had
    already succeeded, which the next state-based reconcile then re-derives and
    re-issues. `tree_update` already had *per-op* tolerance for exactly this
    reason; it just did not cover the case where nothing parses at all, because
    per-op dropping only starts after the response has parsed. Now an
    unparseable reply degrades to "no ops this pass": the safe ops stand, the
    added chunks stay unbound, and the next pass asks again. A crash loses both.

### Dev-set signal after the fixes (NOT reportable — dev corpora, tuned on)

All four dev projects, 545 commits, replayed end to end from the post-bootstrap
baseline. flask additionally ran the attended arm (118 more).

| | flask | httpx | rich | altair |
|---|---|---|---|---|
| commits replayed | 118 | 116 | 153 | 158 |
| symbol moves with a binding | 98 | 19 | 0 | 1365 |
| **moves lost** (bound nowhere after) | **0** | **0** | — | **2** |
| moves keeping the same feature | 92.9% | 100% | — | 97.1% |
| deletions detached correctly | 100% | 100% | 100% | 100% |
| untouched-file invariant | 100% | 100% | 100% | 99.9997% (2) |
| unresolvable bindings at end | **0** of 1078 | **0** of 1258 | **0** of 2263 | **0** of 4048 |
| commits needing no model call | 69.5% | 68.1% | 60.8% | 56.1% |
| harness errors | 0 | 0 | 0 | 1 (fixed by #14) |

Across all four: **1482 bound symbol moves, 2 lost (99.87% survived); zero
unresolvable bindings anywhere; deletions exact everywhere; 2 untouched-file
violations in ~8600 bindings.** Roughly six commits in ten never reach the
model, falling with churn (70% on flask, 56% on altair).

flask's two arms agree on every attribution number and differ only where they
should: unattended ends at coverage 0.981 with 7 proposals pending; attended
ends at **1.000** with 0 pending. Unattended coverage tracks churn (altair 0.869
with 41 pending), which is the backlog, not rot — the unresolvable count is 0 in
both.

The shape to confirm or refute on the held-out set: **no binding is lost**,
deletions and the untouched invariant are near-exact, no address goes dead, and
the residual is re-attribution rather than rot.

### Remaining Phase 1 work before the freeze

1. **Still unexplained, and small enough to be worth explaining**: altair's 2
   lost moves and 2 untouched-file violations. Both are in the traces, so
   diagnosing them costs no replay. A residual we can name is far better in the
   paragraph than a residual we cannot.
2. Decide whether the paper claims *no binding lost* or the stricter *same
   feature*, and write it down before seeing the held-out numbers.
   Recommendation: claim the first, report the second honestly. The first is the
   rot claim; the second is how the tree organizes itself, which legitimately
   changes when the code is reorganized.
3. **Add a high-churn repository to the REPORTING set.** This is the sharpest
   lesson of Phase 1. flask, httpx and rich all reported zero unresolvable
   bindings while a defect had been present the whole time; only altair, chosen
   for churn, exposed it. Five moderate held-out repositories could report a
   clean sweep and be wrong. pydantic is the closest match already in the set —
   screen it before the freeze and swap if its window is quiet.
4. Only then: freeze, and run REPORTING with `--i-am-freezing`.

## 7. The target: draft the two paragraphs now

Writing them with blanks keeps the metric list honest. Anything that does not
fill a blank is out of scope.

> **Setup.** We replayed the commit history of ___ open-source repositories
> (___ Python, ___ TypeScript; ___ to ___ KLOC), building the feature tree at an
> initial commit and then applying each subsequent commit in sequence, ___
> commits in total. After each commit we recorded whether every binding still
> resolved to existing code, and whether attribution followed the change
> correctly, using rename and move detection from git's own similarity analysis
> as ground truth independent of the system's internal signals. Five further
> repositories were used to develop and debug the harness and are excluded from
> all reported figures; the system was frozen before the reported run.

> **Results.** ___% of bindings still resolved after ___ commits, against ___%
> for a static export of the same document. Attribution followed correctly in
> ___% of in-place edits, ___% of moves, ___% of renames and ___% of deletions,
> in each case without invoking the model; ___% of commits were resolved with no
> model call at all, at a median of ___ seconds and ___ tokens per commit. The
> remaining failures were concentrated in ___, where ___. Two improvements
> follow directly: ___ and ___.

The second paragraph ends on improvements rather than on a defence, which is the
right tone for a robustness section. Reporting a known limitation and the fix it
implies reads as confidence. Claiming everything worked does not.
