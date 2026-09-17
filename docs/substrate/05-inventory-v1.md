# codoc user-facing feature inventory

Repo: `/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav` (branch `main`, 2026-09-17).
Nothing was modified. All line numbers are as-read.

Tags: **core** (the product), **useful** (earns its place), **confusing** (real
feature, unclear or overlapping to a user), **redundant** (a second way to do
something that already exists), **dead** (historical, unreachable, or superseded).

---

## 1. CLI (`codoc/cli/main.py`)

Module docstring at `codoc/cli/main.py:1-18` splits these itself into "core
commands" and "plumbing (agents / no-IDE workflows)".

| Command | Line | What it does | Tag |
|---|---|---|---|
| `codoc init` | `main.py:87` | Index repo, LLM-bootstrap the tree, render `tree.codoc`, install hooks. Flags `--root`, `--force`, `--hooks/--no-hooks`, `--doc-language/--lang`. Refuses to run while a daemon watches the repo (`main.py:102-113`), because init rebuilds the index and `--force` deletes the store the daemon has open. | core |
| `codoc watch` | `main.py:183` | The daemon; runs both loops continuously. Flags `--no-realize` (sync the tree but never queue realization), `--dry` (apply tree edits, don't queue), `--auto-realize` (unattended fallback when no interactive session). | core |
| `codoc status` | `main.py:414` | Feature count, pending proposals, recent activity. Tolerant of a corrupt `status.json` (`_read_state`, `main.py:56`). | core |
| `codoc sync` | `main.py:538` | One-shot: apply tree edits (Loop B), then reflect code (Loop A). `--dry`. Docstring notes Loop B no longer spawns a coding agent; code-implying edits are queued in `.codoc/realize.md`. | core |
| `codoc serve` | `main.py:202` | The deployed hub. Flags `--host`, `--port`, `--static-dir`, `--tunnel`, `--insecure-public`. Its own docstring (`main.py:206-222`) states remote GitHub-authorized access "is not finished": `/api/payload`, `/api/media`, `/api/events` are served without authentication, so `--host 0.0.0.0` or `--tunnel` makes the whole tree public. | confusing |
| `codoc realize` | `main.py:324` | Implement the queued directives NOW, foreground. `--engine auto\|sdk\|cli`, `--permission-mode`. The SDK engine prints one compact line per agent action and mirrors each into `.codoc/activity.json`. | useful |
| `codoc accept <e-id>` | `main.py:577` | CLI verdict; writes `inbox.json` and drains Loop B — the same path as the IDE Accept. | useful |
| `codoc reject <e-id>` | `main.py:591` | Drop a pending proposal. | useful |
| `codoc reflect` | `main.py:621` | Recovery-grade code→tree reconciliation, re-derived from current state rather than a temporal diff, so it catches what a crashed cycle dropped. `--scope`. Spawned by the Stop hook when no daemon is running. | useful |
| `codoc history <feature>` | `main.py:481` | One feature's blame timeline (who / when / why), by id or title fragment. `--limit` (default 15). | useful |
| `codoc install-hooks` | `main.py:894` | Idempotent (re)install of the CC hooks, the `codoc-intent` skill, the `/codoc:*` commands, and the MCP registration. | useful |
| `codoc lang [<bcp47>]` | `main.py:729` | Show or set the tree's authoring language in `.codoc/config.json`. Reports what the tree actually contains, not just what is set (`_echo_language_mix`, `main.py:861`). | useful |
| `codoc translate` | `main.py:779` | LLM rewrite of existing prose into the authoring language. `--to`, `--dry-run`, `--limit`, `--yes/-y`. | useful |
| `codoc propose <kind>` | `main.py:651` | Author a proposal from the shell. Eleven options: `--title --description --parent --feature --rationale --bind (repeatable) --after --before --builds/--reflects --root`. Its own docstring positions it for "humans/tests"; every field duplicates an MCP proposal tool an agent would use instead. | redundant |
| `codoc export-markdown` | `main.py:387` | Export the live tree as plain markdown, no ids, no proposals. `--out`, `--title`. Not listed in CLAUDE.md's command set; a second, non-navigable rendering of the same tree. | redundant |
| `codoc migrate` | `main.py:712` | One-time idempotent heal for workspaces predating the 2026-06 store-authoritative refactor. Also runs on every daemon startup. | dead |
| `--version` | `main.py:78` | Print version and exit. | core |

---

## 2. MCP tools (`codoc/mcp/server.py`, implementations `codoc/mcp/tools.py`)

FastMCP over stdio; console script `codoc-mcp` (`server.py:318`). Registered into
the target repo's `.mcp.json` by `install_mcp` (`codoc/agent/install_hooks.py:118`).

### Reads (write nothing to the store)

- **`codoc_context(files, feature_id, include_bindings=True)`** — `server.py:40`, impl `tools.py:196`.
  The preferred read before editing: the features bound to those files, expanded one
  hop along call/import edges, plus a compact indented outline of every tree title and
  nearby graph edges. Bounded by the edit, not the repo size. Returns a `doc_language`
  block (`tools.py:244`). **core**
- **`codoc_tree(root_id, depth=0, include_bindings=False)`** — `server.py:55`, impl `tools.py:99`.
  Whole tree (id, title, description, parent, realized, drift, binding_count, files)
  plus pending proposals. Each feature carries `drift`: `"questioned"` (bound code
  changed, prose not amended), `"binding-lost"`, or null. **core**
- **`codoc_status()`** — `server.py:77`, impl `tools.py:351`. Counts, pipeline state, and
  `dead_refs` / `dead_ref_list` — inline `codoc:` links that no longer resolve to a
  binding (`_dead_refs`, `tools.py:330`). **core**
- **`codoc_history(feature_id, limit=20)`** — `server.py:91`, impl `tools.py:409`.
  Actor / mode / caused_by per applied event, with the title and description snapshots
  amends left behind. **useful**
- **`codoc_plan_status()`** — `server.py:257`, impl `tools.py:886`. Unrealized vs realized
  plan placeholders plus `queued_directives` — realize-queue entries still outstanding,
  i.e. work that arrived from outside the plan. **useful**
- **`codoc_walkthrough_read()`** — `server.py:310`, impl `tools.py:1031`. The walkthrough
  currently on the reader's screen, so an agent can build on the path being followed
  rather than replacing it blind. **useful**

### Writes

- **`codoc_reflect(ops, rationale, caused_by)`** — `server.py:101`, impl `tools.py:648`.
  The one batched write-back and the primary code-first entrypoint. Each op is
  `{kind, feature_id?, parent_id?, title?, description?, binds?, rationale?, caused_by?,
  after_id?, before_id?}` with `kind ∈ attach|detach|refresh|amend|add_node|move_node|retire_node`.
  Binds are `"file.py::symbol_path"`. Safe ops (attach / refresh / detach and small
  amends) apply immediately to the store; structural ops become pending proposals in the
  event ledger. `after_id` / `before_id` name sibling identities rather than indices.
  `caused_by` takes a `⟨d-…⟩` directive id so the IDE groups reflected changes under the
  doc edit that requested them. **core**
- **`codoc_propose_amend(feature_id, title, description, rationale, caused_by, builds=False)`**
  — `server.py:149`, impl `tools.py:583`. Writes a pending amend proposal, shown to the
  user as a tracked-change diff on their own prose. `builds=True` is the unique
  parameter and changes what acceptance means: a plan amendment queues a realize
  directive, a reflection just reconciles wording. The docstring names the cost of
  getting it wrong in either direction. **core**
- **`codoc_propose_retire(feature_id, rationale, delete_code=False, caused_by)`** —
  `server.py:191`, impl `tools.py:626`. `delete_code=False` is detach-only (untrack, keep
  the code); `delete_code=True` queues a code-removal directive on accept. Unique
  parameter. **core**
- **`codoc_realize_progress(done, total, current)`** — `server.py:216`, impl `tools.py:730`.
  Writes progress into `status.json` so the IDE shows "implementing N of M", and renews
  the realizing lease (`codoc/loop/status.py:REALIZING_LEASE_SECONDS`, 300s). **useful**
- **`codoc_walkthrough(question, steps, answer)`** — `server.py:270`, impl `tools.py:944`.
  Writes ephemeral `.codoc/ask.json` only — never the store, never the change ledger, so
  it is safe mid-edit. Each step is `{feature_id, note, quote?, group?, file?, symbol?, line?}`.
  `quote` is checked against the feature's own text (`_quotable_blocks`, `tools.py:922`);
  a quote that is not there is reported back and its step renders without a highlight.
  Consecutive steps sharing a `group` are numbered 1a/1b/1c. Replaces any previous
  walkthrough. **useful**
- **`codoc_walkthrough_clear()`** — `server.py:303`, impl `tools.py:1026`. Deletes
  `ask.json`. Idempotent. **useful**
- **`codoc_propose_add(title, description, parent_id, binds, rationale, caused_by, after_id, before_id)`**
  — `server.py:125`, impl `tools.py:567`. Exactly `codoc_reflect` with one `add_node` op;
  same parameter set, no unique field. Its docstring is a long argument for calling
  `codoc_propose_amend` instead. **redundant**
- **`codoc_propose_move(feature_id, parent_id, rationale, caused_by, after_id, before_id)`**
  — `server.py:175`, impl `tools.py:611`. Exactly one `move_node` op. **redundant**
- **`codoc_attach(feature_id, binds, rationale, caused_by)`** — `server.py:205`, impl
  `tools.py:638`. Exactly one `attach` op. Applied immediately; binding the first code to
  a plan placeholder flips it to realized. **redundant**
- **`codoc_plan_add(title, description, parent_id, binds, rationale)`** — `server.py:225`,
  impl `tools.py:749`. `propose_add` with `source="plan"`. Its own docstring tells the
  agent to use `codoc_propose_amend` instead whenever the plan changes what an existing
  feature does, which is the common case. **confusing**
- **`codoc_await_verdicts(event_ids, timeout=86400.0)`** — `server.py:241`, impl
  `tools.py:762`. BLOCKS the agent's turn, up to 24 hours, until every named proposal is
  accepted or rejected. Verdicts applied before it started are recovered from the event
  ledger. Exists only as `/codoc:plan`'s realization trigger. **confusing**

---

## 3. Claude Code integration

### 3.1 Installer (`codoc/agent/install_hooks.py`)

Called by `codoc init` (default `--hooks`). Four things (`install_hooks`, `:146-200`):

1. Deep-merges the hook block from `codoc/plugin/hooks/hooks.json` into
   `<root>/.claude/settings.json`. Merge is append-not-clobber: per event, existing
   entries whose commands contain `codoc.agent.hook` are replaced, everything else is
   untouched (`_merge_hooks`, `:76`). The `python` placeholder is replaced with
   `sys.executable` at install time (`_resolve_hooks`, `:50`).
2. Copies `SKILL.md` to `<root>/.claude/skills/codoc-intent/`.
3. Copies commands to `<root>/.claude/commands/codoc/`, preserving subdirs so
   `codoc/plan.md` becomes `/codoc:plan`, and DELETES previously-installed codoc
   commands the plugin no longer ships (`:186-191`, e.g. the old `/codoc:realize`).
4. Registers the MCP server in `<root>/.mcp.json` (`install_mcp`, `:118`).

Returns the commands it installed, because the CLI used to print a hardcoded list that
was wrong for months (`:151-155`). Writes are atomic (tmp → `os.replace`).

### 3.2 Slash commands (`codoc/plugin/commands/codoc/`)

- **`/codoc:sync`** — `sync.md`. Reads `codoc_status` and dispatches on `state`:
  `awaiting_impl` → implement `.codoc/realize.md`; `code_drift` → reflect; and so on.
  The single agent-side entry point. Also instructs the agent to honour `doc_language`.
  **core**
- **`/codoc:ask <question>`** — `ask.md`. Answers in the reader's own document by
  drawing a walkthrough. Tells the agent to read the actual code behind the bindings,
  because a walkthrough that only paraphrases descriptions teaches nothing. **useful**
- **`/codoc:plan <task>`** — `plan.md`. Plan-first loop: read context, propose nodes,
  `codoc_await_verdicts`, then implement. Overlaps `/codoc:sync`'s `awaiting_impl`
  branch, and carries the `codoc_plan_add` vs `codoc_propose_amend` decision the tools
  themselves argue about. **confusing**

### 3.3 Hooks (`codoc/plugin/hooks/hooks.json`, handler `codoc/agent/hook.py`)

Handler contract (`hook.py:11-25`): never raises, always exits 0 (a non-zero hook blocks
the agent), never opens `codoc.db` — it reads the `tree.bindings.json` sidecar only, to
avoid WAL contention with the daemon.

| Event | Matcher | Handler | Effect | Tag |
|---|---|---|---|---|
| `PreToolUse` | `Edit\|Write\|MultiEdit\|Read\|Bash` | `hook.py:492` | Records a touch in `.codoc/activity.json`. File → feature resolution narrows in priority order (`_resolve_features`, `:65-107`): symbol level (tree-sitter parse of the edited `old_string`), then directive level (whichever candidate has a handed-off directive on this file), then file level. Drives every live decoration in the IDE. | core |
| `PostToolUse` | same | `hook.py:541` | Same touch record, plus `_nudge_if_queue_grew` (`:505`) telling the WORKING session mid-turn that the realize queue appeared or grew. | core |
| `Stop` | — | `hook.py:290` | Closes the epoch, keeps `touched` for the reconciler, then fire-and-forget `codoc reflect` on the files this session wrote (`_maybe_spawn_reflect`, `:332`). | core |
| `SessionStart` | — | `hook.py:219` | Opens an agent epoch, recording origin (`interactive` by default, `loop_b` via `CODOC_EPOCH_ORIGIN`). `_reopen_epoch_if_needed` (`:246`) is the rising edge for turns 2+. | useful |
| `SessionEnd` | — | `hook.py:326` | Same close as Stop, but reflects only if the final turn wrote. | useful |
| `UserPromptSubmit` | — | `hook.py:549` | Nudges the live session when accepted tree edits are queued for realization. Also `_drain_inbox_fallback` (`:600`) — applies pending inbox verdicts via Loop B when no daemon owns the repo. A second nudge path beside PostToolUse's. | confusing |

Bash commands worth showing are classified for the ribbon by `_classify_bash` (`:392`).

---

## 4. VS Code extension (`vscode-codoc/`)

### 4.1 `contributes` (`vscode-codoc/package.json`)

**Custom editor.** `codoc.tree-editor`, display name "Codoc Tree", selector
`**/.codoc/tree.codoc`, priority `default`. **core**

**No `views` contribution.** The manifest's `contributes` keys are exactly: `menus`,
`languages`, `grammars`, `customEditors`, `commands`, `keybindings`, `configuration`,
`configurationDefaults`, `walkthroughs`. There is no tree view and no activity-bar
panel. `vscode-codoc/src/providers/inlay.ts:8` still refers to "the Feature Tree panel"
mirroring its pill — a **dead** reference to a surface that no longer exists.

**Commands** (30; registration lines are in `vscode-codoc/src/extension.ts`)

| Command | Title | Reg. | What it does | Tag |
|---|---|---|---|---|
| `codoc.setup` | codoc: Set up codoc | `:220` | One-click provision. Ordered steps `ensure-uv → provision → credentials → init → start-daemon` (`src/setup/setup-flow.ts:21-26`); credentials MUST precede init because init runs the LLM bootstrap. | core |
| `codoc.repair` | codoc: Repair / re-run setup | `:222` | Identical handler to `codoc.setup` (`runSetup(context, state)`). | redundant |
| `codoc.startDaemon` | codoc: Start the daemon | `:292` | Start the managed `codoc watch`. | useful |
| `codoc.open` | codoc: Open | `:367` | Open the tree editor. | core |
| `codoc.sync` | codoc: Sync | `:383` | Run `codoc sync`. | useful |
| `codoc.realize` | codoc: Implement queued changes now | `:403` | Run `codoc realize` in a terminal. | useful |
| `codoc.serve.start` | codoc: Share — start the deployed hub | `:466` | Start the hub locally. | useful |
| `codoc.serve.startTunnel` | codoc: Share remotely — hub over a tunnel | `:467` | Start with `--tunnel`. | useful |
| `codoc.serve.stop` | codoc: Stop the deployed hub | `:468` | Stop it. | useful |
| `codoc.acceptProposal` | codoc: Accept proposed change | `:515` | Verdict → `inbox.json`. Palette-hidden (`when:false`). | core |
| `codoc.rejectProposal` | codoc: Reject proposed change | `:516` | As above. Palette-hidden. | core |
| `codoc.acceptAll` | codoc: Accept all proposed changes | `:517` | Bulk verdict. Palette-hidden. | core |
| `codoc.rejectAll` | codoc: Reject all proposed changes | `:518` | Bulk verdict. Palette-hidden. | core |
| `codoc.openRef` | codoc: Open code reference | `:523` | Navigate to `file#symbol`. Palette-hidden; the target of every citation link. | core |
| `codoc.navigateToFeature` | codoc: Navigate to feature | `:655` | By title or id. Palette-hidden. | useful |
| `codoc.openFirstBinding` | codoc: Open First Code Binding | `:620` | ⌥B from the raw editor. | useful |
| `codoc.pickBinding` | codoc: Pick code binding to open | `:597` | Quick pick over a feature's bindings. Palette-hidden. | useful |
| `codoc.find` | codoc: Find in Tree | `:649` | ⌘F → webview find widget. | core |
| `codoc.replace` | codoc: Replace in Tree | `:652` | ⌘⌥F → find with replace row. The only place the tree can be searched or renamed, since `tree.codoc` is a read-only export. | core |
| `codoc.acceptHunkAtCursor` | codoc: Accept Change at Cursor | `:772` | Verdict on the raw-text diff hunk under the cursor. | redundant |
| `codoc.rejectHunkAtCursor` | codoc: Reject Change at Cursor | `:773` | As above. | redundant |
| `codoc.collapseAllFeatures` | codoc: Collapse all (table of contents) | `:703` | Raw-text editor title bar (`editor/title` menu, `navigation@1`). | redundant |
| `codoc.expandAllFeatures` | codoc: Expand all features | `:706` | Raw-text editor title bar (`navigation@2`). | redundant |
| `codoc.collapseFeatureSubtree` | codoc: Collapse feature subtree | `:709` | ⌘⇧[ ; raw-text context menu (`codoc@1`). | redundant |
| `codoc.expandFeatureSubtree` | codoc: Expand feature subtree | `:715` | ⌘⇧] ; raw-text context menu (`codoc@2`). | redundant |
| `codoc.nav.nextSibling` | codoc: Next Sibling Feature | `:731` | Raw-text keyboard nav. No keybinding bound. | dead |
| `codoc.nav.prevSibling` | codoc: Previous Sibling Feature | `:736` | As above. | dead |
| `codoc.nav.parent` | codoc: Go to Parent Feature | `:741` | As above. | dead |
| `codoc.nav.firstChild` | codoc: Go to First Child Feature | `:746` | As above. | dead |
| `codoc.bridge.rearm` | codoc: Re-enable code-pane bridge (auto-open) | `:834` | Undo a dismissal of the auto-opening code pane. An escape hatch for a feature that steals a pane. | confusing |

**Keybindings** (4 entries)

| Chord | Command | `when` |
|---|---|---|
| ⌘⇧[ / ctrl+shift+[ | `codoc.collapseFeatureSubtree` | `editorTextFocus && editorLangId == 'codoc'` |
| ⌘⇧] / ctrl+shift+] | `codoc.expandFeatureSubtree` | same |
| ⌥B | `codoc.openFirstBinding` | same |
| ⌘F / ctrl+f | `codoc.find` | `activeCustomEditorId == 'codoc.tree-editor'` |
| ⌘⌥F / ctrl+h | `codoc.replace` | same |

Three of five are gated on the raw-text editor.

**Settings** (`contributes.configuration.properties`)

| Setting | Default | Meaning | Tag |
|---|---|---|---|
| `codoc.rootDir` | `""` | Root of the codoc-initialized repo; auto-detected when empty. | useful |
| `codoc.foldAttributesOnOpen` | `true` | Collapse each feature's attribute block on open, so the file reads as a table of contents. Raw-text editor only. | redundant |
| `codoc.focusDependencies` | `true` | Dim unrelated features while the cursor rests on a node with dependency edges. Raw-text editor only (`providers/focus.ts`); duplicated by the webview's own Focus toggle. | redundant |
| `codoc.agentGutter` | `true` | Gutter marker on features the agent is editing. Raw-text editor only (`providers/agent.ts`). | redundant |
| `codoc.serverPath` | `""` | No description at all in the manifest. | dead |

`configurationDefaults` for `[codoc]`: `editor.wordWrap: on`, `wrappingIndent: deepIndent`,
`inlayHints.enabled: on`, `guides.indentation: true`, `guides.highlightActiveIndentation: true`
— all raw-text editor settings. **redundant**

**Walkthrough** `codocSetup` "Get started with codoc": two steps, "Set up codoc"
(completes on `onContext:codoc.ready`) and "Open your feature tree" (completes on
`onCommand:codoc.open`), each with a markdown media file under `media/walkthrough/`. **useful**

### 4.2 The raw-text `tree.codoc` editor stack (`vscode-codoc/src/providers/`)

Context: `tree.codoc` is a daemon-written read-only derived export (CLAUDE.md;
`webview/find.ts:5-9` — "the raw text editor, where VS Code's Find widget would work,
was never a place you could actually change anything"), and `webview/doc-view.ts:1437`
records that the `⇄ text` toggle was removed because "the webview is the single surface,
D1; the raw-text editor is still reachable via Reopen Editor With… → Text Editor".
Every provider below therefore decorates a file the user cannot meaningfully edit.

| File | What it gives the user | Tag |
|---|---|---|
| `code-lens.ts:1` | On a SOURCE file: one lens per symbol bound to a feature with queued work (`codoc: ⟳ will change`). Serves the webview flow too. | useful |
| `bridge-lens.ts:1` | On the bridged source file: `◇ implicated by "<title>" — handing off will rework this` while editing, `◆ queued — run /codoc:sync` once committed. Open vs filled diamond is the doc-ahead glyph at its lifecycle weight. | useful |
| `bridge-controller.ts:1` | Opens the feature's primary binding file BESIDE (non-focus-stealing) while you type its prose and lights the implicated decl lines green; caret-leave clears the highlight but the pane stays open. A feature with no symbol-level binding gets a file-level "new code will be added here" marker. | useful |
| `past-content.ts:1` | Read-only `git show` content provider — the "before" side of the History stance's code diff. Deliberately shells out rather than depending on the git extension's activation timing. | useful |
| `file-decoration.ts:1` | Explorer badge + colour on any file in `activity.touched`; cleared when the epoch closes. Independent of the raw editor. | useful |
| `codoc-tree-lens.ts:1` | In the raw editor: a status line on row 0 plus Accept / Reject above every proposal hunk, plus Accept all / Reject all. | redundant |
| `code-actions.ts:1` | Lightbulb quick-fix Accept / Reject, recovering the event id from the hunk text or the sidecar's `by_feature` map. | redundant |
| `completion.ts:1` | Typing `[` offers every known symbol and inserts `[label](codoc:file.py#symbol)`. | redundant |
| `doc-links.ts:1` | Makes `codoc:` citations clickable via `codoc.openRef`. | redundant |
| `hover.ts:1` | Tier-1 hover card on a citation: owning feature's title, one-line gist, binding count or a `plan` marker; dead refs render an explicit unresolved state. | redundant |
| `inlay.ts:1` | One `N refs` chip per feature; click for a quick pick. | redundant |
| `folding.ts:1` | Attribute-only folding, so Fold All yields a clean title outline. | redundant |
| `feature-lines.ts:1` | Shared title-line / diff-hunk regexes. | redundant |
| `symbol.ts:1` | Document symbols from title lines. | redundant |
| `decoration.ts:1` | Hides `⟨f-…⟩` / `⟨e-…⟩` ids, marks dead refs, draws drift and proposal kinds. | redundant |
| `focus.ts:1` | Cursor-driven dependency dimming (1.0 vs 0.45 opacity, 120 ms debounce). | redundant |
| `agent.ts:1` | Static gutter marker on features the agent is touching. | redundant |

### 4.3 Webview toolbar (`vscode-codoc/src/webview/doc-view.ts:1283`, `renderToolbar`)

| Control | Line | What it does for the user | Tag |
|---|---|---|---|
| Path + status chip | `:1284-1295` | `repo / .codoc / tree.codoc`, plus the lifecycle dot and label. | core |
| **Focus** ◎/◉ | `:1303` | Dims tree rows outside the selected feature's dependency neighbourhood (depends-on + used-by). Replaced an earlier dependency-flow panel. Off by default. | useful |
| **Glance** ◿/◢ | `:1320` | Collapse every feature to its one-line pitch, tree-wide. Persisted per workspace. | confusing |
| **History** ◎/◉ | `:1332` | Turns on inline per-span blame and the timeline scrubber. Persisted per workspace. | useful |
| Language switcher | `renderLangSwitch`, `:1064` | Shows what codoc authors in; the menu writes `.codoc/config.json` (stage 1) then offers a standing "Translate N nodes" row (stage 2). Read-only on the hub. | useful |
| **Catch up (N)** ⟳ | `:1352` | Appears only when Loop A rewrote descriptions the reader has not resolved. Clicking walks to the next one past the current selection. Deliberately a line, not a panel — "a log is a place you have to remember to visit, and nobody visits it". | core |
| **Keep all (N)** | `:1385` | Acknowledges every unasked rewrite at once without changing a word; counts on the button how many displaced the author's OWN wording. Restore stays per-node on purpose. | useful |
| **Accept all (N, M to build)** | `:1398` | Bulk accept, with M = how many of the pending proposals ask the agent to write or delete code, stated on the button because "Accept all (7)" hid that. Hover names the origin breakdown. | core |
| **Reject all (N)** | `:1414` | Discard every pending proposal; nothing is written. | core |
| **Commit & send (N)** ✈ | `:1424` | The single hand-off gesture: flush the latest keystroke, settle, and hand the staged code-implying edits to the agent. Equivalent to ⌘S. Shown only while the daemon holds such edits. | core |

### 4.4 Webview decorations — what each one shows

All under `vscode-codoc/src/webview/tiptap/` unless noted.

- **glance** — `glance-decorations.ts:1`. GLANCE MODE. A decoration, never a doc edit:
  `tree.doc.json` and `tree.codoc` are byte-identical whether it is on or off. Sets
  `body.glance` (CSS hides description paragraphs) and drops one inert widget per
  heading carrying the feature's one-line pitch from the sidecar's `FeatureMeta.pitch`.
  When the pitch equals the title the widget is omitted, so on a tree whose pitches fall
  back to titles the toggle looks like it merely deleted the prose. The name explains
  nothing about what it does. **confusing**
- **reveal** — `reveal-decorations.ts:1`. While `sync.phase[fid]` is `editing`/`reflecting`
  the description renders as dim ghost prose; on `done` it resolves word by word
  (22 ms stagger, 420 ms tail, max 140 words). Its own header calls it "the body-prose
  counterpart" of the activity dot — the same fact, drawn twice. **confusing**
- **ribbon** — `agent-ribbon.ts:1`. One quiet line under a heading the agent is working
  in: "Claude · reading sessions.py", from `activity.json` steps via `payload.sync.steps[fid]`.
  Renders only the CURRENT step (stacking them "turned a liveness cue into a block of
  text"). Ticks to a check, holds 700 ms, collapses. Duplicates the presence avatar's
  whisper. **redundant**
- **busy** — `busy-decorations.ts:1`. Shimmer over a feature being rewritten under the
  reader, from two producers: `translating` (a `codoc translate` batch has it pending)
  and `applying` (the agent is reflecting into it). It is also an EDIT GUARD: user
  transactions whose steps touch a busy range are dropped via `filterTransaction`, while
  programmatic `REFLECT_META` transactions pass. Per-section, so a long translation
  never turns the whole document read-only. **core**
- **activity** — `activity-decorations.ts:1`. Small pulsing dot after the heading while
  the agent edits that feature's code; hollow dot while it reflects the work back;
  cleared on `done` or a closed epoch. No hue — colour is reserved for direction. **core**
- **hold** — `hold-decorations.ts:1`. The "queued for the agent" CHIP and the ✕ that
  withdraws it; a pure projection of the daemon's hold set (`payload.awaitingAI`, from
  `codoc/loop/edits.py:hold_set`). The chip's ink is the ink of whoever is waiting. Its
  header records what was DELETED and why: a margin rail and a changed-range underline
  that were the settlement model's human channel drawn a second time, in a different
  colour, from a different baseline — and which, with no baseline, fell back to marking
  the author's `**bold**` runs, so a comment "lit up random emphasis as though it were a
  pending change". **core**
- **auto-edit** — `auto-edit-decorations.ts:1`. The Keep / Restore verdict on the one
  automatic op that changes what the document SAYS (a small Loop A amend). The diff half
  moved to settlement; two compensating heuristics (`locallyEdited`, `arrivedAs`) went
  with it. The asymmetry is deliberate and stated in the buttons: Keep costs nothing,
  Restore is a real edit routed back through the authored-command channel. **core**
- **consult** — `consult-decorations.ts:1`. Underlines `[label](https://…)` links because
  they silently become `Consult:` instructions the realizing agent WebFetches. Mirrors
  `codoc/codoc_file/parse.py:_LINK_RE` exactly; `codoc:` links are excluded by requiring
  an http(s) scheme. `> …` is deliberately NOT decorated, since that path is dead —
  "a cue for a dead path is worse than no cue". A decoration, not a schema mark, so the
  text stays literally what the daemon parses. **useful**
- **ask** — `ask-decorations.ts:1`. The `/codoc:ask` walkthrough drawn in place. Four
  decorations per step at most: a group widget before the heading (only on the step that
  opens a run), an ordinal chip on the heading, a note widget after it with an optional
  jump to code, and a graphite wash on the quoted span. Carries NO hue, because every
  other colour means something in the lifecycle grammar and a walkthrough is not a
  lifecycle state. The wash is suppressed on features already carrying a diff. **useful**
- **settlement** — `settlement-decorations.ts:1`, model `state/settlement.ts`. The ONE
  layer that draws unsettled text, replacing the drawing half of three plugins. One
  visual axis per channel: human → the INK (blue, pulsing while still yours to send),
  plan → the OPACITY (faded, solider once accepted), code → the GROUND (green behind
  what the codebase added, red behind what it cut). The human channel draws no ghost of
  what you removed, because you removed it. Blue ink on a green ground is impossible by
  construction. **core**
- **node-marker** — `node-marker.ts:1`, model `state/node-status.ts:1`. Up to three
  accumulating glyphs at the end of a heading, each answering a different question in
  the ink its channel owns: ● who is behind, ○ whether it was planned, ± whether it
  diverged. Filling means the claim reached the code. Drawn from the SAME
  `state/settlement.claimsFor` the prose is drawn from, so the margin cannot disagree
  with the text. **core** — but see the vocabulary problem in §8.
- **suggestion** — `suggestion-decorations.ts:1`. The resolution affordances for agent
  proposals. Two rules in its header: the PROPOSAL is drawn, not a card about it (an
  amend shows as tracked changes in the prose it changes, a retire strikes the heading,
  an add is a dimmed placeholder at the rank it would take, at the end of its parent's
  subtree); and ONE resolution surface per feature, a quiet Reject/Accept pair at the
  end of the heading line revealed on hover, after the pair had grown in four places. **core**
- **comment** — `comment-decorations.ts:1`. Now only the shared single-open floating-card
  primitive (`showCard` / `isOpenCard`), reused by the hover card. The comment UI itself
  is the right-margin cards in `whole-doc-editor.ts:1456-1960`. **core**
- **blocks** — `block-decorations.ts:1`. Renders `payload.blocks` per feature as calm
  widgets anchored after the heading. Every widget carries `data-block-id` so identity is
  never inferred from content, and a pure reorder sends nothing. Only text-content,
  lower-capable kinds (diagram, latex) get an editable surface; url/image render
  read-only; an unknown kind degrades to an inert placeholder. **useful**
- **find** — `find-decorations.ts:1`. `.ce-find-match` on every hit, `.ce-find-current` on
  the one you are on. Decorations, never marks — a mark would serialize into
  `tree.doc.json` and turn a search into an edit. Searches feature titles and description
  paragraphs only; ids and `codoc:` targets are addresses, not prose. **core**
- **placeholder** — `placeholder.ts:1`. Two empty states: the whole document ("nothing
  here yet, and here is how the first thing gets made", shown unconditionally) and an
  empty description (shown only on the block holding the caret). They exist because `/`
  and `@` were otherwise undiscoverable, and because a hub contributor cannot run
  `codoc init`. CSS `::before`, so nothing is ever in the document. **useful**
- **drag handle** — `drag-handle.ts:1` + geometry `feature-drag.ts:1`. A grip beside each
  feature plus a drop line. Pointer events, not HTML5 drag-and-drop. Event delegation,
  not a listener per handle. A feature is a heading plus everything under it, so the drag
  moves a SLICE; the existing settle pipeline notices the order changed and emits exactly
  one `move` naming the neighbours it landed between. Handles are widget decorations, so
  they can never be typed into, selected, copied, or serialized. **useful**
- **hover card** — `hover-card.ts:1`. The webview twin of the raw-text hover, on a
  `codeRef` chip or a dependency link, from the same `ResolvedCard` contract so the two
  surfaces agree. Transient DOM only; never dispatches a doc transaction. **useful**
- **blame** — `blame-decorations.ts:1`, model `state/inline-blame.ts`. Per-SPAN authorship
  (not per feature), replayed forward from the revision window's word diffs. A span the
  ledger cannot account for stays UNATTRIBUTED rather than credited to the nearest
  editor. Where one author owns a whole description nothing is drawn at all. **useful**
- **code-ref suggestion** — `code-ref-suggestion.ts:1`. `@` opens an autocomplete over
  the sidecar's bound symbols; selecting inserts a `codeRef` node that serializes back to
  `[label](codoc:file#symbol)`. **useful**
- **decoration policy** — `decoration-policy.ts:1`. Not user-facing, but it explains a
  measured cost: nine layers had independently shipped `tr.docChanged → rebuild
  everything`, so typing one character in a 300-feature tree with History on rebuilt
  ~1200 decorations (7.3 ms vs 2.4 ms to map them). Rebuild only on the layer's own meta
  or a heading-structure change; otherwise map. **core**

### 4.5 Other webview surfaces

- **⌘K command palette** — `webview/palette.ts:1` (pure ranking + contextual command set)
  and `webview/palette-view.ts:1` (the centered card, ↑/↓ move, ↵ run, ⇧↵ secondary, Esc).
  Sections: features, actions, recent, quick, view. Every action maps to an existing host
  message; the palette doubles as the lifecycle legend because rows carry the status
  glyphs. **useful**
- **⌘F find/replace widget** — `webview/find.ts:1` (pure, DOM-free, ProseMirror-free) and
  `webview/find-view.ts:1` (Aa / ab / .* toggles, match counter, ↑ ↓, ✕, replace row
  behind a disclosure). Deliberately shaped like the VS Code find bar. Not modal. **core**
- **Timeline scrubber** — `webview/timeline-bar.ts:1`. One row above the document: a
  caption naming the moment, a native `range` input (so it takes arrow keys, Home/End,
  and announces itself to a screen reader), and a way back to Now. Ticks are painted
  behind it. The detail is a hover card rather than a second row. **useful**
- **Revision page** — `webview/revision-view.ts:1`. The read-only reconstruction the
  scrubber shows, with the change made AT that moment marked where it happened (old words
  struck, new underlined). Deliberately NOT the editor: pushing historical text through
  the settle→command pipeline would record the author reverting every feature in the
  tree. Same pane, measure, typography and scroll position, and the editor's own classes,
  so the two cannot drift into looking like different documents. **useful**
- **Provenance card** — `webview/provenance-card.ts:1`. One card, two callers (the
  timeline and the History stance's per-feature label), because "two cards showing the
  same chain in two shapes is how a reader learns to distrust both". Chain: what changed
  → the directive → the prompt → the session → the base commit → the code diff. Only one
  open at a time. **useful**
- **Presence layer** — `webview/presence-layer.ts:1`. A floating avatar over the doc
  surface (and a label-less twin over the tree pane) that glides to the feature an agent
  is touching, trails a comet tail, and whispers what it is doing (label auto-hides after
  4 s). Reads the same `sync` signal as the activity dot and the ribbon. **redundant**
- **Ask bar** — `webview/ask-bar.ts:1`. A pill anchored over the doc pane (absolutely
  positioned, so it never reflows the document) with a truncated echo of the question and
  a stepper. The answer is not printed here; hovering reveals question + answer in the
  shared popover. Host-agnostic, so it works in both homes. **useful**
- **Viewer status chip** — `webview/viewer-status.ts:1`. Renders NOTHING in VS Code. On
  the hub it states your role and whether your last edits landed, and shows a notice when
  something was refused — because previously a read collaborator's settle came back 403
  and was silently dropped, and an offline hub looked exactly like a working one. **core** (hub)
- **Comment threads** — `whole-doc-editor.ts:1456-1960`, model `state/comment-model.ts:1`,
  assembly `state/threads.ts:1`. Selection bubble → in-margin composer aligned to the
  captured range. A thread carries its text, the `codoc:` citations in the commented span
  as the directive's `Edit only:` scope, the sentence it replies to, its scope, and its
  outcome. **"Build it"** (`:1698-1706`, `:1888`) sends the note with `scope:'both'`, asks
  for the prose to follow, and launches `codoc realize` in a terminal. Cards hang in
  existing whitespace; where the margin is too narrow the anchor opens a popover instead
  (`marginFits`, `:1955-1960`). The "Connections" strands line under a feature merges
  feature→feature edges (ranked by coupling weight), bound code, and external Consult
  links, each strand collapsing independently past `THREADS_COLLAPSE_AT`. **core**
- **Minimap rail** — `state/feature-state.ts:202-250`. One tick per feature down the
  doc's right edge, `railState` delegating to `featureState` plus `busy` and `retired`
  (it used to be a parallel copy that had drifted). Eleven labelled states in
  `RAIL_STATE_LABEL`, used for the legend popover and each tick's hover. **confusing**
- **⌘S interception** — `webview/save-chord.ts:1`. The save chord is swallowed from ANY
  focus context and repurposed as a commit, because `tree.codoc` is a derived read-only
  export and a native save would only flash the "content is newer" dialog. **core**
- **Per-feature version gate** — `webview/doc-gate.ts:1`. Not a visible feature, but the
  reason editing is safe: adoption is decided per feature by its HLC version, so a
  daemon-pushed projection advancing feature B cannot revert your in-flight edit to A. **core**
- **Command emitter** — `webview/command-emitter.ts:1`. On the hub the BROWSER emits the
  identity-keyed commands, because it is the only party that sees a projection. Fixes a
  bug where a remote contributor's prose went nowhere AND stalled the surface it was
  made on. **core**

### 4.6 Webview → host messages (`webview/protocol.ts:324-455`)

`auto-edit-seen`, `ready`, `doc-settle` (debounced capture), `commit` (⌘S: stage AND
send), `withdraw-realization`, `hand-off`, `tree-move`, the identity-keyed command union
(`add` / `set_title` / `set_description` / `move` / `retire`, network home only),
`open-binding`, `open-link`, `open-code-diff`, `open-session`, `verdict` (with optional
`edits` carrying the author's amendments to an EDITABLE ghost), `auto-edit-verdict`,
`translate-tree`, `comment-create` (with optional base64 screenshot), `comment-edit`,
`comment-resolve`, `launch-agent`, `set-pref` (`glance` | `blame`), `set-doc-language`,
`bridge-open`, `bridge-dim`, `ask-dismiss`, `block-edit`.

Host → webview (`:457-465`): `doc`, `code-touch` (with `big` for changes large enough to
re-question the prose), `find`.

---

## 5. Blocks (`codoc/blocks/`)

### 5.1 The plugin API (`base.py:1-40`)

"Bidirectional codec" is the MAXIMAL shape; the contract's job is to let a plugin
declare which directions it supports. Three capabilities:

- **`Capability.LIFT`** — code → block. Loop A refreshes the block when bound code
  changes. Read-only on code, idempotent, ungated (it is attribution, and runs even on a
  held feature).
- **`Capability.LOWER`** — block → code. Loop B turns a human edit into a realize
  directive. Never mutates code; hold-gated; ambiguous → held draft.
- **`Capability.CONSULT`** — block → realization context. Passive input the agent reads
  before implementing. No round trip.

Two safety rules are encoded by the TYPES, not by convention: `lift` returns a
`LiftResult` (content only — `no_change()` / `refresh(content)`), so it cannot emit a
directive; `lower` returns a `LowerResult` (`directive` / `draft` / `noop`), so it cannot
mutate code.

Other declarations: `BindingMode.BOUND | AMBIENT`, `Dispatch.DETERMINISTIC | AGENT`,
`BlockLifecycle.PERSISTENT | TRANSIENT`. `LiftContext` carries the feature, its bindings,
an opaque `code_context`, the prior block, a READ-ONLY store handle, and `codoc_dir` for
resolving repo-relative media.

`registry.py:1-45` validates at registration time: a plugin that declares a capability
without OVERRIDING its base method raises `PluginContractError`, so a contract violation
fails at startup rather than silently no-oping mid-loop. An unknown `kind` raises
`UnknownBlockKind`; hosts degrade to an inert placeholder and the loops skip it.

### 5.2 Builtin kinds (`builtins.py:1`)

Registered idempotently, each guarded independently so a missing optional dependency
never blocks the others.

| Kind | File | Capabilities / mode | What it does | Tag |
|---|---|---|---|---|
| `prose` | `prose.py:1` | LIFT + LOWER, plugin-zero | The implicit block-zero backed by `feature.description`, not a row in the `blocks` table. Registering it makes the DEFAULT path itself a plugin so the registry has no special-cased "real" path beside it. Deliberately a thin shell: the real lift is Loop A's LLM pass, the real lower is Loop B's directive builder. | core |
| `diagram` | `diagram.py:1` | LIFT + LOWER, BOUND, deterministic | A mermaid flowchart of the feature's bound code and its 1-hop dependency neighbourhood. Lift is deterministic (no LLM) and re-runs on Loop A. Lower diffs the old vs new edge set, and that delta becomes a precise directive; an unmappable edit (freeform node, unparseable diagram) returns a `draft` rather than a guess. The only true bidirectional non-prose medium. | useful |
| `url` | `reference.py:1` | CONSULT + LIFT | Fetches and caches an excerpt ONCE, through the SSRF chokepoint, so both a human reader and the agent's `consult` get real content. Content is a small JSON envelope (`url/title/excerpt/status/fetched_at/attempted_at`); a failed fetch is cached with a cooldown so a network call inside Loop A never becomes a per-pass stall. | useful |
| `pdf` | `reference.py:1` | CONSULT + LIFT | As url, for a locally-attached PDF. | useful |
| `image` | `reference.py:1` | CONSULT only, AMBIENT | Visual, not text to extract; a regeneration path is explicitly future work. | useful |
| `latex` | `reference.py:1` | CONSULT only, BOUND | A formula the agent should see as context. No round trip, no lift. | confusing |
| `screenshot` | `screenshot.py:1` | CONSULT only, AMBIENT, TRANSIENT | The bug screenshot dropped in a comment thread. The cheapest medium to add: no codec. Attached to a one-shot steer, read as consultation, and DISCARDED on the next render — excluded from the sidecar's `blocks` slice by being transient. | useful |

### 5.3 How a block is created, written back, and exported

- **Created in the UI.** `tiptap/block-slash-menu.ts:1` holds the pure catalog and fuzzy
  filter (Diagram / Image / Formula / Link, each with a Phosphor glyph);
  `tiptap/block-suggestion.ts:1` is the live Suggestion wiring. That second header records
  that the menu "existed as pure, unit-tested logic in block-slash-menu.ts but was never
  wired into the live editor, so there was previously no way to create a block through
  the UI at all". Typing `/` at the start of an empty line inside a description opens the
  popup; picking a kind removes the trigger text and posts a `block-edit` with
  `action:'add'`. Blocks are NOT ProseMirror doc nodes, so picking never inserts document
  content. `image` and `pdf` open a native file dialog instead; cancelling creates
  nothing. **useful** feature, **redundant** two-module split.
- **Edited.** A diagram/latex block blurs → posts `block-edit` `action:'edit'` with prior
  + new content; Loop B's `lower` dispatch turns the delta into a scoped directive.
- **Agent write-back.** There is NO MCP tool for blocks. An agent reaches them only
  indirectly: Loop A's lift pass (`refresh.py:1`) re-derives persistent LIFT blocks from
  fresh code, enforcing attribution-not-intent (lift only replaces content) and doc-wins
  (a block on a HELD feature is SKIPPED so a code-derived refresh can never clobber an
  in-progress human edit). Lift never CREATES a block — authoring is a host action.
- **Fetch boundary.** `fetch_guard.py:1` is the one SSRF chokepoint, called only from a
  plugin's `lift` on the maintainer's own daemon, never from `codoc/serve/*`. Scheme
  allowlist, DNS-resolve-and-reject-private, manual redirect handling with re-validation
  per hop, capped streamed read. The residual TOCTOU gap is documented rather than
  assumed away.
- **Exported / host parity.** `conformance.py:1` defines `canonical_block_view` — the
  normalized per-feature view every conforming host must reproduce from a fixture
  sidecar (`tests/fixtures/blocks_conformance.json`), ordered by `ord`, each block
  carrying `id/kind/content/lifecycle/provenance/ord`. Tolerant of a v5 sidecar with no
  `blocks`. A host that drops, mistypes, or reorders is caught by the diff.

---

## 6. The deployed hub (`codoc serve`, `codoc/serve/`)

### 6.1 Roles and capabilities (`auth.py:1-50`)

A visitor signs in through a GitHub App (auth-code + PKCE web flow) and their
repo-collaborator permission decides their capability:

| GitHub permission | Capability | Can |
|---|---|---|
| read, triage | `SUGGEST` | suggest, comment, withdraw their own |
| write, maintain, admin | `HANDOFF` | all of the above, plus accept, hand off, write verdicts |
| not a collaborator | `NONE` | denied |

The permission check runs with the MAINTAINER / App-installation identity, never the
visitor's token, because the GitHub endpoint requires the caller to have push access.
The browser holds only an opaque HTTP-only cookie (`codoc_session`, default TTL 8 h) —
never a GitHub token. `github_auth.py:1` is the live edge; `auth.py` is the pure,
mockable decision layer.

### 6.2 What a remote user can do (`dispatch.py:1-15`)

Every `WebviewMessage` a browser posts is routed through one capability gate. Two
invariants:

1. **Capability gating** — SUGGEST may settle / comment / withdraw-own; only HANDOFF may
   write verdicts or hand off; NONE is denied everything.
2. **Safe-by-default settle** — a remote `commit` (the editor's Save gesture) is treated
   as a HELD settle, never auto-sent. The ONLY suggestion→execution crossing is the
   explicit `hand-off` command.

### 6.3 Realization path (suggestion → PR)

`realize_trigger.py:1` fires only on directives the daemon marked `handed_off`; held
drafts are excluded. The daemon's `--auto-realize` is DISABLED on the hub — the server
owns the trigger. `realize_hub.py:1` chains `ready_directives → filter_undone →
realize_directive → mark_done`. `realize_pr.py:1` realizes each directive OFF the live
working tree on a dedicated git worktree and feature branch, then, from the ORCHESTRATOR
(not the agent), commits and opens a PR against the base branch. Invariants: the agent
process holds NO GitHub token; code lands as a PR, never a push to base; a write outside
the directive's `Edit only:` scope ABORTS PR creation.

`realize_agent.py:1` binds the policy onto the SDK run. `sandbox.py:1` is the pure
policy: a minimal `ALLOWED_TOOLS` with Bash excluded; `edit_allowed` requires the target
inside the directive scope and outside a denylist (`.github/`, `.claude/`, `.codoc/`,
`.mcp.json`, package manifest and lockfiles — the paths a malicious suggestion would use
to persist past PR review); `is_secret_path` refuses reads of `.env`, `*.key`, token
caches. `consult.py:1` gates WebFetch with a default-EMPTY host allowlist and a
resolve-and-pin public-IP check. `budget.py:1` caps per-session cost, tool calls,
consecutive failures (circuit breaker), and wall time.

### 6.4 Supporting modules

- `payload.py:1` — derives the browser `DocPayload` purely from the daemon's file outputs
  (sidecar, `status.json`, `tree.doc.json`, `edits.json` drafts). A SECOND derivation
  beside the VS Code `buildPayload`, deliberate and documented at `:11-14` because the
  VS Code one mixes in webview-only in-memory state a fresh browser load lacks.
- `push.py:1` — SSE. The daemon re-renders whole files, so the hub pushes a full
  version-guarded payload, with a same-bytes dedup guard so a normalization delta cannot
  fan out to every connected browser.
- `supervise.py:1` — atomic single-owner claim on `.codoc/serve.lock` via `O_EXCL`; a
  stale lock (dead pid) is reclaimed, a live foreign owner respected. Spawns `codoc watch`
  with `CODOC_WATCH_OWNER` + `CODOC_WATCH_PARENT_PID` so the daemon self-exits if the hub
  dies.
- `ratelimit.py:1` — dependency-free token bucket per GitHub identity; 429 when dry.
  Each write wakes the daemon and fans out to every browser, so an unbounded rate is both
  a daemon DoS and a broadcast amplifier.
- `media.py:1` — serves a bare filename from exactly `<codoc_dir>/media/`, restricted to
  the extensions codoc's own writers produce. NO auth gate, matching `/api/payload`.
- `tunnel.py:1` — builds and spawns a Cloudflare named-tunnel launcher; Access policy is
  configured out of band.
- `static.py:1` — serves the same esbuild bundle as the VS Code webview
  (`vscode-codoc/dist/webview/`); resolution order is `--static-dir`, `$CODOC_STATIC_DIR`,
  then the dev build location.

### 6.5 Endpoints (`app.py`)

`/healthz` `:92` · `/api/whoami` `:97` · `/auth/login` `:106` · `/auth/callback` `:124` ·
`/api/logout` `:152` · `/api/command` `:159` · `/api/payload` `:196` ·
`/api/media/{name}` `:207` · `/api/events` `:221` · SPA catch-all `/{full_path}` `:243`.

**Tag: useful** as a capability, **confusing** in its current state — `codoc serve`'s own
docstring says the read endpoints are unauthenticated, which contradicts the elaborate
auth layer gating the write ones. A user cannot tell from the UI which half applies.

---

## 7. Translation and doc language (`codoc/doclang.py`)

`doclang.py:1-19` opens by warning that two things share the word "language":
`codoc/lang/` is PROGRAMMING languages (tree-sitter adapters); this module is the
AUTHORING language of the tree. Orthogonal — a Python repo can have a Mandarin tree.

- **The setting lives in `.codoc/config.json`**, the one `.codoc/` file tracked in git,
  because if it lived only in an env var a contributor running `codoc watch` without it
  would append English into a Chinese tree — "silent corruption of authored intent, which
  is the one thing codoc is supposed to protect". `CODOC_DOC_LANGUAGE` only overrides.
  **core**
- **`codoc lang [tag]`** (`main.py:729`) — show or set. Reports the tree's actual language
  MIX (`_echo_language_mix`, `main.py:861`), not just the setting. **useful**
- **`codoc translate`** (`main.py:779`, `codoc/loop/translate.py`) — one LLM pass per batch
  of features, copying every `codoc:` citation, external link and `**bold**` focus span
  through unchanged. REFUSES a node whose translation dropped one of those, or whose
  title would collide with a sibling's, and preserves each node's `feature_writers` role
  so a human-authored node keeps the strict amend gate. Idempotent: selection is by
  DETECTED language, so an interrupted run is safe to re-run. Previous wording stays in
  the change ledger. **useful**
- **Toolbar switcher** (`doc-view.ts:1064`, `webview/doc-lang.ts:1`) — two staged actions:
  `set-doc-language` writes `config.json` (changing what NEW prose comes out in, never
  rewriting what is there), then a standing "Translate N nodes" row offers stage 2.
  Read-only on the hub. The menu mirrors `codoc/doclang._PROFILES` (parity tested) but is
  "the menu, not the limit" — any BCP-47 tag works via `codoc lang`. **useful**
- **Bilingual by design.** The setting says what codoc ORIGINATES prose in; what the tree
  CONTAINS is observed per node (`detect_prose_language`). Editing existing prose keeps
  the language that prose is already in. Chinese prose carrying English API terms is
  correct writing and nothing flags it. The sidecar and MCP rows carry a `lang` tag only
  for nodes that DIFFER, and the webview stamps it as an `lang` ATTRIBUTE, not a class,
  because the browser reads the attribute for per-element font fallback, line breaking
  (a CJK line may break between any two characters) and quotation conventions. **core**
- **Prompt plumbing.** Four prompts carry a `{{doclang}}` marker expanded into the cached
  prefix (empty for English, so English prompts are unchanged); MCP reads return a
  `doc_language` block (`tools.py:244`) because a coding agent is the one writer with no
  prompt in front of it. `_language_advice` (`tools.py:289`) advises per op. **core**
- **Script-aware helpers.** `norm_key` (NFKC-folds titles so an IME cannot mint a
  duplicate), `terms`, `tokens` (segment per script instead of dropping non-ASCII),
  `clause_chars`, `char_budget`. Deliberately configuration-FREE and per-string, because a
  tree mid-migration holds both languages side by side and `norm_key` runs per feature
  per pass. **core**
- **Live progress.** `.codoc/translate.json`, read by `state/translate-model.ts:1` with a
  300 s lease so a `kill -9` mid-batch cannot leave `running:true` and lock nodes behind
  a skeleton forever; a finished run's summary lingers 120 s. **useful**

---

## 8. Lifecycle and verdict vocabulary the user meets

### 8.1 Pipeline states (`codoc/loop/status.py:1-14`)

Five states answer "where are code and intent relative to each other right now?":
`in_sync`, `code_drift` (Loop A raised proposals awaiting review), `tree_dirty`
(tree edited, code change not yet realized), `awaiting_impl` (accepted edits queued in
`.codoc/realize.md` for `/codoc:sync`), `realizing` (an agent is implementing now, under
a 300 s renewable lease). Surfaced in the status bar, the toolbar chip, `codoc status`,
and `codoc_status`. **core**

### 8.2 Feature state — three overlapping vocabularies

1. **`state/feature-state.ts:1-25`** ranks ONE state per feature, first match wins:
   `working` → `proposed` → `rewritten` → `agreed` → `sent` → `staged` → `planned` →
   `settled` (plus `reflected`). One glyph, one sentence of hover text. Its header
   explains the collapse: a feature could previously wear six markers at once and "the
   row became a legend nobody had".
2. **`state/node-status.ts:1-30`** ACCUMULATES up to three glyphs on the same heading
   (● human, ○ plan, ± divergence), because the settlement channels are not stages of
   each other. The case that proves it: a node planned, then built, and built
   DIFFERENTLY — rank picks one of those three facts and drops the other two. Both models
   are deliberately kept, "each where its shape is true".
3. **`state/feature-state.ts:202-250`** extends the ranking for the minimap rail with
   `busy` and `retired`, giving eleven labelled entries in `RAIL_STATE_LABEL`.

Three answers to "what state is this feature in", two of them by explicit design. This is
the largest single simplification available in the UI. **confusing**

### 8.3 Verdict verbs

- **Accept / Reject** — on any proposal (add, move, retire, amend). Surfaces: the
  per-feature hover pair in the webview (`suggestion-decorations.ts`), the toolbar's
  Accept all / Reject all, the raw editor's CodeLens (`codoc-tree-lens.ts`), the raw
  editor's lightbulb (`code-actions.ts`), the cursor-hunk commands, and `codoc accept` /
  `codoc reject`. An ACCEPT may carry `edits` — the author's amendments to an editable
  ADD ghost. **core** verb, **redundant** surfaces (six).
- **Keep / Restore** — only on an unasked Loop A rewrite. Asymmetric on purpose
  (`auto-edit-decorations.ts:33-40`): Keep costs nothing because the document already says
  this; Restore is a real edit that re-asserts a claim the code may no longer honour, so
  it goes back through the authored-command channel to be reclassified. Keep-all exists;
  Restore-all deliberately does not. **core**
- **Withdraw** — the ✕ on the hold chip. Appends a cancellation to `edits.json`; Loop B
  prunes the directive and releases the hold; the committed prose is KEPT. **useful**
- **Commit & send / hand-off** — ⌘S or the toolbar button. The draft gate makes
  code-implying edits safe-by-default: recorded but not sent until this explicit gesture.
  `hand-off` clears the drafts set, and the daemon's next Loop B pass derives every held
  directive's `handed_off` and writes `realize.md`. **core**
- **Steer** — an inline comment becomes a `STEER FEATURE` directive through `edits.json`
  (`drain_steers`). Scope `code` (change the implementation, leave the prose alone) or
  `both`. A resolved thread lingers about an hour in the margin and then leaves; the
  record is never deleted. **core** — but `state/comment-model.ts:1-21` still describes
  the retired mechanism ("a comment serializes to a `> …` line under its feature in
  `tree.codoc`, and Loop B's existing step-2.7 drain turns it into a STEER directive"),
  which `loop_b.py:1061-1093` and `consult-decorations.ts:12` both say is **dead**.
- **Plan ◇ / ◆** — open diamond while you are still editing the prose, filled once the
  pipeline is awaiting implementation (`bridge-lens.ts:1-12`). Shape carries the phase,
  green carries the direction. **useful**
- **Realize / sync / reflect** — three words for moving work, reachable five ways:
  `codoc realize` (foreground now), `/codoc:sync` (the live session implements the
  queue), `codoc reflect` (recovery-grade code→tree), `codoc watch --auto-realize`
  (`codoc/loop/autorealize.py:1`, the opt-in headless fallback, which that module notes
  "deliberately re-introduces the headless spawn the 2026-05-29 rewrite removed"), and the
  hub's server-owned worker. Two directions, five entry points. **confusing**
- **Direction grammar** (`state/grammar.ts:1-30`) — COLOUR encodes direction, SHAPE
  encodes kind. Only two hues, because the hue answers WHO RESOLVES this: `code-ahead`
  and `yours` are both the reader's to settle (review-blue), `doc-ahead` waits on the
  agent (await-green). `yours` is the third direction and deliberately not a third hue —
  it is `loop_b._resolve_content`'s DEFERRED outcome, where two peers rewrote the same
  lines and neither outranks the other. **core**

### 8.4 The classification table (`codoc/loop/classify.py:1-50`)

Not a UI surface, but it is the ground truth behind every verb above: thirteen numbered
rows mapping (side, detected change, origin) → reaction. Worth keeping intact through any
redesign — row 7 records that doc AMEND mints a HELD draft with NO prose inspection
because "the SYSTEM never guesses intent from English mood", and the note below the table
records that the previous `is_imperative` regex was removed for exactly that reason.

---

## 9. Status bar, `status.json`, and CodeLens

### 9.1 Status bar (`state/status-presentation.ts:1-18`)

Pure and vscode-free so it is testable without the host; `state/workspace-state.ts`
applies the result to the real `StatusBarItem` and turns `warn` into a `ThemeColor`.

Precedence, first match wins:

1. `provisioning` — setup is installing / indexing.
2. `!initialized` — no `.codoc/` yet → offer one-click setup.
3. `daemonDown` — authored edits are sitting in `edits.host.jsonl` with nothing consuming
   them. Outranks every lifecycle state, "because those are read from a `status.json` the
   dead daemon cannot be updating".
4. `agentActive` — a coding agent is touching files, with the file count.
5. lifecycle — `realizing` / `tree_dirty` / `awaiting_impl` / `code_drift` / `in_sync`
   (the `in_sync` label carries the non-retired feature count).

Each view returns text, tooltip, a click command (or none mid-setup), and `warn` — true
for the single "you owe an action" state. It also decides whether an accept just queued
code work that nothing is going to run. **core**

### 9.2 `status.json` (`codoc/loop/status.py`)

Written at the end of every loop pass, so the IDE watches the file and never polls.
Carries `state`, `pending`, a detail string, and the realize queue size counted from
`### ` headings in `realize.md` (`_realize_queue_size`, `:45`). The `realizing` state is
leased (`REALIZING_LEASE_SECONDS = 300`, `:38`) and renewed per directive by
`codoc_realize_progress`, so only a crashed or cancelled pass ever reaches expiry. **core**

### 9.3 CodeLens

- `providers/code-lens.ts:1` — on a SOURCE file, per bound symbol with queued work. **useful**
- `providers/bridge-lens.ts:1` — the live editing companion on the bridged source file;
  additive to the above. **useful**
- `providers/codoc-tree-lens.ts:1` — in the RAW tree editor: a row-0 status line plus
  Accept / Reject over every proposal hunk and Accept all / Reject all. **redundant**

---

## 10. What the code itself argues should go

Places where a module's own header says a feature was a mistake, was replaced, is drawn
twice, or is reachable only for historical reasons:

1. **The raw-text `tree.codoc` editor and its seventeen providers**, on a file that is a
   read-only derived export (`webview/find.ts:5-9`, `webview/doc-view.ts:1437`). Four
   settings, four keybindings, six commands and a `configurationDefaults` block exist
   only to serve it. `code-lens.ts`, `bridge-lens.ts`, `bridge-controller.ts`,
   `past-content.ts` and `file-decoration.ts` serve the webview flow and should survive;
   the other twelve should not.
2. **Presence avatar, agent ribbon, and activity dot** — three renderings of the same
   `sync.phase` / `activity.json` signal (`presence-layer.ts:1`, `agent-ribbon.ts:1`,
   `activity-decorations.ts:1`).
3. **The `reveal` word-by-word fade**, self-described as the body-prose counterpart of
   the activity dot (`reveal-decorations.ts:1`).
4. **`feature-state` versus `node-status`** — two deliberate, documented, coexisting
   answers to "what state is this feature in", plus an eleven-state rail legend
   (`node-status.ts:12-22`, `feature-state.ts:202-250`).
5. **`codoc_propose_add`, `codoc_propose_move`, `codoc_attach`, `codoc_plan_add`** — each
   is a single `codoc_reflect` op with no unique parameter. Only `propose_amend`
   (`builds`) and `propose_retire` (`delete_code`) carry anything reflect cannot express.
6. **`codoc propose` on the CLI** — eleven flags duplicating the MCP proposal surface,
   for a caller its docstring describes as "humans/tests".
7. **`codoc migrate`** — a one-time heal for pre-2026-06 workspaces that still runs on
   every daemon startup.
8. **`codoc.repair`** — same handler as `codoc.setup`.
9. **`codoc export-markdown`** — a second rendering of the tree, undocumented in CLAUDE.md.
10. **Five entry points into realize** (`codoc realize`, `/codoc:sync`, `codoc reflect`,
    `watch --auto-realize`, the hub worker) for two directions.
11. **The `> …` steer path**, dead since U7 (`loop_b.py:1061-1093`) but still described as
    live in `state/comment-model.ts:1-21`'s header.
12. **`codoc.nav.*`** — four commands with no keybindings, on the raw editor.
13. **`codoc.serverPath`** — a setting with no description in the manifest.
14. **`codoc serve`'s auth split** — an elaborate capability layer on the write endpoints
    beside unauthenticated read endpoints, with `--insecure-public` as the acknowledgement.
