# Probe item bank — hearth (Task A)

Design doc §8.3. Each item: type, grounding, closed-book first (record answer +
confidence 1–5), then open-book re-ask (document allowed, code stays closed), record
delta. Interviewer reads verbatim; one "can you say more?" probe max. Scoring 0–2
against the keys below; keys frozen at pre-registration.

## Probe 1 — after the comprehension stage, before the task (pick 5 + anchors)

**F1 (function; Pennington domain model) — ANCHOR, repeats in Probe 2**
> You run `hearth build`, change nothing, and run it again. What does the second run do?

Key: 2 = both levels: per-page rebuilds skipped via content hashes AND index/tag/feed
pages skipped via a signature over the post list; 1 = "it caches / skips unchanged
pages" without the aggregate level; 0 = full rebuild / don't know.

**S1 (structure; Pennington program model) — ANCHOR**
> Walk me through the stages a single post passes through, from a file on disk to
> HTML in `_site`.

Key: 2 = discovery → frontmatter → markdown render → page/URL derivation → template
render → write, with aggregates as a separate pass; 1 = ≥4 stages in order, aggregate
pass missing; 0 = fewer/wrong order.

**R1 (rationale, inherited; planted #4)**
> Why does the dev server serve files from the build output instead of rendering
> pages on request? What alternative was rejected?

Key: 2 = per-request rendering rejected so dev and prod can never disagree; 1 = a
plausible why without the recorded one ("simpler", "faster") and no alternative;
0 = none/wrong.

**R2 (rationale, inherited; planted #1 — the H1 backstory)**
> Index and tag pages are not rebuilt on every build. How does hearth decide when
> they must be, and why was it designed that way?

Key: 2 = signature over the collection's visible fields, chosen over a per-output
dependency graph (recorded: the graph was always subtly wrong after deletes);
1 = signature mechanism without the rejected alternative; 0 = mtime/guess.

**E1 (extension; LaToza & Myers reachability)**
> To add a second output format — say a JSON file per post — which modules would
> change, and which would you leave alone?

Key: 2 = touch build (emit pass) + maybe pages; leave discovery/frontmatter/markdown/
templates alone; mentions the cache/outputs map implication; 1 = right modules, no
cache implication; 0 = scattered.

**D1 (defense)**
> The markdown renderer is written by hand instead of using a library. Would you
> have made the same call? Why or why not?

Key (judgment, not agreement): 2 = a position grounded in a tradeoff (dependency
surface vs. compatibility bugs, the recorded one-file-deploy story counts);
1 = position without grounding; 0 = no position.

## Probe 2 — after the task (changed region + transfer; F1 + S1 anchors re-asked)

**F2 (function; targets H1 comprehension)**
> After your change: someone flips a published post to draft and runs an incremental
> build. Walk me through exactly what rebuilds and why.

Key: 2 = the flip reaches the collection/signature so aggregates rebuild (or: names
their own implementation's actual behaviour *including* its staleness if broken —
accurate self-knowledge of a broken build scores 2 here; correctness is scored in
Layer 1, not in the probe); 1 = page-level only; 0 = wrong model.

**R3 (rationale, made-during-task; H2 articulation)**
> Drafts and the RSS feed: what does your build do now, and why that way?

Key: 2 = states behaviour + a reason referencing a consideration (subscribers,
preview parity, spec silence — any deliberate ground); 1 = states behaviour, reason
is "the agent did it that way"; 0 = doesn't know what their own build does.

**E2 (transfer)**
> Next month the team wants scheduled posts — `publish_at` with a future date stays
> hidden until the date passes. Given what you built today, what changes and where?

Key: 2 = extends their selection mechanism (a second predicate where visibility is
decided) + names the cache consequence (time-based selection means the signature
changes without an edit — the build must be re-run / the signature must include the
date gate); 1 = right place, no cache consequence; 0 = a new scattered filter.

**D2 (defense)**
> You put the draft decision where you did. Argue for the opposite placement — what
> would break, and would anything get better?

Key: 2 = names the real tension (early placement: cache-safe but dev preview needs a
mode; late placement: preview-trivial but invisible to the signature); 1 = one side
only; 0 = no engagement.

## Notes

- Provenance split (design doc §8.3): R1/R2 inherited; R3/D2 made-during-task.
- The ember (Task B) bank mirrors item-for-item once ember exists: F = incremental
  digest; R-inherited = digest signature + store-normalization rationales; E2 =
  "snoozed feeds" transfer.
- Never ask two participants different items: fixed set, fixed order, both conditions
  (codebase differs, item shape matched).

## Pilot-0 findings (2026-08-11, mechanics verification)

- **H1 verified live**: naive late filter (in `build_indexes`) + draft flip →
  `12 pages, 1 rebuilt` (aggregates skipped), home page still lists the draft.
  Only manifests on INCREMENTAL builds — `--force` hides it. Exactly the
  hard-to-verify property the task needs.
- **Correct arm verified**: selection at `assemble` → `aggregates rebuilt`, index
  clean.
- **Bonus depth found**: the minimal correct implementation leaves the drafted
  post's OWN output page on disk (`/posts/<slug>/index.html` still served in prod
  — an information leak). Output removal for excluded sources is a third
  gradation: add to Layer-3 scoring notes and to the acceptance test
  (prod build must not contain the draft's HTML file at all).

## ember (Task B) — mechanics verified (2026-08-12)

Matched trap confirmed empirically, mirror of hearth's:
- **Naive arm**: mute filtered in `render_digest` (downstream of `digest_signature`)
  → "nothing new to announce", digest page untouched, muted feed still shown. FIRES.
- **Correct arm**: filter where `assemble_digest` gathers items (upstream of the
  signature) → digest regenerated without the feed. CLEAN.
- **H3-equivalent**: archive + search.json retain the muted feed's items (their own
  signature over ALL items, separate from the digest's). HOLDS.
- **Subtlety**: a `feeds.toml` edit does NOT invalidate the digest (fetch config
  only) — so a mute flag added there and consumed by a renderer-level filter stays
  trapped. HOLDS.
Snapshot: ~/repos/test-workspace/snapshots/ember-bare.tar.gz (2,275 src LOC,
171 tests, 12 commits). Still needed before use: codoc bootstrap + seeding pass
(planted digest-signature rationale + gap), baseline CLAUDE.md export, ember
probe items (mirror the hearth bank), and the ember card calibration runs.
