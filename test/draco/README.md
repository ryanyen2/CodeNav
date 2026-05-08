# test/draco

Small Python codebase (Draco visualization recommendation system) used as a fixture for CodeNav integration tests.

## Semantic tree flow test

`test_semantic_tree_flow.py` simulates the bidirectional semantic tree ↔ codebase loop:

1. **Sync (code → tree):** Calls `POST /semantic_tree/sync` on this directory, saves the returned tree to `semantic_tree.md`.
2. **Edit tree:** Applies a meaningful edit to the tree (e.g. change a feature line), then calls `POST /semantic_tree/tree_edit` and checks that the response includes operations and target code locations (fpath, entity_name, line_range).
3. **Edit code:** Adds a trivial function to `run.py`, calls sync again, and asserts the tree updates (incremental) and entity count increases.

### Run

From **server/** (so `uv` and `requests` are available and `server/.env` is used):

```bash
cd server
uv run python ../test/draco/test_semantic_tree_flow.py
```

Start the API server first in another terminal: `cd server && uv run python main.py`.

### Generated files

- **semantic_tree.md** — Last synced semantic tree (markdown). User can edit this and re-run tree_edit or use it as the base for manual edits.
- **semantic_tree_edited.md** — Example edited tree produced by the test (one feature line changed).
- **.codenav/** — Sync state and index (sync_state.json, index/). Created by the first sync; used for incremental sync and for tree_edit base when using `path`.

### Env

Loaded from `server/.env` when the script runs. `CODENAV_API_BASE` (default `http://localhost:8001`), `CODENAV_LLM_PROVIDER`, `CODENAV_LLM_MODEL`, and embedder/LLM keys must be set so sync and tree_edit work.
