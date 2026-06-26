---
date: 2026-06-25
topic: notion-host-integration
status: requirements
supersedes_note: picks up the "Notion as a first-class host" thread deferred in docs/brainstorms/2026-06-22-agent-native-notebook-protocol-requirements.md
---

# Notion as a codoc Host (Ongoing Authoring Home)

## Outcome

Let someone who lives in **Notion** author and maintain a codoc feature tree from
inside Notion — edit intent, see structural proposals, accept/reject them, and
watch code follow — without opening VS Code or the hub. Notion becomes a real
**host** (per the host contract in the agent-native notebook protocol brainstorm),
not just a read-only mirror. Apple Notes is an explicitly **thinner, later,
likely read-mostly** instance of the same contract.

## Context

- The 2026-06-22 agent-native notebook protocol brainstorm defined a `host contract`
  (R12/R13: render blocks + proposals + lifecycle, route edits to `lower`, write the
  verdict/draft channels, reflect live) and drew the multi-host fan-out
  (webview / standalone / Obsidian / hub). It **deferred Notion** with the note that
  its cloud/block-API model "cannot carry real-time inline overlay + accept/reject
  without degrading it; revisit as a read-mostly host." This brainstorm accepts that
  Notion is a degraded surface and asks what authoring loop *is* workable there.
- **Notion dev platform** offers a real integration surface: REST API, webhooks,
  hosted *Workers* (continuous upsert with declarative schema + cursor), CLI, MCP,
  and an Alpha Agent/External-Agents API. Markdown conversion is built in.
  It has **no formal inline suggestion / accept-reject edit API** — only comments,
  @mentions, and database status properties as structured affordances.
- **Apple Notes** (cf. apples-notes-gpt) is AppleScript `osascript` I/O against an
  HTML body, polling-only, macOS-only — essentially zero structured verdict
  affordances. Hence: Notion first, Notes much later.
- The `codoc serve` hub is already a **file-channel client** (reads `.codoc/*`,
  writes only verdict/draft channels, never `tree.codoc`). The Notion bridge is
  architecturally its sibling.

## Decision: Approach A (page-as-authoring-surface) as the spine

A bridge process mirrors the tree to a **Notion page** (the continuous documentation
article, matching the webview redesign), syncs both ways via Notion API + webhooks,
and routes block edits through `lower`. Structural proposals render as blocks; their
**accept/reject rides a Notion affordance** (button or a small synced status property —
borrowing the database-status mechanism from the rejected Approach B for verdicts
only). Truth stays in `.codoc/`; Notion is a live projection + edit-capture surface.

Rejected alternatives:
- **B — Notion database (rows = features):** native, robust verdicts + sync, but
  sacrifices the continuous-doc reading payoff and has no home for steering (`>`) /
  focus (`**bold**`) signals. Its status-property verdict mechanism is borrowed into A.
- **C — codoc-as-agent Notion calls:** rides the new dev primitives but is
  request/response, not a *living* synced document — too weak on live catch-up, and
  leans on Alpha/waitlisted APIs.

## Requirements

- **R1.** A standalone bridge process (sibling of `codoc serve`) that is a file-channel
  client: reads `.codoc/*`, writes only the verdict/draft channels, never `tree.codoc`.
- **R2.** Render the feature tree to a Notion page as the continuous documentation
  article: nodes → headings/blocks, descriptions → prose, preserving order.
- **R3.** Capture intent edits made in Notion blocks, diff against last-synced state,
  and route code-implying edits through `lower` → realization (respecting the draft /
  hand-off gate).
- **R4.** Surface every structural proposal type (ADD / MOVE / RETIRE / AMEND) as
  Notion blocks, and capture **accept/reject** via a Notion affordance (button or
  synced status property) written back to the verdict channel.
- **R5.** Live catch-up: as realization lands, push updated blocks back to the Notion
  page (host→Notion), so the author sees "the codebase moved."
- **R6.** Map the three markdown-native signals: steering (`>` blockquote) →
  Notion quote blocks; focus (`**bold**`) → preserved bold; external `Consult:` links →
  preserved links. Code citations (`codoc:` URIs) need a Notion-renderable form
  (resolved in planning).
- **R7.** Apple Notes is out of v1 scope but the bridge's contract must not assume
  Notion-only affordances in a way that blocks a later thin/read-mostly Notes host.

## Scope boundaries

**Deferred for later**
- Apple Notes host (thin, read-mostly).
- Real-time co-editing / cursors in Notion (live catch-up ≠ co-editing, per the hub's v1 stance).
- The Agent/External-Agents (Approach C) conversational path.

**Outside this product's identity**
- Letting Notion become the source of truth — `.codoc/` and the local repo stay authoritative.
- Cloud custody of repo access or API keys — realization stays on the maintainer's machine.

## Dependencies / Assumptions

- **Assumption (unvalidated demand):** this brainstorm is exploratory ("wanna see the
  possible integration first") — no observed user has yet asked to author from Notion.
  Treat adoption value as a hypothesis to test, not established.
- Notion API access (integration token / connection) and webhook reachability.
- Block↔markdown round-trip is lossy enough to need explicit fidelity handling.

## Outstanding questions

- **Conflict resolution (primary risk):** the local repo and the Notion page can both
  edit the same node between syncs. Notion gives ~push via webhooks but **no
  single-writer lock** like the webview's authoritative `tree.doc.json`. How is a
  concurrent-edit conflict detected and resolved? (planning)
- Verdict affordance: Notion **button** vs **status property** vs **comment keyword** —
  which is least fragile and most discoverable? (planning)
- How are `codoc:` code citations represented in a Notion page so they stay useful
  (and ideally navigable back to the repo)? (planning)
- Webhook latency / API rate limits vs the "live catch-up" feel. (planning)
