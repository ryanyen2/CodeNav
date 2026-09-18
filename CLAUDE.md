# CLAUDE.md

Guidance for Claude Code working in this repository. The design lives in
`docs/substrate/` (read `01-calculus.md` first); this file is the map.

## What codoc is

codoc keeps a human-intent-level view of a codebase as a document of
**features** (a heading plus paragraphs, citing code inline) and keeps that
document in step with the code in both directions while three parties edit it
concurrently: a person, a coding agent, and codoc's own reflective loop.

The substrate is an **attributed edit calculus** (`codoc/calculus/`): a small
set of operations over identified blocks, where every character carries the
op that inserted it. Every surface is a function of the op log. The one rule
that binds the design: **a diff may draw; it never decides what happened.**
The single exception is `ingest`, the boundary where an LLM's or a
translator's whole-text write becomes ops, once, under stated rules.

## Commands

```bash
pip install -e .                 # Python 3.11–3.12; the venv is .venv
codoc init                       # index, bootstrap the tree, install hooks
codoc watch                      # the daemon (the only applier)
codoc status | sync | realize    # where things stand; one pass; build the queue
codoc handoff <feature> --asked  # the CLI's ⌘S
codoc accept|reject <plan>       # verdicts
codoc done <feature>             # mark a request realized when the build moved no bound symbol
codoc history <feature>          # ops and per-word attribution
codoc lang [tag] | translate     # authoring language; convert existing prose
codoc rebuild | snapshot         # replay the logs; fold them
codoc import-v1                  # bring a v1 codoc.db into the log

python3.11 -m pytest tests/      # Python tests (206)
cd vscode-codoc && npx vitest run && npx tsc --noEmit && node esbuild.config.mjs
```

## Layout

```
codoc/calculus/   the kernel: ids, text (segments + marks), doc (blocks, heading
                  fold), ops, store (cache + indexes), apply (20 handlers,
                  two-phase, inverses), ingest (the one diff), derive (views:
                  description, ink/ground/plan claims, phase, directive, history,
                  projection), export (tree.codoc Markdown ⇄ ops), inline_md
codoc/substrate/  clock (HLC), log (session files, inbox, acks), workspace (the
                  applier façade: rebuild, submit, ingest, drain_inbox), client
                  (write through the inbox or apply when no daemon), publish
                  (tree.codoc, projection.json, realize.md, status.json),
                  daemon, snapshot, import_v1
codoc/kinds/      block kinds on the extension interface: Kind (text fields,
                  schema, lift/lower/consult/text_view/write_back), the shipped
                  kinds (diagram, image, url, pdf, screenshot, comment), write
                  (the one boundary turning content into block.set + ingest),
                  lift (the loop's pass), fetch_guard
codoc/loops/      context (ego-graph selection), reflect (code → document),
                  realize (hand-off, queue, settle), bootstrap, translate
codoc/graph/      the code dependency graph (in memory, JSON cache) + flows
codoc/mcp/        the agent's tools: context, tree, status, history, reflect,
                  propose, write_block, realize_progress, walkthrough*
codoc/cli/main.py, codoc/agent/ (hooks, installer, prompts wrappers),
codoc/prompts/, codoc/doclang.py, codoc/lang/ (tree-sitter), codoc/pipelines/
codoc/loop/       KEPT v1 leaves: diff (the index change set), activity (epochs),
                  ask, autorealize, sdk_realize, status (lease), why, gitref,
                  fsio, locks, filenames
vscode-codoc/src/ calc/ (projection types, shadow, recorder, adopt, claims),
                  editor/ (schema, commands, decorations, editor, app), host/
                  (provider, workspace), extension.ts, setup/, daemon/, serve/
```

## Files under `.codoc/`

| file | role |
|---|---|
| `log/<session>.jsonl` | the truth; one file per daemon session; **tracked** |
| `log/snapshot-*.jsonl`, `log/archive/` | folded logs; tracked |
| `tree.codoc` | Markdown export (`## Title {#b-id}`); tracked, derived, never parsed |
| `config.json` | authoring language etc.; tracked |
| `projection.json`, `status.json`, `realize.md`, `activity.json`, `ask.json` | derived or ephemeral; ignored |
| `inbox/<client>.jsonl`, `inbox/<client>.ack.jsonl` | client submissions and the daemon's acks; ignored |
| `cache/` | SQLite/graph/index caches; ignored |

## Rules that are load-bearing

- One applier per checkout: the daemon. Every other process writes through
  `inbox/` (or applies itself when no daemon runs, `substrate.client`).
- Ops name characters by identity `(op, k)`, never offsets. A client may name
  its own unacked characters as `["$cid", cid, k]`.
- A plan is a tag on segments plus `suggest_del` marks, not a branch. Accept
  keeps the agent as author; reject kills by identity.
- Attribution is written by the inserting op. `human:*`, `agent:*`, `loop`,
  `translator:*`, `import`. A role table (`Roles`) decides who may write the
  trunk; a refused op is recorded with `conflict="authority"`.
- No heuristics in the loops for identity or intent. If you find yourself
  adding a ratio, a regex over prose, or a nearest-match, stop and read
  `docs/substrate/01-calculus.md` §1 and `06-cut-list.md`.
- Inverses are recorded at apply; undo is a new op; `replay(log) == store`.

## Tests that pin the calculus

`tests/calculus/test_apply_properties.py`: verdicts resolve by identity under
split/move/merge; a conflict never moves the state; un-applying a suffix
equals replaying the prefix. Keep them green before anything else.
