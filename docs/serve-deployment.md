# Deploying the codoc hub (`codoc serve`)

The hub serves the intent tree to GitHub-authorized remote users **from your own
always-on machine** — no cloud holds your repo, keys, or agent. It binds
localhost; remote reach is an **outbound** tunnel, so no inbound port is opened.

> Status: Tier 1 (async suggest → hand-off → PR). Real-time co-editing is deferred.

## Run it

```bash
pip install -e '.[serve]'     # fastapi + uvicorn + sse-starlette
codoc serve --root . --port 8787            # localhost only
codoc serve --root . --port 8787 --tunnel   # + a cloudflared tunnel
```

`codoc serve` atomically claims single ownership of the repo and supervises one
`codoc watch` daemon (a VS Code window opened on the same repo defers to it).

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

- Visitors sign in with the **auth-code + PKCE** web flow; sessions are
  server-side, HTTP-only `Secure` `SameSite` cookies (GitHub tokens never reach
  the browser).
- A visitor's capability is their **repo-collaborator permission**, looked up with
  the **maintainer/App-installation** identity (the `/collaborators/{user}/permission`
  endpoint needs the caller to have push access):
  - `read` / `triage` → **suggest** (suggest, comment, withdraw your own)
  - `write` / `maintain` / `admin` → **hand-off** (also accept, hand off)
  - not a collaborator → **denied**

> The live OAuth endpoints + GitHub-API resolver are wired from App config
> (client id/secret, installation key) at deploy time; the authorization decision
> + sessions are implemented and tested in `codoc/serve/auth.py`.

## Safety

- Remote code-implying edits are **held** by default; the **only** suggestion→
  execution crossing is an explicit **hand-off** (write role), consumed by the
  realization trigger.
- State-changing requests require a custom **CSRF** header and pass a per-identity
  **rate limit** (token bucket).
- Realized code lands on a **feature branch as a PR** — never a push to `main`;
  the agent runs in an **enforced sandbox** with a scoped token it never holds.
