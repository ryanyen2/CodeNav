---
description: |
  Read and update the codoc feature tree through the codoc MCP tools. Use when:
  - you changed code and the tree should say so
  - the user asks to add, change or remove a feature
  - you need to know which feature owns a piece of code
  - the user mentions "codoc", "feature tree" or "plan"
---

# codoc

This repo has a codoc feature tree: an outline of what the code is for.

- A feature is a heading with a description under it and the code symbols it owns (`file.py::symbol`).
- The tree is a log of edits in `.codoc/log/`. `.codoc/tree.codoc` is a Markdown export of it.
- Never edit anything under `.codoc/` by hand. Use the `codoc` MCP tools.

## Read first

| tool | returns | use it |
|---|---|---|
| `codoc_context(files=[…])` | the features bound to those files, one hop of related features, each with `blocks` (its description, block by block, with ids), `bindings`, and an outline of every title | before any write |
| `codoc_context(feature_id=…)` | that feature, the features under it, and the features owning code it calls or that calls it | before building one feature |
| `codoc_tree` | the whole tree and the open plans | tree-wide work only |
| `codoc_status` | `state` (`synced`, `edited`, `queued`, `realizing`, `reported`, `proposed`), what is left to build (`queued_list`) and what you reported this turn (`reported_list`) | to decide what to do |
| `codoc_history(feature_id)` | a feature's ops and who wrote which words | to see why it reads as it does |

Every read returns `doc_language`. When its `code` is not `en`, write every
title, description and rationale in that language. Identifiers, paths, `codoc:`
targets and anything in backticks stay exactly as they are in the code.

## Edits

`codoc_reflect` and `codoc_propose` take a list of edits:

| `kind` | fields | effect |
|---|---|---|
| `attach` | `feature_id`, `bindings` | the feature owns those symbols |
| `detach` | `feature_id`, `bindings` | it no longer does |
| `add_node` | `id` (temporary, optional), `parent_id` (optional), `title`, `description`, `bindings` | a new feature |
| `amend` | `feature_id`, `title` (optional), `blocks` | change a title or a description |
| `move_node` | `feature_id`, `parent_id` | put a feature under another parent |
| `delete_node` | `feature_id` | propose deleting a feature |

- `bindings` is a list of `"file.py::symbol"` strings.
- `description` is Markdown.
- `amend.blocks` changes a description block by block. Each block in `codoc_context` is `{"id", "text"}`, where `text` is that block as Markdown: a paragraph, a `- ` bullet, a `1. ` step, a whole table, or a whole fenced diagram or snippet.

| entry | effect |
|---|---|
| `{"id": "b-…", "text": "…"}` | that block now reads `text`; keep its marker unless you mean to change what kind of block it is |
| `{"id": "b-…", "delete": true}` | proposes removing that block |
| `{"text": "…"}` | new blocks after the block the previous entry names (at the end of the description when first) |

A block you do not list is unchanged. A list item's indentation is its level, as
`codoc_context` shows it: flush left is level 1, four spaces level 2, eight level 3.

## After you change code: `codoc_reflect(ops, rationale, caused_by)`

One call with everything your change implies.

1. `attach` the symbols you wrote or changed to the feature they belong to. Prefer an existing feature. `add_node` only when nothing covers the new code, and put related new symbols under one node.
2. `amend` a block only when it says something the code does not do, or when you added a capability the description does not mention. Change that block. Leave the others as they are.
3. `rationale` is one plain line saying what the edits change in the tree. It is the note on any plan they open. Describe the code as it is, not what changed: no "now", "no longer" or "updated to", in the rationale or in amended text.
4. When the work came from a hand-off in `.codoc/realize.md`, pass its `d:…` id as `caused_by`. That reports the hand-off and you are done with it. It settles when your turn ends; there is nothing to wait for.

Where it lands:

| edit | lands |
|---|---|
| `attach`, `detach` | at once |
| a small rewording of a block the loop or an agent wrote | at once |
| a larger rewrite, any change to words a person wrote, a new or removed block, `add_node`, `move_node`, `delete_node` | as a plan the person accepts or rejects in the editor |
| anything on a feature with edits the person has not handed off | as a plan |

Tell the user when you left a plan.

## Before you change code: `/codoc:plan <task>`

Proposes the change as tree edits with `codoc_propose` and stops. Accepting the
plan hands the work to you. `/codoc:realize` then builds what is queued.

## Writing a description

{{include:style}}

## Do not

- edit `.codoc/` by hand, or invent or change a `b-…` id;
- add a feature whose title duplicates an existing one (attach instead);
- rewrite a description to restyle it;
- write in a language other than `doc_language`, or translate an identifier.
