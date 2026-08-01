# Deploying the codoc hub (`codoc serve`)

The hub serves the intent tree to GitHub-authorized remote users **from your own
always-on machine** — no cloud holds your repo, keys, or agent. It binds
localhost; remote reach is an **outbound** tunnel, so no inbound port is opened.

> Status: Tier 1 (async suggest → hand-off → PR). Real-time co-editing is deferred.

## Run it

```bash
pip install -e '.[serve]'     # fastapi + uvicorn + sse-starlette + httpx
codoc serve --root . --port 8787            # localhost only, no auth needed
codoc serve --root . --port 8787 --tunnel   # exposed — REQUIRES auth configured (below)
```

`codoc serve` atomically claims single ownership of the repo and supervises one
`codoc watch` daemon (a VS Code window opened on the same repo defers to it).

**Safe by default.** With no auth configured, the hub binds `127.0.0.1` and serves
the tree openly to that machine only. `--tunnel` (or a non-localhost `--host`) is
**refused** unless GitHub auth is configured — otherwise the whole tree + code map
would be public. To knowingly expose an *unauthenticated* hub (a throwaway demo),
add `--i-understand-unauthenticated`.

## Configure GitHub auth (required to expose the hub)

Register a GitHub **OAuth App** (or a GitHub App with an OAuth-capable client) whose
callback URL is `https://<your-tunnel-host>/auth/callback`, then set:

```bash
export CODOC_GITHUB_CLIENT_ID=...          # the OAuth client id
export CODOC_GITHUB_CLIENT_SECRET=...       # the OAuth client secret
export CODOC_GITHUB_TOKEN=...               # a token WITH PUSH ACCESS to the repo
                                            # (App installation token or maintainer PAT) —
                                            # used ONLY for the collaborator-permission
                                            # check and the PR; NEVER handed to any agent
export CODOC_SERVE_REPO=owner/repo          # the repo being served
# optional:
export CODOC_CONSULT_ALLOWLIST=docs.example,api.foo   # hosts the realize agent may WebFetch
                                                       # (default: NONE — every fetch denied)
export CODOC_SERVE_BASE=main                # PR base branch (default main)
```

With these set, `codoc serve` gates **every** `/api/*` route (reads included) on a
valid session, and the sign-in flow (`/auth/login` → GitHub → `/auth/callback`) is
live.

## Exposure (recommended: Cloudflare Tunnel + Access)

`cloudflared` makes an **outbound-only** connection — no public IP, no inbound
ports. Cloudflare **Access** is the deny-by-default edge gate (GitHub OIDC).

1. Create a **named tunnel** bound to the single local service `http://127.0.0.1:8787`
   (never `0.0.0.0`/the whole host).
2. Put a Cloudflare **Access** policy in front of the hostname (allow only your
   collaborators' GitHub identities).
3. The origin **still** validates the Access JWT *and* runs its own
   collaborator-permission check (defense in depth — `codoc/serve/auth.py`).

**Alternative — Tailscale Funnel/Serve:** stronger isolation if every collaborator
joins your tailnet (Serve is tailnet-private; Funnel is public ingress that never
gets packet access to your tailnet).

## Authorization (GitHub App)

Identity + capability come from a **GitHub App** (not an OAuth App): least
privilege (installed on one repo), short-lived rotating tokens.

- Visitors sign in with the **authorization-code** web flow (`/auth/login` →
  github.com → `/auth/callback`); sessions are server-side, the browser holds only an
  opaque HTTP-only `SameSite=Lax` cookie (`Secure` over https), and GitHub tokens
  never reach the browser.
- A visitor's capability is their **repo-collaborator permission**, looked up with
  the **maintainer/App** token (the `/collaborators/{user}/permission` endpoint needs
  the caller to have push access — never the visitor's token):
  - `read` / `triage` → **suggest** (suggest, comment, withdraw your own)
  - `write` / `maintain` / `admin` → **hand-off** (also accept, hand off)
  - not a collaborator → **denied** (the session is never created)

The live edge is `codoc/serve/github_auth.py` (OAuth exchange + collaborator
resolver, HTTP injected for tests); the decision + sessions are `codoc/serve/auth.py`;
both are wired into the running app by `codoc serve` from the env vars above.

## Safety

- Remote code-implying edits are **held** by default; the **only** suggestion→
  execution crossing is an explicit **hand-off** (write role), consumed by the
  realization trigger.
- State-changing requests require a custom **CSRF** header and pass a per-identity
  **rate limit** (token bucket).
- Realized code lands on a **feature branch as a PR** — never a push to `main`. The
  hub's realize worker (`codoc/serve/realize_hub.py`) fires only on handed-off
  directives and runs each on a dedicated git **worktree**.
- The realize agent runs in an **enforced sandbox** (`codoc/serve/realize_agent.py`):
  Read/Edit/Write/Glob/Grep only (**no Bash**), edits confined to the directive's
  scope, secret/CI/manifest paths refused, and WebFetch allowed **only** for hosts in
  `CODOC_CONSULT_ALLOWLIST` that resolve to a public IP (SSRF-hardened). The agent's
  environment is **scrubbed of every GitHub token** — only the orchestrator opens the
  PR. If the sandbox can't be enforced (SDK missing/unsupported), the agent runs
  **nothing** rather than falling back to an unsandboxed run.
