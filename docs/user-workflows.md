# codoc — supported user workflows

A task-oriented guide to the ways you can work with codoc *today*. codoc keeps a
**feature tree** — a navigable map of what your code is *for* — in sync with the
code, in both directions. This doc is organized by **who's working** (you alone, a
team sharing a repo, or remote contributors); for the mechanics of each direction
see the README's [Flow 1 / Flow 2 / Flow 3](../README.md).

> **One-line auth answer:** the local workflows need **no codoc or GitHub auth** —
> only your LLM provider key (or the keyless Claude Code login). The *one* exception
> is the deployed hub for remote contributors, which uses a **GitHub App**.

| Workflow | Who | Where edits happen | Auth you set up |
|---|---|---|---|
| **1. Solo** | one person | VS Code (or CLI) on your machine | none (LLM key only) |
| **2. Team via git** | several people, each with a checkout | each person's own VS Code | none (LLM key only) |
| **3. Deployed hub** | remote contributors with **no checkout** | a browser, served from one machine | a **GitHub App** |

---

## Workflow 1 — Solo (the default)

You edit code and intent on one machine; codoc keeps them aligned.

1. **Install + init.** Install the **codoc** VS Code extension and run *"codoc: Set
   up codoc"* (or `uv tool install codoc` + `codoc init`). This indexes the repo,
   proposes the initial tree, and starts the `codoc watch` daemon. See
   [getting-started](getting-started-claude-code.md).
2. **Edit code → the tree follows.** Save source files; codoc auto-applies safe
   updates and surfaces structural proposals (add/move/retire) inline in
   `tree.codoc` with **✓ / ✗** CodeLens. (Flow 1.)
3. **Edit intent → the code follows.** Edit a feature's title/description in the
   **Codoc Tree** view (or ask Claude Code to). An edit that *requests* code
   ("should…", "Add…", an accepted plan node) queues a directive; you run
   **`/codoc:sync`** in your Claude Code session to implement it. Purely
   *describing* existing code never triggers a build. (Flow 2.)

Nothing leaves your machine; the only network call is to your LLM provider.

## Workflow 2 — A team sharing one repo (local + git)

Several developers, each with their own checkout, sharing the feature tree through
your normal git remote (GitHub, GitLab, a bare repo — codoc doesn't care).

- **Commit** `.codoc/tree.codoc` (and the webview's `.codoc/tree.doc.json`). The
  feature tree is your team's shared intent, versioned with the code.
- **Don't commit** the rest of `.codoc/` (`codoc.db`, `lancedb/`, `cocoindex.db/`,
  the transient control files) — it's **derived per checkout** and rebuilt by
  `codoc init` / `codoc watch`. The shipped `.gitignore` already encodes this split.
- **Each teammate runs their own `codoc watch`** on their own checkout — there is
  no shared daemon and no codoc server.
- **Concurrent edits merge as text.** Two people editing `tree.codoc` resolve
  through a **normal git merge**; codoc re-attributes code to the merged tree on
  the next pass. (Today codoc is single-writer *per checkout*, and the change
  ledger records every human edit as `actor=human` — *which* teammate edited is
  not modeled yet.)

This is the right model when everyone can clone the repo and run codoc locally.

## Workflow 3 — Remote contributors via the deployed hub (`codoc serve`)

For collaborators who **don't have a checkout** (a designer, a PM, a contributor on
another machine). You run the **deployed hub** from your always-on machine; they
open a link and edit intent in the browser — the *same* editor, no install.

### Start it from VS Code

- Click **`$(broadcast) Share`** in the status bar, or run **"codoc: Share — start
  the deployed hub"** from the Command Palette.
- The hub starts at `http://127.0.0.1:8787`; the extension offers **Open in
  browser**, **Copy link**, and **Remote access…**. (Equivalent CLI:
  `codoc serve --root . --port 8787`.)
- This local link is for *this machine / your LAN*. For genuinely remote
  contributors, use **"codoc: Share remotely — start the hub over a tunnel"**
  (`codoc serve --tunnel`) and complete the one-time deploy setup below.

### How collaborators work (Tier 1: async suggest → hand-off → PR)

1. They open the link and **sign in with GitHub**. Their repo-collaborator
   permission sets what they can do: **read → suggest** (suggest edits, comment),
   **write → hand-off** (also accept and hand work to the agent); non-collaborators
   are denied.
2. They edit intent in the browser. Code-implying edits are **held by default** —
   nothing touches your repo or spends budget. Status streams live (offline edits
   queue and sync on reconnect).
3. You (or any write-collaborator) **hand off** an accepted suggestion. Only then
   does the hub realize it — on an isolated **git worktree**, with the agent in an
   enforced sandbox (no secrets, no CI/settings) — opening a **code PR**, never a
   push to `main`.
4. After the PR merges, the daemon re-indexes and everyone's tree catches up.

The hub is a **separate process** (peer to the VS Code extension): it supervises
the daemon and only ever writes the verdict/draft channels — never `tree.codoc`,
never code outside the worktree.

### One-time deploy setup (for the tunnel path)

Remote reach needs a **GitHub App** (identity + the collaborator-permission gate)
and a tunnel (cloudflared / Tailscale), optionally fronted by **Cloudflare Access**
as a deny-by-default edge gate. The GitHub App client id/secret + installation key
and the tunnel are deploy-time configuration — full instructions in
[`serve-deployment.md`](serve-deployment.md). Real-time co-editing is a planned
fast-follow; today's hub is async (suggest → hand-off → PR).

---

## Which workflow am I?

- **Just me** → Workflow 1. No setup beyond the extension.
- **My team, everyone clones the repo** → Workflow 2. Commit `tree.codoc`; the
  rest is automatic.
- **Someone needs to weigh in without cloning** → Workflow 3. Click **Share**;
  add the GitHub App + tunnel when they're off your machine.

See also: [getting-started](getting-started-claude-code.md) ·
[README flows](../README.md) · [collaborative-editing model
(human↔agent)](codoc-collaborative-editing-model.md) ·
[deploy reference](serve-deployment.md).
