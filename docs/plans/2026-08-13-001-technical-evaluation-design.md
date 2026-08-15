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

## 6.4 Drift experiment: first result (flask)

Built and run. `evals/localize/` holds it: `setup` restores the document to the
starting commit and replays forward to 60 commits short of the tip, leaving a
frozen copy and a maintained copy of the same prose plus a block of history
neither has seen; `tasks` derives requests from those held-out commits with
paths and symbol names stripped; `eval` runs three arms through an instrumented
four-tool search loop at temperature 0.

Two errors in my own setup had to be found first, and both would have produced a
confident null:

* **The replay never re-rendered the export.** `run_loop_a` updates the store;
  in production the daemon is `tree.codoc`'s sole writer, and the harness has no
  daemon. The "maintained" document came out byte-identical to the frozen one.
* **The export is the wrong artifact anyway.** `tree.codoc` is written for a
  person and carries almost no inline citations — one, in flask's case — because
  the addresses reach an agent through the MCP reads. Handing it over compared
  prose against prose. The aid is now built from the store, mirroring
  `codoc_tree(include_bindings=True)`.

With that corrected the manipulation has real strength: the frozen document
carries 36 addresses pointing at files that no longer exist, the maintained one
carries 0.

| arm | median steps | F1 | recall | found any | exact | tokens |
|---|---|---|---|---|---|---|
| no document | 4.0 | 0.576 | 0.775 | 0.867 | 0.20 | 5.2K |
| frozen at start | 2.0 | 0.544 | 0.725 | 0.867 | 0.167 | 23.2K |
| maintained | 2.0 | 0.548 | 0.767 | 0.867 | 0.20 | 35.0K |

**Read honestly: a document halves the number of looks and improves nothing
about what is found, at four to seven times the tokens.** The steps result
replicates RPG and Intent.lisp. The accuracy result replicates the AGENTS.md
null (2602.11988), including its cost finding, only larger. Maintained beats
frozen on recall by 0.042, but paired per task it is 7 better, 5 worse, 18 tied
— a coin flip at n=30.

**Why flask cannot answer this.** Its frozen document is barely stale: 36 dead
file references out of 456 addresses, because flask does not move code much. For
the maintained copy to beat the frozen one, the frozen one has to have rotted,
and here it mostly has not. The experiment is now correctly built and was run on
the wrong repository.

### 6.4.1 altair — the effect exists, and only where code actually moves

Same experiment on the highest-churn corpus. 25 tasks, 3 arms, 3 repeats, 225
runs, 0 errors after a retry was added (a burst of connection failures had cost
46 of the first 123, evenly across arms).

| arm | median steps | F1 | recall | found any | tokens |
|---|---|---|---|---|---|
| no document | 4 | 0.247 | 0.293 | 0.440 | 8.8K |
| frozen at start | 3 | 0.257 | 0.328 | 0.453 | 233K |
| **maintained** | **2** | **0.292** | **0.400** | **0.533** | 174K |

Paired per task, on recall: maintained beats frozen 10–3 (62 tied, mean +0.072,
sign test p=0.046) and beats no document 16–3 (56 tied, mean +0.107, p=0.002).
F1 is much noisier and does not separate (14–15 against frozen).

**The maintained document also costs 25% FEWER tokens than the frozen one**
(174K vs 233K) while doing better. A stale document does not merely fail to
help — it sends the agent to files that no longer exist, and it pays for the
detour.

The contrast with flask is the finding. flask showed nothing on any measure,
and flask barely moves code: 36 dead addresses in its frozen copy against
altair's 137, in a document a quarter the size. **The benefit of maintenance
appears only in repositories where code actually moves, which are exactly the
repositories where documentation rots.** A single-corpus evaluation on a stable
project would have concluded there is no effect.

Honest limits: one corpus, 25 tasks, and recall is the only metric that
separates. The steps result (halved) replicates prior work and is present for
the frozen arm too, so it is about *having* a document. Recall is the one that
distinguishes *maintaining* it.

**Superseded decision.** One more attempt on the highest-drift corpus available —
altair (2112 symbol moves) or pydantic (231 file renames) — with more tasks, and
if that is null too, report the null and stop. The case for one more run is that
the manipulation there is an order of magnitude stronger; the case against is
that the steps effect is already identical for frozen and maintained, which says
the benefit is *having* a document rather than *maintaining* one, and more drift
may not change that. Either outcome is publishable and neither threatens the
paper, since this was always the optional upside and the robustness section
stands on its own.

## 6.45 A repository-wide rename defeats attribution entirely (hypothesis)

Found while setting up the second drift corpus, after the freeze, on a project
in neither the development nor the reporting set. **It is the most important
result in this document and it is a failure.**

hypothesis commit `99452c93` renames the `hypothesis-python/` directory to
`hypothesis/`: 440 files, 6556 definitions moving, contents byte-identical.
Every one should relocate by the deterministic path — identical `tokens_hash`,
pass 1, no model needed. What Loop A actually did:

| | |
|---|---|
| relocations | **0** |
| auto ops | `detach: 73, attach: 2` |
| bindings lost | **6275 of 7209** |
| unresolvable after | **6875** |
| coverage | 1.00 → **0.039** |
| model calls | 2 (52K tokens), 290s |

The document did not degrade, it collapsed. Afterwards 95% of its addresses
point at paths that no longer exist, and the "maintained" copy is no better than
the frozen one — which is why the hypothesis drift experiment cannot run: there
is no maintained arm left.

**Why nothing caught it earlier.** Every corpus so far renamed files in tens.
altair's largest was 24 renames in the v5→v6 bump, and that commit relocated
1341 chunks correctly. 440 renames does not. There is a scale threshold between
them, and every project in the dev and reporting sets sits below it.

**Root-cause progress (not finished).** The changeset reaching Loop A was nearly
empty — 73 removals where ~7000 were due — so relocation never had candidates to
match. Ruled out and confirmed so far, via a synthetic reproduction that renames
a directory of N identical-content files
(`scratchpad/repro_rename.py`, one N per process):

* **Scoped index reads are fine.** Reading 800 scoped files returns every
  expected chunk, so the `files=` push-down is not truncating.
* **`compute_changeset` is correct at N = 5, 50 and 200.** Each returns
  `added == removed == all chunks`, every one fingerprint-matched between the
  two sides, and the index ends with zero old-path rows. A relocation pass
  handed this would re-attach everything. Each run finishes in seconds.
* **At N = 440 it does not complete.** The index grew to 118 MB (for ~1300
  chunks) and the process sat at 0% CPU for over 20 minutes without printing
  its first line. For comparison, the whole real hypothesis workspace is 70 MB.
  So the failure is in the indexing layer at scale, not in the diff logic
  above it, and the production symptom fits: that pass took 290 seconds and
  emerged with a changeset describing only the 11 ordinary modifications.

**The pathology, isolated.** Holding the file count fixed at 50 and varying only
the number of definitions per file — so only the CHUNK volume changes:

| chunks moved | rename pass | index size |
|---|---|---|
| 300 | 18.2s | 4 MB |
| 2000 | **392.2s** | 24 MB |

6.7× the chunks costs **21.5× the time**: a scaling exponent of ~1.6, badly
superlinear. Extrapolated to production's ~7000 chunks that is **roughly 50
minutes** for a single rename commit. The first repro's stall at 440 files was
this same curve, not a separate problem.

Note what this does NOT explain. In both runs above the changeset was
*correct* — `added == removed == every chunk`, all fingerprint-matched, no stale
rows left. Slow but right. Production was **wrong**: 73 removals where ~7000
were due, in a pass that finished in 290 seconds when the curve predicts ~50
minutes. A pass that returns early AND incomplete is a different failure from
one that is merely slow, and the two are probably related — something appears to
be giving up partway — but that link is not yet demonstrated.

**Fix applied to (1), and it is partial — say so.** Timing the stages of a
2000-chunk rename pass: reads 0.1s, LanceDB optimize 1.7s, **cocoindex's own
`update_blocking` 520.9s of the 523s total**. The time is inside the third-party
indexer and codoc does not control it.

What codoc did control was the debris. `optimize` was reclaiming nothing,
because its 30-minute retention window is measured in wall clock while passes
arrive seconds apart, so every version it would drop was too young. Retention is
now 60 seconds (`CODOC_INDEX_RETENTION_S`), which is still four orders of
magnitude more than the millisecond read it exists to protect. Measured on
consecutive rename passes of 1000 chunks:

| retention | pass 1 | pass 2 | pass 3 | pass 4 |
|---|---|---|---|---|
| 30 minutes (before) | 9.4 MB | — | — | — |
| 60 seconds (after) | **3.8 MB** | 5.7 MB | 6.4 MB | 7.9 MB |

Read this carefully, because it is less of a fix than it first looks. Per-pass
debris drops about 2.5× (9.4 → 3.8 MB after one pass), but the table **still
grows across passes** — 1.6 MB at rest, 7.9 MB after four — so reclamation is
partial, not complete. The honest statement is that the index accumulates more
slowly, not that it stops.

What did NOT compound is time: 162s, 137s, 174s, 142s across the four passes,
flat within noise. So the superlinearity measured earlier is in the size of a
single change, not in debris left by earlier ones — which also means the
retention change was never going to fix the time, and it does not.

**The per-pass time is unresolved.** It is cocoindex's `update_blocking`, and
addressing it means batching the work before handing it over, or taking it up
with that library.

So there are two defects here, and only one is characterized:

1. **Re-indexing is superlinear in chunks moved** (measured, reproducible,
   `scratchpad/repro2.py`). On its own this makes a large refactor take an hour.
2. **A large rename yields an incomplete changeset** (observed once, in
   production, not yet reproduced synthetically). This is the one that destroys
   attribution, and it is the one still open.

Next: reproduce (2) directly by replaying hypothesis commits 100→101 against the
step-100 store, with the changeset dumped, rather than inferring it from the
synthetic curve. The step-100 state is reachable — `.codoc.baseline` plus the
recorded step list — but the replay to get there costs an hour, so it is worth
doing once and keeping the result.

**What it means for the paper.** Phase 2's numbers stand: they measure what they
measured, on the projects they were run on, and none of those projects contains
a change of this shape. But the claim has to be qualified honestly, because a
directory rename is not exotic — it is one of the most common large refactors
there is, and it is exactly when a reader most needs the document to still work.
The two-paragraph section should say that attribution survives ordinary change
and that a repository-wide restructure currently defeats it, with this number.
Reporting the good result and omitting this one would be indefensible.

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

15. **A repository can shadow codoc's own dependencies and stop it starting.**
    Bootstrapping pydantic and starlette failed outright. `codoc init` runs with
    the working directory set to the repository, `python -m` puts that directory
    on the import path, and a checkout of pydantic then shadows the pydantic
    codoc itself imports — dying on a version check before any codoc code ran.
    The harness now runs the CLI from codoc's own root and points at the target
    with `--root`.

    Worth carrying into the product, not just the harness: a real user runs
    `codoc init` from inside their repository, which is exactly the shadowing
    condition. Any project sharing a top-level package name with one of codoc's
    dependencies is affected. Not fixed here — it is a packaging/invocation
    question, it arrived after the freeze, and changing how the CLI resolves
    imports is not a change to make between Phase 1 and Phase 2.

16. **The churn screen over-promised, and the held-out set is thin on
    relocation.** pydantic was chosen for the reporting set on the strength of
    231 file renames — the high-churn case that the dev phase proved was
    necessary. In the frozen run it detected 155 symbol moves and only **5** of
    them were bound. 149 came from one commit renaming
    `tests/mypy/outputs/1.10.1/…` to `tests/mypy/outputs/…`, a versioned
    directory of mypy plugin fixtures, and **zero of those files are in the
    index at all** — codoc excludes them, so they can never carry a binding.

    Two things to correct, neither of them in codoc:

    * The screen counts git renames without asking whether the moved files are
      indexed. It should count movement only in files codoc actually binds,
      which is the population the relocation claim is about.
    * `symbol_facts` filters `facts.touched` by extension and subdirectory but
      not by codoc's own exclusions, so `symbol_moves_seen` overstates what was
      testable. This did NOT corrupt any rate: every rate is computed from
      `move_bound_n`, which was always the honest denominator, and both numbers
      are reported side by side.

    **This is not fixed by re-picking the corpus.** Choosing a different
    held-out project after seeing the numbers is precisely the tuning the freeze
    exists to prevent. The reporting run stands as it is; the relocation class
    is reported at whatever strength the held-out set gives it, the development
    set's 1482 bound relocations are reported separately and labelled as such,
    and the screening defect is stated as a limitation. The lesson for a next
    round is to screen on bound movement, not on git renames.

### FREEZE — 2026-08-14

Phase 1 is closed. altair, the hardest development project, verifies clean on
every axis that was failing: harness errors 1 → 0, untouched-file violations
13 → 2 → 0, unresolvable bindings 28 → 0, and unrequested re-attribution halved
(38 → 19) as a side effect of the change-set guard. Deletions stay exact and no
address is dead anywhere.

Six defects fixed, five in codoc and one in the harness's own understanding of
what it was scoring. The residual we keep and can name is 2 lost relocations in
1364, both cases where a move also rewrote the code and the shape was not
distinctive, inside a bulk refactor of over a thousand simultaneous changes.

Two improvements follow directly and are deliberately NOT implemented before the
frozen run, because adding capability after seeing the number is how tuning gets
mistaken for measurement:

1. Match a relocation by qualified name when content and shape both fail. The
   evaluation's own ground truth does exactly this and found all 1482 moves.
2. Nothing further on re-attribution; the guard shipped and the remaining 19 are
   the tree reorganizing with the code, which is intended.

The five held-out projects were screened before the freeze and need no swap:
pydantic carries 231 file renames, so the high-churn case that exposed the worst
defect is represented. typer is thin (34 commits touch code) and is kept as the
small case rather than replaced.

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

## 6.9 PHASE 2 RESULT — held-out projects, frozen system (2026-08-14)

Five projects, 615 commits, 26,250 bound regions, **0 harness errors**.

| | click | typer | pydantic | starlette | pyright-ts | total |
|---|---|---|---|---|---|---|
| commits | 136 | 34 | 172 | 130 | 143 | 615 |
| bound regions at end | 1398 | 2922 | 2423 | 1475 | 18032 | 26250 |
| relocations owned | 39 | 29 | 5 | 0 | 1 | 74 |
| **lost** | 0 | 1 | 0 | 0 | 0 | **1** |
| deletions released | 1/1 | 8/8 | 11/11 | 0/0 | 105/105 | **125/125** |
| untouched-file checks | 189K | 74K | 342K | 191K | 2497K | 3293K |
| **violations** | 0 | 0 | 0 | 0 | 0 | **0** |
| **pointing at absent code** | 0 | 0 | 0 | 0 | 0 | **0** |
| commits with no model call | 35% | 53% | 78% | 55% | 34% | 34–78% |

22.8M tokens, 65 minutes. `pyright-ts` is TypeScript, so the deletion and
untouched-file results are not Python-only.

**What is strong:** nothing became unreachable anywhere; every deletion
released; zero violations in 3.3M checks.

**What is weak, and must be said in the paper:** the relocation arm rests on 74
cases. The held-out set moves code far less than the development set, where the
same measurement covered 1482 relocations and lost 2. Reported as such, with the
development figure given separately and labelled.

**Cost is a range, not a mean.** 34% to 78% is too wide to average honestly.

### Incident: an empty document scores perfectly

starlette's first bootstrap failed on the dependency-shadowing problem (#15) and
left a partial `.codoc`. `prepare` saw the directory, printed "already
bootstrapped", skipped the bootstrap, and snapshotted a baseline with **0
features**. The frozen run then replayed 130 commits against an empty document
and returned a flawless score on every axis, because there was nothing in it to
break.

Caught only by noticing 23 bindings where a framework should have thousands.
Two guards added: a failed bootstrap now clears its partial workspace, and
`prepare` refuses to snapshot a baseline with zero features whatever the exit
code said. starlette was re-prepared (109 features, 1370 bindings) and re-run;
that is the row above.

Re-preparing a held-out project after a *setup* failure is not corpus shopping —
there was no result to be influenced by, only an artifact that was never built.
Re-picking a project because its numbers were disappointing would be, and is not
what happened. Stated in the paper's limitations either way, because "an absent
test and a passing test look identical from their results" is the reason the
whole protocol exists.

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
