# Getting Started — codoc × Claude Code

This guide walks you from a fresh repo to a live integration where:

- **Claude Code** edits files in your session using its own credentials.
- **codoc** observes every code change in real time via the git post-commit hook and `codoc watch`.
- The `.codoc/tree/_index.codoc` file is your single editing surface — proposals appear inline, you accept or edit them in place.
- `git commit` records a semantic snapshot so codoc state is versioned 1:1 with git history.

---

## 1. Prerequisites

- **Python 3.11+**
- **git** (codoc installs a post-commit hook automatically)
- **Claude Code CLI** — install per https://docs.claude.com/en/docs/claude-code (codoc never sees your API key)
- An `OPENAI_API_KEY` (or compatible base URL) for codoc-side LLM calls. codoc uses `gpt-4o-mini` by default; override with `CODOC_MODEL`.

```bash
export OPENAI_API_KEY=sk-...
```

## 2. Install codoc

```bash
git clone <your-fork-or-this-repo>
cd CodeNav
uv pip install -e .
```

Verify:

```bash
codoc --help
```

## 3. Initialise your project

```bash
cd ~/code/my-project
codoc init
```

`codoc init` does everything in one command:

1. Creates `.codoc/` and installs the git post-commit hook.
2. Clusters your codebase and proposes a feature tree (bootstrap). Features are grouped semantically — at most 5 top-level themes with sub-features nested beneath.
3. Prints a summary and waits for your review.

```
Initialized codoc at /path/to/.codoc
Installed git post-commit hook

No features yet — running bootstrap to attribute your codebase ...
[bootstrap:semantic] 3 top-level clusters found.
[bootstrap:semantic] 12 proposals emitted.
  Extracted 87 chunks, emitted 12 proposals.
Review proposals with 'codoc proposals'.
```

## 4. Review and accept bootstrap proposals

```bash
codoc proposals        # list pending
codoc accept --all     # accept everything (re-renders tree automatically)
```

After accepting, `.codoc/codoc.db` holds the canonical feature tree and `.codoc/tree/_index.codoc` is rendered automatically. Open it to see your feature tree:

```
- Authentication flow
    purpose: handle user login, session creation, and token lifecycle
    rationale: centralises auth so no controller handles tokens directly [ref: src/auth.py]
    scenario:
        given a valid username and password
        when  the user submits login credentials
        then  a session token is returned and stored [ref: src/auth.py::create_session]

- Notification dispatch queue
    purpose: queue and flush email + in-app notifications
    ...
```

Commit the `.codoc/` directory to version-control it alongside your code.

## 5. The `.codoc` file — your editing surface

`_index.codoc` is both the rendered view and the edit interface. You write directly into it; `codoc projection sync` reads your changes and applies them to the DB.

### Feature markers

| Marker | Status |
|---|---|
| `- Title` | Live realized feature |
| `* Title` | Placeholder — stub awaiting feedforward |
| `~ Title` | Retired |
| `? kind: slug` | Pending proposal (diff hunk) |

### Inline code references

Two forms — both work, both tracked in the citations table:

```
@create_session                         # shorthand @symbol
[ref: src/auth.py::create_session]      # explicit path::symbol
```

When a referenced symbol moves or is deleted, codoc marks it stale inline (`[⚠ @create_session]`) on the next render.

### Editing fields

Edit any field directly in the file:

```
- Authentication flow
    purpose: handle login, session creation, and token refresh
    rationale: centralized auth means no route handler ever touches tokens @create_session
    scenario:
        given valid credentials
        when  POST /login is called
        then  a fresh token is issued [ref: src/auth.py::create_session]
    needs: token-lifecycle, rate-limiting
```

Save the file, then run:

```bash
codoc projection sync
```

Sync reads your edits, applies them to the DB (AMEND/INTRODUCE/RETIRE transactions), refreshes citations including `@symbol` references, and re-renders the file.

## 6. Placeholder authoring and the feedforward loop

The fastest way to stub a new feature is to write `* Title` anywhere in the file:

```
* Rate limiting
* Audit log
    Admins need a tamper-evident record of who changed what.
```

After `codoc projection sync`, two pending INTRODUCE proposals appear in the file as `+ - Rate limiting` diff hunks. Accept them:

```bash
codoc accept --all
```

The features now exist with `status=placeholder`. If you're running `codoc watch`, it detects the placeholders and calls the **feedforward agent** — an LLM that fills in the missing spec and produces a coding plan:

```
* Rate limiting                         → accepted → placeholder in DB

codoc watch detects placeholder
→ feedforward agent proposes:

? feedforward: rate-limiting
+     purpose: cap API requests per user per minute
+     rationale: token-bucket per user_id prevents bursts without blocking @check_rate_limit
+     scenario:
+         given an authenticated user making repeated requests
+         when  the per-minute limit is exceeded
+         then  HTTP 429 is returned and the quota resets after 60s
+     plan: create rate_limit.py, modify @request_handler
```

Accept the feedforward proposal → realize pipeline spawns `claude -p` to write the code → after Claude exits, the **feedback agent** compares what was actually written against the plan and proposes rationale corrections for any divergences.

### Three stages in one file

| Stage | What you see |
|---|---|
| **Author** | `* Title` or `* Title` + prose — stub with no spec |
| **Feedforward** | `? feedforward: slug` diff hunk — LLM-proposed spec + plan |
| **Feedback** | `? feedback: slug (note)` diff hunk — divergence from plan after Claude ran |

All three render in the same `_index.codoc` file using the same diff-hunk format. Nothing leaves the file.

## 7. Code changes → reflect

Every `git commit` automatically runs the reflective pipeline via the post-commit hook:

```
git add src/auth.py && git commit -m "add rotate_session"

# Hook output:
Reflect complete (HEAD~1..HEAD).
  Changed files    : 1
  Changed chunks   : 4
  Proposals emitted: 3

New proposals:
  [0000001…]  absorb   src/auth.py::rotate_session
  [0000001…]  absorb   src/auth.py::verify_password
  [0000001…]  absorb   src/auth.py::__module__
```

Review proposals with `codoc proposals`, then:

```bash
codoc accept --all
```

The tree re-renders automatically with the new bindings attributed to the right features.

### On-save reflection (no git required)

```bash
codoc reflect --file src/auth.py
```

Runs the same pipeline without a git commit. Useful when editing interactively with Claude Code.

## 8. Continuous watching

`codoc watch` combines both flows in a single daemon:

```bash
codoc watch
```

- **Code changes** (`.py`, `.ts`, `.go`, etc.) → debounced 500ms → `run_reflect_files`
- **`.codoc/tree/*.codoc` changes** → `sync_from_dir` → if new placeholders → feedforward → re-render → if accepted feedforward → realize

```bash
codoc watch --no-realize    # watch and reflect; skip Claude realize pass
codoc watch --dry-realize   # build realize prompt but don't spawn claude
```

## 9. Retiring features

Mark a feature retired by changing its marker to `~` in the file:

```
~ Audit log
```

Then `codoc projection sync`. The feature is retired in the DB; the `~` marker persists in the rendered file as a tombstone.

## 10. The bindings sidecar

Bindings never appear in the human file. They live in `.codoc/tree/_index.bindings.json`:

```json
{
  "019e55b7-c1ee-77d3-…": [
    {"uuid": "…", "file": "src/auth.py", "symbol": "src/auth.py::create_session"},
    {"uuid": "…", "file": "src/auth.py", "symbol": "src/auth.py::rotate_session"}
  ]
}
```

The VSCode extension reads this sidecar for CodeLens ("▸ 7 bindings · auth.py"), hover source previews, and definition navigation — all without an API call.

## 11. The git mental model

codoc tracks semantic intent the way git tracks code. The analogy holds exactly:

| git | codoc |
|---|---|
| Working tree | source code + `_index.codoc` |
| Staging area | pending proposals (`proposal=1` in the DB) |
| `git commit` | `codoc accept` — flips proposal to canonical |
| HEAD | latest accepted transaction HLC |
| History | append-only `transactions` table + `log.jsonl` |
| `git diff <ref>` | `codoc diff HEAD~3` |
| `git log` SHA | `SNAPSHOT` transaction written by post-commit hook |

Every `git commit` automatically writes a `SNAPSHOT` transaction containing the git SHA + the codoc HLC at that moment.

## 12. Optional — VSCode extension

The `vscode-codoc/` extension renders bindings as CodeLens, supports `@symbol` autocomplete and cmd-click navigation, and shows a live gutter pulse when Claude is editing.

```bash
cd $CODOC_REPO/vscode-codoc
npm install
npm run build
# Then F5 in VSCode or "Install from VSIX" against the build output.
```

Open your project. You should see:

- **Status bar**: `$(check) codoc: 12` (feature count when clean) or `$(bell) codoc: 3 proposals`.
- **`_index.codoc` IS the UI**: proposals appear inline with `? ` prefix. CodeLens above each: **Accept / Edit & Accept / Reject**.
- **`@symbol` autocomplete**: type `@` in a rationale or scenario field to autocomplete from your codebase.
- **Cmd-click `@symbol`**: jumps to the symbol definition.
- **Hover `@symbol`**: shows the first 8 lines of the symbol's source.
- **Fold per feature**: collapse any feature block to its title line.

### Key commands

| Command | Shortcut / trigger |
|---|---|
| `codoc: Sync` | Status bar click |
| `codoc: Open tree` | `Cmd+K Cmd+C` |
| Accept proposal | `Cmd+Enter` on a proposal line |
| Reject proposal | `Cmd+Shift+Backspace` on a proposal line |
| Accept all proposals | Command Palette |
| Render (hard refresh) | Command Palette |

## 13. End-to-end smoke test

```bash
# In your project directory
codoc init                  # bootstrap + review
codoc accept --all          # accept proposals, tree renders automatically
```

Open `_index.codoc`. Add a placeholder:

```
* Password reset flow
```

Save, then run:

```bash
codoc projection sync       # creates INTRODUCE proposal
codoc accept --all          # accepts it; feature exists with status=placeholder
```

Now start the watcher:

```bash
codoc watch
```

Within seconds you should see a `? feedforward: password-reset-flow` diff hunk appear in `_index.codoc`. Accept it (`codoc accept --all`) to trigger the realize pipeline.

After `git commit`, the post-commit hook runs reflect automatically. `codoc proposals` lists any new absorb proposals for code that changed.

## 14. Troubleshooting

| Symptom | Likely cause |
|---|---|
| `codoc: offline` in status bar | `codoc server --port 8001` isn't running |
| Proposals appear but tree doesn't re-render | Run `codoc projection render` manually |
| `@symbol` not resolving | Symbol not in any accepted feature's bindings yet; run `codoc reflect --file <path>` |
| Feedforward never fires | `codoc watch` must be running; check it's not stuck on a prior reflect |
| `status=realized` on placeholder | Upgrade: older codoc wrote status without committing — re-run `codoc projection sync` |
| Retire via `~ Title` errors | Make sure there are no other edits on the same feature in the same sync |

To skip the post-commit hook for a single commit:

```bash
git commit --no-verify -m "..."
```

## 15. What's next

- **Browse your tree**: `codoc list` or open `_index.codoc` in VSCode.
- **Add features top-down**: write `* My new feature` with a one-line description, save, let feedforward propose the full spec.
- **See semantic history**: `codoc diff HEAD~3` shows what the feature tree looked like three commits ago.
- **Validate quality**: `codoc gate-run --report` shows accept-verbatim % and light-edit median for your proposals.
- **Search**: `codoc search <term>` fuzzy-matches across slug and intent.

When in doubt, `codoc status` shows features, pending proposals, and the last change timestamp.
