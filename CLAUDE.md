# CLAUDE.md

## What codoc is

codoc keeps a document of a codebase's features (a heading, then prose, lists, tables,
diagrams and code that cite the code) in step with the code in both directions. A person,
a coding agent and codoc's own loop edit it at the same time. The document is an op log:
every edit is an op, one daemon applies ops in order, and every view (the editor, the
export, the agent's prompt) is computed from the state the log produces.

## Commands

```bash
pip install -e .                 # Python 3.11-3.12
codoc init                       # index, build the first tree, install the Claude Code hooks
codoc watch                      # the daemon, the only applier
codoc status | sync | realize    # where things stand; one pass; build the queue now
codoc handoff <feature> --asked  # hand a feature off (the editor's ⌘S)
codoc accept | reject <plan>     # verdicts; accepting a codoc_propose plan also hands off
codoc done <feature>             # mark a hand-off realized when the build moved no bound symbol
codoc history <feature>          # ops and per-word attribution
codoc lang [tag] | translate     # the authoring language; convert existing prose
codoc rebuild | snapshot         # replay the logs; fold them
codoc validate-logs              # check session logs a merge would add
codoc serve                      # the hub
codoc install-hooks              # the hooks, /codoc:* commands and MCP registration
```

## Packages

| package | holds | may import |
|---|---|---|
| `calculus/` | document model, applier, ingest, Markdown codecs, views | nothing in codoc |
| `workspace/` | the `.codoc` directory: layout, config, clock, log, inbox, agent epoch, snapshot, roles, Workspace, log validation | calculus |
| `index/` | language adapters, chunk index, change set, dependency graph, git facts | nothing in codoc |
| `llm/` | provider, prompt loading, the LLM passes | doclang |
| `kinds/` | block kinds | calculus, workspace |
| `loops/` | bootstrap, reflect, realize, translate, context, edits (tree edits to ops) | all above |
| `daemon/` | daemon, client, reads, publish, pidfile | all above |
| `claude/` | Claude Code hooks, installer, plugin, SDK realize | daemon and below |
| `cli/`, `mcp/`, `serve/` | surfaces | daemon and below; `cli` also `claude`, `mcp`, `serve` |

`doclang.py` (the authoring language) imports `workspace`. `codoc/prompts/` holds the
prompt templates. `vscode-codoc/src/` is the editor and the VS
Code extension ([docs/editor.md](docs/editor.md)).

## Files under `.codoc/`

| file | git | role |
|---|---|---|
| `log/<session>.jsonl` | tracked | The op log, one file per session. The truth. |
| `log/snapshot-<hlc>.jsonl`, `log/archive/` | tracked | A folded prefix of the log; the files it folded. |
| `tree.codoc` | ignored | The Markdown export. Derived; never read back. Ignored because the daemon rewrites it on every edit, which would block a pull, a checkout or a `stash pop`. |
| `config.json` | tracked | `doc_language`, `roles`, `index_exclude`. |
| `inbox/<client>.jsonl`, `inbox/<client>.ack.jsonl`, `inbox/offsets.json` | ignored | Submissions, acks, drain offsets. |
| `projection.json`, `history.json`, `status.json`, `realize.md` | ignored | Derived. |
| `activity.json`, `watch.pid`, `applier.lock`, `serve.lock` | ignored | Local state. |
| `cache/`, `cocoindex.db`, `lancedb/` | ignored | The code graph and the chunk index. |
| `media/` | tracked | Files that image, screenshot and pdf blocks reference. |

## Rules

- One applier per checkout: the daemon. Every other process writes through `inbox/`, or
  applies under `applier.lock` when no daemon runs (`daemon/client.py`).
- Ops name characters by identity `(op, k)`, never by offset. A client names its own
  unacked characters as `["$cid", cid, k]`.
- `ingest` is the only place a diff decides what happened. Content from a model, a
  translator or an agent becomes ops there, or through `block_bodies` for new blocks.
- A plan is a tag on segments plus `suggest_del` marks and `suggest_mark` records, not a
  branch. Accept keeps the proposer as author; reject kills by identity.
- Every character records the op, actor and clock that minted it. The role table in
  `config.json` decides who may write; a refused op is recorded with `conflict:
  "authority"` and replay does not ask again.
- Inverses are recorded at apply; undo is a new op; replaying the log rebuilds the store
  (I6).
- No heuristics for identity or intent: identity is never inferred from text similarity
  or a nearest match, and intent never from a regex over prose. An anchor that does not
  resolve is a recorded conflict (I5).
- No op touches a source file (I7). A hand-off is the gesture that turns words into a
  request.

## Specs

| file | covers |
|---|---|
| [docs/model.md](docs/model.md) | The document model, ops, conflict codes, the Markdown form, views, invariants. |
| [docs/concurrency.md](docs/concurrency.md) | Processes, files, sessions, replay, roles, conflicts, plans, git, snapshots. |
| [docs/workflows.md](docs/workflows.md) | The two loops and each gesture: what is recorded, what the person sees. |
| [docs/kinds.md](docs/kinds.md) | Block kinds and their codecs. |
| [docs/editor.md](docs/editor.md) | The ProseMirror editor. |
| [docs/architecture.md](docs/architecture.md) | Modules, the agent integration, the hub, environment variables. |

## Tests

```bash
env -u OPENAI_API_KEY -u ANTHROPIC_API_KEY .venv/bin/python -m pytest tests -q -p no:cacheprovider
ruff check --select F codoc tests
cd vscode-codoc && npx vitest run && npx tsc --noEmit && node esbuild.config.mjs
```

No test calls a model; unsetting the keys keeps it that way. `tests/loops/test_roundtrip.py`
runs the extension's vitest suite and skips when `vscode-codoc/node_modules` is absent.

`tests/` mirrors the packages; shared builders are in `tests/helpers.py`, and no test module
imports another. `tests/calculus/test_apply_properties.py` pins the calculus:
verdicts resolve by identity under split, move and merge; a conflict never moves the state;
un-applying a suffix equals replaying the prefix. Keep it green before anything else.
