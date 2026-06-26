# Notion bridge — deployment

The Notion bridge (`codoc notion`) makes a **Notion page** an ongoing authoring
surface for the feature tree: edit intent in the page, see ADD/MOVE/RETIRE/AMEND
proposals as callouts, accept/reject them with comment commands, and watch code
follow. It is the architectural sibling of `codoc serve` — a separate, file-channel
process that **defers** to an existing daemon and never writes `tree.codoc`.

See `docs/architecture.md` for internals and `CLAUDE.md` for the one-paragraph
overview. This doc is setup + operations.

## Prerequisites

- `pip install -e '.[notion]'` (adds `notion-client`, `fastapi`, `uvicorn`).
- A running daemon: `codoc watch` (or `codoc serve`). The bridge **defers** to it —
  it refuses to start if no daemon owns the repo, and never spawns its own.
- A Notion **internal connection** (formerly "integration") and its token.

## 1 · Create an internal connection

1. In Notion: **Settings → Connections → Develop or manage integrations → New
   integration** → internal, scoped to your workspace.
2. Copy the **token** (an "installation access token" / bearer token).
3. Grant content + comment capabilities (read content, update content, read
   comments, insert comments).

## 2 · Share the page with the connection

Notion is grant-by-sharing: the connection sees nothing until you add it.

1. Create (or pick) the page that will hold the tree.
2. On that page: **••• → Connections → Add connections →** your connection. This
   grants the page **and all children** — the bridge writes the whole subtree.
3. Copy the page id from its URL (the 32-hex id).

## 3 · Configure the environment

| Variable | Required | Meaning |
|---|---|---|
| `CODOC_NOTION_TOKEN` | yes | the connection's bearer token |
| `CODOC_NOTION_PAGE_ID` | yes | the page that holds the tree |
| `CODOC_NOTION_VERSION` | no | API version pin (default `2026-03-11`) |
| `CODOC_NOTION_POLL_INTERVAL` | no | polling seconds (default `60`) |
| `CODOC_NOTION_WEBHOOK_SECRET` | no | the webhook `verification_token`; absent ⇒ polling-only |

## 4 · Run

```bash
codoc watch            # (terminal 1) the daemon the bridge defers to
codoc notion           # (terminal 2) the bridge — polling mode if no webhook secret
```

## Webhook vs polling

- **Polling (default).** No inbound port; fits codoc's local-first identity. The
  bridge polls the page's `last_edited_time` every `CODOC_NOTION_POLL_INTERVAL`
  seconds. Higher latency, steady API spend against Notion's ~3 req/sec ceiling.
- **Webhook (lower latency).** Set `CODOC_NOTION_WEBHOOK_SECRET` and expose
  `POST /notion/webhook`. This is the repo's **first inbound network boundary** — the
  serve hub deliberately used outbound tunnels. Every event's `X-Notion-Signature` is
  verified (HMAC-SHA256 on the raw body, timing-safe) before any parse; the one-time
  `verification_token` handshake is answered automatically; deliveries are deduped by
  `deliveryId` and reordered by `timestamp`. Events are signals only — the bridge
  reconciles from the API.

## Security posture

- **Webhook signature** is mandatory; forged/tampered events are rejected (401).
- **Consult links** authored in Notion are filtered through the SSRF guard
  (https-only, default-empty host allowlist, resolve-and-pin public IPs) before the
  realizing agent fetches them.
- **Notion content is data, never instructions.** The only command surface is the
  explicit `/accept` · `/reject` verdict in a comment on a proposal callout.
- Token and repo access stay on the machine running the bridge; nothing is delegated
  to Notion's cloud.

## Authoring model (what works, what's degraded)

- **Edit intent** in a feature's toggle → an `AMEND` flows to code (auto-handed-off).
- **Create a feature** → add a toggle; it is authored as a new node (descriptive by
  default — building code is the plan/imperative path, as elsewhere in codoc).
- **Move** → drag a toggle under another; **steer** → add a quote block under a
  feature.
- **Accept/reject a proposal** → comment `/accept` or `/reject` on its callout
  (Notion has no inline ✓/✗ — this is the accepted degraded-surface trade-off).

## Known v1 limitations

- **Conflict resolution is best-effort.** Notion exposes no lock or conditional
  write; concurrent edits to the same node between syncs can lose a change. Content
  writes use context-anchored search/replace that fails loudly rather than clobbering;
  true per-node merge (CRDT) is deferred.
- **Block-level write reconciliation** (precise create/update/delete diffing against
  live Notion) is landed incrementally and validated against a live workspace; cold
  authoring works today.
- **Typed-media blocks** (diagram/screenshot) are not rendered into Notion yet — prose
  only. When added they must match the host-conformance `canonical_block_view`.
- **Retire** is via the proposal flow, not by deleting a toggle (deletions are not
  treated as retires, by design — too easy to do by accident).
- Single internal connection / one workspace; multi-workspace OAuth is deferred.
