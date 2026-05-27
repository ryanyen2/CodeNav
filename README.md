# codoc

codoc maintains a **human-intent-level feature tree** synchronized to a codebase. Each node is a *feature*: a named unit of intent that binds to code chunks across many files. The tree is first-class authored intent — not LLM-derived. Code attribution is a secondary index kept in sync by a two-loop reflective pipeline.

## Two interaction flows

**Flow 1 — Bottom-up (code → tree):**
You (or an agent) change source files → codoc detects what moved → auto-applies safe updates (refresh bindings, small description tweaks) → surfaces structural proposals (add/move/retire nodes) in-situ in `tree.codoc` → you Accept/Reject via the VS Code CodeLens.

**Flow 2 — Top-down (tree → code):**
You (or Claude Code) edit or add features in `tree.codoc` → codoc builds a coding directive → spawns `claude -p` (headless) to write the code → re-reflects to refine the tree if intent was under-specified.

## Claude Code integration

codoc integrates with Claude Code via **hooks + a skill file** — no MCP server,
no VS Code plugin, no port. `codoc init` installs both automatically:

- **Hooks** in `.claude/settings.json` — fire on `SessionStart`/`Stop`/`PreToolUse`/`PostToolUse`
  to maintain `.codoc/activity.json` (live agent touch log → VS Code gutter decorations).
- **Skill** in `.claude/skills/codoc-intent/SKILL.md` — loaded automatically by every
  Claude Code session in the repo; teaches Claude to propose changes via `codoc propose`
  before touching code, then wait for your Accept.

**The propose-then-implement loop:**
1. You ask Claude Code to add/change a feature.
2. Claude runs `codoc propose add_node …` — a plan proposal appears in-situ in `tree.codoc` (no code touched yet).
3. You review the description, Accept in VS Code → verdict → `inbox.json`.
4. Loop B builds a directive from the accepted intent and spawns `claude -p` to write the code.
5. Loop A re-reflects on the written files — may surface additional proposals if intent was under-specified.

## Requirements

- Python 3.11+
- SQLite WAL (bundled)
- cocoindex + LanceDB for incremental embedded vector indexing (auto-installed)
- tree-sitter parsing (Python and TypeScript supported)
- OpenAI-compatible LLM API

## Quick start

```bash
pip install -e .

export CODOC_PROVIDER=openai
export CODOC_MODEL=gpt-4o
export OPENAI_API_KEY=sk-...

cd my-repo
codoc init        # index repo, propose initial tree, write .codoc/tree.codoc
codoc watch       # run both loops as you edit code / tree.codoc
```

## Core commands

```bash
codoc init                # index repo + propose initial feature tree
codoc watch               # daemon: bidirectional sync as you work
codoc watch --dry         # reflect + build directives, but don't spawn the agent
codoc watch --no-realize  # sync the tree but never spawn the coding agent
codoc status              # feature count, pending proposals, recent activity
codoc sync                # one-shot: apply tree edits, then reflect code
```

## The `tree.codoc` file

The only human surface. Located at `.codoc/tree.codoc`:

```
- Authentication flow  ⟨f-3a9c2e⟩
    Handles login, session creation, and token lifecycle.

    Cites [session creation](codoc:auth.py#AuthManager.create_session).

  - Token rotation  ⟨f-7b1d04⟩
      Refreshes session tokens before expiry.

  ~ Legacy password auth  ⟨f-2c8b01⟩
      Deprecated in favour of OAuth.
```

**Markers:**
- `-` — live feature
- `~` — retired feature (struck-through in the IDE)

**IDs** (`⟨f-…⟩`) — stable feature identifiers written by the backend; hidden by
the VS Code extension decoration. Never edit them.

**Inline refs** — `[label](codoc:file.py#symbol)` markdown links cite code.
The parser extracts them; the IDE makes them clickable.

**Indentation** — 2 spaces per level; determines parent/child relationships.

**Proposals** render in-situ, at the tree position where the change would land:

```
- Authentication flow  ⟨f-3a9c2e⟩
    Handles login, session creation, and token lifecycle.

+ - Rate limiting  ⟨e-9f01c2⟩
+     Caps API requests per user per minute.

  - Token rotation  ⟨f-7b1d04⟩
```

`+` add / `-` retire / `~` move·amend. Each block is blank-line terminated.
Accept or Reject using the VS Code CodeLens buttons — no text syntax to type.
Verdicts flow through `.codoc/inbox.json`; the daemon applies them.

## `.codoc/` layout

```
.codoc/
  tree.codoc          — human-authored feature tree (commit with your code)
  tree.bindings.json  — IDE sidecar: feature↔symbol index + dependency edges (v2)
  status.json         — loop lifecycle: in_sync / code_drift / tree_dirty / realizing
  inbox.json          — verdict channel: Accept/Reject writes here, daemon drains it
  codoc.db            — features + bindings + event log (SQLite WAL)
  lancedb/            — cocoindex-managed chunk index: AST + embeddings + hashes
  cocoindex.db/       — cocoindex internal memoization (resumes interrupted indexing)
```

Commit `tree.codoc` (and optionally `codoc.db`) alongside source so the intent
map is versioned with the code.

## Architecture — two loops

**Loop A (code → codoc):** diff the chunk index → auto-apply safe ops (REFRESH,
ATTACH, DETACH, small AMEND) → one LLM pass for anything structural →
structural ops become pending Events (proposals).

**Loop B (codoc → code):** drain `inbox.json` verdicts → parse `tree.codoc`,
diff against store → apply user edits immediately → build a coding directive
from each code-implying op → spawn `claude -p` once → re-run Loop A on what
was written.

A single LLM pass with the full change set plus every existing node title
prevents duplicates. `UNIQUE(file, symbol_path)` in the store ensures a chunk
binds to at most one feature.

## Sidecar schema (v2)

```json
{
  "version": 2,
  "by_feature": { "f-id": [{"file": "path.py", "symbol": "path.py::Class.method"}] },
  "by_file":    { "path.py": [{"symbol": "...", "feature_id": "f-id", "feature_title": "Title"}] },
  "features":   { "f-id": {"title": "Title", "parent_id": null} },
  "feature_edges": { "f-id": [{"to": "f-other", "weight": 4, "kinds": ["call"]}] }
}
```

`feature_edges` aggregates `code_edges` (call/import) into feature-level coupling;
the VS Code extension uses it to dim unrelated features when the cursor rests on a
node that has dependency edges.

## Environment variables

| Var | Default | Description |
|---|---|---|
| `CODOC_PROVIDER` | `openai` | LLM provider (`openai` or `ollama`) |
| `CODOC_MODEL` | `gpt-4o` | LLM model name |
| `OPENAI_API_KEY` | — | OpenAI API key |
| `CODOC_BASE_URL` | — | Custom OpenAI-compatible base URL |
| `CODOC_EMBEDDER_MODEL` | `all-MiniLM-L6-v2` | Sentence-transformer model for embeddings |
| `CODOC_LOG_PROMPTS` | — | Set to `1` to log LLM prompt+response to stderr |

## Tests

```bash
python3.11 -m pytest tests/
```

Fixtures: `test/draco/` (small Python), `test/requests/` (real-world Python),
`test/mosaic/` (TypeScript), `test/altair/`, `test/gofish-python/`, `test/nanochat/`.

---

See [docs/getting-started-claude-code.md](docs/getting-started-claude-code.md) for the full workflow guide and
[docs/how-codoc-works.html](docs/how-codoc-works.html) for the architectural deep-dive.
