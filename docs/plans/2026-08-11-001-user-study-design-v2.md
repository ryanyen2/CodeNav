# CoDoc User Study — Design v2

*Supersedes the v1 draft. Everything marked **[decide]** needs a call before piloting;
**[verify]** means check the source before it goes in the paper. Changes from the v1
draft are argued inline; §0 is the summary of what moved and why.*

---

## 0. What changed from the v1 draft, and why

| # | Change | Why |
|---|---|---|
| 1 | Named the *unit of comparison* explicitly (§2.1): protocol vs. convention, not tool vs. no-tool | The baseline bundles away the "you compared against nothing" attack, but a new attack replaces it: "which ingredient mattered?" We can't ablate at n=12, so we must define the compared object honestly and trace mechanism at the log level instead |
| 2 | One binary trap → **three graded hazards per task**, composite 0–8 spec-integrity score (§5.3) | A single binary at n=12 is a coin-flip per participant carrying the whole paper; exact McNemar on 12 pairs detects only enormous effects. Three hazards on different channels (doc-only / judgment / test-visible) give a graded score AND tell us *which channel* the tool helps |
| 3 | The two naive implementations now break **different requirements** (the early/late filter tension, §5.2) | "Not too easy for a one-shot agent" must come from real design tension, not obscurity. Filter early → dev requirement breaks; filter late → cache invariant breaks. There is no default that satisfies both; the participant must actually decide |
| 4 | Purpose-built codebase reframed from a concession into an **instrument** (§4.2) | The hazard sites must be bespoke or model priors solve the task from Jekyll/Hugo memory. Comprehension research has always used controlled programs; say so, cite it, and publish the repo |
| 5 | **Sonnet-5 calibration protocol** with pass/fail criteria (§6) | "Not one-shot-able" is a claim we must *demonstrate*, pre-data, with the exact model participants will use — and file as supplementary material |
| 6 | Questionnaire rebuilt: balanced/reverse-keyed items, system-blind wording, discriminant scenarios where the baseline *should* win (§8.6) | The single biggest rigor challenge we'll face. A questionnaire where every item flatters the tool is unpublishable at CHI regardless of the means |
| 7 | Added **review-coverage + sign-off** measures (§8.2) | The pitch is "prompting produces changes here and there that are hard to verify" — so verification behaviour must be measured, not implied |
| 8 | Warm-up micro-task in *each* condition; 105-minute single session (§7) | Tool-familiarity asymmetry (everyone knows Claude Code, nobody knows CoDoc) is a bias *against* the baseline-novelty confound argument only if we equalize exposure |
| 9 | Pre-registration on OSF, with the RQ1 threshold and hazard rubric frozen (§9) | Cheap to do, and converts three "you could have decided this post hoc" reviews into citations of the prereg |
| 10 | Instrumentation build-list for the extension (§10) | Half the measures need logs the tool doesn't write yet; that's engineering work that must precede pilot 1 |

---

## 1. Thesis

When an agent writes the code, the written account of a codebase has to be kept up by
both sides or it dies. CoDoc makes the document a place both work: the person writes
what a part is for and hands it off, the agent writes back what it changed, and the
system keeps both attached to the code without either side overwriting the other.

**On Naur.** Unchanged from v1 draft: Naur frames the loss and structures the probes
(what → why → how to extend); it is not what the paper proves. Keep it out of the
thesis sentence.

## 2. Research questions

**RQ1 — Co-authorship.** Do both sides actually write in the document, and what does
each contribute? Where does each consequential change originate: written in the
document and handed off, typed into chat, or edited directly in code?

**RQ2 — Faithfulness and its cost.** Does the document stay true to the code as the
agent works, and what does that cost in review effort? How often do people act on the
document without opening the code, and is that trust warranted?

**RQ3 — Understanding and agency.** After working this way, can the person say what
the system does, why it is built as it is, and what would have to change to extend it
— and did the consequential decisions during the task actually pass through them?

### 2.1 What is actually being compared — write this down before a reviewer does

Both conditions get: the same agent (Claude Code, Sonnet 5), the same starting
document content, the same instruction that the document matters, and an agent-side
mandate to maintain it. The manipulated variable is the **protocol**: in CoDoc the
document is bound to code (live bindings), changes to it are verdicts (accept /
reject / hand-off), and the system enforces two-sidedness (holds, proposals,
provenance). In the baseline the document is a **convention**: a markdown file both
parties are told to maintain, with nothing enforcing that they do or reconciling them
when they don't.

This is a bundle-vs-bundle comparison and we cannot attribute effects to individual
mechanisms experimentally at n=12. Two mitigations, stated in the paper: (a) process
tracing — the logs record *which* mechanism fired before each observed effect (a
constraint caught because the hold surfaced it is distinguishable from one caught by
reading); (b) the discussion attributes claims to mechanisms only where the traces
support it. Do not claim more.

**One confound to surface honestly:** the baseline's maintenance skill consumes agent
turns and context, so baseline task time and token cost are inflated by our own
manipulation. Report agent-turn overhead in both conditions and never cite baseline
slowness as a CoDoc win.

## 3. Conditions

Within-subjects, two conditions, order counterbalanced.

### 3.1 CoDoc

VS Code + CoDoc extension + Claude Code in the integrated terminal. Feature document
bootstrapped from the repo, planted material seeded (§4.4). Participant may use any
mix of: editing the document, chatting with the agent, editing code directly.

### 3.2 Baseline — Claude Code with a document that fights back

Unchanged in spirit from the v1 draft (it is the right call), tightened in detail:

1. `CLAUDE.md` = the CoDoc document exported to markdown — same features, same
   descriptions, same planted rationale and constraint, code references as
   `file.py::symbol` paths. Verified byte-comparable at seeding time.
2. A **maintenance skill** (`.claude/skills/doc-maintenance`): after each change,
   update affected sections; record what changed and why; note new code no section
   describes; flag sections the change made stale. Written and piloted before
   anything else. The skill must be *good* — a strawman skill is a strawman baseline.
3. The participant is told the document is theirs to keep current and will be the
   basis of questions later (same sentence, both conditions, so the incentive to read
   and maintain is equalized).

**Known asymmetry to accept:** Claude Code auto-loads `CLAUDE.md`, so the baseline
*agent* will often respect the planted constraint even if the human never reads it.
This is fine — it is realistic, and it moves the expected effect from "the code broke"
to "the human didn't know," which is exactly RQ3's territory. Do not design the
constraint so deep in the doc that the agent misses it; that would be rigging.

**If the baseline holds up** — document stays faithful, constraint respected, human
aware — that is a real and publishable finding about how far convention + a capable
agent gets you, and the paper's contribution shifts to the co-authorship and agency
results. Decide *now* that we will publish that shape too. **[decide — but the honest
answer is yes]**

## 4. Materials

### 4.1 Codebase criteria (unchanged targets, one addition)

Size 2,000–3,500 LOC · 10–16 files · 20–28 bootstrap features · instantly familiar
domain · green test suite · low notoriety — **plus: bespoke internals at every hazard
site** (§4.2). Verify by running bootstrap and skimming the outline in ≤6 min.

### 4.2 Primary codebase — `hearth`, a purpose-built markdown SSG

**Decision: purpose-built, and framed as an instrument, not a concession.** Three
reasons, all of which go in the paper:

1. **Prior-proofing.** Draft posts is *the* canonical SSG feature; Sonnet has strong
   priors from Jekyll/Hugo. A trimmed real SSG keeps canonical internals, so the
   agent's priors are *correct* and the task collapses to one prompt. The hazard
   sites must be places where priors mislead — which means bespoke designs.
2. **Hazard control.** The planted constraint must be real (violating it must
   actually break something testable), findable-but-not-obvious, and stable across
   participants. That is only constructible.
3. **Precedent.** Program-comprehension studies have always used controlled
   materials (Pennington's programs, Soloway's plans, LaToza's artificial tasks).
   Publish the repo + tasks + rubric as supplementary material.

**The build (exists at `~/repos/test-workspace/hearth`):** ~2,500 LOC stdlib-only
Python, 13 modules: cli → config → discovery → frontmatter → markdown → templates →
pages/collection → indexes (paginated) → feeds/sitemap → assets → **incremental
cache** → build orchestration → dev server. Ten sample posts, real templates, green
pytest suite, plausible solo-dev git history (feeds the why-evidence channel).

**The load-bearing bespoke design** (the H1 site): incremental builds rebuild a page
when its content hash changes, and rebuild *aggregates* (home/tag/archive pages,
feed, sitemap) only when the **aggregate signature** — a hash over the collection's
(url, title, date, tags, summary) tuples — changes. Any rule that changes which posts
an aggregate shows must therefore land where the collection is formed (or feed the
signature); a filter applied downstream in a renderer is invisible to it and the
aggregates go silently stale. Nothing in the code states this globally; the planted
document does.

### 4.3 Second codebase — `ember`, a feed reader / digest generator

Matched shape: fetch → parse (RSS/Atom) → normalize → dedupe → store (sqlite) →
digest renderer (daily HTML digest) → notifier → CLI. Same size targets, same
bespoke-hazard principle. The H1-equivalent: the digest is built incrementally,
gated on a **digest signature** over the items selected for inclusion; the H2
scope call: whether "muted" feeds still appear in search/archive; the H3 tension:
mute at fetch time (breaks archive requirement) vs at render time (breaks the
signature). Build only after `hearth` pilots clean — every lesson transfers.
**[decide: build ember vs. adapt an existing candidate — recommend build, same
reasons as §4.2]**

### 4.4 Planted material (seeded into the document post-bootstrap, pre-session)

**(a) Recorded design decisions ×4** — feature descriptions carrying a real rationale
and a rejected alternative, genuinely unrecoverable from code:
  1. Why aggregates are gated on a signature rather than rebuilt every time
     (rejected: mtime-based dependency graph — "we tried tracking per-output deps;
     the graph was always subtly wrong after deletes").
  2. Why config is resolved once at startup and passed down (rejected: ambient
     global — testability).
  3. Why the markdown renderer is bespoke (rejected: dependency — the one-file
     deploy story).
  4. Why the dev server serves from the build output rather than rendering on
     request (rejected: per-request rendering — so dev and prod can never disagree).
  These are the *inherited-rationale* probe targets.

**(b) The planted constraint (H1)**, recorded on the incremental-cache feature:

> Anything that affects which pages an aggregate shows must be visible where the
> collection is formed — the aggregate signature is computed from the collection,
> so a filter applied later in a renderer will not trigger an aggregate rebuild,
> and index/tag/feed pages go stale on the next incremental build.

**(c) A gap** — ~~one small live region de-scoped from any feature before the
session~~ **CANNOT BE SEEDED (verified 2026-08-12).** Detaching bindings to create
unowned code does not survive: the next Loop A reflect re-attaches them, which is
codoc working correctly (unowned chunks get claimed). No seeding needed anyway —
the task itself produces the gap, because the draft predicate / mode parameter is
new code no existing feature owns. Setup instruction: run `codoc reflect` AFTER
seeding and before the session, so the tree is genuinely in sync at t=0.

**Seeding parity check:** export the seeded CoDoc document to markdown, diff against
the baseline `CLAUDE.md`, require content-identical prose.

## 5. Tasks

### 5.1 What the task must force (unchanged table from v1 draft)

Multi-step change · spans several features · an over-reach worth rejecting · a
requirement stated before code exists · new unowned code · at least one part of the
diff worth pushing back on. Pilot for "cannot be finished by accepting the first
diff."

### 5.2 Task A — Draft posts (`hearth`)

**The card (verbatim; shown as an image, never as copyable text):**

> Add draft support to hearth. A post marked as a draft must not appear anywhere in
> a production build. When someone runs the dev server, drafts must appear so they
> can be previewed. Decide anything the card doesn't specify, and be ready to
> explain your decisions.

**The card must NOT state the incremental-correctness requirement.** Calibration
run 1 (2026-08-11) proved why: a card that says "publishing a draft must be fully
reflected on the very next build" hands the agent H1 in the prompt — Sonnet 5
one-shotted a full-marks implementation from it (collection-level `.visible(mode)`,
signature fed the filtered collection, outputs cleaned via the deleted-file path).
Incremental correctness is an *implicit* requirement of the codebase, recorded in
the document — finding it is the study's point. The full-spec card is retained as
the **C3 oracle prompt** (it doubles as proof the task is achievable in-session:
~6 min of agent time, 243 tests green, all hazards passed).

**Why this stays the task:** universally understood, zero domain onboarding — and on
this codebase it is genuinely hard to get *right*, because the two obvious
implementations each break a stated requirement:

- **Filter early** (discovery/collection, mode-blind): the signature sees the change
  (cache correct) but the dev server loses drafts → violates the preview requirement.
- **Filter late** (in the index/feed renderers): dev preview trivially works, but the
  signature never sees draft flips → stale aggregates → violates the correctness
  requirement, invisibly (the page itself rebuilds; only aggregates go stale).
- **Correct:** selection becomes an input to collection assembly (a build mode),
  which both feeds the signature and gives the dev server its variant. Several valid
  shapes (mode flag through `build()`, config key, env) — that's the open decision
  space, not a single golden path.

**Open decisions to log (who settled it: participant-before, agent-then-accepted,
agent-unnoticed):**
1. Does draft mean unrendered, or rendered-but-unlinked?
2. Do drafts stay out of the RSS feed and sitemap? (the over-reach bait — the agent
   will likely handle these unprompted; deciding either way deliberately scores)
3. How does dev mode differ — flag, env var, or config key? Where does the mode
   enter the pipeline?
4. What marks a draft — frontmatter key only, or also a `_drafts/` convention?

**The three hazards (composite spec-integrity score, §8.4):**

| Hazard | Channel | Detects | Fires when |
|---|---|---|---|
| **H1** — stale aggregates | Document-only (planted §4.4b) | Whether recorded constraints reach the person/agent and survive into code | Filter applied downstream of the collection; caught only by the constraint test or by reading the planted invariant |
| **H2** — feed/sitemap scope | Judgment call | Whether decisions are *made* vs. defaulted | Nobody ever decides; credit is for a deliberate call either way, logged as such |
| **H3** — dev preview | Test-visible | Ordinary requirement discipline | Mode-blind early filtering; caught by running the dev server or the acceptance test |

H1 is the headline (it is what a maintained account buys you). H3 is the control
hazard — both conditions should mostly catch it, and if they don't the task was too
hard. H2 measures agency, not correctness.

### 5.3 Task B — Muted feeds (`ember`), matched

> Add mute support to ember. Items from a muted feed must not appear in the daily
> digest or trigger notifications, but must still be fetched, stored, and visible in
> search and the archive. Muting and unmuting must be fully reflected in the next
> digest.

Same three-hazard structure: H1 = digest signature (planted), H2 = whether muted
items count toward the unread badge / statistics (judgment), H3 = archive/search
retention (test-visible). Finalize when ember exists. **[decide]**

## 6. Task calibration with Sonnet 5 — protocol, not vibes

Run before any pilot, re-run after any task or codebase change, file as
supplementary material.

**C1 — Unassisted one-shot (×5 runs).** Fresh clone, baseline setup *without* the
planted document (bare repo, no CLAUDE.md), the card text as the only prompt,
Sonnet 5, no human interaction, agent may run tests. Score each run on the full
rubric (§8.4). **Calibrated iff ≤1 of 5 runs achieves a perfect Layer-3 score, and
every failure is a specification failure (H1/H2/undecided scope), not a competence
failure (crashes, can't find the code).** If runs fail on competence, the codebase is
too weird — fix the code, not the task.

**C2 — Documented one-shot (×5 runs).** Same, but with the seeded `CLAUDE.md`
present. Expected: H1 failures drop (the agent reads the constraint), H2 stays mixed.
This measures how much of the effect the *document content alone* buys — the
difference between C1 and C2 is the "a prompt could close the gap" number, measured
before a reviewer asks for it.

**C3 — Oracle spec (×2 runs).** A fully-specified prompt (every decision made, the
invariant quoted). Must succeed and complete within the task time budget — proves
the task is achievable in-session and the failures above are informational, not
capability, limits. *(Run 1 done 2026-08-11: PASSED — see §5.2.)*

**C1′ — Paraphrase probe (×3 runs).** The realistic under-specified prompt a
participant would actually type ("add draft posts; hidden in prod, visible in dev").
This, not the verbatim card, is the ceiling that matters: participants author their
own instructions, and v1 showed instruction quality varies. If C1′ splits on H1
while C3 passes, the study's phenomenon — outcome tracks specification quality —
is demonstrated before a single participant is run.

**C4 — Prior probe (×1).** Ask Sonnet 5, without the repo: "how would you implement
draft posts in a static site generator?" Archive the answer. It documents the
canonical prior (Jekyll/Hugo conventions) and lets the paper show precisely where
the prior misleads on this codebase.

### 6.1 Calibration results — COMPLETE SUITE (2026-08-11/12, 9 runs) — and what they change

| Run | Setup | Model | H1 index | Feed | Output cleanup | Tests |
|---|---|---|---|---|---|---|
| C3 oracle (run 1) | bare + full card | Sonnet 5 | PASS | excluded | PASS | 243 ✓ |
| C1′ runs 2–5 (×4) | bare + minimal card | Sonnet 5 | PASS ×4 | excluded ×4 | PASS ×4 | 242–243 ✓ |
| C2 runs 1–2 (×2) | CLAUDE.md + skill + minimal card | Sonnet 5 | PASS ×2 | excluded ×2 | PASS ×2 | 243–244 ✓ |
| Weaker tier (×1) | bare + minimal card | Haiku 4.5 | PASS | excluded | **FAIL — drafted page still served in prod** | 239 ✓ |
| C4 prior probe | no repo | Sonnet 5 | — | — | — | — |

**Findings, consolidated:**
1. **Sonnet 5 is 7-for-7 on every hazard from any prompt.** The correct placement
   (collection assembly) is also the model's *prior*: the C4 probe — no repo in
   sight — already recommends "filter at the content-loading/collection stage,
   not at render time." The render-level trap is not where this model class goes.
2. **The prior is silent on everything repo-specific.** C4's 75 lines contain
   zero mentions of incremental builds, caches, signatures, staleness, or output
   cleanup. Sonnet compensates by reading the code; **Haiku does not fully** —
   it left the drafted post's own page served in the production output (the
   information-leak rung). Model capability, not prompt quality, determined
   whether the repo-specific consequences got handled.
3. **The baseline maintenance skill WORKS at the agent tier.** Both C2 runs kept
   CLAUDE.md faithful — updated sections, recorded the dev-server exception with
   cross-references, and one run *quoted the planted invariant back* when
   justifying its placement. §3.2's "if the baseline holds up" branch is the
   branch we are on; the paper's shape is decided now, not in the results.
   (Qualitative note: skill-maintained `Code:` reference lists balloon into
   unreadable symbol blobs — keeping addresses in prose is exactly the job live
   bindings exist to do.)
4. **Run-to-run variance is entirely H2-class**: one run added draft badges and
   a CI flag and argued a URL-collision policy; others did none of that and
   never surfaced the choice. What varies is which decisions get *made and
   said*, not whether the code works.

**What the two runs DID vary on is scope decisions**: badges vs none, a
`--drafts` CI flag vs none, deliberate collision policy vs silence, feed handling
argued vs inherited. Run-to-run variance lives entirely in the H2 class.

**Design consequences (adopted):**
1. The study's discriminating layer is the HUMAN one, by evidence and not by
   hope: awareness probes, who-settled-each-decision, review coverage, decision
   survival in the document. The completion layers stay (they anchor "the work
   got done in both conditions") but are expected near-ceiling; say so in the
   prereg and the paper.
2. Raise the task's **decision density** rather than chase an agent-breaking
   hazard: the card stays silent on feed/sitemap, badges, mode interface, and
   draft marking, and the rubric credits *deliberate, recorded* resolutions.
   The realistic failure mode this study measures is not "the agent wrote a bug"
   — it is "nobody can say what was decided, why, or whether it was checked."
3. H1 keeps its role in the *probe* layer (can the participant explain the
   cache interaction their build now has?) and as the baseline-vs-CoDoc
   awareness comparison — the constraint is in both conditions' documents; the
   question is whether it reaches the person.
4. ~~Remaining calibration~~ **DONE — full suite complete (see table above).**
   Calibration artifacts (agent summaries, scored results log, C4 probe text)
   archived from the pilot scratchpad; copy into supplementary material at
   submission time. Protocol note for the paper: C4 must run with an empty
   working directory — a first attempt ran inside a completed workspace and the
   model described the existing implementation instead of its prior.

## 7. Procedure

Two stages per condition: comprehend → probe → modify → probe.

| Phase | Min | Notes |
|---|---:|---|
| Pre-session questionnaire | — | Async: demographics, experience, agentic-tool habits, diff-reading screen |
| Briefing | 5 | Neutral: "two workflows for working with a coding agent." Never "our tool" |
| **Per condition (×2):** | | |
| Guided walkthrough + warm-up micro-task | 6 | Scripted identically for both; warm-up = "find where X is described, make the agent rename it" — touches doc, agent, accept in CoDoc / doc, agent in baseline |
| Stage 1 — comprehend | 6 | Free exploration, think-aloud |
| Probe 1 (closed-book → open-book) | 6 | §8.3 |
| Stage 2 — modification task | 17 | Think-aloud; hard stop at 20 with "wrap up" call at 15 |
| Sign-off + Probe 2 | 6 | Sign-off statement (§8.2), then probes on the changed region + transfer |
| Questionnaires | 4 | UMUX-LITE, RTLX, custom block |
| **Between conditions: break** | 3 | |
| Scenario preferences + semi-structured interview | 14 | §8.6, both conditions on the table |
| **Total** | **≈105** | |

**Session format: one 105-minute session**, compensated accordingly. Two-session
splits cost dropout and re-warm-up time and break the same-sitting comparison that
within-subjects buys. Keep NASA-TLX as RTLX (raw, unweighted) to save minutes.
**[decide — recommend single session]**

**Counterbalancing.** System order × codebase assignment fully crossed → 4 cells.
n=12 gives 3/cell; **n=16 (4/cell) is meaningfully better for the price of four
sessions** — decide by recruiting reality. **[decide]**

**Participants.** Experienced developers who use agentic coding tools weekly+.
Screen: "when an agent proposes a multi-file change, how often do you read the diff
before accepting?" — exclude "never." Brief: "you are responsible for the result
being *correct*, not merely green; you will be asked to explain the code afterward."

## 8. Measures

### 8.1 RQ1 — Co-authorship (unchanged from v1 draft, plus one)

Origin-of-change distribution · content coding of document edits (6 categories) ·
**who-settled-each-open-decision** (the sharpest agency measure — keep) · agent-side
write-backs classified. Addition: **decision survival** — of the decisions settled in
chat, how many exist in *any* durable artifact at session end (doc, code comment,
commit message)? Chat-settled-and-vanished is the phenomenon the thesis predicts;
count it directly.

### 8.2 RQ2 — Faithfulness and review cost

Document–code alignment (2/1/0 per feature, blind rater, before/after) · drift counts
(orphaned features, unowned code, mis-bound chunks) · proposal flow + Loop-A
attribution accuracy · review-interaction time and accept/reject counts.

**New — review coverage.** From screen recording + logs: the fraction of changed
hunks the participant visibly inspected (opened + dwelled ≥2s) before sign-off.
This is the "changes here and there, hard to verify" pitch, measured.

**New — the sign-off.** Before Probe 2: *"Is this change correct and complete? State
your confidence 1–5 and what it rests on."* Coded for grounds: ran tests / read the
diff / read the document / the agent said so. Confidence–correctness gap per
condition is a finding either way.

**Warranted trust** (kept): acted-on-document-without-opening-code events × whether
the claim was true.

### 8.3 RQ3 — Understanding and agency

Instrument as in the v1 draft (Sillito question types; Pennington/Schulte levels;
LaToza reachability for extension items; closed-book → open-book with the delta as
the number; SAGAT-style freeze framing **[verify]**). Constraints retained: no
repeated questions between probes except a 2-item anchor set; Probe 2 targets the
changed region + one transfer item; rationale items split by provenance (inherited
vs. made-during-task).

**Scoring, frozen before data:** function/structure/extension items 0–2 against a
written key; rationale items 0–2 (0 = none/wrong, 1 = what without why, 2 = why with
the constraint or rejected alternative); **defense items 0–2** (0 = no position,
1 = position without grounding, 2 = position grounded in a tradeoff — *agreeing* with
the recorded decision scores 2 if grounded; disagreement is not required, judgment
is). Interviewer reads questions from a script verbatim; probing limited to "can you
say more?" once.

### 8.4 Task completion — three layers (kept), with the composite

Layer 1 execution (pre-written acceptance tests + repo suite + **the H1 constraint
test**: build → flip draft → incremental build → assert aggregates updated; and the
H3 test: dev build shows the draft, prod hides it). Layer 2 localization 0–3.
Layer 3 requirement rubric, reweighted to the hazards:

| Requirement | Weight |
|---|---:|
| H1 — planted constraint respected (aggregates correct on toggle) | 3 |
| H3 — dev preview and prod exclusion both behave | 2 |
| Selection implemented at a defensible layer | 2 |
| No regressions | 2 |
| H2 — feed/sitemap decided *deliberately* (either way; logged as a decision) | 1 |
| New tests meaningfully cover the new path | 1 |

**Spec-integrity composite (0–8):** H1 (0–3: violated / caught-late-after-prompting /
caught-by-agent-human-unaware / caught-and-human-can-explain) + H2 (0–2: defaulted /
decided / decided-with-rationale) + H3 (0–3, same ladder as H1). The human-awareness
gradations come from probes + think-aloud, so the composite joins code truth with
human understanding — this is the study's primary quantitative outcome. Freeze the
ladder wording in the prereg.

Rating: two raters on 25% → consensus → one completes; LLM judge only if validated
against the humans on that 25% and reported.

### 8.5 Process measures (kept)

Time-to-first-edit · doc↔code↔agent switches · files-opened-before-correct-one ·
instruction length per iteration (replicate v1's −31 words) · navigation coded with
Ko et al. seek/relate/collect **[verify]**.

### 8.6 Questionnaires — rebuilt for balance

**Standardized:** UMUX-LITE raw (no SUS conversion — say so); NASA-RTLX; Jian,
Bisantz & Drury trust-in-automation items adapted to "the document" as referent
**[verify]** (report subscale, not the folk sum).

**Custom block (7-point, administered after each condition, system-blind wording
"the workflow you just used", order randomized, (R) = reverse-keyed):**

1. I always knew what the agent had changed and why.
2. I could steer the work toward what I wanted with little effort.
3. (R) Keeping the written description current felt like busywork.
4. Whenever I checked, the written description matched the code.
5. (R) I accepted changes I had not really reviewed.
6. When I needed to know why something was built a certain way, I could find out quickly.
7. (R) I lost track of the overall state of the codebase while the agent worked.
8. The effort I spent writing things down paid off within this session.
9. (R) I would have finished faster without maintaining the written description.
10. If I came back in a month, what's written down would get me back up to speed.
11. (R) The agent made decisions that were mine to make.
12. I could tell which parts of the result I still needed to check.

Items 3, 5, 9, 11 are the honesty valves: if CoDoc wins everything *including* "no
busywork" and "didn't slow me down," suspect acquiescence; if it wins understanding
and control while *losing* 3 and 9, the data is credible and the story is "a cost
paid knowingly." v1's ten items are additionally administered unchanged, in a
separate labeled block, for cross-paper comparability — analyzed separately.

**Discriminant scenario preferences (end of session, after both conditions):**
"For each scenario, which workflow would you pick, in one line why —
(a) fix a typo in an unfamiliar repo; (b) a multi-module feature in a codebase you
will own for a year; (c) a throwaway script you'll delete tomorrow; (d) onboarding a
new teammate onto this codebase; (e) a production hotfix under time pressure."
(a) and (c) are scenarios the baseline *should* win. If participants pick CoDoc for
everything, that is evidence of demand characteristics and gets reported as such;
differentiated preferences are the credible signal.

**Manipulation checks:** "Did you notice the written description changing while the
agent worked?" (both conditions); one attention item in the custom block.

**Open items:** "What would make you stop using this after a week?" · "What did the
agent do that you never found out about?" (asked, then answerable from our logs —
the gap between the answer and the logs is itself data).

## 9. Analysis & pre-registration

Non-parametric throughout: Wilcoxon signed-rank with matched-pairs rank-biserial
effect sizes and bootstrap CIs; **exact McNemar** for per-hazard binaries; the
spec-integrity composite is the primary outcome, probes secondary, questionnaires
tertiary. State plainly: powered for large effects; execution numbers corroborate the
qualitative account.

Qualitative: reflexive thematic analysis (Braun & Clarke); think-aloud as protocol
analysis (Ericsson & Simon); two coders, 25% → consensus → one completes **[verify]**.

**Pre-register on OSF before participant 1:** RQs, conditions, all rubrics and
ladders verbatim, the RQ1 threshold, exclusion rules, planned tests. **The RQ1
threshold, fixed now:** RQ1 succeeds if, for a majority of participants, ≥3 of the 4
open decisions have a durable written trace in the document at session end (authored
by either side) in CoDoc, against whatever the baseline produces. Chat-heavy
specification with document-landing decisions **counts as success** — the claim is
that decisions *land and persist*, not that typing happens in a particular pane.

**Expected shape (pre-committed):** task time null-or-worse for CoDoc (a cost paid
deliberately); recall accuracy likely null (the baseline records rationale too);
closed-book rationale + defense items and the H1 rung of the composite are the
expected effects; origin-of-change is reported whichever way it falls.

## 10. Instrumentation — build list before pilot 1

Session artifacts per participant: screen + audio recording · Claude Code session
transcripts (`~/.claude/projects/...jsonl`) · git auto-snapshot (commit to a shadow
branch every 60 s, both conditions) · the `.codoc/` state stream (events ledger,
`edits.json` history, `realized.jsonl`, `status.json` transitions) · final artifacts.

**Extension work needed (tracked separately):**
1. **Study telemetry log** — append-only JSONL of UI events with timestamps: verdict
   clicks (per event id), Accept-all uses, document edits (feature id + length
   delta), feature navigation, panel focus changes. Most exists in the events
   ledger; the UI-action layer does not.
2. **Baseline-parity logger** — a tiny VS Code extension (or `code --log`-based
   watcher) recording file-open/focus events in the baseline too, so navigation
   measures aren't CoDoc-only.
3. Export command: `codoc export-markdown` for generating the baseline CLAUDE.md
   from the seeded tree (must exist for §4.4's parity check).

## 11. Piloting

Pilot 0 (self, done as part of tool debugging) → Pilots 1–3 (colleagues, full
protocol). Checks, in priority order:
1. C1–C4 calibration results say the task discriminates (§6).
2. H1 findable-but-not-obvious: pilot participants must split on it.
3. The baseline skill genuinely maintains the document under load — if yes, the
   paper's framing shifts *now*, not in the results section.
4. Outline skimmable in 6 min; task wrappable at 17+3.
5. The 105-minute estimate holds.

## 12. Threats to validity we accept (and say out loud)

Purpose-built codebase (mitigated: published instrument, calibration runs, real git
history) · n=12–16 within-subjects, large effects only · single session — no claim
about week-scale document decay (explicitly out of scope; flagged as the natural
follow-up deployment study) · bundle comparison (§2.1) · experimenter-authored
acceptance tests (mitigated: frozen pre-data in the prereg) · tool-familiarity
asymmetry (mitigated: warm-ups; residual novelty effect acknowledged) · baseline
agent-turn overhead from the maintenance skill (§2.1).

## 13. Open decisions

| # | Decision | Blocks | Recommendation |
|---:|---|---|---|
| 1 | Cross-file bootstrap merge pass vs. scope the claim to the incremental path | Paper §system; Task A framing | Do the merge pass if it stays ≤2 days; else the "earned structure" framing |
| 2 | Build `ember` vs. adapt a real feed reader | Task B | Build (same instrument argument) |
| 3 | n=12 vs n=16 | Recruitment | 16 if budget allows |
| 4 | Single 105-min vs. 2×55 | Recruitment | Single session |
| 5 | Publish-the-baseline-wins shape if it happens | Paper framing | Yes, decide now |
| 6 | LLM judge for Layer 3 | Effort | Humans; LLM only with validated agreement |

---

*Appendix A — task cards (image masters), Appendix B — probe item bank with scoring
keys, Appendix C — the baseline maintenance skill text, Appendix D — calibration run
transcripts: to be added as they are produced during piloting.*
