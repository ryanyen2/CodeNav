# Deploying the hub (`codoc serve`)

The hub serves the tree to the repository's GitHub collaborators from the
maintainer's own machine. It binds localhost; remote reach is an outbound
tunnel, so no inbound port is opened and no cloud holds the repo or a key.

## Run it

```bash
pip install -e '.[serve]'                  # fastapi + uvicorn + sse-starlette + httpx
codoc serve --root . --port 8787           # localhost only; the one viewer is the owner
codoc serve --root . --port 8787 --tunnel  # exposed: requires GitHub auth (below)
```

`codoc serve` claims single ownership of the repo (`.codoc/serve.lock`),
supervises one `codoc watch` daemon, and serves the same editor bundle the
extension uses (`vscode-codoc/dist/webview`; `--static-dir` or
`$CODOC_STATIC_DIR` to point elsewhere). Exposing it without auth is refused.

## GitHub auth

Register an OAuth App whose callback is `https://<tunnel-host>/auth/callback`,
then:

```bash
export CODOC_GITHUB_CLIENT_ID=…
export CODOC_GITHUB_CLIENT_SECRET=…
export CODOC_GITHUB_TOKEN=…        # a token WITH PUSH ACCESS: used only to read collaborator permissions
export CODOC_SERVE_REPO=owner/repo
export CODOC_TUNNEL_CONFIG=…       # optional: a cloudflared named-tunnel config
```

A visitor signs in; their collaborator permission becomes their **role** in the
calculus (`Roles`, spec §7.3): `admin`/`write`/`maintain` → `maintainer`,
`read`/`triage` → `suggest`, anyone else is denied. The hub records the role in
`.codoc/cache/roles.json`; the daemon applies every op from
`inbox/hub-<login>.jsonl` as `human:<login>` under that role, so a suggester's
edits can only be plans of their own and a forged verdict is a recorded
`authority` conflict. The browser holds an opaque cookie, never a token.

## What travels

Nothing new. A contributor's ops are in the daemon's session logs like
anyone's; a realized change is a PR whose logs carry the tree's history with
it. The hub does not build hand-offs itself yet: a maintainer's daemon (or
`codoc realize` on a checkout that has the logs) does. The PR worker on a
worktree is the next piece; until then a hand-off on the hub queues the
directive for whoever runs the realize pass.

## Routes

`/` the editor · `/auth/login`, `/auth/callback`, `/api/logout` ·
`/api/whoami` · `/api/events` (SSE: `projection`, `status`, this viewer's
`acks`) · `/api/message` (POST with `x-codoc-csrf`: `submit`, `set-language`
for maintainers) · `/api/media/<name>` · `/healthz`.
