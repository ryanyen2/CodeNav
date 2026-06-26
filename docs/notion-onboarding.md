# Notion bridge — onboarding (first-time setup)

A hand-held, step-by-step walkthrough to connect codoc to a Notion page so you can
author the feature tree from Notion and watch code follow. For the operational
reference (env-var table, ongoing ops) see [`notion-deployment.md`](notion-deployment.md);
for internals see `docs/architecture.md`.

The Notion-side steps below were fact-checked against Notion's developer docs
(June 2026). Notion occasionally renames UI surfaces — where a label might drift, the
doc link is cited so you can confirm. Notion's terminology recently moved from
"integrations" to **connections**, and the token is now the **installation access
token** (formerly "Internal Integration Secret").

---

## What you'll end up with

```
Notion page  ⇄  codoc notion (bridge)  ⇄  .codoc/*  ⇄  codoc watch (daemon)  ⇄  your code
```

You edit intent in a Notion page; the bridge syncs it into codoc's channels; the
daemon realizes code and reflects results back into the page.

---

## Prerequisites

1. **codoc installed with the Notion extra:**
   ```bash
   pip install -e '.[notion]'
   ```
2. **A repo already initialized with codoc** (`codoc init` has been run, so `.codoc/`
   exists with a feature tree).
3. **A running daemon.** The bridge *defers* to it and refuses to start otherwise:
   ```bash
   codoc watch        # leave running in its own terminal
   ```
4. **A Notion account** with permission to create connections in your workspace
   (workspace owners; or a member if your workspace allows member-created
   connections).

---

## Step 1 · Create an internal Notion connection

> Source: [Create a Notion integration](https://developers.notion.com/docs/create-a-notion-integration)

1. Go to **https://www.notion.so/profile/integrations** (the developer portal).
2. In the sidebar's **Build** section, select **Internal connections**, then click
   **Create a new connection** (older docs/screens may still say "New integration").
3. Give it a name (e.g. `codoc`) and pick the **workspace** it can be installed in.
4. Open the **Configuration** tab and copy the **installation access token**. Treat it
   like a password — it grants API access to every page you later share with this
   connection.
   - Keep this tab handy; you'll paste the token into `CODOC_NOTION_TOKEN` in Step 3.
5. Confirm the connection has content + comment capabilities (read content, update
   content, read comments, insert comments). These are the defaults for an internal
   connection; if your portal exposes a capabilities toggle, ensure read/update
   content and comment read + insert are enabled (the bridge reads the page, writes
   updates, and reads/replies to verdict comments).

> ⚠️ If the token is ever exposed, return to **Configuration** and refresh the
> installation access token, then update `CODOC_NOTION_TOKEN`.

---

## Step 2 · Create and share the tree page

A fresh connection can see **nothing** until you explicitly share a page with it.
This is the #1 cause of empty results / 404s.

> Source: [Create a Notion integration → sharing](https://developers.notion.com/docs/create-a-notion-integration)

1. In Notion, create (or pick) the page that will hold your feature tree — e.g. a
   page titled **"<your-repo> · codoc tree"**. Leave it empty; the bridge populates it.
2. On that page, click the **`•••`** (More) menu in the top-right corner.
3. Scroll to **`+ Add Connections`**.
4. Search for your connection (`codoc`) and select it.
5. **Confirm** the connection can access the page **and all of its child pages**
   (the bridge writes the whole subtree under this page).
6. Copy the **page id** from the page URL. It's the 32-character hex string at the end
   of the URL (ignore any `?v=` query and the title slug):
   ```
   https://www.notion.so/My-codoc-tree-1a2b3c4d5e6f7081920a1b2c3d4e5f60
                                        └────────── page id ───────────┘
   ```
   You'll paste this into `CODOC_NOTION_PAGE_ID` in Step 3.

---

## Step 3 · Configure codoc

Export the two required variables (and optionally the version pin / poll interval):

```bash
export CODOC_NOTION_TOKEN="ntn_xxx…"          # the installation access token (Step 1)
export CODOC_NOTION_PAGE_ID="1a2b3c4d…5f60"   # the tree page id (Step 2)
# optional:
export CODOC_NOTION_VERSION="2026-03-11"      # API version pin (default shown)
export CODOC_NOTION_POLL_INTERVAL="60"        # seconds between polls (default 60)
```

> Tip: put these in a local `.env` / shell profile, not in the repo.

---

## Step 4 · First run (polling mode)

With `codoc watch` already running in another terminal:

```bash
codoc notion
```

You should see a line like:
```
codoc notion · polling · deferring to daemon · /path/to/.codoc
```

- If it says **"no codoc daemon owns this repo"**, start `codoc watch` first (Step 0
  prerequisite) — the bridge never spawns its own daemon.
- If it errors that the `notion` extra is missing, re-run `pip install -e '.[notion]'`.

Within a poll interval, your feature tree appears in the Notion page as a set of
**nested toggles** (one per feature; children nested inside). Editing happens here.

---

## Step 5 · (Optional) Enable webhooks for low latency

Polling (Step 4) works with zero extra setup. Webhooks cut latency to seconds but
require a **public HTTPS endpoint** — Notion explicitly cannot deliver to `localhost`.

> Source: [Webhooks](https://developers.notion.com/reference/webhooks)

1. **Expose the bridge's webhook endpoint publicly.** The bridge serves
   `POST /notion/webhook` on the `--port` you pass (default `8788`). For a
   maintainer's machine, tunnel it (codoc ships a cloudflared helper used by
   `codoc serve`; ngrok works too):
   ```bash
   codoc notion --port 8788        # terminal A
   cloudflared tunnel --url http://127.0.0.1:8788   # terminal B → prints an https URL
   ```
2. In the developer portal, open your connection's settings → **Webhooks** tab →
   **`+ Create a subscription`**.
3. Enter your public **Webhook URL** ending in `/notion/webhook`
   (e.g. `https://<your-tunnel>.trycloudflare.com/notion/webhook`). It **must be HTTPS
   and publicly reachable** — localhost is rejected.
4. Choose event types. At minimum select **`page.content_updated`** and
   **`comment.created`** (block edits surface as `page.content_updated`; there are no
   block-level events). `page.created`/`deleted`/`moved` and `comment.updated` are also
   useful.
5. Click **`Create subscription`**.
6. **Verify.** Notion immediately sends a one-time POST containing a
   `verification_token`. The bridge logs/echoes it. Copy that token, then in the
   **Webhooks** tab click **`⚠️ Verify`**, paste the token, and click
   **`Verify subscription`**. (If none arrived, use **`Resend token`**.)
7. Set the token as the bridge's signing secret and restart the bridge:
   ```bash
   export CODOC_NOTION_WEBHOOK_SECRET="<the verification_token>"
   codoc notion --port 8788
   ```
   The bridge now verifies every event's `X-Notion-Signature`
   (`sha256=<HMAC-SHA256(body, verification_token)>`, timing-safe) before acting, and
   reconciles from the API on each signal. The startup line changes to
   `… · webhook+polling · …`.

> You can change subscribed events anytime, but the **URL is fixed after
> verification** — to change it, delete and recreate the subscription (and update
> `CODOC_NOTION_WEBHOOK_SECRET` with the new token).

---

## Step 6 · Try the authoring loop

1. **Edit intent.** In the Notion page, open a feature's toggle and edit its
   description paragraph. Within a sync, the daemon picks it up and (because the
   Notion host is authoritative) realizes the code change. Watch your repo.
2. **Add a feature.** Add a new toggle with a title + a description paragraph. It's
   authored as a new feature node (descriptive by default — building code follows
   codoc's normal plan/imperative rules).
3. **Steer the agent.** Add a quote block under a feature ("> please add tests") — it
   becomes a one-shot steering directive.
4. **Decide a proposal.** When codoc proposes a structural change, it appears as a
   colored **callout** in the page. **Comment `/accept` or `/reject`** on that callout
   to decide (Notion has no inline ✓/✗ — this comment command is the verdict surface).

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Page stays empty after `codoc notion` starts | The connection isn't shared with the page — redo Step 2 (`••• → + Add Connections`). |
| `no codoc daemon owns this repo` | Start `codoc watch` (or `codoc serve`) first. |
| `the 'notion' extra is not installed` | `pip install -e '.[notion]'`. |
| Webhook events never arrive | URL isn't public/HTTPS (localhost is rejected) — tunnel it; or the subscription isn't verified yet (Step 5.6). |
| Webhook returns 401 | Signature mismatch — `CODOC_NOTION_WEBHOOK_SECRET` must be exactly the `verification_token` from Step 5.6. |
| `rate_limited` / slow syncs | Notion caps ~3 requests/sec per connection; large trees sync incrementally. Raise `CODOC_NOTION_POLL_INTERVAL` if polling aggressively. |
| An edit didn't reach code | Confirm the daemon is running and not `--dry`; check `.codoc/status.json` / `realize.md`. |

## Known v1 limitations

See [`notion-deployment.md` → Known v1 limitations](notion-deployment.md#known-v1-limitations):
best-effort conflict resolution (no Notion lock), incremental block-write
reconciliation, prose-only rendering (typed-media blocks deferred), retire-via-proposal
(not by deleting a toggle), and single-workspace internal-connection auth.
