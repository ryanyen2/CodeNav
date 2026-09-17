# codoc v2 — what survives, what is cut, and why

Source: `05-inventory-v1.md` (every v1 surface, tagged from the code's own
comments) read against `01-calculus.md`. The rule for a cut is one of: the
calculus makes it unnecessary; it is one of several renderings of one fact; it
decorates a surface that cannot be edited; or its own header says it was a
mistake.

## 1. Surfaces

| v1 | v2 | reason |
|---|---|---|
| Codoc Tree webview | **kept**, the only editing surface | |
| raw-text `tree.codoc` editor: 12 providers, 4 settings, 4 keybindings, 6 commands, `configurationDefaults` | **cut** | decorates a read-only export; the webview has every one of them |
| `code-lens`, `bridge-lens`, `bridge-controller`, `past-content`, `file-decoration` | kept | they act on source files, not on the export |
| `codoc serve` hub | kept; **all** endpoints behind the session cookie; `--insecure-public` cut | an auth layer on writes beside open reads is a hole, not a feature |
| Claude Code hooks, `/codoc:sync`, `/codoc:ask`, MCP | kept, MCP surface halved (§4) | |

## 2. Feature state and marks

| v1 | v2 |
|---|---|
| three vocabularies: ranked `feature-state`, accumulating `node-status`, 11-state rail | **one** `phase(f)` enum from the calculus, plus two derived facts (`planned`, `divergent`) the marker may add as badges; rows, rail and marker read the same function |
| settlement (ink / opacity / ground) | kept as the drawing; its three-baseline arithmetic replaced by reading runs and marks |
| inline blame (word-diff replay) | replaced by reading runs |
| `glance` | **cut**; its own header says the name explains nothing and on most trees it looks like the prose was deleted |
| `reveal` word-by-word fade | **cut**; the activity dot drawn a second time |
| presence avatar, agent ribbon, activity dot | **one**: the activity dot with the current step as its hover |
| `busy` shimmer + edit guard | kept; the guard is what stops a person typing into a block a translator is rewriting |
| `hold` chip + Withdraw | kept; Withdraw is the inverse of `feature.handoff` |
| `auto-edit` Keep / Restore | replaced: a loop trunk edit is ground; **Catch up** walks a per-viewer read cursor over unacknowledged loop batches; Keep advances the cursor; Restore is undo of the batch as the person's own op |
| `consult` underline, `ask` overlay, `find`, `placeholder`, drag handle, hover card, comments, blocks, palette, timeline, revision page, provenance card, viewer status, ⌘S chord | kept |
| `retired` lifecycle, `~` marker, struck-through nodes | **cut**: delete is delete, History has it, undo restores it |
| `planned` lifecycle | derived: no bindings and a hand-off newer than the last realization |
| Suggesting stance | **cut**: who edits what decides whether an edit is a plan |

## 3. Verdicts and hand-off

| v1 | v2 |
|---|---|
| Accept / Reject on six surfaces | `plan.accept` / `plan.reject` from the heading pair, the toolbar bulk pair, and the CLI; nothing else |
| accept-time edits to a ghost | editing inside a proposed span is an ordinary op; accept takes what is there |
| Keep / Restore | see §2 |
| Commit & send, ⌘S | `feature.handoff`, unchanged in meaning |
| Steer via `edits.json` | a comment block with `scope=code` |
| `> …` steer lines | already dead; the description in `comment-model.ts` goes with it |
| drafts, intents, handoffs, cancellations, comment_resolves, block_edits, steers channels; `inbox.json`; `edits.host.jsonl`; `inbox.host.jsonl` | **one inbox per client**, one op vocabulary |

## 4. CLI and MCP

| v1 | v2 |
|---|---|
| `init`, `watch`, `status`, `sync`, `realize`, `history`, `lang`, `translate`, `serve`, `install-hooks`, `accept`, `reject` | kept |
| `reflect` | folded into `sync --code` (Loop A only, state-based); the Stop hook calls that |
| `propose` (11 flags) | **cut**; agents use MCP, tests use the library |
| `export-markdown` | **cut**; `tree.codoc` is the Markdown |
| `migrate` | **cut**; v2 has one import path for a v1 workspace: `codoc import-v1` |
| `rebuild` | new: replay the logs into the cache |
| `snapshot` | new: compact the logs |
| MCP reads: `context`, `tree`, `status`, `history`, `walkthrough_read` | kept; `plan_status` folded into `status` |
| MCP writes: `reflect` (trunk, safe ops), `propose` (one tool, opens a plan with any ops), `write_block`, `realize_progress`, `walkthrough`, `walkthrough_clear` | `propose_add/move/retire/amend`, `plan_add`, `attach` fold into `reflect`/`propose`; `await_verdicts` **cut** (a plan ends the turn; acceptance hands off and the existing prompt nudge wakes the session) |
| `/codoc:plan` | kept as a thin prompt over `propose`; the tool-choice argument in its text goes |

## 5. Realize entry points

Two directions, two entries. `codoc realize` and `/codoc:sync` consume the
same queue (foreground engine vs the live session); `codoc watch
--auto-realize` stays the unattended flag; the hub's worker is `codoc realize`
on a worktree. `codoc reflect` is `codoc sync --code`.

## 6. Storage and control files

| v1 (`.codoc/`) | v2 |
|---|---|
| `codoc.db` (authoritative) | `cache/store.db`, rebuildable |
| `tree.codoc` (export, daemon-written), `tree.doc.json`, `tree.bindings.json`, `tree.index.json` | `tree.codoc` (Markdown export, committed) + one `projection.json` for the editor and hub |
| `edits.json`, `edits.host.jsonl`, `inbox.json`, `inbox.host.jsonl` | `inbox/<client>.jsonl` |
| `realize.md`, `realize.json`, `realized.jsonl` | `realize.md` (the rendered prompt; regenerable); outcomes are `feature.realized` ops |
| `revisions.json`, `drift.json`, `resolution.json` | derived from the log |
| `status.json`, `activity.json`, `translate.json`, `ask.json`, `intent.jsonl` | `status.json` and `activity.json` stay ephemeral (lease, epoch, steps); `ask.json` stays ephemeral; `translate.json` becomes a status field; captured prompts ride the hand-off op |
| `config.json` | kept, tracked |
| `log/<session>.jsonl` | new, tracked, the truth |

## 7. Loop internals

| v1 | v2 |
|---|---|
| `phase.py` (eight-way consolidation) | `derive.phase`, one function over the store |
| `classify.py` decision table | kept as the table; rows 7–13 become one derivation from op actor, watermarks and plan tags |
| `apply.py` amend gate (`preserved_ratio`) | the ingest's exact deleted-fraction, with the stricter threshold when the block's last author is a person |
| `title_dedup.py`, `merge3.py`, `migrate.py`, `surface.py`, `reconcile.py`, `revisions.py`, `inbox.py`, `intent.py` | cut |
| `_norm_title` soft identity, `_placeholder_owner` guessing | cut; a plan node is a heading with a plan tag, and the LLM attaches to it by id |
| `divergence.py` | kept (pure over `caused_by`) |
| `doclang.py` helpers (`norm_key`, `terms`, `tokens`, `clause_chars`) | kept where a lexical heuristic remains legitimate: title collision warnings, the ingest's word segmentation, language detection |
| bootstrap (per-file + org pass), `subtree.py`, `why.py`, `gitref.py`, `fsio.py`, `locks.py` | kept |
| `loop_a.py` change set, relocation detection, coverage net | kept; output becomes ops |
| `loop_b.py` (1,979 lines) | replaced by `apply` + `derive.directives` |
| `codoc_file/` (parse, render, diff, doc_render, doc_parse) | replaced by `export.py` (Markdown out) and `import_v1.py` |

## 8. Block kinds

`prose` is no longer a plugin (paragraphs are the calculus). `diagram`, `url`,
`pdf`, `image`, `screenshot` are kept on the v2 kind interface. `latex` is cut;
a formula is a paragraph with inline math until someone needs a kind for it.
The two-module slash menu becomes one.

## 9. Things the study harness depended on

`replay/play.py` and the logger read v1 control files. They are study
artefacts, not product; they stay on `main` and are not ported.
