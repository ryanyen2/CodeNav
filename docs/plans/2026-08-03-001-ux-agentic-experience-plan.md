# 2026-08-03-001 — UX + agentic-experience overhaul

**Goal.** Make the extension surface the agent's process informatively without
overwhelming, give edits visible consequences and histories, make code↔doc
navigation effortless, and make feature descriptions teach the *theory* of the
codebase (Naur 1985) — purpose, design choices, rationale, in plain language.

**Design reference.** `docs/prototypes/codoc-editor/` (paper-white, hairline,
pill geometry, no shadows/gradients; SF Pro Rounded display; reduced-motion
aware). Its signature moves: the agent action **ribbon** (live step play-by-play
that collapses to a one-line summary), the **presence caret** whose flag icon
reflects what the agent is doing (`icons-agent.js` workers), **citation chips**
with an inline code **peek** window, ghost→resolved **word-by-word reveal**, and
the Flowistry-style **dependency-flow panel**. The webview already implements
much of this (presence-layer, agent-ribbon, reveal-decorations, codeRef chips) —
the work below closes the gaps, it does not start over.

---

## §0 Shipped in this session (2026-08-03)

All green: 1228 pytest · 687 vitest · tsc · esbuild. Uncommitted.

1. **Theory-building description prompts** — `codoc/prompts/tree_update.txt`
   (Rule 6 rewritten + new Rule 7, examples restyled, `author_intent` legend),
   `bootstrap_file.txt` (Field rules + examples), `bootstrap_org.txt` (theme
   descriptions). Descriptions now: purpose first in plain words → key design
   choice + its why; jargon banned; ≤3 sentences; at most ONE inline
   `[symbol()](codoc:file#symbol)` citation taken verbatim from bindings;
   reserved channels (`> `, `**bold**`, external links) fenced off. Cache-safe
   (frozen-prefix edits only — one-time recache).
2. **Intent capture** — `codoc/loop/intent.py` (+`INTENT_FILENAME`): the
   `UserPromptSubmit` hook now stashes the user's prompt (was discarded) into
   gitignored `.codoc/intent.jsonl`; Loop A threads the epoch-owning session's
   fresh tail into the LLM as `changes["author_intent"]`; `read_status` /
   `codoc_status` expose `recent_intent` for session resume.
3. **Blame backend** — events schema v3: indexed `feature_id` column
   (backfilled), `store.events_for_feature()`, ADD pre-mint in `apply_op` (the
   creation event now carries the real id), `codoc_history` MCP tool,
   `codoc history <id-or-title>` CLI.
4. **Directive outcomes** — `.codoc/realized.jsonl`
   (`edits._log_realized`/`read_realized`): completed directives persist
   (idempotent, bounded) when the realize queue drains, joinable against
   `events.caused_by` — the durable "here's what happened to your edit" record.
5. **Code→doc navigation fixed** — `codoc.navigateToFeature` now reveals the
   feature in the live Codoc Tree **webview** (panel registry +
   `reveal-feature` message, buffered until first paint; raw-text reveal only
   as fallback). Previously it dumped users into the raw tree.codoc text.
6. **Misplaced decorations fixed** — new
   `vscode-codoc/src/webview/tiptap/display-text.ts`: baseline↔current diffs
   now run in *display space* (every inline atom = one U+FFFC char, so char
   offsets ≡ doc positions — codeRef-chip paragraphs diff precisely instead of
   being skipped) and paragraphs pair by exact-match + token-similarity
   **alignment** (`alignParas`) instead of raw index — one inserted/removed
   paragraph no longer shifts every later underline onto the wrong text. Both
   `hold-decorations.ts` and `captured-decorations.ts` rewired; 11 regression
   tests in `src/test/display-text.test.ts`.

---

> **Status update (later on 2026-08-03):** W3 SHIPPED in full — directive-
> completion notifications (`state.onDidRealize` off a `realized.jsonl` watcher,
> memento-deduped, "Show feature" jumps into the webview), the prose-only
> `✓ saved` heading flash (host `savedPending` → held-check drain on the daemon
> echo), session-aware pending wording (`sync.sessionLive` → hold badge/rail
> tooltips: "lands next agent turn" vs "run /codoc:sync"), and the verdict
> timeout now shows an explicit transient notice instead of silently reverting.
> W1's STEP VOCABULARY also shipped: the hook's PreToolUse matcher now includes
> `Bash`; test runs and repo-mutating git verbs are classified
> (`hook._classify_bash`) into typed `recent[]` action entries attributed to
> the features being edited, and the ribbon renders them (`AgentStep.kind`,
> mono styling) — "editing a.py → running pytest → git commit". Remaining in
> W1: per-agent identity, caret worker-icon variants, restraint audit.

## W1 — Agent-process surfacing (the ribbon + caret, finished)

Current state: presence avatar + whisper label, ribbon steps from the
`activity.json` `recent[]` tool log, heading dots, ghost→reveal. Gaps and work:

- **Per-agent identity.** Everything renders as one "Claude"
  (`src/state/presence.ts:102-125`). Add `agent: {name, kind}` to the
  activity.json epoch (hook.py knows the session; Claude Code exposes the tool
  name; the hub's realize agent can stamp its own). Presence flag + ribbon
  header + blame actor colors all key off it. Schema change is additive.
- **Richer step vocabulary (git · tests · consult).** The ribbon only sees
  Edit/Write/Read touches. Extend the PreToolUse matcher to `Bash` and
  classify command lines (`git commit` → "committing", `pytest`/`vitest` →
  "running tests", WebFetch → "consulting <domain>") into typed `recent[]`
  entries; `featureSteps` (`src/state/activity-model.ts:214`) maps them to the
  prototype's step lines with per-type icons (the `icons-agent.js` workers).
  This is the prototype's "reading fanout.py → root cause → writing migration →
  opening PR" narrative, driven by real signals.
- **Caret activity icon.** The presence flag already carries a phase glyph;
  swap in the worker-icon set per step type (read/edit/test/git) so the caret
  itself says what the agent is doing. Reduced-motion honored (CSS gate
  exists).
- **Live realize progress.** Keep `implementing M/N · title` (status detail) as
  the whisper; add the directive's plain-language gloss on hover (manifest
  `text` is already in the sidecar hold detail).
- **Restraint rule (avoid overwhelm).** One moving element at a time: the caret
  moves OR the ribbon appends, never both animating; steps cap at 5 with
  collapse-to-summary (already implemented); no signal ever *only* animates —
  every animated state has a static end-state (dot/badge).

> **Status update (2026-08-03, session 3):** W2, W1-identity, W5, W6 SHIPPED,
> plus W4 source-side anchoring. Green: 1253 pytest · 711 vitest · tsc · esbuild.
> - **W2 blame surfaces** — sidecar `feature_history` slice (bounded per-feature
>   who/when/why, ONE shared `recent_events` scan with the changes feed,
>   rationale-capped); `blame-model.ts` (relative time, actor role/label,
>   summary); `blame-decorations.ts` (heading "who · when" label + hover trace +
>   author-role attribution rail); a toolbar **History** toggle (persisted pref).
> - **W1 per-agent identity** — hook stamps `epoch.agent.id` from `CODOC_AGENT`
>   (default claude-code); `agentRole` threads it into `sync.agent` →
>   presence/ribbon/blame attribute to the real agent (roleName/roleInk already
>   map codex/gemini/cursor).
> - **W5** — composer-drop FIXED (a projection arriving while a comment composer/
>   selection bubble is open is now DEFERRED and re-applied on close, not dropped;
>   `shouldDeferProjection` pinned). Items 1 (null-fid) and 3 (no-version gate)
>   VERIFIED NOT BUGS: a projection heading always carries a version the daemon
>   advances (so "keeps local forever" can't occur — the gate correctly holds a
>   pending edit only against a *stale* projection), and an unminted feature has
>   no server-side phase/hold state to show. Left the load-bearing merge gate
>   untouched rather than churn it.
> - **W6** — Loop B directives now cite the author's captured prompt
>   (`build_directive(..., author_intent=recent_intent(codoc_dir))` →
>   `Author asked: "…"`); realize.txt tells the agent the ask is ground truth.
> - **W4 source-side anchoring** — the pure decl scanner (`declLines`/`declName`,
>   shared by decoration.ts + code-lens.ts) now recognizes module-scope
>   arrow/function/class consts (`export const h = () => {}`), matching the TS
>   indexer's emission — previously those features got NO code decoration/lens.
>   Nested locals stay excluded (no false positives). Pinned in bridge.test.ts.
>
> **Genuinely remaining (pure-visual, EDH-gated — build + verify in the running
> IDE, not unit-testable):** W4 inline code peek (⌥-click chip → embedded snippet
> — marginal over the existing chip→open-Beside, which works); W1 caret
> worker-icon variants + restraint audit; the Flowistry dependency-flow panel.
> These are presentation polish on top of a now-complete data/logic layer.

## W2 — Blame mode (traces of edit history)

Backend shipped (§0.3). Remaining — the surfaces:

- **Sidecar history slice.** `write_sidecar` adds `feature_history`: per
  feature, the last ~8 applied events `{at, kind, actor, mode, caused_by,
  rationale}` via the new indexed query (cheap). The webview is file-channel
  only, so this is the transport.
- **Blame toggle in the webview.** A toolbar "History" stance: description
  paragraphs get a left attribution rail colored by last-amender
  (human=neutral ink, agent=teal, per-agent hue once W1 identity lands);
  heading hover (or the existing hover-card) grows a timeline section — three
  rows of "who · when · why" (rationale / caused_by-resolved directive gloss),
  with "show all" → the full `codoc history` output in a quick-pick or
  read-only editor.
- **Description-diff time travel (later).** AMEND events store full snapshots;
  a hover diff of the previous vs current description is derivable with the
  existing wordDiff. Defer until the timeline proves useful.

## W3 — Consequence + state feedback (know what happens without waiting)

- **Directive completion toast.** The daemon (or host file-watch on
  `realized.jsonl`) surfaces "✓ Implemented: <gloss> — view changes" per
  completed directive; clicking opens the feature + its `caused_by` events
  (files touched from the joined reflect ops). Data shipped in §0.4.
- **Prose-only edit confirmation.** Today a prose-only edit "commits live and
  raises nothing" (`tree-editor.ts:600`). Add the transient "saved to tree ✓"
  micro-confirmation on the heading dot (fade after ~2s; no modal, no noise).
- **Session-aware nudge.** "Pending — run /codoc:sync" renders whether or not a
  session is live. Gate the wording on `epoch_alive`: live session → "will be
  implemented next turn"; none → "run /codoc:sync (or `codoc realize`)".
- **Verdict acks over blind timers.** Accept/Reject uses an optimistic 5s
  revert (`doc-view.ts:280-293`). Loop B already deletes the event on accept —
  the next projection is the ack; replace the timer with
  "ack-on-projection-that-drops-the-event, timeout→explicit error toast", not a
  silent revert.

## W4 — Code↔doc navigation (finish the loop)

- Shipped: code→webview reveal (§0.5). Forward doc→code chips already work.
- **Inline peek (prototype's signature).** Chip click currently opens the file
  Beside. Add modifier-click (⌥) → inline peek: an embedded, read-only snippet
  window under the paragraph (prototype `.peek` styling; content via the
  existing `open-binding` host round-trip + document symbol resolution). Esc or
  click-out collapses. Keep plain click = open Beside (current muscle memory).
- **Dependency-flow panel (Flowistry-style).** The right-panel neighborhood
  graph from the prototype: focused feature → depends-on (up), used-by (down),
  bound code chips; data already exists (`feature_edges` sidecar slice + graph
  cache). Hover → flash the doc section; click → navigate. Slice toggle dims
  non-neighborhood sections (focus dimming already exists — reuse).
- **Source-side anchoring.** `providers/decoration.ts` matches decl lines by
  regex — renamed/duplicate leaves mis-anchor. Move to
  DocumentSymbolProvider-based resolution (already used by `codoc.openRef`),
  falling back to regex.

## W5 — Robustness backlog (unexpected-usage bugs, from the audit)

**2026-08-03 adversarial pass** (simulated real-developer abuse; all fixes
test-pinned): (a) a single corrupt `op_json` row bricked the v3 migration —
store unopenable — fixed with a `json_valid` guard
(`tests/store/test_feature_history.py`); (b) authored prose could FORGE tree
structure — `- text ⟨f-id⟩` / `~ - …` lines in a description minted a phantom
node and truncated the real description; an id token in a title made the
feature vanish from the parse — fixed by write-boundary sanitization in
`apply_op` (`parse.sanitize_authored_*`, `tests/codoc_file/test_hostile_prose.py`);
(c) a UTF-8 BOM (Notepad hand-edit) swallowed the first feature → read as a
retire — stripped at `parse_text` entry; (d) valid-JSON-of-the-wrong-type in a
control file crashed four readers including the hook (blocking user turns) —
`read_json` now shape-guards + quarantines; (e) the `read_manifest` drain now
double-checks under `_edits_lock` (verified by an 8-process stress harness:
zero duplicate/lost outcomes, drafts survive). Held without fixes: torn /
binary / huge control files (quarantine), concurrent intent appends, fake
directive headings + literal `<<<CACHE_BREAK>>>` in prose, CRLF/tab hand
edits. Accepted with eyes open: hand-deleting an id = "new feature" semantics
(title-adoption defends); duplicate hand-pasted ids = last-wins; captured
intent is the user's own prompt flowing into the tree-update LLM (structural
ops still gate through proposals); descriptions stored BEFORE the sanitizer
only clean up on their next amend (a `codoc migrate` sweep could do it
eagerly); Loop B's read→build→write manifest cycle is still not fully atomic
against a concurrent drain (narrow window; passes are loop_lock-serialized).

Fixed earlier this session: decoration display-space + alignment (§0.6),
reverse-nav (§0.5). Remaining, in priority order:

1. **Null-fid asymmetry** — a brand-new feature is under-decorated until its id
   mints (`whole-doc-editor.ts:699` patchMintedIds). Key transient UI
   (activity/reveal) by `fid ?? localId` like captured already does, and map
   minted ids through in one place.
2. **Composer-open drops projections** (`whole-doc-editor.ts:1137`): a
   projection arriving while a comment composer is open is discarded entirely —
   queue it and apply on close instead.
3. **No-version headings keep local forever** (`doc-gate.ts:121-163`): a
   heading missing its HLC `version` never accepts remote updates; log + fall
   back to accept-when-idle.
4. **Presence anchor drift** — avatar anchors by DOM query + rects, re-laid on
   rAF; margin comment cards don't re-layout on scroll
   (`whole-doc-editor.ts:1020`). Consolidate on one anchored-overlay utility.
5. **Manual tree.codoc edits while the webview is open** — the daemon owns the
   file; direct text edits are legal in the raw editor but their interplay with
   an open webview (settle vs external reload) deserves a property test around
   `gateProjection` + baselines.
6. **Spark ticks vs setDoc rebuild** (`whole-doc-editor.ts:495-533`): timers
   hold references to replaced DOM nodes — re-query by fid on fire.

## W6 — Intent & rationale (deeper Claude Code integration)

Shipped: capture + Loop A threading + resume surface (§0.2). Next:

- **Directives cite the author's ask.** `build_directive` embeds the freshest
  matching intent line ("The author asked: …") so the realizing agent
  implements the stated goal, not a reconstruction.
- **Rationale into blame.** The tree-update pass now receives author intent —
  its op `rationale` fields (already stored per event) become real "why" lines
  in the W2 timeline. Close the loop by showing them.
- **Transcript-grade rationale (exploratory).** The Stop hook payload carries
  `transcript_path`; a cheap tail-summarization into a per-epoch "what was
  decided" note could feed descriptions for large sessions. Prototype behind an
  env flag; measure value before making it default.

---

## Sequencing

1. **W3** (consequence feedback) — highest leverage per line of code; data
   already shipped. Then **W1 identity + step vocabulary** (schema additive).
2. **W2 surfaces** (sidecar slice → hover timeline → rail).
3. **W4 peek + flow panel** (design-heavy; prototype is the spec).
4. **W5** items 1–3 next session; 4–6 opportunistic.
5. **W6** directive-citation next time Loop B is touched.

**Verification.** Each W ships with vitest for the pure logic + an EDH manual
pass for the visual layer; pytest suites guard the Python channels. The eval
rubric (`tests/eval/`) should gain a description-quality judged dimension
("does the description state purpose + why in plain language?") to measure the
§0.1 prompt overhaul on real bootstraps.
