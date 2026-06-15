# Residual review findings — feat/steering-emphasis-links-sdk

Source: ce-code-review run `20260612-180557-cb69a71c` (9 reviewers; artifacts under
`/tmp/compound-engineering/ce-code-review/20260612-180557-cb69a71c/`). All
gated/mechanical findings were applied on-branch (`fix(review): …`). The items
below are design-level follow-ups, accepted as known residuals.

1. **Realize-queue concurrency + done-tracking** (`codoc/loop/loop_b.py`,
   correctness + adversarial). A Loop B append can race `/codoc:sync` deleting
   `realize.md`/`realize.json` (TOCTOU between `read_manifest` and the writes),
   and an append re-lists every manifest entry, so a *fresh* session could
   re-implement directives a previous session already finished. Proper fix:
   a filelock spanning the read→write window plus per-directive done-tracking
   (drop a manifest entry when its `⟨d-id⟩` comes back via
   `codoc_reflect(caused_by=…)`), or a lock-taking `codoc_queue_done` MCP tool.

2. **`codoc_steer` MCP tool** (`codoc/mcp/tools.py`, agent-native). Humans can
   steer via `> …`; agents have no equivalent authoring path (and are forbidden
   from editing `.codoc/`). Add `codoc_steer(feature_id, note, caused_by="")`
   routing through the same manifest-append helper, stamped `actor=agent`.

3. **Webview settle can delete `> …` notes** (`vscode-codoc/src/state/
   doc-serialize.ts`, api-contract). `renderTreeFromDoc` knows nothing of
   steering comments; a webview settle in the window between typing a note in
   the raw editor and Loop B draining it would drop the note. Fix direction:
   a PMDoc blockquote node that serializes back to `> …`, or skip serialization
   when the on-disk text contains steering lines.

4. **Watch epoch suppression strands mid-epoch steering** (`codoc/loop/
   watch.py`, correctness). During an open agent epoch, tree.codoc writes are
   not routed to Loop B, so a mid-generation `> …` note waits for epoch close
   instead of appending to the live queue. Consider letting comment-bearing
   parses through (comments only append + re-render).

5. **No liveness timeout on unattended realize** (`codoc/loop/autorealize.py`,
   reliability). A hung SDK/CLI child freezes the auto-realize cycle with
   status `realizing`. Track spawn time in WatchState; kill + reset to
   `awaiting_impl` after a configurable timeout. (Parity gap shared with the
   pre-existing `claude -p` engine.)

6. **Trust boundary of unattended `acceptEdits` + Consult links**
   (`codoc/loop/sdk_realize.py`, adversarial). The unattended engine fetches
   URLs cited in tree.codoc and edits files without prompts. Permission mode is
   configurable (`--permission-mode`); consider a scheme/host allowlist for
   Consult URLs on the unattended path.

7. **Stored descriptions containing literal `>` lines** (`codoc/codoc_file/
   parse.py`, correctness, narrow). Render never emits `>` lines (comments are
   never stored), but an LLM-amended description containing one would be
   re-read as a steering comment. Escape-on-render or diff-aware detection if
   it ever bites.

## Frontend inline comments (2026-06-13)

The webview inline-comment surface (select → bubble menu → composer → `> …`
steering line; `comment-model.ts` + `comment-decorations.ts` + the host
lifecycle in `tree-editor.ts`) was reviewed adversarially (ce-adversarial-reviewer,
agent `a98fe75c`). Three load-bearing findings were fixed on-branch — the
adjacent-`>`-line MERGE that collapsed two threads (now a blank separates runs),
the `editComment` `serialized:false` RESURRECTION (now stays `true`, so a
concurrent drain flips to `sent` instead of re-queuing), and harvested-id CHURN
(now a deterministic content hash, `harvestCommentId`). Both have regression
tests in `comment-model.test.ts`. The remaining two are accepted residuals:

8. **Two threads with byte-identical emitted notes on ONE feature**
   (`comment-model.ts reconcileComments`, correctness, narrow). Thread identity
   in the `> …` channel is by `(featureId, noteText)`, so two comments whose
   `re "anchor": body` lines are identical collapse — one drain flips both to
   `sent`, or a phantom harvest appears. Requires the SAME anchor snippet AND
   body on the same feature, so it's rare (distinct selections → distinct
   anchors). A per-line id marker would fix it but would pollute the steering
   note the agent reads. Left as-is.

9. **`sent` marker lingers while a repo never returns to `in_sync`**
   (`comment-model.ts` line ~192, cosmetic). A drained comment's faded ✓ is
   dropped only at `status==in_sync`; on a chronically-busy repo (or with no
   daemon, where an open note isn't drained until the next prompt) the marker
   persists. Not data loss — the thread store stays consistent and the global
   status bar/activity dots carry the live signal; the semantics ("clears when
   the tree settles") are defensible. A spawn-time/observed-once GC could clear
   it sooner if it bites.
